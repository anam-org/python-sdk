"""TWCC (Transport-Wide Congestion Control) feedback for aiortc.

aiortc only implements REMB (receiver-side bandwidth estimation). The Anam
engine's Pion WebRTC server uses GCC (Google Congestion Control) with TWCC
feedback from the receiver to ramp up video encoder bitrate. Without TWCC,
GCC stays at its initial estimate (~500 kbps) instead of ramping to the
configured maximum (2-5 Mbps), causing visible quality pulsing at keyframe
boundaries.

This module monkey-patches aiortc's RTCRtpReceiver to:
1. Advertise transport-cc support in the SDP offer
2. Collect packet arrival times using a shared tracker across all receivers
3. Send TWCC feedback every ~50ms from the video receiver

The patch is idempotent and must be called before any RTCPeerConnection is
created (i.e. before StreamingClient.connect).

Implementation based on draft-holmer-rmcat-transport-wide-cc-extensions-01
and informed by aiortc PR #1411 (https://github.com/aiortc/aiortc/pull/1411).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from struct import pack
from typing import Optional

logger = logging.getLogger(__name__)

# RTCP constants
_RTCP_RTPFB = 205
_RTCP_RTPFB_TWCC = 15

_installed = False


def install() -> None:
    """Install TWCC feedback on aiortc. Idempotent, call before connect."""
    global _installed
    if _installed:
        return
    _installed = True

    # 1. Add transport-cc header extension to SDP for both audio and video
    from aiortc.codecs import HEADER_EXTENSIONS
    from aiortc.rtcrtpparameters import RTCRtpHeaderExtensionParameters

    twcc_uri = (
        "http://www.ietf.org/id/"
        "draft-holmer-rmcat-transport-wide-cc-extensions-01"
    )
    for kind in ("video", "audio"):
        exts = HEADER_EXTENSIONS.get(kind, [])
        if not any(e.uri == twcc_uri for e in exts):
            exts.append(RTCRtpHeaderExtensionParameters(id=5, uri=twcc_uri))

    # 2. Shared state across all receivers
    tracker = _TwccTracker()
    state = {"mono_origin": None, "last_send_mono": 0.0}

    # 3. Patch _handle_rtp_packet to collect arrivals and send feedback
    import aiortc.rtcrtpreceiver as _recv_mod

    _ReceiverCls = _recv_mod.RTCRtpReceiver
    _orig_handle_rtp = _ReceiverCls._handle_rtp_packet

    async def _patched_handle_rtp(
        self: _ReceiverCls, packet, arrival_time_ms: int  # type: ignore[override]
    ) -> None:
        tsn = packet.extensions.transport_sequence_number
        if tsn is not None:
            now = time.monotonic()
            if state["mono_origin"] is None:
                state["mono_origin"] = now
                state["last_send_mono"] = now
            arrival_us = int((now - state["mono_origin"]) * 1_000_000)
            tracker.add(tsn, arrival_us)

            # Send feedback every ~50ms, only from the video receiver.
            kind = getattr(self, "_RTCRtpReceiver__kind", None)
            if kind == "video" and (now - state["last_send_mono"]) >= 0.050:
                rtcp_ssrc = getattr(
                    self, "_RTCRtpReceiver__rtcp_ssrc", None
                )
                if rtcp_ssrc is not None:
                    pkt = tracker.build_feedback(
                        ssrc=rtcp_ssrc, media_ssrc=packet.ssrc
                    )
                    if pkt is not None:
                        try:
                            transport = getattr(
                                self, "_RTCRtpReceiver__transport", None
                            )
                            if transport:
                                await transport._send_rtp(bytes(pkt))
                        except Exception:
                            pass
                state["last_send_mono"] = now

        await _orig_handle_rtp(self, packet, arrival_time_ms)

    _ReceiverCls._handle_rtp_packet = _patched_handle_rtp  # type: ignore[assignment]
    logger.debug("TWCC feedback installed")


# ---------------------------------------------------------------------------
# TWCC packet tracker and serialiser
# ---------------------------------------------------------------------------


def _uint16_gt(a: int, b: int) -> bool:
    return ((a - b) & 0xFFFF) < 0x8000 and a != b


def _encode_twcc_chunks(statuses: list[int]) -> bytes:
    """Run-length encode statuses into 16-bit chunks."""
    data = b""
    i = 0
    while i < len(statuses):
        status = statuses[i]
        run = 1
        while i + run < len(statuses) and statuses[i + run] == status:
            run += 1
        remaining = run
        while remaining > 0:
            chunk_run = min(remaining, 0x1FFF)
            data += pack("!H", (status << 13) | chunk_run)
            remaining -= chunk_run
        i += run
    return data


def _pack_rtcp(packet_type: int, count: int, payload: bytes) -> bytes:
    assert len(payload) % 4 == 0
    return (
        pack(
            "!BBH",
            (2 << 6) | (count & 0x1F),
            packet_type,
            len(payload) // 4,
        )
        + payload
    )


@dataclass
class _RtcpTwccPacket:
    ssrc: int
    media_ssrc: int
    base_sequence_number: int
    packet_status_count: int
    reference_time: int
    feedback_packet_count: int
    packet_results: list[tuple[int, Optional[int]]]

    def __bytes__(self) -> bytes:
        statuses: list[int] = []
        deltas: list[tuple[int, int]] = []

        if self.packet_results:
            ref_time_us = self.reference_time * 64_000
            last_time_us = ref_time_us
            for _, recv_delta_us in self.packet_results:
                if recv_delta_us is None:
                    # Should not happen with interpolation, but handle gracefully
                    statuses.append(0)
                else:
                    abs_time_us = ref_time_us + recv_delta_us
                    delta_us = abs_time_us - last_time_us
                    delta_ticks = delta_us // 250
                    if 0 <= delta_ticks <= 255:
                        statuses.append(1)
                        deltas.append((1, delta_ticks))
                    else:
                        statuses.append(2)
                        deltas.append((2, delta_ticks))
                    last_time_us = abs_time_us

        chunk_data = _encode_twcc_chunks(statuses)
        delta_data = b""
        for status, ticks in deltas:
            if status == 1:
                delta_data += pack("!B", ticks & 0xFF)
            elif status == 2:
                delta_data += pack("!h", ticks)

        ref_time = self.reference_time & 0xFFFFFF
        payload = pack("!LL", self.ssrc, self.media_ssrc)
        payload += pack(
            "!HH", self.base_sequence_number, self.packet_status_count
        )
        payload += pack("!B", (ref_time >> 16) & 0xFF)
        payload += pack("!B", (ref_time >> 8) & 0xFF)
        payload += pack("!B", ref_time & 0xFF)
        payload += pack("!B", self.feedback_packet_count & 0xFF)
        payload += chunk_data
        payload += delta_data
        pad_len = (4 - len(payload) % 4) % 4
        payload += b"\x00" * pad_len

        return _pack_rtcp(_RTCP_RTPFB, _RTCP_RTPFB_TWCC, payload)


class _TwccTracker:
    """Collects packet arrival times and builds TWCC feedback reports.

    A single instance is shared across audio and video receivers since
    transport-wide sequence numbers span both streams. Only the video
    receiver sends the feedback.

    Missing sequence numbers (typically audio packets not yet delivered
    to the video receiver's handler) are interpolated with the previous
    packet's arrival time rather than reported as lost. This prevents
    GCC from misinterpreting cross-stream scheduling gaps as congestion.
    """

    _REF_TIME_UNIT_US = 64_000

    def __init__(self) -> None:
        self._packets: dict[int, int] = {}  # seq -> arrival_time_us
        self._min_seq: Optional[int] = None
        self._max_seq: Optional[int] = None
        self._feedback_count: int = 0

    def add(self, twcc_seq: int, arrival_time_us: int) -> None:
        if twcc_seq in self._packets:
            return
        self._packets[twcc_seq] = arrival_time_us
        if self._min_seq is None or _uint16_gt(self._min_seq, twcc_seq):
            self._min_seq = twcc_seq
        if self._max_seq is None or _uint16_gt(twcc_seq, self._max_seq):
            self._max_seq = twcc_seq

    def build_feedback(
        self, ssrc: int, media_ssrc: int
    ) -> Optional[_RtcpTwccPacket]:
        if (
            self._min_seq is None
            or self._max_seq is None
            or not self._packets
        ):
            return None

        base_seq = self._min_seq

        # Find reference time from first received packet
        ref_time_us: Optional[int] = None
        s = base_seq
        while True:
            if s in self._packets:
                ref_time_us = self._packets[s]
                break
            if s == self._max_seq:
                break
            s = (s + 1) & 0xFFFF
        if ref_time_us is None:
            return None

        reference_time = ref_time_us // self._REF_TIME_UNIT_US
        ref_base_us = reference_time * self._REF_TIME_UNIT_US

        # Build results, interpolating missing packets rather than
        # reporting them as lost.
        packet_results: list[tuple[int, Optional[int]]] = []
        seq = base_seq
        last_known_us = ref_base_us
        while True:
            arrival = self._packets.get(seq)
            if arrival is not None:
                last_known_us = arrival
                recv_delta_us = arrival - ref_base_us
            else:
                # Interpolate: place just after the last known arrival
                interp_us = last_known_us + 250  # 0.25ms
                last_known_us = interp_us
                recv_delta_us = interp_us - ref_base_us
            packet_results.append((seq, recv_delta_us))
            if seq == self._max_seq:
                break
            seq = (seq + 1) & 0xFFFF

        fb_count = self._feedback_count & 0xFF
        self._feedback_count = (self._feedback_count + 1) & 0xFF

        self._packets.clear()
        self._min_seq = None
        self._max_seq = None

        return _RtcpTwccPacket(
            ssrc=ssrc,
            media_ssrc=media_ssrc,
            base_sequence_number=base_seq,
            packet_status_count=len(packet_results),
            reference_time=reference_time,
            feedback_packet_count=fb_count,
            packet_results=packet_results,
        )
