"""Main Anam client for streaming AI avatar interactions."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable, TypeVar

from av.audio.frame import AudioFrame
from av.video.frame import VideoFrame

from ._agent_audio_input_stream import AgentAudioInputStream
from ._api import CoreApiClient
from ._streaming import StreamingClient
from ._talk_message_stream import TalkMessageStream
from .errors import ConfigurationError, SessionError
from .types import (
    AgentAudioInputConfig,
    AnamEvent,
    ClientOptions,
    ConnectionClosedCode,
    Message,
    MessageRole,
    MessageStreamEvent,
    PersonaConfig,
    SessionInfo,
    SessionOptions,
)

logger = logging.getLogger(__name__)

# Type variable for event callbacks
T = TypeVar("T")
EventCallback = Callable[..., Awaitable[None]] | Callable[..., None]


class AnamClient:
    """Client for streaming interactions with Anam AI avatars.

    The AnamClient provides a simple interface for connecting to Anam's
    AI avatar streaming service. It handles session management, WebRTC
    connections (with callbacks), and provides async iterators for video/audio frames.

    Example:
        ```python
        from anam import AnamClient

        client = AnamClient(
            api_key="your-api-key",
            persona_id="your-persona-id",
        )

        async with client.connect() as session:
            # Consume video frames
            async for frame in session.video_frames():
                # Process video frame
                image = frame.to_ndarray(format="rgb24")

            # Consume audio frames
            async for frame in session.audio_frames():
                # Process audio frame
                samples = frame.to_ndarray()
        ```
    """

    def __init__(
        self,
        api_key: str | None = None,
        persona_id: str | None = None,
        persona_config: PersonaConfig | None = None,
        options: ClientOptions | None = None,
        *,
        session_token: str | None = None,
    ):
        """Initialize the Anam client.

        Authenticate with either an API key or a pre-minted session token.

        API-key authentication requires either `persona_id` for a simple setup
        or `persona_config` for full configuration control. A session token
        already contains the server-side persona and session snapshot, so it
        must not be combined with either persona argument.

        Args:
            api_key: Your Anam API key. Mutually exclusive with `session_token`.
            persona_id: ID of the persona to use (simple setup).
            persona_config: Full persona configuration (advanced setup).
            options: Additional client options.
            session_token: A pre-minted Anam session token. Mutually exclusive
                with `api_key` and persona configuration.

        Raises:
            ConfigurationError: If configuration is invalid.

        Example:
            Simple setup:
            ```python
            client = AnamClient(
                api_key="your-api-key",
                persona_id="your-persona-id",
            )
            ```

            Advanced setup:
            ```python
            client = AnamClient(
                api_key="your-api-key",
                persona=PersonaConfig(
                    persona_id="your-persona-id",
                    system_prompt="You are a helpful assistant...",
                    voice_id="emma",
                ),
            )
            ```

            Pre-minted session token:
            ```python
            client = AnamClient(session_token="your-session-token")
            ```
        """
        # Before direct API-key session starts were introduced, the first
        # positional argument was commonly a pre-minted session token. API keys
        # never contain dots, while Anam session JWTs do, so retain that legacy
        # form while recommending the explicit session_token keyword.
        if (
            api_key
            and "." in api_key
            and not session_token
            and not persona_id
            and not persona_config
        ):
            session_token = api_key
            api_key = None

        has_api_key = bool(api_key)
        has_session_token = bool(session_token)
        if has_api_key == has_session_token:
            raise ConfigurationError("Provide exactly one of api_key or session_token")

        if has_session_token:
            if persona_id or persona_config:
                raise ConfigurationError(
                    "session_token cannot be combined with persona_id or persona_config"
                )
        else:
            if not persona_id and not persona_config:
                raise ConfigurationError(
                    "Either persona_id or persona config must be provided with api_key"
                )
            if persona_id and persona_config:
                raise ConfigurationError("Provide either persona_id or persona config, not both")

        self._api_key = api_key
        self._session_token = session_token
        self._options = options or ClientOptions()

        # Create persona config
        self._persona_config: PersonaConfig | None
        if persona_config:
            self._persona_config = persona_config
        elif persona_id:
            self._persona_config = PersonaConfig(persona_id=persona_id)
        else:
            self._persona_config = None

        # Event callbacks
        self._event_callbacks: dict[AnamEvent, list[EventCallback]] = {
            event: [] for event in AnamEvent
        }

        # Internal state
        self._api_client: CoreApiClient | None = None
        self._session_info: SessionInfo | None = None
        self._streaming_client: StreamingClient | None = None
        self._is_streaming = False
        self._message_history: list[Message] = []

    def on(self, event: AnamEvent) -> Callable[[T], T]:
        """Decorator to register an event handler.

        Args:
            event: The event type to listen for.

        Returns:
            Decorator function.

        Example:
            ```python
            @client.on(AnamEvent.CONNECTION_ESTABLISHED)
            async def handle_connection():
                print("Connected!")
            ```
        """

        def decorator(func: T) -> T:
            self._event_callbacks[event].append(func)  # type: ignore
            return func

        return decorator

    def add_listener(self, event: AnamEvent, callback: EventCallback) -> None:
        """Add an event listener.

        Args:
            event: The event type to listen for.
            callback: The callback function.
        """
        self._event_callbacks[event].append(callback)

    def remove_listener(self, event: AnamEvent, callback: EventCallback) -> None:
        """Remove an event listener.

        Args:
            event: The event type.
            callback: The callback to remove.
        """
        if callback in self._event_callbacks[event]:
            self._event_callbacks[event].remove(callback)

    async def _emit(self, event: AnamEvent, *args: Any, **kwargs: Any) -> None:
        """Emit an event to all registered callbacks."""
        for callback in self._event_callbacks[event]:
            try:
                result = callback(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("Error in event callback for %s: %s", event.value, e)

    def connect(
        self, session_options: SessionOptions = SessionOptions()
    ) -> "_SessionContextManager":
        """Connect to Anam and start streaming.

        Returns:
            An async context manager that yields a Session.

        Raises:
            SessionError: If connection fails.
            AuthenticationError: If API key is invalid.

        Example:
            ```python
            async with client.connect() as session:
                await session.talk("Hello!")
                await asyncio.sleep(30)
            ```

            Or without context manager:
            ```python
            session = await client.connect_async()
            try:
                await session.talk("Hello!")
            finally:
                await session.close()
            ```
        """
        return _SessionContextManager(self, session_options)

    async def connect_async(self, session_options: SessionOptions = SessionOptions()) -> "Session":
        """Connect to Anam and start streaming (without context manager).

        Args:
            session_options: Session options (default: SessionOptions(enable_session_replay=True, video_quality="high")).

        Returns:
            A Session object for interacting with the avatar.

        Note:
            You must call session.close() when done.
        """
        if self.is_streaming:
            raise SessionError("Already connected. Call close() first.")

        logger.info("Connecting to Anam...")

        # Create API client and start session
        self._api_client = CoreApiClient(
            api_key=self._api_key,
            session_token=self._session_token,
            options=self._options,
        )

        self._session_info = await self._api_client.start_session(
            persona_config=self._persona_config,
            session_options=session_options,
        )

        # Create streaming client with callbacks
        self._streaming_client = StreamingClient(
            session_info=self._session_info,
            on_message=self._handle_data_message,
            on_connection_established=self._handle_connection_established,
            on_connection_closed=self._handle_connection_closed,
            on_session_ready=self._handle_session_ready,
            on_talk_stream_interrupted=self._handle_talk_stream_interrupted,
            custom_ice_servers=self._options.ice_servers,
        )

        # Connect
        await self._streaming_client.connect()
        self._is_streaming = True

        return Session(self)

    async def _handle_data_message(self, data: dict[str, Any]) -> None:
        """Handle data channel message."""
        message_type = data.get("messageType", "")
        msg_data = data.get("data", {})

        if not isinstance(msg_data, dict):
            logger.debug("Ignoring data channel message with invalid payload: %s", message_type)
            return

        correlation_id = self._extract_correlation_id(msg_data)

        if message_type == "speechText":
            # Convert to MessageStreamEvent for incremental updates
            message_id = msg_data.get("message_id", "")
            role_str = msg_data.get("role", "assistant")
            content = msg_data.get("content", "")
            content_index = msg_data.get("content_index", 0)
            end_of_speech = msg_data.get("end_of_speech", False)
            interrupted = msg_data.get("interrupted", False)
            timestamp = msg_data.get("timestamp", "")

            # Create message ID similar to JS SDK: "{role}::{message_id}"
            stream_event_id = f"{role_str}::{message_id}"

            # Determine role
            if role_str.lower() == "user":
                role = MessageRole.USER
            elif role_str.lower() == "persona":
                role = MessageRole.ASSISTANT
            else:
                role = MessageRole.ASSISTANT

            # Emit incremental stream event
            stream_event = MessageStreamEvent(
                id=stream_event_id,
                content=content,
                role=role,
                content_index=content_index,
                end_of_speech=end_of_speech,
                interrupted=interrupted,
                correlation_id=correlation_id,
            )
            await self._emit(AnamEvent.MESSAGE_STREAM_EVENT_RECEIVED, stream_event)

            # Update message history
            self._process_message_stream_event(stream_event, timestamp)

            # Emit final message when speech ends (for backward compatibility)
            if end_of_speech:
                # Find the complete message in history
                complete_message = next(
                    (msg for msg in self._message_history if msg.id == stream_event_id),
                    None,
                )
                if complete_message:
                    await self._emit(AnamEvent.MESSAGE_RECEIVED, complete_message)
                    await self._emit(
                        AnamEvent.MESSAGE_HISTORY_UPDATED, self._message_history.copy()
                    )
        elif message_type == "userSpeechStarted":
            await self._emit(AnamEvent.USER_SPEECH_STARTED, correlation_id)
        elif message_type == "userSpeechEnded":
            await self._emit(AnamEvent.USER_SPEECH_ENDED, correlation_id)

    @staticmethod
    def _extract_correlation_id(data: dict[str, Any]) -> str | None:
        """Extract a turn correlation ID from backend event payloads."""
        correlation_id = data.get("user_action_correlation_id") or data.get("correlationId")
        return correlation_id if isinstance(correlation_id, str) else None

    def _process_message_stream_event(self, event: MessageStreamEvent, timestamp: str) -> None:
        """Process a message stream event and update message history."""
        # Find existing message with same ID (for both user and persona messages)
        existing_index = next(
            (i for i, msg in enumerate(self._message_history) if msg.id == event.id),
            None,
        )

        if existing_index is not None:
            # Update existing message by appending new content
            existing = self._message_history[existing_index]
            self._message_history[existing_index] = Message(
                id=existing.id,
                role=existing.role,
                content=existing.content + event.content,
                timestamp=existing.timestamp or timestamp,
                interrupted=existing.interrupted or event.interrupted,
            )
        else:
            # Add new message (first chunk)
            new_message = Message(
                id=event.id,
                role=event.role,
                content=event.content,
                timestamp=timestamp,
                interrupted=event.interrupted,
            )
            self._message_history.append(new_message)

    async def _handle_connection_established(self) -> None:
        """Handle connection established."""
        logger.info("Connection established")
        await self._emit(AnamEvent.CONNECTION_ESTABLISHED)

    async def _handle_session_ready(self) -> None:
        """Handle session ready (signalling: ready to receive user audio or TTS)."""
        await self._emit(AnamEvent.SESSION_READY)

    async def _handle_talk_stream_interrupted(self, correlation_id: str) -> None:
        """Handle talk stream interrupted signal from server."""
        await self._emit(AnamEvent.TALK_STREAM_INTERRUPTED, correlation_id)

    async def _handle_connection_closed(self, code: str, reason: str | None) -> None:
        """Handle connection closed."""
        logger.debug("Connection closed")
        self._is_streaming = False
        await self._emit(AnamEvent.CONNECTION_CLOSED, code, reason)

    def create_agent_audio_input_stream(
        self, config: AgentAudioInputConfig
    ) -> AgentAudioInputStream:
        """Create an agent audio input stream for sending PCM audio data.

        Args:
            config: Audio format configuration.

        Returns:
            AgentAudioInputStream instance.

        Raises:
            SessionError: If session is not started.
        """
        if not self._streaming_client:
            raise SessionError("Failed to create agent audio input stream: session is not started")
        return self._streaming_client.create_agent_audio_input_stream(config)

    async def close(self) -> None:
        """Close the connection and clean up resources."""
        if self._streaming_client and self.is_streaming:
            self._is_streaming = False
            await self._handle_connection_closed(ConnectionClosedCode.NORMAL.value, None)
            await self._streaming_client.close()
            self._streaming_client = None
            self._session_info = None
            self._message_history.clear()
            logger.info("Client closed")

    @property
    def is_streaming(self) -> bool:
        """Check if currently streaming."""
        return self._is_streaming

    @property
    def session_id(self) -> str | None:
        """Get the current session ID."""
        return self._session_info.session_id if self._session_info else None

    def get_message_history(self) -> list[Message]:
        """Get the current message history.

        Returns:
            A list of messages in the conversation history.
        """
        return self._message_history.copy()

    def set_persona_config(self, persona_config: PersonaConfig) -> None:
        """Set the persona configuration.

        Args:
            persona_config: The persona configuration to set.
        """
        self._persona_config = persona_config

    def get_persona_config(self) -> PersonaConfig | None:
        """Get the current persona configuration.

        Returns:
            The current persona configuration, or None if not set.
        """
        return self._persona_config


class _SessionContextManager:
    """Async context manager for AnamClient.connect()."""

    def __init__(self, client: AnamClient, session_options: SessionOptions):
        self._client = client
        self._session: Session | None = None
        self._session_options = session_options

    async def __aenter__(self) -> "Session":
        """Enter the context and connect."""
        self._session = await self._client.connect_async(self._session_options)
        return self._session

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the context and close the session."""
        if self._session:
            await self._session.close()


class Session:
    """An active streaming session with an Anam avatar.

    Use this class to interact with the avatar: make it speak,
    send messages, or control the session.

    This class is returned by `AnamClient.connect()` and supports
    use as an async context manager.
    """

    def __init__(self, client: AnamClient):
        """Initialize the session.

        Args:
            client: The parent AnamClient.
        """
        self._client = client
        self._closed = False
        self._close_event = asyncio.Event()

        # Listen for connection close
        client.add_listener(AnamEvent.CONNECTION_CLOSED, self._on_closed)

    def _get_persona_config(self) -> PersonaConfig:
        if not self._client:
            raise SessionError("Client not found")
        if not self._client._persona_config:
            raise SessionError("Persona configuration not found")
        return self._client._persona_config

    async def _on_closed(self, code: str, reason: str | None) -> None:
        """Handle connection closed."""
        self._closed = True
        self._close_event.set()

    async def talk(self, content: str) -> None:
        """Make the avatar speak the given text directly.

        This sends text directly to TTS, bypassing the LLM.
        Unsuitable for streaming text.
        Simpler, but higher latency than send_talk_stream().

        Args:
            content: The text for the avatar to speak.

        Raises:
            SessionError: If not connected.
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")

        logger.debug("Talk: %s", content[:50] + "..." if len(content) > 50 else content)
        await self._client._streaming_client.send_talk(content)

    async def send_message(self, content: str) -> None:
        """Send a text message as the user.

        This simulates user speech input via text.

        Args:
            content: The message text.

        Raises:
            SessionError: If not connected or if LLM is not available.
        """
        # Validate that LLM is available for processing messages
        persona_config = self._get_persona_config()

        # Check a persona and LLM are consuming the text messages
        if persona_config.persona_id is None and (
            persona_config.llm_id == "CUSTOMER_CLIENT_V1" or persona_config.llm_id is None
        ):
            logger.warning(
                "Persona ID and LLM ID are not set, messages will not be processed by the backend."
            )

        streaming = await self._ensure_data_channel_open()
        streaming.send_user_message(content)

    async def interrupt(self) -> None:
        """Interrupt the avatar if it's speaking.

        Raises:
            SessionError: If not connected.
        """
        streaming = await self._ensure_data_channel_open()
        streaming.send_interrupt()

    async def send_director_note_cue(
        self,
        tag: str,
        *,
        at_seconds: float | None = None,
        in_seconds: float | None = None,
    ) -> None:
        """Send a director-note cue over the active session data channel.

        Args:
            tag: Director-note cue tag (for example ``"playful"``).
            at_seconds: Optional turn-relative cue time in seconds.
            in_seconds: Optional delay relative to receipt time in seconds.

        Raises:
            SessionError: If not connected, data channel is unavailable, or send fails.
            ValueError: If a provided timing value is not finite.
        """
        for field_name, value in (("at_seconds", at_seconds), ("in_seconds", in_seconds)):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{field_name} must be a finite number")

        payload: dict[str, Any] = {
            "message_type": "director_note_cue",
            "cue": {"tag": tag},
        }

        if at_seconds is not None:
            payload["at_seconds"] = at_seconds

        if in_seconds is not None:
            payload["in_seconds"] = in_seconds

        streaming = await self._ensure_data_channel_open()
        if not streaming.send_data_message(json.dumps(payload)):
            raise SessionError("Failed to send director note cue over data channel")

    async def _ensure_data_channel_open(self) -> StreamingClient:
        """Return the streaming client once the data channel is ready."""
        if not self._client._streaming_client:
            raise SessionError("Not connected")

        streaming = self._client._streaming_client
        if not getattr(streaming, "_data_channel_open", False):
            logger.debug("Waiting for data channel to open...")
            if not await streaming.wait_for_data_channel(timeout=10.0):
                raise SessionError("Data channel did not open in time")
        return streaming

    def create_talk_stream(self, correlation_id: str | None = None) -> TalkMessageStream:
        """Create a talk message stream for sending text chunks to TTS.

        The stream manages correlation_id internally so you don't need to track
        it across chunks. Use this for streaming LLM output. All chunks in the
        same speech share one correlation_id for interruption handling.

        Args:
            correlation_id: Optional ID. If not provided, a UUID is generated.

        Returns:
            TalkMessageStream with send() and end() methods.

        Raises:
            SessionError: If not connected.

        Example:
            ```python
            stream = session.create_talk_stream()
            for i, chunk in enumerate(llm_chunks):
                await stream.send(chunk, end_of_speech=(i == len(llm_chunks) - 1))
            ```
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")

        signalling_client = self._client._streaming_client._signalling_client
        if not signalling_client:
            raise SessionError("Signalling client not initialized")

        if correlation_id is None or correlation_id.strip() == "":
            correlation_id = str(uuid.uuid4())

        return TalkMessageStream(
            correlation_id=correlation_id,
            signalling_client=signalling_client,
            client=self._client,
        )

    async def send_talk_stream(self, content: str) -> None:
        """Send a single text message directly to TTS via WebSocket signalling.

        Convenience method for one-off messages. Sends text directly to TTS,
        bypassing the LLM. For streaming multiple chunks, use create_talk_stream()
        instead to manage the stream.

        Args:
            content: The text for the avatar to speak.

        Raises:
            SessionError: If not connected.
        """
        stream = self.create_talk_stream()
        await stream.send(content, end_of_speech=True)

    def create_agent_audio_input_stream(
        self, config: AgentAudioInputConfig
    ) -> AgentAudioInputStream:
        """Create an agent audio input stream for sending PCM audio data.

        Args:
            config: Audio format configuration.

        Returns:
            AgentAudioInputStream instance.

        Raises:
            SessionError: If not connected.
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")
        return self._client._streaming_client.create_agent_audio_input_stream(config)

    def send_user_audio(
        self,
        audio_bytes: bytes,
        sample_rate: int,
        num_channels: int,
    ) -> None:
        """Send raw user audio samples to Anam for processing.

        This method accepts 16-bit PCM samples and adds them to the audio buffer
        for transmission via WebRTC. The audio track is created lazily when first
        audio arrives. Audio is only added to the buffer after the connection is
        established, to avoid accumulating stale audio.

        Args:
            audio_bytes: Raw audio data (16-bit PCM).
            sample_rate: Sample rate of the input audio (Hz).
            num_channels: Number of channels in the input audio (1=mono, 2=stereo).

        Raises:
            SessionError: If not connected.
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")
        self._client._streaming_client.send_user_audio(
            audio_bytes=audio_bytes,
            sample_rate=sample_rate,
            num_channels=num_channels,
        )

    def video_frames(self) -> AsyncIterator[VideoFrame]:
        """Get video frames as an async iterator.

        Yields:
            VideoFrame: PyAV VideoFrame objects from the WebRTC stream.

        Raises:
            SessionError: If not connected.

        Example:
            ```python
            async for frame in session.video_frames():
                # Process video frame
                image = frame.to_ndarray(format="rgb24")
            ```
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")
        return self._client._streaming_client.video_frames()

    def audio_frames(self) -> AsyncIterator[AudioFrame]:
        """Get audio frames as an async iterator.

        Yields:
            AudioFrame: PyAV AudioFrame objects from the WebRTC stream.
            Audio frames are decoded PCM: 16-bit, 48kHz, stereo samples.

        Raises:
            SessionError: If not connected.

        Example:
            ```python
            async for frame in session.audio_frames():
                # Process audio frame
                samples = frame.to_ndarray()
            ```
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")
        return self._client._streaming_client.audio_frames()

    def mute_input(self) -> None:
        """Mute microphone input (if enabled)."""
        # TODO: Implement audio track muting
        logger.debug("Muting input audio")

    def unmute_input(self) -> None:
        """Unmute microphone input (if enabled)."""
        # TODO: Implement audio track unmuting
        logger.debug("Unmuting input audio")

    async def wait_until_closed(self) -> None:
        """Wait until the session is closed.

        This is useful for keeping the session alive until
        the server closes it or an error occurs.
        """
        await self._close_event.wait()

    async def close(self) -> None:
        """Close the session."""
        self._closed = True
        self._close_event.set()
        await self._client.close()

    @property
    def is_active(self) -> bool:
        """Check if the session is still active."""
        return not self._closed and self._client.is_streaming

    @property
    def session_id(self) -> str | None:
        """Get the session ID."""
        return self._client.session_id

    async def __aenter__(self) -> Session:
        """Enter async context."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context and close session."""
        await self.close()
