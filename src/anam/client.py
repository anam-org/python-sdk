"""Main Anam client for streaming AI avatar interactions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable, TypeVar

from av.audio.frame import AudioFrame
from av.video.frame import VideoFrame

from ._agent_audio_input_stream import AgentAudioInputStream
from ._api import CoreApiClient
from ._streaming import StreamingClient
from .errors import ConfigurationError, SessionError
from .types import (
    AgentAudioInputConfig,
    AnamEvent,
    ClientOptions,
    Message,
    MessageRole,
    MessageStreamEvent,
    PersonaConfig,
    SessionInfo,
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
        api_key: str,
        persona_id: str | None = None,
        persona_config: PersonaConfig | None = None,
        options: ClientOptions | None = None,
    ):
        """Initialize the Anam client.

        You must provide either `persona_id` for a simple setup, or `persona_config`
        for full configuration control. `persona_config` takes precedence over `persona_id`.

        Args:
            api_key: Your Anam API key.
            persona_id: ID of the persona to use (simple setup).
            persona_config: Full persona configuration (advanced setup).
            options: Additional client options.

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
        """
        # Validate configuration
        if not api_key:
            raise ConfigurationError("api_key is required")

        if not persona_id and not persona_config:
            raise ConfigurationError("Either persona_id or persona config must be provided")

        if persona_id and persona_config:
            raise ConfigurationError("Provide either persona_id or persona config, not both")

        self._api_key = api_key
        self._options = options or ClientOptions()

        # Create persona config
        if persona_config:
            self._persona_config = persona_config
        else:
            self._persona_config = PersonaConfig(persona_id=persona_id)  # type: ignore

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

    def connect(self) -> "_SessionContextManager":
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
        return _SessionContextManager(self)

    async def connect_async(self) -> "Session":
        """Connect to Anam and start streaming (without context manager).

        Returns:
            A Session object for interacting with the avatar.

        Note:
            You must call session.close() when done.
            Prefer using `async with client.connect()` instead.
        """
        if self._is_streaming:
            raise SessionError("Already connected. Call close() first.")

        logger.info("Connecting to Anam...")

        # Create API client and start session
        self._api_client = CoreApiClient(
            api_key=self._api_key,
            options=self._options,
        )

        self._session_info = await self._api_client.start_session(
            persona_config=self._persona_config,
        )

        # Create streaming client with callbacks
        self._streaming_client = StreamingClient(
            session_info=self._session_info,
            on_message=self._handle_data_message,
            on_connection_established=self._handle_connection_established,
            on_connection_closed=self._handle_connection_closed,
            disable_input_audio=self._options.disable_input_audio,
            custom_ice_servers=self._options.ice_servers,
        )

        # Connect
        await self._streaming_client.connect()
        self._is_streaming = True

        return Session(self)

    async def _handle_data_message(self, data: dict[str, Any]) -> None:
        """Handle data channel message."""
        message_type = data.get("messageType", "")

        if message_type == "speechText":
            # Convert to MessageStreamEvent for incremental updates
            msg_data = data.get("data", {})
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

    async def _handle_connection_closed(self, code: str, reason: str | None) -> None:
        """Handle connection closed."""
        logger.info("Connection closed: %s %s", code, reason)
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
        if self._streaming_client:
            await self._streaming_client.close()
            self._streaming_client = None

        self._session_info = None
        self._is_streaming = False
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

    def __init__(self, client: AnamClient):
        self._client = client
        self._session: Session | None = None

    async def __aenter__(self) -> "Session":
        """Enter the context and connect."""
        self._session = await self._client.connect_async()
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
        The avatar will speak exactly what you provide.

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
        if not self._client._streaming_client:
            raise SessionError("Not connected")

        # Validate that LLM is available for processing messages
        persona_config = self._get_persona_config()

        # Check a persona and LLM are consuming the text messages
        if persona_config.persona_id is None and (
            persona_config.llm_id == "CUSTOMER_CLIENT_V1" or persona_config.llm_id is None
        ):
            logger.warning(
                "Persona ID and LLM ID are not set, messages will not be processed by the backend."
            )

        # Wait for data channel to be ready
        streaming = self._client._streaming_client
        if not getattr(streaming, "_data_channel_open", False):
            logger.debug("Waiting for data channel to open...")
            if not await streaming.wait_for_data_channel(timeout=10.0):
                raise SessionError("Data channel did not open in time")

        streaming.send_user_message(content)

    async def interrupt(self) -> None:
        """Interrupt the avatar if it's speaking.

        Raises:
            SessionError: If not connected.
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")

        # Wait for data channel to be ready (same as send_message)
        streaming = self._client._streaming_client
        if not getattr(streaming, "_data_channel_open", False):
            logger.debug("Waiting for data channel to open...")
            if not await streaming.wait_for_data_channel(timeout=10.0):
                raise SessionError("Data channel did not open in time")

        streaming.send_interrupt()

    async def send_talk_stream(
        self,
        content: str,
        start_of_speech: bool = True,
        end_of_speech: bool = True,
        correlation_id: str | None = None,
    ) -> None:
        """Stream text directly to TTS via WebSocket signalling.

        This sends text directly to TTS for streaming scenarios,
        bypassing the LLM. For simple talk commands, use talk() instead.

        Args:
            content: The text for the avatar to speak.
            start_of_speech: Whether this is the start of a speech sequence.
            end_of_speech: Whether this is the end of a speech sequence.
            correlation_id: Optional ID to correlate with interruptions.

        Raises:
            SessionError: If not connected.
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")

        signalling_client = self._client._streaming_client._signalling_client
        if not signalling_client:
            raise SessionError("Signalling client not initialized")

        # The send_talk_stream_input method will buffer the message if WebSocket isn't ready
        await signalling_client.send_talk_stream_input(
            content=content,
            start_of_speech=start_of_speech,
            end_of_speech=end_of_speech,
            correlation_id=correlation_id,
        )

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
        await self._client.close()
        self._closed = True
        self._close_event.set()

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
