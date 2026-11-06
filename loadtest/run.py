#!/usr/bin/env python3
"""Runs the load test against the Docker Compose stack and writes a results file.

    python3 loadtest/run.py                      # defaults: 200 viewers, 50 bidders, 60 s
    python3 loadtest/run.py --viewers 500 --bidders 100 --duration 120

Steps:
1. Start the stack with loadtest/compose.yml on top (simulated bidders off).
2. Create a fresh auction through the admin API.
3. Read GET /api/metrics, run k6 (loadtest/bid_and_watch.js), read it again.
4. Check what Postgres saved: no accepted bid missing, amounts rising with seq,
   the auction's high bid equal to its top saved bid.
5. Write loadtest/results/<UTC time>-<commit>.json and print a summary.

Exits 1 if a check fails. Needs only Python 3.11+ and Docker Compose; no packages.
Numbers describe the machine this ran on, which the results file records.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "loadtest" / "results"
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "loadtest/compose.yml"]
REJECT_REASONS = ("too_low", "too_high", "closed", "paused", "not_found", "duplicate_bid_id")


def main() -> int:
    args = parse_args()
    base_url = f"http://localhost:{os.environ.get('BACKEND_PORT', '8000')}"
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin")

    if not args.no_up:
        print("Starting the stack (simulated bidders off)...", flush=True)
        build = [] if args.no_build else ["--build"]
        compose("up", "--detach", *build, "--wait", "--wait-timeout", "300", "backend")

    started_at = datetime.now(UTC)
    auction = create_auction(base_url, admin_password, args)
    auction_id = auction["id"]
    print(f"Auction {auction_id} created.", flush=True)

    before = get_json(f"{base_url}/api/metrics")
    print(
        f"Running k6: {args.viewers} viewers, {args.bidders} bidders, "
        f"a bid every ~{args.bid_interval_ms} ms each, for {args.duration} s...",
        flush=True,
    )
    k6 = run_k6(auction_id, args)
    after = get_json(f"{base_url}/api/metrics")

    k6_numbers = summarize_k6(k6, args.duration)
    server = metrics_delta(before, after)
    saved = check_postgres(auction_id)
    checks = make_checks(k6_numbers, server, saved)

    result = {
        "label": args.label,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "commit": git_commit(),
        "machine": machine(),
        "parameters": {
            "viewers": args.viewers,
            "bidders": args.bidders,
            "bid_interval_ms": args.bid_interval_ms,
            "duration_s": args.duration,
            "start_price": args.start_price,
            "auction_id": auction_id,
            "target": "docker compose (loadtest/compose.yml), k6 inside the Compose network",
        },
        "client": k6_numbers,
        "server": server,
        "postgres": saved,
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "k6_summary": k6,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{stamp}-{result['commit']['short']}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print_summary(result)
    print(f"\nResults: {path.relative_to(REPO_ROOT)}")

    if args.down:
        compose("down")
    return 0 if result["passed"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--viewers", type=int, default=200, help="open auction pages")
    parser.add_argument("--bidders", type=int, default=50, help="signed-in bidders")
    parser.add_argument(
        "--bid-interval-ms", type=int, default=1000, help="mean pause between one bidder's bids"
    )
    parser.add_argument("--duration", type=int, default=60, help="seconds of bidding")
    parser.add_argument("--start-price", type=int, default=10_000, help="minor units")
    parser.add_argument("--label", default="", help="free text saved in the results file")
    parser.add_argument("--no-up", action="store_true", help="the stack is already running")
    parser.add_argument("--no-build", action="store_true", help="use the images already built")
    parser.add_argument("--down", action="store_true", help="stop the stack afterwards")
    return parser.parse_args()


def compose(*args: str, capture: bool = False) -> str:
    completed = subprocess.run(
        [*COMPOSE, *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return completed.stdout or ""


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def create_auction(base_url: str, admin_password: str, args: argparse.Namespace) -> Any:
    # Open well past the run, so the close never lands inside it.
    end_at = datetime.now(UTC) + timedelta(seconds=args.duration + 600)
    body = json.dumps(
        {
            "title": f"Load test {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
            "start_price": args.start_price,
            "end_at": end_at.isoformat(),
        }
    ).encode()
    request = urllib.request.Request(
        f"{base_url}/api/admin/auctions",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Admin-Password": admin_password},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        sys.exit(f"Creating the auction failed: {error.code} {error.read().decode()}")


def run_k6(auction_id: str, args: argparse.Namespace) -> Any:
    env = {
        "AUCTION_ID": auction_id,
        "START_PRICE": str(args.start_price),
        "VIEWERS": str(args.viewers),
        "BIDDERS": str(args.bidders),
        "BID_INTERVAL_MS": str(args.bid_interval_ms),
        "DURATION_S": str(args.duration),
    }
    env_flags = [flag for name, value in env.items() for flag in ("-e", f"{name}={value}")]
    output = compose(
        "run",
        "--rm",
        "--no-deps",
        *env_flags,
        "k6",
        "run",
        "--quiet",
        "/scripts/bid_and_watch.js",
        capture=True,
    )
    begin, end = output.find("SUMMARY_BEGIN"), output.find("SUMMARY_END")
    if begin < 0 or end < 0:
        sys.exit(f"k6 printed no summary. Its output:\n{output}")
    return json.loads(output[begin + len("SUMMARY_BEGIN") : end])


def _values(k6: Any, name: str) -> dict[str, Any]:
    metric = k6.get("metrics", {}).get(name)
    return metric["values"] if metric else {}


def _trend(k6: Any, name: str) -> dict[str, float | None]:
    values = _values(k6, name)
    return {
        "samples": values.get("count", 0),
        "p50": values.get("med"),
        "p95": values.get("p(95)"),
        "p99": values.get("p(99)"),
        "max": values.get("max"),
    }


def _count(k6: Any, name: str) -> int:
    return int(_values(k6, name).get("count", 0))


def summarize_k6(k6: Any, bidding_s: int) -> dict[str, Any]:
    """What the k6 clients measured. Bids per second is over the bidding time."""
    accepted = _count(k6, "bids_accepted")
    rejected = {
        reason: _count(k6, f"bids_rejected{{reason:{reason}}}") for reason in REJECT_REASONS
    }
    errors = _count(k6, "bids_error")
    sent = accepted + sum(rejected.values()) + errors
    run_s = k6.get("state", {}).get("testRunDurationMs", 0) / 1000
    ended_right = _values(k6, "viewer_ended_on_final_state")
    return {
        "bids": {
            "sent": sent,
            "accepted": accepted,
            "rejected": rejected,
            "error": errors,
            "per_second": round(sent / bidding_s, 1),
        },
        "bid_latency_ms": _trend(k6, "bid_latency"),
        "bid_to_own_update_ms": _trend(k6, "bid_to_update"),
        "websockets": {
            "connections": _trend(k6, "ws_connect")["samples"],
            "connect_ms": _trend(k6, "ws_connect"),
            "messages": _count(k6, "ws_messages"),
            # A gap in seq made the page refetch the snapshot, as the front end does.
            "resyncs": _count(k6, "ws_resyncs"),
            "out_of_order": _count(k6, "ws_out_of_order"),
            "unexpected_closes": _count(k6, "ws_unexpected_closes"),
            "updates_behind_at_end": _count(k6, "ws_behind_at_end"),
            "viewers_on_final_state": ended_right.get("passes", 0),
            "viewers_checked": ended_right.get("passes", 0) + ended_right.get("fails", 0),
        },
        "run_duration_s": round(run_s, 1),
    }


def metrics_delta(before: Any, after: Any) -> dict[str, Any]:
    """What the server counted during the run (GET /api/metrics, after minus before).
    Latency is the server's recent window, which the run dominates once it has
    recorded at least that many bids; otherwise it mixes in earlier bids."""
    restarted = before["started_at"] != after["started_at"]
    base = {"accepted": 0, "error": 0, "rejected": {}} if restarted else before["bids"]
    return {
        "server_restarted_during_run": restarted,
        "bids": {
            "accepted": after["bids"]["accepted"] - base["accepted"],
            "rejected": {
                reason: count - base["rejected"].get(reason, 0)
                for reason, count in after["bids"]["rejected"].items()
            },
            "error": after["bids"]["error"] - base["error"],
        },
        "place_bid_latency_ms": after["latency_ms"],
    }


def check_postgres(auction_id: str) -> dict[str, Any]:
    """Reads what the source of truth saved for the auction."""
    auction = str(uuid.UUID(auction_id))  # validated, so it is safe inside the SQL
    sql = f"""
        WITH b AS (
            SELECT seq, amount,
                   lag(seq) OVER (ORDER BY seq) AS prev_seq,
                   lag(amount) OVER (ORDER BY seq) AS prev_amount
            FROM bids WHERE auction_id = '{auction}'
        )
        SELECT json_build_object(
            'saved_bids', (SELECT count(*) FROM b),
            'amount_order_violations',
                (SELECT count(*) FROM b WHERE prev_amount IS NOT NULL AND amount <= prev_amount),
            'seq_jumps',
                (SELECT count(*) FROM b WHERE prev_seq IS NOT NULL AND seq <> prev_seq + 1),
            'top_saved_amount', (SELECT max(amount) FROM b),
            'last_saved_seq', (SELECT max(seq) FROM b),
            'auction_status', a.status,
            'auction_high_amount', a.high_amount,
            'auction_high_seq', a.high_seq
        )
        FROM auctions a WHERE a.id = '{auction}'
    """
    output = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "auction",
        "-d",
        "auction",
        "-At",
        "-c",
        sql,
        capture=True,
    )
    return json.loads(output)


def make_checks(client: Any, server: Any, saved: Any) -> list[dict[str, Any]]:
    def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
        return {"name": name, "passed": bool(passed), "detail": detail}

    accepted = client["bids"]["accepted"]
    ws = client["websockets"]
    return [
        check(
            "every confirmed bid is saved",
            saved["saved_bids"] >= accepted,
            f"{accepted} bids confirmed to k6 bidders, {saved['saved_bids']} saved in Postgres",
        ),
        check(
            "the server's count matches Postgres",
            server["bids"]["accepted"] == saved["saved_bids"],
            f"server counted {server['bids']['accepted']} accepted, "
            f"Postgres holds {saved['saved_bids']}",
        ),
        check(
            "saved bids are in the right order",
            saved["amount_order_violations"] == 0,
            f"{saved['amount_order_violations']} bids not above the bid before them, by seq",
        ),
        check(
            "the auction's high bid is the top saved bid",
            saved["auction_high_amount"] == saved["top_saved_amount"]
            and saved["auction_high_seq"] == saved["last_saved_seq"],
            f"high {saved['auction_high_amount']} at seq {saved['auction_high_seq']}, top saved "
            f"{saved['top_saved_amount']} at seq {saved['last_saved_seq']}",
        ),
        check(
            "every viewer ends on the right high bid, with nothing out of order",
            ws["viewers_checked"] > 0
            and ws["viewers_on_final_state"] == ws["viewers_checked"]
            and ws["out_of_order"] == 0,
            f"{ws['viewers_on_final_state']} of {ws['viewers_checked']} viewers on the final "
            f"state; {ws['out_of_order']} events out of order, {ws['resyncs']} resyncs after a "
            f"gap, {ws['unexpected_closes']} unexpected closes",
        ),
        check(
            "at least one bid was accepted",
            accepted > 0,
            f"{accepted} accepted",
        ),
    ]


def git_commit() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {
        "sha": git("rev-parse", "HEAD"),
        "short": git("rev-parse", "--short", "HEAD") or "unknown",
        "dirty": bool(git("status", "--porcelain")),
    }


def machine() -> dict[str, Any]:
    """The host, and what Docker sees (on Docker Desktop, its VM)."""
    docker = subprocess.run(
        ["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True, check=False
    )
    info = json.loads(docker.stdout) if docker.returncode == 0 else {}
    return {
        "os": platform.platform(),
        "cpus": os.cpu_count(),
        "docker_server": info.get("ServerVersion"),
        "docker_cpus": info.get("NCPU"),
        "docker_memory_gb": round(info["MemTotal"] / 2**30, 1) if info.get("MemTotal") else None,
        "note": "Everything ran on this one machine: the app, Postgres, Redis and k6.",
    }


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


def print_summary(result: dict[str, Any]) -> None:
    client = result["client"]
    bids = client["bids"]
    latency = client["bid_latency_ms"]
    update = client["bid_to_own_update_ms"]
    ws = client["websockets"]
    print("\nLoad test results")
    print(
        f"  Bids sent: {bids['sent']} ({bids['per_second']}/s): {bids['accepted']} accepted, "
        f"{sum(bids['rejected'].values())} rejected, {bids['error']} errors"
    )
    print(
        f"  Bid response time: p50 {_ms(latency['p50'])}, p95 {_ms(latency['p95'])}, "
        f"p99 {_ms(latency['p99'])}, max {_ms(latency['max'])}"
    )
    print(
        f"  Bid to the bidder's own live update: p50 {_ms(update['p50'])}, "
        f"p95 {_ms(update['p95'])}, p99 {_ms(update['p99'])}"
    )
    print(
        f"  WebSockets: {ws['connections']} connections, {ws['messages']} messages delivered, "
        f"{ws['resyncs']} resyncs after a gap"
    )
    for check in result["checks"]:
        print(f"  [{'PASS' if check['passed'] else 'FAIL'}] {check['name']}: {check['detail']}")


if __name__ == "__main__":
    sys.exit(main())
