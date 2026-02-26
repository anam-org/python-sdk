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

    async with client.connect() as session:
        # Consume video frames using async iterator
        async def consume_video():
            async for frame in session.video_frames():
                # yields a PyAV VideoFrame
                img = frame.to_ndarray(format="rgb24")  # numpy array (H, W, 3) RGB format

        # Consume audio frames using async iterator
        async def consume_audio():
            async for frame in session.audio_frames():
                # yields a PyAV AudioFrame
                samples = frame.to_ndarray()

        # Run both consumers concurrently
        import asyncio
        await asyncio.gather(
            consume_video(),
            consume_audio(),
        )
    ```

For more information, see https://docs.anam.ai
"""

from av.audio.frame import AudioFrame
from av.video.frame import VideoFrame

from ._agent_audio_input_stream import AgentAudioInputStream
from ._talk_message_stream import TalkMessageStream, TalkMessageStreamState
from ._version import __version__
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
    MessageStreamEvent,
    PersonaConfig,
    SessionOptions,
    SessionReplayOptions,
)

__all__ = [
    # Main client
    "AnamClient",
    "Session",
    "TalkMessageStream",
    "TalkMessageStreamState",
    # Types
    "AgentAudioInputConfig",
    "AgentAudioInputStream",
    "AnamEvent",
    "AudioFrame",
    "ClientOptions",
    "ConnectionClosedCode",
    "Message",
    "MessageRole",
    "MessageStreamEvent",
    "PersonaConfig",
    "SessionOptions",
    "SessionReplayOptions",
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
