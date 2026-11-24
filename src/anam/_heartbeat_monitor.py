import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class _PendingHeartbeat:
    seq: int
    sent_at: float


@dataclass
class _UnconfirmedChunk:
    correlation_id: str
    confirmed_by: int
    tracked_at: float


class HeartbeatMonitor:
    ACK_TIMEOUT_SECONDS = 3.0
    SUSPECT_AFTER_SECONDS = 1.5
    PROBE_COALESCE_SECONDS = 0.1

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.enabled = True
        self._next_seq = 0
        self._pending: list[_PendingHeartbeat] = []
        self._unconfirmed: list[_UnconfirmedChunk] = []
        self._last_sent_at = float("-inf")

    def next_seq(self) -> int:
        seq = self._next_seq
        self._next_seq += 1
        return seq

    def record_sent(self, seq: int) -> None:
        if not self.enabled:
            return
        now = self._clock()
        self._last_sent_at = now
        self._pending.append(_PendingHeartbeat(seq, now))

    def track_chunk(self, correlation_id: str) -> None:
        if not self.enabled:
            return
        self._unconfirmed.append(_UnconfirmedChunk(correlation_id, self._next_seq, self._clock()))

    def is_tracked(self, correlation_id: str) -> bool:
        return any(chunk.correlation_id == correlation_id for chunk in self._unconfirmed)

    def on_ack(self, seq: int) -> float | None:
        answered = next((hb for hb in self._pending if hb.seq == seq), None)
        if answered is None:
            return None
        self._pending = [hb for hb in self._pending if hb.seq > seq]
        self._unconfirmed = [chunk for chunk in self._unconfirmed if chunk.confirmed_by > seq]
        return self._clock() - answered.sent_at

    def is_suspect(self) -> bool:
        return bool(self._pending) and (
            self._clock() - self._pending[0].sent_at >= self.SUSPECT_AFTER_SECONDS
        )

    def drop(self, correlation_id: str) -> None:
        self._unconfirmed = [
            chunk for chunk in self._unconfirmed if chunk.correlation_id != correlation_id
        ]

    def reset(self) -> list[str]:
        self._pending = []
        return self._fail_unconfirmed()

    def expire(self) -> list[str]:
        self._pending = []
        return self._fail_unconfirmed()

    def disable(self) -> None:
        self.enabled = False
        self._pending = []
        self._unconfirmed = []

    def needs_probe(self) -> bool:
        return any(chunk.confirmed_by == self._next_seq for chunk in self._unconfirmed)

    def probe_due_at(self) -> float | None:
        waiting = [chunk for chunk in self._unconfirmed if chunk.confirmed_by == self._next_seq]
        if not waiting:
            return None
        return max(self._last_sent_at + self.PROBE_COALESCE_SECONDS, waiting[0].tracked_at)

    def ack_deadline(self) -> float | None:
        if not self._pending:
            return None
        return self._pending[0].sent_at + self.ACK_TIMEOUT_SECONDS

    def _fail_unconfirmed(self) -> list[str]:
        failed = list(dict.fromkeys(chunk.correlation_id for chunk in self._unconfirmed))
        self._unconfirmed = []
        return failed
