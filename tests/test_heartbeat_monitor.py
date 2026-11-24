"""Tests for the heartbeat monitor state machine."""

import pytest

from anam._heartbeat_monitor import HeartbeatMonitor


@pytest.fixture
def clock() -> list[float]:
    return [0.0]


@pytest.fixture
def monitor(clock: list[float]) -> HeartbeatMonitor:
    return HeartbeatMonitor(clock=lambda: clock[0])


def send_heartbeat(monitor: HeartbeatMonitor) -> int:
    seq = monitor.next_seq()
    monitor.record_sent(seq)
    return seq


def test_ack_confirms_chunks_written_before_heartbeat(monitor: HeartbeatMonitor) -> None:
    monitor.track_chunk("a")
    seq = send_heartbeat(monitor)

    assert monitor.on_ack(seq) is not None
    assert monitor.reset() == []


def test_ack_does_not_confirm_chunks_written_after_heartbeat(monitor: HeartbeatMonitor) -> None:
    seq = send_heartbeat(monitor)
    monitor.track_chunk("b")

    monitor.on_ack(seq)

    assert monitor.reset() == ["b"]


def test_unknown_seq_confirms_nothing(monitor: HeartbeatMonitor) -> None:
    monitor.track_chunk("a")
    send_heartbeat(monitor)

    assert monitor.on_ack(99) is None
    assert monitor.reset() == ["a"]


def test_ack_returns_round_trip_time(monitor: HeartbeatMonitor, clock: list[float]) -> None:
    seq = send_heartbeat(monitor)
    clock[0] = 0.25

    assert monitor.on_ack(seq) == pytest.approx(0.25)
    assert monitor.on_ack(seq) is None


def test_ack_clears_earlier_pending_heartbeats(monitor: HeartbeatMonitor) -> None:
    first = send_heartbeat(monitor)
    second = send_heartbeat(monitor)

    monitor.on_ack(second)

    assert monitor.ack_deadline() is None
    assert monitor.on_ack(first) is None


def test_expire_fails_unconfirmed_once_and_dedupes(monitor: HeartbeatMonitor) -> None:
    monitor.track_chunk("a")
    monitor.track_chunk("a")
    monitor.track_chunk("b")
    send_heartbeat(monitor)

    assert monitor.expire() == ["a", "b"]
    assert monitor.expire() == []
    assert monitor.ack_deadline() is None


def test_suspect_after_threshold(monitor: HeartbeatMonitor, clock: list[float]) -> None:
    seq = send_heartbeat(monitor)

    clock[0] = 1.4
    assert not monitor.is_suspect()
    clock[0] = 1.5
    assert monitor.is_suspect()
    monitor.on_ack(seq)
    assert not monitor.is_suspect()


def test_probe_due_and_ack_deadline(monitor: HeartbeatMonitor, clock: list[float]) -> None:
    assert monitor.probe_due_at() is None
    monitor.track_chunk("a")
    assert monitor.needs_probe()
    assert monitor.probe_due_at() == 0.0

    send_heartbeat(monitor)
    assert not monitor.needs_probe()
    assert monitor.probe_due_at() is None
    assert monitor.ack_deadline() == pytest.approx(3.0)

    clock[0] = 0.05
    monitor.track_chunk("b")
    assert monitor.probe_due_at() == pytest.approx(0.1)


def test_probe_waits_for_first_waiting_chunk(monitor: HeartbeatMonitor, clock: list[float]) -> None:
    send_heartbeat(monitor)
    clock[0] = 2.0
    monitor.track_chunk("a")

    assert monitor.probe_due_at() == pytest.approx(2.0)


def test_drop_prevents_failure(monitor: HeartbeatMonitor) -> None:
    monitor.track_chunk("a")
    monitor.track_chunk("b")
    send_heartbeat(monitor)

    monitor.drop("a")

    assert not monitor.is_tracked("a")
    assert monitor.reset() == ["b"]


def test_disabled_tracks_nothing(monitor: HeartbeatMonitor, clock: list[float]) -> None:
    monitor.track_chunk("a")
    send_heartbeat(monitor)

    monitor.disable()
    assert not monitor.enabled
    monitor.track_chunk("b")
    send_heartbeat(monitor)
    clock[0] = 10.0

    assert not monitor.is_tracked("b")
    assert not monitor.is_suspect()
    assert not monitor.needs_probe()
    assert monitor.ack_deadline() is None
    assert monitor.expire() == []
    assert monitor.reset() == []


def test_seq_never_resets_after_reset(monitor: HeartbeatMonitor) -> None:
    first = send_heartbeat(monitor)
    monitor.reset()
    second = send_heartbeat(monitor)
    monitor.disable()

    assert second == first + 1
    assert monitor.next_seq() == second + 1


def test_stale_ack_after_reset_confirms_nothing(monitor: HeartbeatMonitor) -> None:
    old = send_heartbeat(monitor)
    monitor.reset()
    monitor.track_chunk("a")
    send_heartbeat(monitor)

    assert monitor.on_ack(old) is None
    assert monitor.reset() == ["a"]


def test_is_tracked(monitor: HeartbeatMonitor) -> None:
    assert not monitor.is_tracked("a")
    monitor.track_chunk("a")
    assert monitor.is_tracked("a")
    seq = send_heartbeat(monitor)
    monitor.on_ack(seq)
    assert not monitor.is_tracked("a")
