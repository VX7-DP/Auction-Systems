"""The in-process bid metrics behind GET /api/metrics (session 6, CLAUDE.md section 15)."""

import uuid

from app.contract import BidResult, RejectReason
from app.metrics.recorder import BidMetrics


def _accepted() -> BidResult:
    return BidResult(bid_id=uuid.uuid4(), status="accepted", seq=1)


def _rejected(reason: RejectReason) -> BidResult:
    return BidResult(bid_id=uuid.uuid4(), status="rejected", reason=reason)


def _error() -> BidResult:
    return BidResult(bid_id=uuid.uuid4(), status="error")


def test_a_new_recorder_reports_zeros_and_no_percentiles() -> None:
    snapshot = BidMetrics().snapshot()

    assert snapshot.bids.accepted == 0
    assert snapshot.bids.error == 0
    # Every reason is listed, so a client never has to guess a missing key.
    assert snapshot.bids.rejected == dict.fromkeys(RejectReason.__args__, 0)
    assert snapshot.latency_ms.samples == 0
    assert snapshot.latency_ms.p50 is None
    assert snapshot.latency_ms.p95 is None
    assert snapshot.latency_ms.p99 is None
    assert snapshot.latency_ms.max is None


def test_outcomes_are_counted_apart_with_reasons_kept_separate() -> None:
    metrics = BidMetrics()
    for result in (
        _accepted(),
        _accepted(),
        _accepted(),
        _rejected("too_low"),
        _rejected("too_low"),
        _rejected("closed"),
        _error(),
    ):
        metrics.record(result, 0.001)

    bids = metrics.snapshot().bids
    assert bids.accepted == 3
    assert bids.error == 1
    assert bids.rejected["too_low"] == 2
    assert bids.rejected["closed"] == 1
    assert bids.rejected["too_high"] == 0


def test_percentiles_use_the_nearest_rank() -> None:
    metrics = BidMetrics()
    # 1 ms to 100 ms, recorded out of order.
    for ms in reversed(range(1, 101)):
        metrics.record(_accepted(), ms / 1000)

    latency = metrics.snapshot().latency_ms
    assert latency.samples == 100
    assert latency.p50 == 50
    assert latency.p95 == 95
    assert latency.p99 == 99
    assert latency.max == 100


def test_every_outcome_counts_towards_latency() -> None:
    metrics = BidMetrics()
    metrics.record(_accepted(), 0.002)
    metrics.record(_rejected("too_low"), 0.004)
    metrics.record(_error(), 0.006)

    latency = metrics.snapshot().latency_ms
    assert latency.samples == 3
    assert latency.p50 == 4
    assert latency.max == 6


def test_latency_covers_only_the_most_recent_window() -> None:
    metrics = BidMetrics(window=10)
    for ms in range(1, 16):  # 1 ms to 15 ms; only 6 ms to 15 ms stay in the window
        metrics.record(_accepted(), ms / 1000)

    snapshot = metrics.snapshot()
    assert snapshot.latency_ms.samples == 10
    assert snapshot.latency_ms.p50 == 10
    assert snapshot.latency_ms.max == 15
    # The counters are totals since start, not limited to the window.
    assert snapshot.bids.accepted == 15
