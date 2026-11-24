"""WebSocket signalling client for Anam services."""

import asyncio
import functools
import json
import logging
import time
from enum import Enum
from typing import Any, Awaitable, Callable

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State

from ._heartbeat_monitor import HeartbeatMonitor
from .types import AgentAudioInputPayload, SessionInfo

logger = logging.getLogger(__name__)

_DispatchItem = tuple[Callable[[], Awaitable[None]], bool]


async def _noop(*_: Any) -> None:
    return None


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
        on_close: Callable[[int | None, str], Awaitable[None]] | None = None,
        on_talk_stream_delivery_failed: Callable[[str], Awaitable[None]] | None = None,
    ):
        """Initialize the signalling client.

        Args:
            session_info: Session information from API.
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
        self._on_close = on_close
        self._on_talk_stream_delivery_failed = on_talk_stream_delivery_failed

        self._ws: ClientConnection | None = None
        self._stop_signal = False
        self._permanently_closed = False
        self._server_ended = False
        self._connection_attempts = 0
        self._send_buffer: list[dict[str, Any]] = []
        self._buffer_overflowed = False
        self._send_lock = asyncio.Lock()
        self._dispatch_queue: asyncio.Queue[_DispatchItem] = asyncio.Queue()

        self._monitor = HeartbeatMonitor()
        self._engine_sends_heartbeat_acks: bool | None = None
        self._ever_opened = False
        self._unanswered_replacements = 0
        self._give_up = False
        self._failed_cids: set[str] = set()
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
        opened_at = asyncio.get_running_loop().time()

        self._dispatch_task = asyncio.create_task(self._dispatch())
        await self._activate(ws)
        self._run_task = asyncio.create_task(self._supervise(ws, opened_at))

        if self._on_open:
            try:
                await self._on_open()
            except BaseException:
                await self.close()
                raise

    def _owned_tasks(self) -> list[asyncio.Task[None]]:
        tasks = (self._run_task, self._dispatch_task, self._heartbeat_task, self._watchdog_task)
        return [task for task in tasks if task is not None]

    async def _activate(self, ws: ClientConnection) -> None:
        self._watchdog_task = asyncio.create_task(self._watch(ws))
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
        tasks = [task for task in (self._heartbeat_task, self._watchdog_task) if task is not None]
        self._heartbeat_task = None
        self._watchdog_task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._write_started is not None and self._write_started[0] is ws:
            self._write_started = None
        self._queue_failures(self._monitor.reset())

    async def _supervise(self, ws: ClientConnection, opened_at: float) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await self._receive(ws)
            await self._deactivate(ws)
            code, reason = ws.close_code, ws.close_reason or ""
            if self._stop_signal or self._server_ended:
                return
            logger.warning("Signalling socket closed: code=%s reason=%s", code, reason)
            if self._give_up:
                logger.error("Signalling socket replaced too often without an answer")
                self._terminate(code, reason)
                return

            if loop.time() - opened_at >= self.STABLE_CONNECTION_SECONDS:
                self._connection_attempts = 0
            self._connection_attempts += 1

            new_ws = await self._reconnect()
            if new_ws is None:
                if not self._stop_signal:
                    self._terminate(code, reason)
                return
            ws = new_ws
            opened_at = loop.time()
            await self._activate(ws)

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

    def _terminate(self, code: int | None, reason: str) -> None:
        self._permanently_closed = True
        buffered = [_talk_correlation_id(message) for message in self._send_buffer]
        self._send_buffer.clear()
        self._queue_failures([cid for cid in buffered if cid is not None])
        on_close = self._on_close or _noop
        self._dispatch_queue.put_nowait((functools.partial(on_close, code, reason), True))

    async def _receive(self, ws: ClientConnection) -> None:
        try:
            async for raw in ws:
                if self._stop_signal:
                    return
                self._handle_incoming(raw)
        except ConnectionClosed:
            pass

    def _handle_incoming(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
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
            self._server_ended = True
        elif action_type == SignalAction.SESSION_READY.value:
            acks = isinstance(payload, dict) and payload.get("sendsHeartbeatAcks") is True
            self._engine_sends_heartbeat_acks = acks
            if not acks:
                self._monitor.disable()
        elif action_type == SignalAction.TALK_STREAM_INTERRUPTED.value:
            correlation_id = payload.get("correlationId") if isinstance(payload, dict) else None
            if isinstance(correlation_id, str):
                self._monitor.drop(correlation_id)
        if self._on_message:
            self._dispatch_queue.put_nowait((functools.partial(self._on_message, data), False))

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
            if correlation_id not in self._failed_cids:
                self._failed_cids.add(correlation_id)
                new.append(correlation_id)
        return new

    def _queue_failures(self, correlation_ids: list[str]) -> None:
        callback = self._on_talk_stream_delivery_failed
        for correlation_id in self._claim_failures(correlation_ids):
            if callback:
                self._dispatch_queue.put_nowait(
                    (functools.partial(callback, correlation_id), False)
                )

    async def _report_now(self, correlation_ids: list[str]) -> None:
        callback = self._on_talk_stream_delivery_failed
        for correlation_id in correlation_ids:
            if self._stop_signal or callback is None:
                return
            try:
                await callback(correlation_id)
            except Exception as e:
                logger.error("Error handling talk stream delivery failure: %s", e)

    async def _dispatch(self) -> None:
        while not self._stop_signal:
            call, last = await self._dispatch_queue.get()
            try:
                await call()
            except Exception as e:
                logger.error("Error handling signalling callback: %s", e)
            if last:
                return

    async def _send_heartbeats(self, ws: ClientConnection) -> None:
        next_beat = time.monotonic() + self._heartbeat_interval
        while True:
            self._probe_wake.clear()
            due = next_beat
            probe_at = self._monitor.probe_due_at()
            if probe_at is not None:
                due = min(due, probe_at)
            delay = due - time.monotonic()
            if delay > 0:
                try:
                    await asyncio.wait_for(self._probe_wake.wait(), delay)
                except asyncio.TimeoutError:
                    pass
                continue
            async with self._send_lock:
                if self._stop_signal or ws.state is not State.OPEN:
                    return
                seq = self._monitor.next_seq()
                # websockets writes the frame before awaiting drain, so the ack can arrive first.
                self._monitor.record_sent(seq)
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

    async def _watch(self, ws: ClientConnection) -> None:
        while True:
            self._watchdog_wake.clear()
            now = time.monotonic()
            ack_deadline = self._monitor.ack_deadline()
            stall_deadline = None
            if self._write_started is not None and self._write_started[0] is ws:
                stall_deadline = self._write_started[1] + self.WRITE_STALL_SECONDS
            deadlines = [d for d in (ack_deadline, stall_deadline) if d is not None]
            if not deadlines:
                await self._watchdog_wake.wait()
                continue
            if min(deadlines) > now:
                try:
                    await asyncio.wait_for(self._watchdog_wake.wait(), min(deadlines) - now)
                except asyncio.TimeoutError:
                    pass
                continue
            if stall_deadline is not None and stall_deadline <= now:
                logger.warning("Signalling write stalled; replacing the socket")
            else:
                failed = self._monitor.expire()
                self._queue_failures(failed)
                if self._engine_sends_heartbeat_acks is None and not failed:
                    continue
                logger.warning("Signalling heartbeat not answered; replacing the socket")
            if self._unanswered_replacements >= self._max_reconnection_attempts:
                self._give_up = True
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

    async def _flush(self, ws: ClientConnection) -> None:
        self._buffer_overflowed = False
        while self._send_buffer and not self.is_permanently_closed:
            if ws.state is not State.OPEN:
                return
            message = self._send_buffer.pop(0)
            correlation_id = _talk_correlation_id(message)
            if correlation_id is not None:
                if correlation_id in self._failed_cids:
                    continue
                if self._engine_sends_heartbeat_acks is not False:
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

    def _buffer(self, message: dict[str, Any]) -> list[str]:
        self._send_buffer.append(message)
        if len(self._send_buffer) <= self.SEND_BUFFER_LIMIT:
            return []
        index = self._eviction_index()
        if index is None:
            return []
        evicted = self._send_buffer.pop(index)
        if not self._buffer_overflowed:
            self._buffer_overflowed = True
            logger.warning(
                "Signalling send buffer is full; dropping the oldest %s message",
                evicted.get("actionType"),
            )
        correlation_id = _talk_correlation_id(evicted)
        return self._claim_failures([correlation_id]) if correlation_id is not None else []

    def _eviction_index(self) -> int | None:
        actions = [message.get("actionType") for message in self._send_buffer]
        if SignalAction.AGENT_AUDIO_INPUT.value in actions:
            return actions.index(SignalAction.AGENT_AUDIO_INPUT.value)
        for index, action in enumerate(actions):
            if action != SignalAction.OFFER.value:
                return index
        return None

    async def send_message(self, message: dict[str, Any]) -> None:
        """Send a message, or buffer it until a socket is open.

        Args:
            message: The message to send.
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
            failed = await self._send_or_buffer(message)
        await self._report_now(failed)

    async def _send_or_buffer(self, message: dict[str, Any]) -> list[str]:
        ws = self._ws
        if ws is None or ws.state is not State.OPEN:
            return self._buffer(message)
        try:
            await self._write(ws, message)
        except Exception as e:
            logger.warning("Failed to send %s message: %s", message.get("actionType"), e)
        return []

    async def send_end_session(self) -> None:
        """Inform the backend to end the session gracefully. Best effort, as we are on the shutdown path."""
        self._stop_signal = True
        self._send_buffer.clear()
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
            await asyncio.wait_for(
                self._send_end_session(ws, message), timeout=self.SHUTDOWN_TIMEOUT
            )
            logger.debug("Sent endsession for session %s", self._session_id)
        except asyncio.TimeoutError:
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

    async def send_agent_audio_input(self, payload: AgentAudioInputPayload) -> None:
        """Send agent audio input message to the server.

        Args:
            payload: The audio input payload.
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
        await self.send_message(message)

    async def send_agent_audio_input_end(self) -> None:
        """Send agent audio input end signal to the server.

        Signals the end of the current audio sequence/turn.
        """
        message = {
            "actionType": SignalAction.AGENT_AUDIO_INPUT_END.value,
            "sessionId": self._session_id,
            "payload": {},
        }
        await self.send_message(message)

    async def send_talk_stream_input(
        self,
        content: str,
        correlation_id: str,
        start_of_speech: bool = True,
        end_of_speech: bool = True,
        utterance_id: str | None = None,
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
        failed: list[str] = []
        async with self._send_lock:
            if self._stop_signal:
                logger.debug("Dropping talk stream chunk: signalling is closed")
                return
            if correlation_id in self._failed_cids:
                logger.debug("Dropping talk stream chunk for a stream that already failed")
                return
            if self._permanently_closed:
                failed = self._claim_failures([correlation_id])
            elif self._engine_sends_heartbeat_acks is False or not self._ever_opened:
                failed = await self._send_or_buffer(message)
            elif self._engine_sends_heartbeat_acks is True and self._monitor.is_suspect():
                logger.warning("Refusing talk stream chunk: the signalling socket looks dead")
                failed = self._claim_failures([correlation_id])
            elif self._ws is None or self._ws.state is not State.OPEN:
                failed = self._claim_failures([correlation_id])
            else:
                failed = await self._write_tracked(self._ws, correlation_id, message)
        await self._report_now(failed)

    async def _write_tracked(
        self, ws: ClientConnection, correlation_id: str, message: dict[str, Any]
    ) -> list[str]:
        self._track(correlation_id)
        try:
            await self._write(ws, message)
        except Exception as e:
            logger.warning("Failed to send talk stream chunk: %s", e)
            if self._monitor.is_tracked(correlation_id):
                self._monitor.drop(correlation_id)
                return self._claim_failures([correlation_id])
        return []

    async def close(self) -> None:
        self._stop_signal = True
        self._send_buffer.clear()

        current = asyncio.current_task()
        tasks = [task for task in self._owned_tasks() if task is not current]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        if self._ws:
            try:
                await self._dispose(self._ws)
            finally:
                self._ws = None

        logger.debug("SignallingClient closed")

    async def _dispose(self, ws: ClientConnection) -> None:
        try:
            await asyncio.wait_for(ws.close(), timeout=self.SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Timed out closing signalling socket; aborting transport")
            transport = getattr(ws, "transport", None)
            if transport is not None:
                transport.abort()
