"""Anam AI Python SDK - Real-time AI avatar streaming.

This SDK provides a simple interface for connecting to Anam's AI avatar
streaming service, handling WebRTC connections, and processing video/audio
frames.

Example:
    Basic usage:

    ```python
    from anam import AnamClient, AnamEvent

    client = AnamClient(
        api_key="your-api-key",
        persona_id="your-persona-id",
    )

    @client.on(AnamEvent.VIDEO_FRAME)
    async def handle_video(frame):
        # Process video frame
        img = frame.to_ndarray()  # numpy array (H, W, 3) RGB format

    @client.on(AnamEvent.AUDIO_FRAME)
    async def handle_audio(frame):
        # Process audio frame
        # frame is a PyAV AudioFrame - access all PyAV properties directly
        samples = frame.to_ndarray()

    async with client.connect() as session:
        await session.talk("Hello! How can I help you?")
        await session.wait_until_closed()
    ```

For more information, see https://docs.anam.ai
"""

from ._version import __version__
from ._agent_audio_input_stream import AgentAudioInputStream
from .client import AnamClient, Session
from .errors import (
    AnamError,
    AuthenticationError,
    ConfigurationError,
    ConnectionError,
    ErrorCode,
    SessionError,
)
from .types import (
    AgentAudioInputConfig,
    AnamEvent,
    ClientOptions,
    ConnectionClosedCode,
    Message,
    MessageRole,
    PersonaConfig,
    VideoFrame,
)

__all__ = [
    # Main client
    "AnamClient",
    "Session",
    # Types
    "AgentAudioInputConfig",
    "AgentAudioInputStream",
    "AnamEvent",
    "ClientOptions",
    "ConnectionClosedCode",
    "Message",
    "MessageRole",
    "PersonaConfig",
    "VideoFrame",
    # Errors
    "AnamError",
    "AuthenticationError",
    "ConfigurationError",
    "ConnectionError",
    "ErrorCode",
    "SessionError",
    # Version
    "__version__",
]
