"""WebRTC streaming client for Anam services."""

import asyncio
import contextvars
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable

import aiohttp
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCDataChannel,
    RTCIceCandidate,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from av.audio.frame import AudioFrame
from av.video.frame import VideoFrame

from ._agent_audio_input_stream import AgentAudioInputStream
from ._signalling import SignalAction, SignallingClient
from ._user_audio_input_track import UserAudioInputTrack
from .errors import SessionError
from .types import AgentAudioInputConfig, ConnectionClosedCode, SessionInfo

logger = logging.getLogger(__name__)

# Set while the delivery failure and CONNECTION_CLOSED listeners of a session run. Child tasks
# copy the context, so a task that such a listener starts sees the value too.
_notifying: contextvars.ContextVar["StreamingClient | None"] = contextvars.ContextVar(
    "anam_terminal_notification", default=None
)


async def _call_listener(call: Awaitable[None]) -> None:
    try:
        await call
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise
        logger.error("A session listener raised CancelledError")
    except Exception as e:
        logger.error("Error in session listener: %s", e)


class StreamingClient:
    """WebRTC client for streaming audio/video with Anam.

    Handles peer connection setup, track management, and data channels.
    """

    ICE_CANDIDATE_POOL_SIZE = 2

    def __init__(
        self,
        session_info: SessionInfo,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_connection_established: Callable[[], Awaitable[None]] | None = None,
        on_connection_closed: Callable[[str, str | None], Awaitable[None]] | None = None,
        on_session_ready: Callable[[], Awaitable[None]] | None = None,
        on_server_warning: Callable[[str], Awaitable[None]] | None = None,
        on_talk_stream_interrupted: Callable[[str], Awaitable[None]] | None = None,
        custom_ice_servers: list[dict[str, Any]] | None = None,
        on_talk_stream_delivery_failed: Callable[[str], Awaitable[None]] | None = None,
        on_torn_down: Callable[[], None] | None = None,
    ):
        """Initialize the streaming client.

        Args:
            session_info: Session information from API.
            on_message: Callback for data channel messages.
            on_connection_established: Callback when connected.
            on_session_ready: Callback when sessionready signal is received (ready to receive TTS).
            on_server_warning: Callback(payload) when the server sends a warning.
            on_talk_stream_interrupted: Callback(correlation_id) when talk stream is interrupted.
            custom_ice_servers: Custom ICE servers (optional).
            on_talk_stream_delivery_failed: Callback(correlation_id) for a talk stream whose
                delivery is unconfirmed.
            on_torn_down: Called once media and signalling are torn down, before the failure
                and CONNECTION_CLOSED listeners run.
        """
        self._session_info = session_info
        self._session_id = session_info.session_id

        # Callbacks (only for connection events and messages)
        self._on_message = on_message
        self._on_connection_established = on_connection_established
        self._on_connection_closed = on_connection_closed
        self._on_session_ready = on_session_ready
        self._on_server_warning = on_server_warning
        self._on_talk_stream_interrupted = on_talk_stream_interrupted
        self._on_talk_stream_delivery_failed = on_talk_stream_delivery_failed
        self._on_torn_down = on_torn_down

        # Configuration
        self._ice_servers = custom_ice_servers or session_info.ice_servers

        # State
        self._peer_connection: RTCPeerConnection | None = None
        self._signalling_client: SignallingClient | None = None
        self._data_channel: RTCDataChannel | None = None
        self._connection_received_answer = False
        self._remote_ice_buffer: list[RTCIceCandidate] = []
        self._video_track: MediaStreamTrack | None = None
        self._audio_track: MediaStreamTrack | None = None
        self._is_connected = False
        self._agent_audio_input_stream: AgentAudioInputStream | None = None
        self._user_audio_input_track: UserAudioInputTrack | None = None
        self._audio_transceiver = None  # Store transceiver for lazy track creation
        self._connection_ready = asyncio.Event()
        self._started = False
        self._end: tuple[ConnectionClosedCode, str | None] | None = None
        self._end_task: asyncio.Task[None] | None = None
        self._teardown_task: asyncio.Task[None] | None = None
        self._torn_down = False
        self._notified = False
        self._finished = False
        self._signalling: SignallingClient | None = None

    async def connect(self, timeout: float = 30.0) -> None:
        """Start the streaming connection.

        Establishes WebSocket signalling and WebRTC peer connection.
        Waits for the connection to be fully established before returning.

        Args:
            timeout: Maximum time to wait for connection (seconds).
        """
        logger.info("Starting streaming connection for session: %s", self._session_id)
        if self._end is not None:
            raise SessionError("Session ended while connecting")

        # Create signalling client
        self._signalling_client = SignallingClient(
            session_info=self._session_info,
            on_message=self._handle_signal_message,
            on_open=self._on_signalling_open,
            on_end=self._terminate,
            on_talk_stream_delivery_failed=self._report_failure,
        )

        # Connect to signalling server
        try:
            await self._signalling_client.connect()
        except Exception as e:
            if self._end is None:
                raise
            # The teardown closed the peer connection under the offer.
            logger.debug("Signalling open failed after the session ended: %s", e)

        # Wait for WebRTC connection to be established
        try:
            async with asyncio.timeout(timeout):
                await self._connection_ready.wait()
        except TimeoutError:
            logger.warning("Connection timed out waiting for WebRTC, proceeding anyway")
        if self._end is not None:
            await self.close()
            raise SessionError("Session ended while connecting")
        self._started = True
        logger.info("Streaming connection ready")

    def _terminate(self, code: ConnectionClosedCode, reason: str | None) -> None:
        """Latch the cause that ends the session and start the teardown. The first cause wins."""
        if self._end is not None:
            return
        logger.info("Ending streaming session: %s %s", code.value, reason or "")
        self._end = (code, reason)
        self._is_connected = False
        self._connection_ready.set()
        if self._signalling_client is not None:
            self._signalling_client.stop()
        self._end_task = asyncio.create_task(self._finish())

    def _start_teardown(self) -> asyncio.Task[None]:
        assert self._end is not None
        task = self._teardown_task
        if task is None or (task.done() and not self._torn_down):
            send_end_session = self._end[0] is not ConnectionClosedCode.SERVER_CLOSED
            task = self._teardown_task = asyncio.create_task(self._teardown(send_end_session))
        return task

    async def _finish(self) -> None:
        assert self._end is not None
        code, reason = self._end
        await self._start_teardown()
        if self._on_torn_down is not None:
            self._on_torn_down()
        await self._notify(code, reason)
        self._finished = True

    async def _report_failure(self, correlation_id: str) -> None:
        if self._on_talk_stream_delivery_failed is None:
            return
        token = _notifying.set(self)
        try:
            await self._on_talk_stream_delivery_failed(correlation_id)
        finally:
            _notifying.reset(token)

    async def _notify(self, code: ConnectionClosedCode, reason: str | None) -> None:
        if self._notified:
            return
        self._notified = True
        signalling = self._signalling_client or self._signalling
        failures: list[str] = []
        if signalling is not None:
            await signalling.finish_failure_report()
            failures = signalling.take_failures(
                include_unconfirmed=code is not ConnectionClosedCode.NORMAL
            )
        token = _notifying.set(self)
        try:
            for correlation_id in failures:
                if self._on_talk_stream_delivery_failed:
                    await _call_listener(self._on_talk_stream_delivery_failed(correlation_id))
            if self._started and self._on_connection_closed:
                await _call_listener(self._on_connection_closed(code.value, reason))
        finally:
            _notifying.reset(token)

    async def _on_signalling_open(self) -> None:
        if self._peer_connection is not None or self._end is not None:
            return
        logger.debug("Signalling connected, initializing peer connection")
        await self._init_peer_connection()
        await self._create_and_send_offer()

    async def _handle_signal_message(self, message: dict[str, Any]) -> None:
        """Handle incoming signalling messages."""
        action_type = message.get("actionType", "").lower()
        payload = message.get("payload")

        logger.debug("Signal message received: %s", action_type)

        if action_type == SignalAction.ANSWER.value:
            await self._handle_answer(payload)

        elif action_type == SignalAction.ICE_CANDIDATE.value:
            await self._handle_ice_candidate(payload)

        elif action_type == SignalAction.WARNING.value:
            logger.warning("Server warning: %s", payload)
            if self._on_server_warning and isinstance(payload, str):
                await self._on_server_warning(payload)

        elif action_type == SignalAction.SESSION_READY.value:
            logger.info("Session ready")
            if self._on_session_ready:
                await self._on_session_ready()

        elif action_type == SignalAction.TALK_STREAM_INTERRUPTED.value:
            correlation_id = payload.get("correlationId") if isinstance(payload, dict) else None
            logger.debug("Talk stream interrupted: %s", correlation_id)
            if self._on_talk_stream_interrupted and correlation_id:
                await self._on_talk_stream_interrupted(correlation_id)

    async def _handle_answer(self, payload: dict[str, Any]) -> None:
        """Handle SDP answer from server."""
        if not self._peer_connection:
            logger.error("No peer connection for answer")
            return

        logger.info("Received SDP answer, setting remote description")
        answer = RTCSessionDescription(
            sdp=payload["sdp"],
            type=payload["type"],
        )
        await self._peer_connection.setRemoteDescription(answer)
        self._connection_received_answer = True
        logger.info(
            "Remote description set, flushing %d buffered ICE candidates",
            len(self._remote_ice_buffer),
        )

        # Flush buffered ICE candidates
        await self._flush_ice_buffer()

    async def _handle_ice_candidate(self, payload: dict[str, Any]) -> None:
        """Handle ICE candidate from server."""
        if not self._peer_connection:
            logger.error("No peer connection for ICE candidate")
            return

        candidate = self._create_ice_candidate(payload)
        logger.debug(
            "Remote ICE candidate: %s:%s (%s)", candidate.ip, candidate.port, candidate.type
        )

        if self._connection_received_answer:
            try:
                await self._peer_connection.addIceCandidate(candidate)
                logger.debug("Added ICE candidate to peer connection")
            except Exception as e:
                logger.error("Failed to add ICE candidate: %s", e)
        else:
            self._remote_ice_buffer.append(candidate)
            logger.debug("Buffered ICE candidate (waiting for answer)")

    def _create_ice_candidate(self, payload: dict[str, Any]) -> RTCIceCandidate:
        """Create RTCIceCandidate from payload."""
        candidate_str = payload["candidate"]
        parts = candidate_str.split()

        # Parse the candidate string
        # Format: candidate:foundation component protocol priority ip port type ...
        return RTCIceCandidate(
            component=int(parts[1]),
            foundation=parts[0].split(":")[1],
            ip=parts[4],
            port=int(parts[5]),
            priority=int(parts[3]),
            protocol=parts[2],
            type=parts[7],
            sdpMid=payload.get("sdpMid", ""),
            sdpMLineIndex=payload.get("sdpMLineIndex", 0),
        )

    async def _flush_ice_buffer(self) -> None:
        """Add all buffered ICE candidates to peer connection."""
        if not self._peer_connection:
            return

        for candidate in self._remote_ice_buffer:
            try:
                await self._peer_connection.addIceCandidate(candidate)
            except Exception as e:
                logger.error("Failed to add ICE candidate: %s", e)

        self._remote_ice_buffer.clear()

    async def _init_peer_connection(self) -> None:
        """Initialize the WebRTC peer connection."""
        # Configure ICE servers
        ice_servers = []
        logger.info("Configuring ICE servers: %d server(s)", len(self._ice_servers))
        for server in self._ice_servers:
            urls = server.get("urls", [])
            # Handle both single URL string and list of URLs
            if isinstance(urls, str):
                urls = [urls]
            logger.info(
                "Adding ICE server: urls=%s, username=%s, has_credential=%s",
                urls,
                server.get("username"),
                bool(server.get("credential")),
            )
            ice_servers.append(
                RTCIceServer(
                    urls=urls,
                    username=server.get("username"),
                    credential=server.get("credential"),
                )
            )

        if not ice_servers:
            logger.warning("No ICE servers configured - connection may fail behind NAT/firewall")

        config = RTCConfiguration(iceServers=ice_servers)
        self._peer_connection = RTCPeerConnection(configuration=config)

        # Set up event handlers using decorator pattern (like working old SDK)
        @self._peer_connection.on("icecandidate")
        async def on_ice_candidate(candidate: RTCIceCandidate | None) -> None:
            """Send local ICE candidates to the remote peer."""
            if candidate:
                logger.info(
                    "Local ICE candidate: %s",
                    candidate.candidate[:80] if candidate.candidate else "None",
                )
                if self._signalling_client:
                    ice_message = {
                        "actionType": SignalAction.ICE_CANDIDATE.value,
                        "sessionId": self._session_id,
                        "payload": {
                            "candidate": candidate.candidate,
                            "sdpMid": candidate.sdpMid,
                            "sdpMLineIndex": candidate.sdpMLineIndex,
                        },
                    }
                    await self._signalling_client.send_message(ice_message)
                    logger.info("ICE candidate sent to server")
            else:
                logger.info("ICE gathering completed (null candidate)")

        @self._peer_connection.on("iceconnectionstatechange")
        def on_ice_connection_state_change() -> None:
            if not self._peer_connection:
                return
            state = self._peer_connection.iceConnectionState
            logger.debug("ICE connection state: %s", state)
            if state in ("connected", "completed"):
                self._mark_connected()
            elif state == "failed":
                logger.error(
                    "ICE connection failed - check TURN/STUN server configuration and network connectivity"
                )
                self._connection_ready.set()

        @self._peer_connection.on("connectionstatechange")
        def on_connection_state_change() -> None:
            if not self._peer_connection:
                return
            state = self._peer_connection.connectionState
            logger.debug("Connection state: %s", state)
            if state == "closed":
                self._terminate(ConnectionClosedCode.WEBRTC_FAILURE, None)

        @self._peer_connection.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            logger.info("Received %s track", track.kind)
            if track.kind == "video":
                self._video_track = track
            elif track.kind == "audio":
                self._audio_track = track

        # Set up data channel
        await self._setup_data_channel()

        # Set up transceivers
        # Video: receive only
        self._peer_connection.addTransceiver("video", direction="recvonly")

        # Audio: send/receive (track created lazily when first audio arrives via send_user_audio())
        self._audio_transceiver = self._peer_connection.addTransceiver(
            "audio", direction="sendrecv"
        )

        logger.debug("Peer connection initialized")

    async def _setup_data_channel(self) -> None:
        """Set up the data channel for messaging."""
        if not self._peer_connection:
            return

        self._data_channel = self._peer_connection.createDataChannel(
            "session",
            ordered=True,
        )

        # Initialize to False in case there's a stale value from a previous session
        self._data_channel_open = False

        # Check if channel is already open
        if self._data_channel.readyState == "open":
            self._data_channel_open = True

        @self._data_channel.on("open")
        def on_open() -> None:
            logger.debug("Data channel opened")
            self._data_channel_open = True

        @self._data_channel.on("close")
        def on_close() -> None:
            logger.debug("Data channel closed")
            self._data_channel_open = False

        @self._data_channel.on("message")
        async def on_message(message: str) -> None:
            try:
                data = json.loads(message)
                if self._on_message:
                    await self._on_message(data)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse data channel message: %s", e)

    def _mark_connected(self) -> None:
        if self._is_connected or self._end is not None:
            return
        self._is_connected = True
        self._connection_ready.set()
        if self._on_connection_established:
            asyncio.create_task(self._on_connection_established())

    async def video_frames(self) -> AsyncIterator[VideoFrame]:
        """Get video frames as an async iterator.

        Yields:
            VideoFrame: PyAV VideoFrame objects from the WebRTC stream.

        Raises:
            RuntimeError: If video track is not available.

        Example:
            ```python
            async for frame in streaming_client.video_frames():
                # Process video frame
                image = frame.to_ndarray(format="rgb24")
            ```
        """
        if not self._video_track:
            raise RuntimeError("Video track not available. Wait for connection to be established.")

        logger.debug("Starting video frame iterator")
        frame_count = 0

        while True:
            try:
                frame = await self._video_track.recv()
                frame_count += 1

                if frame_count == 1:
                    logger.info("First video frame received: %dx%d", frame.width, frame.height)
                    # Signal connection established on first actual frame
                    self._mark_connected()

                yield frame

            except Exception as e:
                if "MediaStreamError" in str(type(e).__name__):
                    logger.debug("Video track ended after %d frames", frame_count)
                    break
                logger.error("Error receiving video frame: %s", e)
                raise

    async def audio_frames(self) -> AsyncIterator[AudioFrame]:
        """Get audio frames as an async iterator.

        Yields:
            AudioFrame: PyAV AudioFrame objects from the WebRTC stream.
            Audio frames are decoded PCM: 16-bit, 48kHz, stereo samples.

        Raises:
            RuntimeError: If audio track is not available.

        Example:
            ```python
            async for frame in streaming_client.audio_frames():
                # Process audio frame
                samples = frame.to_ndarray()
            ```
        """
        if not self._audio_track:
            raise RuntimeError("Audio track not available. Wait for connection to be established.")

        logger.debug("Starting audio frame iterator")
        frame_count = 0

        while True:
            try:
                # Receive audio frame from WebRTC
                # Incoming audio are decoded (PCM) WebRTC OPUS: 16 bit 48kHz stereo samples.
                frame = await self._audio_track.recv()
                frame_count += 1

                if frame_count == 1:
                    logger.debug("First audio frame received: %dHz", frame.sample_rate)
                    logger.debug("Audio frame layout: %s", frame.layout)
                    logger.debug("Audio frame channels: %d", frame.layout.nb_channels)
                    logger.debug("Audio frame layout name: %s", frame.layout.name)
                    logger.debug("Audio frame format: %s", frame.format.name)
                    logger.debug("Audio frame samples: %d", frame.samples)
                    # Signal connection established on first audio frame if video hasn't yet
                    self._mark_connected()

                yield frame

            except Exception as e:
                if "MediaStreamError" in str(type(e).__name__):
                    logger.debug("Audio track ended after %d frames", frame_count)
                    break
                logger.error("Error receiving audio frame: %s", e)
                raise

    async def _create_and_send_offer(self) -> None:
        """Create and send WebRTC offer."""
        if not self._peer_connection or not self._signalling_client:
            logger.error("Cannot create offer: missing peer connection or signalling")
            return

        # Create offer
        offer = await self._peer_connection.createOffer()
        await self._peer_connection.setLocalDescription(offer)

        # Get the local description (with gathered ICE candidates)
        local_desc = self._peer_connection.localDescription

        logger.debug("Sending WebRTC offer")
        await self._signalling_client.send_offer(
            sdp=local_desc.sdp,
            sdp_type=local_desc.type,
        )

    def send_data_message(self, message: str) -> bool:
        """Send a message through the data channel.

        Args:
            message: The message string to send.

        Returns:
            True if message was sent, False otherwise.
        """
        if self._data_channel and getattr(self, "_data_channel_open", False):
            self._data_channel.send(message)
            return True
        else:
            logger.warning("Data channel not open, message not sent")
            return False

    async def wait_for_data_channel(self, timeout: float = 10.0) -> bool:
        """Wait for the data channel to open.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if data channel opened, False if timeout or the session is ending.
        """
        start = asyncio.get_event_loop().time()
        while not getattr(self, "_data_channel_open", False):
            if self.is_ending or asyncio.get_event_loop().time() - start > timeout:
                return False
            await asyncio.sleep(0.1)
        return True

    def send_user_message(self, content: str) -> None:
        """Send a user text message.

        Args:
            content: The message text.
        """
        import datetime

        message = {
            "content": content,
            "timestamp": datetime.datetime.now(datetime.timezone.utc)
            .replace(tzinfo=None)
            .isoformat(),
            "session_id": self._session_id,
            "message_type": "speech",
        }
        self.send_data_message(json.dumps(message))

    def send_interrupt(self) -> None:
        """Send interrupt command to stop persona speaking."""
        import datetime

        message = {
            "message_type": "interrupt",
            "session_id": self._session_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.send_data_message(json.dumps(message))

    async def send_talk(self, content: str) -> None:
        """Send a single text message directly to TTS via REST API.

        Convenience method for one-off messages. Sends text directly to TTS,
        bypassing the LLM, but slightly higher latency than send_talk_stream().
        For streaming multiple chunks, use create_talk_stream() to manage the stream.

        Args:
            content: The text for the avatar to speak.

        Raises:
            RuntimeError: If session info is not available.
            aiohttp.ClientError: If the HTTP request fails.
        """
        # Build engine URL from session info
        # The engine_host includes the full path (e.g., connect-eu.anam.ai/v1/webrtc/engines/xxx)
        protocol = self._session_info.engine_protocol
        host = self._session_info.engine_host

        engine_base_url = f"{protocol}://{host}"
        url = f"{engine_base_url}/talk?session_id={self._session_id}"

        logger.debug("Sending talk command to %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={"Content-Type": "application/json"},
                json={"content": content},
            ) as response:
                if not response.ok:
                    error_text = await response.text()
                    logger.error("Talk command failed: %s %s", response.status, error_text)
                    raise RuntimeError(
                        f"Failed to send talk command: {response.status} {error_text}"
                    )
                logger.info("Talk command sent successfully to %s", url)

    def create_agent_audio_input_stream(
        self, config: AgentAudioInputConfig
    ) -> AgentAudioInputStream:
        """Create an agent audio input stream for sending PCM audio data.

        Args:
            config: Audio format configuration.

        Returns:
            AgentAudioInputStream instance.

        Raises:
            RuntimeError: If signalling client is not available.
        """
        if not self._signalling_client:
            raise RuntimeError(
                "Failed to create agent audio input stream: signalling client is not available"
            )
        self._agent_audio_input_stream = AgentAudioInputStream(config, self._signalling_client)
        return self._agent_audio_input_stream

    def get_agent_audio_input_stream(self) -> AgentAudioInputStream | None:
        """Get the current agent audio input stream if one exists.

        Returns:
            The agent audio input stream or None if not created.
        """
        return self._agent_audio_input_stream

    @property
    def is_ending(self) -> bool:
        """Whether the session has started ending. Stays True once set."""
        return self._end is not None

    @property
    def is_connected(self) -> bool:
        """Check if the streaming connection is active."""
        return self._is_connected

    @property
    def video_track(self) -> MediaStreamTrack | None:
        """Get the raw video track (for advanced usage)."""
        return self._video_track

    @property
    def audio_track(self) -> MediaStreamTrack | None:
        """Get the raw audio track (for advanced usage)."""
        return self._audio_track

    async def close(self) -> None:
        """Close the streaming connection and clean up resources.

        Waits for the teardown, then for the CONNECTION_CLOSED listeners. It does not wait
        for the listeners when it is called from a delivery failure or CONNECTION_CLOSED
        listener of this session, from a task that such a listener started, or from a task
        that is being cancelled. A listener must not await another task that calls close()
        unless it cancels that task first.
        """
        self._terminate(ConnectionClosedCode.NORMAL, None)
        await asyncio.shield(self._start_teardown())
        current = asyncio.current_task()
        if _notifying.get() is self or (current is not None and current.cancelling()):
            return
        task = self._end_task
        if task is None or (task.done() and not self._finished):
            task = self._end_task = asyncio.create_task(self._finish())
        if task is current:
            return
        await asyncio.shield(task)

    @property
    def teardown_finished(self) -> bool:
        return self._torn_down

    async def _teardown(self, send_end_session: bool) -> None:
        logger.debug("Closing streaming client")
        self._is_connected = False
        if self._signalling_client:
            self._signalling = self._signalling_client
            if send_end_session:
                try:
                    # Notify the backend before closing the socket.
                    await self._signalling_client.send_end_session()
                except Exception as e:
                    logger.warning("Error sending endsession: %s", e)
            try:
                await self._signalling_client.close()
            except Exception as e:
                logger.warning("Error closing signalling client: %s", e)
            finally:
                self._signalling_client = None

        # This clears the audio queue and prevents recv() from generating more frames
        if self._user_audio_input_track:
            try:
                self._user_audio_input_track.close()
                logger.debug("Closed user audio input track")
            except Exception as e:
                logger.warning("Error closing user audio input track: %s", e)
            finally:
                self._user_audio_input_track = None

        if self._peer_connection:
            try:
                await self._peer_connection.close()
            except Exception as e:
                logger.warning("Error closing peer connection: %s", e)
            finally:
                self._peer_connection = None
        self._torn_down = True
        logger.debug("Streaming client closed")

    def send_user_audio(
        self,
        audio_bytes: bytes,
        sample_rate: int,
        num_channels: int,
    ) -> None:
        """Send raw user audio samples to Anam for processing."""
        if not self._peer_connection:
            raise RuntimeError("Peer connection not initialized. Call connect() first.")
        if num_channels != 1 and num_channels != 2:
            raise RuntimeError("Invalid number of channels. Must be 1 or 2.")

        # Create track lazily when first audio arrives
        if self._user_audio_input_track is None:
            logger.info(
                f"Creating user audio input track: sample_rate={sample_rate}Hz, "
                f"channels={num_channels}"
            )

            self._user_audio_input_track = UserAudioInputTrack(sample_rate, num_channels)

            # Add track to transceiver (lazy track creation)
            if self._audio_transceiver and self._audio_transceiver.sender:
                try:
                    self._audio_transceiver.sender.replaceTrack(self._user_audio_input_track)
                    logger.info("Added user audio track to transceiver")
                except Exception as e:
                    raise RuntimeError(f"Failed to add user audio track: {e}") from e
            else:
                raise RuntimeError("Audio transceiver not properly initialized")
        if self._peer_connection.connectionState == "connected":
            # Avoid accumulating stale audio, only queue audio when connection is established.
            self._user_audio_input_track.add_audio_samples(audio_bytes, sample_rate, num_channels)

    def __del__(self) -> None:
        """Cleanup on destruction to prevent warnings."""
        # Clear peer connection reference if close() wasn't called explicitly.
        # Note: This won't prevent RTCPeerConnection.__del__ from being called
        # if the object is garbage collected independently, but it helps in
        # cases where StreamingClient is destroyed without calling close().
        # The proper fix is to always call close() explicitly.
        if self._peer_connection is not None:
            try:
                self._peer_connection = None
            except Exception:
                pass
