"""Main Anam client for streaming AI avatar interactions."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

from ._api import CoreApiClient
from ._agent_audio_input_stream import AgentAudioInputStream
from ._streaming import StreamingClient
from .errors import ConfigurationError, SessionError
from .types import (
    AgentAudioInputConfig,
    AnamEvent,
    AudioFrame,
    ClientOptions,
    Message,
    MessageRole,
    PersonaConfig,
    SessionInfo,
    VideoFrame,
)

logger = logging.getLogger(__name__)

# Type variable for event callbacks
T = TypeVar("T")
EventCallback = Callable[..., Awaitable[None]] | Callable[..., None]


class AnamClient:
    """Client for streaming interactions with Anam AI avatars.

    The AnamClient provides a simple interface for connecting to Anam's
    AI avatar streaming service. It handles session management, WebRTC
    connections, and provides callbacks for video/audio frames and messages.

    Example:
        ```python
        from anam import AnamClient, AnamEvent

        client = AnamClient(
            api_key="your-api-key",
            persona_id="your-persona-id",
        )

        @client.on(AnamEvent.VIDEO_FRAME)
        async def handle_video(frame):
            # Process video frame
            pass

        async with client.connect() as session:
            await session.talk("Hello!")
            await session.wait_until_closed()
        ```
    """

    def __init__(
        self,
        api_key: str,
        persona_id: str | None = None,
        persona: PersonaConfig | None = None,
        options: ClientOptions | None = None,
    ):
        """Initialize the Anam client.

        You must provide either `persona_id` for a simple setup, or `persona`
        for full configuration control.

        Args:
            api_key: Your Anam API key.
            persona_id: ID of the persona to use (simple setup).
            persona: Full persona configuration (advanced setup).
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

        if not persona_id and not persona:
            raise ConfigurationError("Either persona_id or persona must be provided")

        if persona_id and persona:
            raise ConfigurationError("Provide either persona_id or persona, not both")

        self._api_key = api_key
        self._options = options or ClientOptions()

        # Create persona config
        if persona:
            self._persona_config = persona
        else:
            self._persona_config = PersonaConfig(persona_id=persona_id)  # type: ignore

        if self._persona_config.avatar_id and not self._persona_config.enable_audio_passthrough:
            raise ConfigurationError("enable_audio_passthrough must be True when avatar_id is provided")

        # Event callbacks
        self._event_callbacks: dict[AnamEvent, list[EventCallback]] = {
            event: [] for event in AnamEvent
        }

        # Internal state
        self._api_client: CoreApiClient | None = None
        self._session_info: SessionInfo | None = None
        self._streaming_client: StreamingClient | None = None
        self._is_streaming = False

    def on(self, event: AnamEvent) -> Callable[[T], T]:
        """Decorator to register an event handler.

        Args:
            event: The event type to listen for.

        Returns:
            Decorator function.

        Example:
            ```python
            @client.on(AnamEvent.VIDEO_FRAME)
            async def handle_video(frame: VideoFrame):
                img = frame.to_ndarray()
                # Process the frame...
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
            on_video_frame=self._handle_video_frame,
            on_audio_frame=self._handle_audio_frame,
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

    async def _handle_video_frame(self, frame: VideoFrame) -> None:
        """Handle incoming video frame."""
        await self._emit(AnamEvent.VIDEO_FRAME, frame)

    async def _handle_audio_frame(self, frame: AudioFrame) -> None:
        """Handle incoming audio frame."""
        await self._emit(AnamEvent.AUDIO_FRAME, frame)

    async def _handle_data_message(self, data: dict[str, Any]) -> None:
        """Handle data channel message."""
        message_type = data.get("messageType", "")

        if message_type == "speech_text":
            # Convert to Message object
            msg_data = data.get("data", {})
            message = Message(
                role=MessageRole(msg_data.get("role", "assistant")),
                content=msg_data.get("content", ""),
                timestamp=msg_data.get("timestamp", ""),
            )
            await self._emit(AnamEvent.MESSAGE_RECEIVED, message)

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
            raise SessionError(
                "Failed to create agent audio input stream: session is not started"
            )
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

    async def _on_closed(self, *args: Any) -> None:
        """Handle connection closed."""
        self._closed = True
        self._close_event.set()

    async def talk(self, content: str) -> None:
        """Make the avatar speak the given text.

        Args:
            content: The text for the avatar to speak.

        Raises:
            SessionError: If not connected.
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")

        # Use the engine API to send talk command
        # For now, we'll use the data channel
        logger.debug("Talk: %s", content[:50] + "..." if len(content) > 50 else content)

        # TODO: Implement talk via engine API
        # For now, this could be implemented via the data channel
        # or a REST call to the engine

    async def send_message(self, content: str) -> None:
        """Send a text message as the user.

        This simulates user speech input via text.

        Args:
            content: The message text.

        Raises:
            SessionError: If not connected.
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")

        # Wait for data channel to be ready
        streaming = self._client._streaming_client
        if not getattr(streaming, "_data_channel_open", False):
            logger.debug("Waiting for data channel to open...")
            if not await streaming.wait_for_data_channel(timeout=10.0):
                raise SessionError("Data channel did not open in time")

        streaming.send_user_message(content)

    def interrupt(self) -> None:
        """Interrupt the avatar if it's speaking.

        Raises:
            SessionError: If not connected.
        """
        if not self._client._streaming_client:
            raise SessionError("Not connected")

        self._client._streaming_client.send_interrupt()

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
