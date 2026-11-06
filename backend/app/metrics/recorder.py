"""App-level bid metrics, kept in this process's memory.

Every place_bid call is recorded: its outcome is counted, and its duration joins a
window of the most recent calls, which the latency percentiles are taken from.

The tradeoff: the numbers belong to this server instance and reset when it restarts.
That fits one instance on one VM, and keeps Redis free of state that can't be
rebuilt from Postgres (CLAUDE.md section 5, rule 3). Summing several instances would
need a shared store.
"""

import math
from collections import Counter, deque
from datetime import UTC, datetime

from app.contract import BidCounts, BidLatency, BidResult, MetricsSnapshot, RejectReason

WINDOW = 10_000
REJECT_REASONS: tuple[RejectReason, ...] = RejectReason.__args__


def _nearest_rank(ordered: list[float], percent: int) -> float:
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return ordered[rank - 1]


class BidMetrics:
    def __init__(self, window: int = WINDOW) -> None:
        self.started_at = datetime.now(UTC)
        self._accepted = 0
        self._error = 0
        self._rejected: Counter[RejectReason] = Counter()
        self._latency_ms: deque[float] = deque(maxlen=window)

    def record(self, result: BidResult, seconds: float) -> None:
        if result.status == "accepted":
            self._accepted += 1
        elif result.status == "rejected" and result.reason is not None:
            self._rejected[result.reason] += 1
        else:
            self._error += 1
        self._latency_ms.append(round(seconds * 1000, 3))

    def snapshot(self) -> MetricsSnapshot:
        ordered = sorted(self._latency_ms)
        if ordered:
            latency = BidLatency(
                samples=len(ordered),
                p50=_nearest_rank(ordered, 50),
                p95=_nearest_rank(ordered, 95),
                p99=_nearest_rank(ordered, 99),
                max=ordered[-1],
            )
        else:
            latency = BidLatency(samples=0, p50=None, p95=None, p99=None, max=None)
        return MetricsSnapshot(
            started_at=self.started_at,
            bids=BidCounts(
                accepted=self._accepted,
                rejected={reason: self._rejected[reason] for reason in REJECT_REASONS},
                error=self._error,
            ),
            latency_ms=latency,
        )


_current: BidMetrics | None = None


def reset() -> BidMetrics:
    """Starts counting from zero. Called when the app starts."""
    global _current
    _current = BidMetrics()
    return _current


def current() -> BidMetrics:
    return _current if _current is not None else reset()


def record_bid(result: BidResult, seconds: float) -> None:
    current().record(result, seconds)
