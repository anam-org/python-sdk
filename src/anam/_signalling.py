"""WebSocket signalling client for Anam services."""

import asyncio
import functools
import json
import logging
import time
import weakref
from enum import Enum
from typing import Any, Awaitable, Callable, NamedTuple, Protocol

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State

from ._heartbeat_monitor import HeartbeatMonitor
from .types import AgentAudioInputPayload, ConnectionClosedCode, SessionInfo

logger = logging.getLogger(__name__)


class _Dispatch(NamedTuple):
    call: Callable[[], Awaitable[None]]
    failed_correlation_id: str | None = None


class TalkStreamTarget(Protocol):
    def _mark_delivery_failed(self) -> None: ...

    def _mark_interrupted(self) -> None: ...


async def _noop(*_: Any) -> None:
    return None


async def _wait(event: asyncio.Event, timeout: float) -> None:
    # asyncio.wait_for on Python 3.11 can swallow a cancel that races with the wait completing.
    try:
        async with asyncio.timeout(timeout):
            await event.wait()
    except TimeoutError:
        pass


class HeartbeatAcks(Enum):
    """Whether the engine answers heartbeats, as announced in sessionready."""

    UNKNOWN = "unknown"
    YES = "yes"
    NO = "no"


class SignalAction(str, Enum):
    """Signal message action types."""

    OFFER = "offer"
    ANSWER = "answer"
    ICE_CANDIDATE = "icecandidate"
    END_SESSION = "endsession"
    HEARTBEAT = "heartbeat"
    WARNING = "warning"
    SESSION_READY = "sessionready"
    TALK_STREAM_INPUT = "talkstream"
    TALK_STREAM_INTERRUPTED = "talkinputstreaminterrupted"
    AGENT_AUDIO_INPUT = "agentaudioinput"
    AGENT_AUDIO_INPUT_END = "agentaudioinputend"


def _talk_correlation_id(message: dict[str, Any]) -> str | None:
    if message.get("actionType") != SignalAction.TALK_STREAM_INPUT.value:
        return None
    payload = message.get("payload")
    correlation_id = payload.get("correlationId") if isinstance(payload, dict) else None
    return correlation_id if isinstance(correlation_id, str) else None


class SignallingClient:
    """WebSocket client for signalling with Anam engine.

    Handles the WebSocket connection for WebRTC signalling,
    including offer/answer exchange and ICE candidate trickling.
    """

    DEFAULT_HEARTBEAT_INTERVAL = 5
    DEFAULT_MAX_RECONNECTION_ATTEMPTS = 5
    SHUTDOWN_TIMEOUT = 2.0
    RECONNECT_BACKOFF_SECONDS = 0.1
    STABLE_CONNECTION_SECONDS = 5.0
    RECONNECT_OPEN_TIMEOUT_SECONDS = 5.0
    WRITE_STALL_SECONDS = 3.0
    SEND_BUFFER_LIMIT = 2000

    def __init__(
        self,
        session_info: SessionInfo,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_open: Callable[[], Awaitable[None]] | None = None,
        on_end: Callable[[ConnectionClosedCode, str | None], None] | None = None,
        on_talk_stream_delivery_failed: Callable[[str], Awaitable[None]] | None = None,
    ):
        """Initialize the signalling client.

        Args:
            session_info: Session information from API.
            on_message: Called on the dispatcher for each signalling message.
            on_open: Awaited once the first socket is open.
            on_end: Called synchronously when the session ends without close(): the server
                sent endsession or the socket could not be kept open. The client has stopped.
            on_talk_stream_delivery_failed: Called on the dispatcher with the correlation id
                of a talk stream whose delivery is unconfirmed.
        """
        self._session_info = session_info
        self._session_id = session_info.session_id
        self._heartbeat_interval = (
            session_info.heartbeat_interval_seconds or self.DEFAULT_HEARTBEAT_INTERVAL
        )
        self._max_reconnection_attempts = (
            session_info.max_reconnection_attempts or self.DEFAULT_MAX_RECONNECTION_ATTEMPTS
        )

        self._on_message = on_message
        self._on_open = on_open
        self._on_end = on_end
        self._on_talk_stream_delivery_failed = on_talk_stream_delivery_failed or _noop

        self._ws: ClientConnection | None = None
        self._live_ws: ClientConnection | None = None
        self._stop_signal = False
        self._permanently_closed = False
        self._server_ended = False
        self._connection_attempts = 0
        self._send_buffer: list[dict[str, Any]] = []
        self._buffer_overflowed = False
        self._send_lock = asyncio.Lock()
        self._dispatch_queue: asyncio.Queue[_Dispatch] = asyncio.Queue()
        self._dispatching: _Dispatch | None = None

        self._monitor = HeartbeatMonitor()
        self._heartbeat_acks = HeartbeatAcks.UNKNOWN
        self._ever_opened = False
        self._unanswered_replacements = 0
        self._failed_cids: set[str] = set()
        self._interrupted_cids: set[str] = set()
        self._talk_streams: dict[str, list[weakref.ref[TalkStreamTarget]]] = {}
        self._write_started: tuple[ClientConnection, float] | None = None
        self._probe_wake = asyncio.Event()
        self._watchdog_wake = asyncio.Event()

        self._run_task: asyncio.Task[None] | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None

        self._ws_url = self._construct_websocket_url()
        logger.info("SignallingClient WebSocket URL: %s", self._ws_url)

    def _construct_websocket_url(self) -> str:
        """Build the WebSocket URL from session info."""
        protocol = self._session_info.engine_protocol
        host = self._session_info.engine_host
        endpoint = self._session_info.signalling_endpoint

        ws_protocol = "wss" if protocol == "https" else "ws"

        # Extract just the hostname if engine_host contains a path
        # (e.g., "connect-eu.anam.ai/v1/webrtc/engines/xxx")
        if "/" in host:
            hostname = host.split("/")[0]
        else:
            hostname = host

        # The signalling endpoint should be the full path
        base_url = f"{ws_protocol}://{hostname}{endpoint}"
        return f"{base_url}?session_id={self._session_id}"

    @property
    def is_permanently_closed(self) -> bool:
        return self._permanently_closed or self._stop_signal

    @property
    def server_ended(self) -> bool:
        return self._server_ended

    async def connect(self) -> None:
        logger.debug("Connecting to signalling server")
        try:
            ws = await connect(self._ws_url, close_timeout=2.0)
        except Exception as e:
            logger.error("Failed to connect to signalling server: %s", e)
            raise
        if self._stop_signal:
            await self._dispose(ws)
            return
        logger.info("WebSocket connection established")
        opened_at = time.monotonic()

        await self._activate(ws, opened_at)
        if self._stop_signal:
            await self.close()
            return
        self._run_task = asyncio.create_task(self._supervise(ws, opened_at))

        if self._on_open:
            try:
                await self._on_open()
            except BaseException:
                await self.close()
                raise

    def stop(self) -> None:
        """Stop reconnecting, sending and dispatching at once. close() releases the socket.

        A callback that is running on the dispatcher is left to finish.
        """
        self._stop_signal = True
        current = asyncio.current_task()
        for task in (self._run_task, self._heartbeat_task, self._watchdog_task):
            if task is not None and task is not current:
                task.cancel()
        dispatch_task = self._dispatch_task
        if dispatch_task is not None and dispatch_task is not current and self._dispatching is None:
            dispatch_task.cancel()

    def take_failures(self, include_unconfirmed: bool) -> list[str]:
        """Remove the undelivered failure reports from the dispatcher and return their ids.

        Other queued callbacks are dropped. The send buffer is cleared. With
        include_unconfirmed, the buffered and unconfirmed talk chunks are claimed as failures
        too. Call this after stop().
        """
        taken = []
        while not self._dispatch_queue.empty():
            item = self._dispatch_queue.get_nowait()
            if item.failed_correlation_id is not None:
                taken.append(item.failed_correlation_id)
        buffered = [_talk_correlation_id(message) for message in self._send_buffer]
        self._send_buffer.clear()
        if include_unconfirmed:
            unconfirmed = self._monitor.abandon()
            taken += self._claim_failures(
                unconfirmed + [cid for cid in buffered if cid is not None]
            )
        return taken

    async def finish_failure_report(self) -> None:
        """Wait until the failure report that the dispatcher delivers now has finished.

        Call this after stop(), so that the dispatcher exits after that report.
        """
        task = self._dispatch_task
        item = self._dispatching
        if task is None or task is asyncio.current_task():
            return
        if item is None or item.failed_correlation_id is None:
            return
        await asyncio.wait([task])

    def register_talk_stream(self, correlation_id: str, stream: TalkStreamTarget) -> None:
        """Mark this stream when a delivery failure is claimed for its correlation id.

        A new stream makes a reused correlation id usable again.
        """
        self._failed_cids.discard(correlation_id)
        self._interrupted_cids.discard(correlation_id)
        forget = functools.partial(self._forget_talk_stream, correlation_id)
        self._talk_streams.setdefault(correlation_id, []).append(weakref.ref(stream, forget))

    def _forget_talk_stream(self, correlation_id: str, ref: weakref.ref[TalkStreamTarget]) -> None:
        refs = self._talk_streams.get(correlation_id)
        if refs is None:
            return
        if ref in refs:
            refs.remove(ref)
        if not refs:
            del self._talk_streams[correlation_id]

    def _owned_tasks(self) -> list[asyncio.Task[None]]:
        tasks = (self._run_task, self._dispatch_task, self._heartbeat_task, self._watchdog_task)
        return [task for task in tasks if task is not None]

    def _end(self, code: ConnectionClosedCode, reason: str | None) -> None:
        if self._stop_signal:
            return
        self._permanently_closed = True
        buffered = [_talk_correlation_id(message) for message in self._send_buffer]
        self._send_buffer.clear()
        self._queue_failures([cid for cid in buffered if cid is not None])
        self.stop()
        if self._on_end:
            self._on_end(code, reason)

    async def _activate(self, ws: ClientConnection, opened_at: float) -> None:
        self._live_ws = ws
        self._watchdog_task = asyncio.create_task(self._watch(ws, opened_at))
        try:
            async with self._send_lock:
                self._ws = ws
                self._ever_opened = True
                await self._flush(ws)
        except asyncio.CancelledError:
            if self._ws is not ws:
                ws.transport.abort()
            raise
        self._heartbeat_task = asyncio.create_task(self._send_heartbeats(ws))

    async def _deactivate(self, ws: ClientConnection) -> None:
        if self._ws is ws:
            self._ws = None
        if self._live_ws is ws:
            self._live_ws = None
        tasks = [task for task in (self._heartbeat_task, self._watchdog_task) if task is not None]
        self._heartbeat_task = None
        self._watchdog_task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._write_started is not None and self._write_started[0] is ws:
            self._write_started = None
        self._queue_failures(self._monitor.abandon())

    async def _supervise(self, ws: ClientConnection, opened_at: float) -> None:
        try:
            await self._keep_open(ws, opened_at)
        except Exception:
            logger.exception("Signalling supervisor failed")
            self._end(ConnectionClosedCode.SIGNALLING_FAILURE, "Signalling failed unexpectedly")

    async def _keep_open(self, ws: ClientConnection, opened_at: float) -> None:
        while True:
            await self._receive(ws)
            if self._stop_signal:
                return
            await self._deactivate(ws)
            if self._stop_signal:
                return
            reason = ws.close_reason or None
            logger.warning("Signalling socket closed: code=%s reason=%s", ws.close_code, reason)
            if time.monotonic() - opened_at >= self.STABLE_CONNECTION_SECONDS:
                self._connection_attempts = 0
            self._connection_attempts += 1

            new_ws = await self._reconnect()
            if new_ws is None:
                self._end(ConnectionClosedCode.SIGNALLING_FAILURE, reason)
                return
            ws = new_ws
            opened_at = time.monotonic()
            await self._activate(ws, opened_at)

    async def _reconnect(self) -> ClientConnection | None:
        while self._connection_attempts <= self._max_reconnection_attempts:
            delay = self.RECONNECT_BACKOFF_SECONDS * self._connection_attempts
            logger.info(
                "Reconnecting signalling in %.1f seconds (attempt %d/%d)",
                delay,
                self._connection_attempts,
                self._max_reconnection_attempts,
            )
            await asyncio.sleep(delay)
            if self._stop_signal:
                return None
            try:
                ws = await connect(
                    self._ws_url,
                    close_timeout=2.0,
                    open_timeout=self.RECONNECT_OPEN_TIMEOUT_SECONDS,
                )
            except Exception as e:
                logger.warning("Signalling reconnect failed: %s", e)
                self._connection_attempts += 1
                continue
            if self._stop_signal:
                await self._dispose(ws)
                return None
            logger.info("Signalling socket reconnected")
            return ws
        logger.error("Max reconnection attempts reached")
        return None

    async def _receive(self, ws: ClientConnection) -> None:
        try:
            async for raw in ws:
                if self._stop_signal:
                    return
                try:
                    self._handle_incoming(raw)
                except Exception:
                    logger.exception("Failed to handle signalling message")
                if self._stop_signal:
                    return
        except ConnectionClosed:
            pass

    def _handle_incoming(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except ValueError as e:
            logger.error("Failed to parse message: %s", e)
            return
        if not isinstance(data, dict):
            logger.error("Ignoring signalling message that is not an object")
            return
        action_type = str(data.get("actionType", "")).lower()
        payload = data.get("payload")
        if action_type == SignalAction.HEARTBEAT.value:
            self._handle_heartbeat_answer(payload)
            return
        logger.debug("Received signal: %s", action_type)
        if action_type == SignalAction.END_SESSION.value:
            reason = payload if isinstance(payload, str) else "Session ended by server"
            logger.info("Session ended by server: %s", reason)
            self._server_ended = True
            self._queue_failures(self._monitor.abandon())
            self._end(ConnectionClosedCode.SERVER_CLOSED, reason)
            return
        if action_type == SignalAction.SESSION_READY.value:
            if isinstance(payload, dict) and payload.get("sendsHeartbeatAcks") is True:
                self._heartbeat_acks = HeartbeatAcks.YES
            else:
                self._heartbeat_acks = HeartbeatAcks.NO
                self._monitor.disable()
        elif action_type == SignalAction.TALK_STREAM_INTERRUPTED.value:
            correlation_id = payload.get("correlationId") if isinstance(payload, dict) else None
            if isinstance(correlation_id, str):
                self._interrupted_cids.add(correlation_id)
                self._retire(correlation_id)
                for stream in self._live_streams(correlation_id):
                    stream._mark_interrupted()
        if self._on_message:
            self._enqueue(_Dispatch(functools.partial(self._on_message, data)))

    def _handle_heartbeat_answer(self, payload: Any) -> None:
        seq = payload.get("seq") if isinstance(payload, dict) else None
        # bool is an int subclass; a true "seq" is not an answer.
        if type(seq) is not int:
            return
        if self._monitor.on_ack(seq) is not None:
            self._unanswered_replacements = 0
            self._watchdog_wake.set()

    def _claim_failures(self, correlation_ids: list[str]) -> list[str]:
        new = []
        for correlation_id in correlation_ids:
            if correlation_id in self._failed_cids or correlation_id in self._interrupted_cids:
                continue
            self._failed_cids.add(correlation_id)
            new.append(correlation_id)
            self._retire(correlation_id)
            for stream in self._live_streams(correlation_id):
                stream._mark_delivery_failed()
        return new

    def _retire(self, correlation_id: str) -> None:
        """Forget the unconfirmed and buffered chunks of a stream that has ended.

        A new stream that reuses the correlation id then cannot inherit them.
        """
        self._monitor.drop(correlation_id)
        self._send_buffer[:] = [
            message
            for message in self._send_buffer
            if _talk_correlation_id(message) != correlation_id
        ]

    def _live_streams(self, correlation_id: str) -> list[TalkStreamTarget]:
        streams = (ref() for ref in self._talk_streams.get(correlation_id, []))
        return [stream for stream in streams if stream is not None]

    def _queue_failures(self, correlation_ids: list[str]) -> None:
        callback = self._on_talk_stream_delivery_failed
        for correlation_id in self._claim_failures(correlation_ids):
            self._enqueue(_Dispatch(functools.partial(callback, correlation_id), correlation_id))

    def _enqueue(self, item: _Dispatch) -> None:
        self._dispatch_queue.put_nowait(item)
        if self._stop_signal:
            return
        if self._dispatch_task is None or self._dispatch_task.done():
            self._dispatch_task = asyncio.create_task(self._dispatch())

    async def _dispatch(self) -> None:
        while not self._stop_signal:
            item = await self._dispatch_queue.get()
            self._dispatching = item
            try:
                await item.call()
            except Exception as e:
                logger.error("Error handling signalling callback: %s", e)
            finally:
                self._dispatching = None
            # Queue.get() and free locks do not yield. A failure listener that resends during a
            # reconnect queues a new failure at once and would otherwise starve the loop.
            await asyncio.sleep(0)

    def _owns(self, ws: ClientConnection) -> bool:
        return not self._stop_signal and self._live_ws is ws

    async def _send_heartbeats(self, ws: ClientConnection) -> None:
        next_beat = time.monotonic() + self._heartbeat_interval
        while self._owns(ws):
            self._probe_wake.clear()
            due = next_beat
            probe_at = self._monitor.probe_due_at()
            if probe_at is not None:
                due = min(due, probe_at)
            delay = due - time.monotonic()
            if delay > 0:
                await _wait(self._probe_wake, delay)
                continue
            async with self._send_lock:
                if self._stop_signal or ws.state is not State.OPEN:
                    return
                # websockets writes the frame before awaiting drain, so the ack can arrive first.
                seq = self._monitor.start_heartbeat()
                self._watchdog_wake.set()
                message = {
                    "actionType": SignalAction.HEARTBEAT.value,
                    "sessionId": self._session_id,
                    "payload": {"seq": seq},
                }
                try:
                    await self._write(ws, message)
                except Exception as e:
                    logger.warning("Failed to send heartbeat: %s", e)
            next_beat = time.monotonic() + self._heartbeat_interval

    async def _watch(self, ws: ClientConnection, opened_at: float) -> None:
        while self._owns(ws):
            self._watchdog_wake.clear()
            now = time.monotonic()
            ack_deadline = self._monitor.ack_deadline()
            stall_deadline = None
            stalled_since = None
            if self._write_started is not None and self._write_started[0] is ws:
                stalled_since = self._write_started[1]
                stall_deadline = stalled_since + self.WRITE_STALL_SECONDS
            deadlines = [d for d in (ack_deadline, stall_deadline) if d is not None]
            if not deadlines:
                await self._watchdog_wake.wait()
                continue
            if min(deadlines) > now:
                await _wait(self._watchdog_wake, min(deadlines) - now)
                continue
            if stalled_since is not None and stall_deadline is not None and stall_deadline <= now:
                logger.warning("Signalling write stalled; replacing the socket")
                # Without acks only a stable period resets the count. Measure it up to the
                # start of the stall, so that the stall time does not count as stable.
                if stalled_since - opened_at >= self.STABLE_CONNECTION_SECONDS:
                    self._unanswered_replacements = 0
            else:
                failed = self._monitor.abandon()
                self._queue_failures(failed)
                if self._heartbeat_acks is HeartbeatAcks.UNKNOWN and not failed:
                    continue
                logger.warning("Signalling heartbeat not answered; replacing the socket")
            if self._unanswered_replacements >= self._max_reconnection_attempts:
                logger.error("Signalling socket replaced too often without an answer")
                self._end(ConnectionClosedCode.SIGNALLING_FAILURE, None)
            else:
                self._unanswered_replacements += 1
            ws.transport.abort()
            return

    async def _write(self, ws: ClientConnection, message: dict[str, Any]) -> None:
        self._write_started = (ws, time.monotonic())
        self._watchdog_wake.set()
        try:
            await ws.send(json.dumps(message))
        finally:
            self._write_started = None

    def _track(self, correlation_id: str) -> None:
        self._monitor.track_chunk(correlation_id)
        self._probe_wake.set()

    def _is_dead_stream(self, correlation_id: str) -> bool:
        return correlation_id in self._failed_cids or correlation_id in self._interrupted_cids

    async def _flush(self, ws: ClientConnection) -> None:
        assert self._send_lock.locked()
        self._buffer_overflowed = False
        while self._send_buffer and not self.is_permanently_closed:
            if ws.state is not State.OPEN:
                return
            message = self._send_buffer.pop(0)
            correlation_id = _talk_correlation_id(message)
            if correlation_id is not None:
                if self._is_dead_stream(correlation_id):
                    continue
                if self._heartbeat_acks is not HeartbeatAcks.NO:
                    self._track(correlation_id)
            try:
                await self._write(ws, message)
            except Exception as e:
                # websockets may have written the frame before raising; resending could duplicate it.
                logger.warning("Lost buffered %s message: %s", message.get("actionType"), e)
                if correlation_id is not None and self._monitor.is_tracked(correlation_id):
                    self._monitor.drop(correlation_id)
                    self._queue_failures([correlation_id])
                return

    def _buffer(self, message: dict[str, Any]) -> None:
        self._send_buffer.append(message)
        if len(self._send_buffer) <= self.SEND_BUFFER_LIMIT:
            return
        index = self._eviction_index()
        if index is None:
            return
        evicted = self._send_buffer.pop(index)
        if not self._buffer_overflowed:
            self._buffer_overflowed = True
            logger.warning(
                "Signalling send buffer is full; dropping the oldest %s message",
                evicted.get("actionType"),
            )
        correlation_id = _talk_correlation_id(evicted)
        if correlation_id is not None:
            self._queue_failures([correlation_id])

    def _eviction_index(self) -> int | None:
        actions = [message.get("actionType") for message in self._send_buffer]
        if SignalAction.AGENT_AUDIO_INPUT.value in actions:
            return actions.index(SignalAction.AGENT_AUDIO_INPUT.value)
        for index, action in enumerate(actions):
            if action != SignalAction.OFFER.value:
                return index
        return None

    async def send_message(
        self, message: dict[str, Any], admitted: Callable[[], None] | None = None
    ) -> None:
        """Send a message, or buffer it until a socket is open.

        Args:
            message: The message to send.
            admitted: Called when the message is accepted, before the write starts. A
                cancellation after this point does not stop the message.
        """
        # Convert enum to string if needed
        if "actionType" in message and isinstance(message["actionType"], SignalAction):
            message["actionType"] = message["actionType"].value

        if self.is_permanently_closed:
            logger.debug("Dropping %s message: signalling is closed", message.get("actionType"))
            return
        async with self._send_lock:
            if self.is_permanently_closed:
                logger.debug("Dropping %s message: signalling is closed", message.get("actionType"))
                return
            if admitted is not None:
                admitted()
            await self._send_or_buffer(message)

    async def _send_or_buffer(self, message: dict[str, Any]) -> None:
        assert self._send_lock.locked()
        ws = self._ws
        if ws is None or ws.state is not State.OPEN:
            self._buffer(message)
            return
        try:
            await self._write(ws, message)
        except Exception as e:
            logger.warning("Failed to send %s message: %s", message.get("actionType"), e)

    async def send_end_session(self) -> None:
        """Inform the backend to end the session gracefully. Best effort, as we are on the shutdown path."""
        self.stop()
        ws = self._ws
        if ws is None or ws.state is not State.OPEN:
            logger.debug("Skip sending endsession: signalling socket is not open")
            return
        message = {
            "actionType": SignalAction.END_SESSION.value,
            "sessionId": self._session_id,
            "payload": {},
        }
        try:
            async with asyncio.timeout(self.SHUTDOWN_TIMEOUT):
                await self._send_end_session(ws, message)
            logger.debug("Sent endsession for session %s", self._session_id)
        except TimeoutError:
            logger.warning("Timed out sending endsession")
        except Exception as e:
            logger.warning("Failed to send endsession: %s", e)

    async def _send_end_session(self, ws: ClientConnection, message: dict[str, Any]) -> None:
        async with self._send_lock:
            if ws.state is State.OPEN:
                await self._write(ws, message)

    async def send_offer(self, sdp: str, sdp_type: str) -> None:
        """Send WebRTC offer to the server.

        Args:
            sdp: The SDP offer string.
            sdp_type: The SDP type (usually "offer").
        """
        message = {
            "actionType": SignalAction.OFFER.value,
            "sessionId": self._session_id,
            "payload": {
                "connectionDescription": {
                    "sdp": sdp,
                    "type": sdp_type,
                },
                "userUid": self._session_id,
            },
        }
        logger.debug("Sending offer")
        await self.send_message(message)

    async def send_ice_candidate(
        self,
        candidate: str,
        sdp_mid: str | None,
        sdp_mline_index: int | None,
    ) -> None:
        """Send an ICE candidate to the server.

        Args:
            candidate: The ICE candidate string.
            sdp_mid: The media stream ID.
            sdp_mline_index: The media line index.
        """
        message = {
            "actionType": SignalAction.ICE_CANDIDATE.value,
            "sessionId": self._session_id,
            "payload": {
                "candidate": candidate,
                "sdpMid": sdp_mid,
                "sdpMLineIndex": sdp_mline_index,
            },
        }
        await self.send_message(message)

    async def send_agent_audio_input(
        self, payload: AgentAudioInputPayload, admitted: Callable[[], None] | None = None
    ) -> None:
        """Send agent audio input message to the server.

        Args:
            payload: The audio input payload.
            admitted: See send_message().
        """
        # Convert to camelCase to match backend expectations
        message = {
            "actionType": SignalAction.AGENT_AUDIO_INPUT.value,
            "sessionId": self._session_id,
            "payload": {
                "audioData": payload.audio_data,
                "encoding": payload.encoding,
                "sampleRate": payload.sample_rate,
                "channels": payload.channels,
                "sequenceNumber": payload.sequence_number,
            },
        }
        if payload.sample_rate < 16000:
            logger.warning(
                f"Sample rate {payload.sample_rate}Hz is very low. Quality of speech and avatar is negatively impacted. Consider providing 24kHz audio for better results."
            )
        if payload.sample_rate > 24000:
            logger.warning(
                f"Sample rate {payload.sample_rate}Hz is high. Latency is negatively affected for minimal audio quality gain. Consider resampling to 24kHz"
            )
        await self.send_message(message, admitted)

    async def send_agent_audio_input_end(self, admitted: Callable[[], None] | None = None) -> None:
        """Send agent audio input end signal to the server.

        Signals the end of the current audio sequence/turn.

        Args:
            admitted: See send_message().
        """
        message = {
            "actionType": SignalAction.AGENT_AUDIO_INPUT_END.value,
            "sessionId": self._session_id,
            "payload": {},
        }
        await self.send_message(message, admitted)

    async def send_talk_stream_input(
        self,
        content: str,
        correlation_id: str,
        start_of_speech: bool = True,
        end_of_speech: bool = True,
        utterance_id: str | None = None,
        admitted: Callable[[], bool] | None = None,
    ) -> None:
        """Send talk stream input to make the avatar speak text directly.

        This bypasses the LLM and sends text directly to TTS.

        Args:
            content: The text for the avatar to speak.
            correlation_id: ID to correlate this message with interruptions.
                Callers should use TalkMessageStream which manages this.
            start_of_speech: Whether this is the start of a speech sequence.
            end_of_speech: Whether this is the end of a speech sequence.
            utterance_id: Optional ID marking the first chunk of a new utterance. None
                continues the current utterance; a different ID queues a new one after it.
                Validated by TalkMessageStream.send(); not re-checked here.
            admitted: Called under the send lock before the chunk is written or buffered.
                It returns False when the originating stream can no longer send, and the
                chunk is then dropped. A replacement stream with the same correlation id
                does not make it live again.

        Raises:
            RuntimeError: If the session has ended.
        """
        payload: dict[str, Any] = {
            "content": content,
            "startOfSpeech": start_of_speech,
            "endOfSpeech": end_of_speech,
            "correlationId": correlation_id,
        }
        if utterance_id is not None:
            payload["utteranceId"] = utterance_id

        message = {
            "actionType": SignalAction.TALK_STREAM_INPUT.value,
            "sessionId": self._session_id,
            "payload": payload,
        }
        async with self._send_lock:
            if self.is_permanently_closed:
                raise RuntimeError("Cannot send a talk stream chunk: the session has ended")
            if (admitted is not None and not admitted()) or self._is_dead_stream(correlation_id):
                logger.debug("Dropping talk stream chunk for an interrupted or failed stream")
            elif self._heartbeat_acks is HeartbeatAcks.NO or not self._ever_opened:
                await self._send_or_buffer(message)
            elif self._heartbeat_acks is HeartbeatAcks.YES and self._monitor.is_suspect():
                logger.warning("Refusing talk stream chunk: the signalling socket looks dead")
                self._queue_failures([correlation_id])
            elif self._ws is None or self._ws.state is not State.OPEN:
                self._queue_failures([correlation_id])
            else:
                await self._write_tracked(self._ws, correlation_id, message)

    async def _write_tracked(
        self, ws: ClientConnection, correlation_id: str, message: dict[str, Any]
    ) -> None:
        self._track(correlation_id)
        try:
            await self._write(ws, message)
        except Exception as e:
            logger.warning("Failed to send talk stream chunk: %s", e)
            if self._monitor.is_tracked(correlation_id):
                self._monitor.drop(correlation_id)
                self._queue_failures([correlation_id])

    async def close(self) -> None:
        """Stop the client and close the socket.

        A callback that is running on the dispatcher is not cancelled or awaited.
        """
        self.stop()
        current = asyncio.current_task()
        tasks = [
            task
            for task in (self._run_task, self._heartbeat_task, self._watchdog_task)
            if task is not None and task is not current
        ]
        dispatch_task = self._dispatch_task
        if dispatch_task is not None and dispatch_task is not current and self._dispatching is None:
            tasks.append(dispatch_task)
        await asyncio.gather(*tasks, return_exceptions=True)

        ws, self._ws, self._live_ws = self._ws, None, None
        if ws is not None:
            await self._dispose(ws)

        logger.debug("SignallingClient closed")

    async def _dispose(self, ws: ClientConnection) -> None:
        try:
            async with asyncio.timeout(self.SHUTDOWN_TIMEOUT):
                await ws.close()
        except TimeoutError:
            logger.warning("Timed out closing signalling socket; aborting transport")
            transport = getattr(ws, "transport", None)
            if transport is not None:
                transport.abort()
