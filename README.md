# Anam AI Python SDK

Official Python SDK for [Anam AI](https://anam.ai) - Real-time AI avatar streaming.

[![PyPI version](https://badge.fury.io/py/anam-ai.svg)](https://badge.fury.io/py/anam-ai)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Installation

```bash
# Using pip
pip install anam-ai

# Using uv (recommended)
uv add anam-ai

# With optional display utilities (for testing)
pip install anam-ai[display]
```

## Quick Start

```python
import asyncio
import cv2
from anam import AnamClient, AnamEvent, VideoFrame, AudioFrame

async def main():
    # Create client with your API key and persona
    client = AnamClient(
        api_key="your-api-key",
        persona_id="your-persona-id",
    )

    # Handle video frames
    @client.on(AnamEvent.VIDEO_FRAME)
    async def on_video(frame: VideoFrame):
        # frame.to_ndarray() returns numpy array (H, W, 3) in RGB format
        rgb_img = frame.to_ndarray()
        # Convert RGB to BGR for OpenCV display
        img = cv2.cvtColor(rgb_img, cv2.COLOR_RGB2BGR)
        print(f"Video frame: {frame.width}x{frame.height}")

    # Handle audio frames
    @client.on(AnamEvent.AUDIO_FRAME)
    async def on_audio(frame: AudioFrame):
        # frame.to_ndarray() returns numpy array of int16 samples
        samples = frame.to_ndarray()
        print(f"Audio frame: {len(samples)} samples at {frame.sample_rate}Hz")

    # Connect and stream
    async with client.connect() as session:
        print(f"Connected! Session: {session.session_id}")
        
        # Keep streaming for 60 seconds
        await asyncio.sleep(60)

asyncio.run(main())
```

## Features

- 🎥 **Real-time video streaming** - Receive avatar video frames as numpy arrays
- 🔊 **Real-time audio streaming** - Receive avatar audio as numpy arrays
- 💬 **Two-way communication** - Send text messages and receive responses
- 🎯 **Event-driven API** - Simple decorator-based event handlers
- 📝 **Fully typed** - Complete type hints for IDE support
- 🔒 **Server-side ready** - Designed for server-side Python applications

## API Reference

### AnamClient

The main client class for connecting to Anam AI.

```python
from anam import AnamClient, PersonaConfig, ClientOptions

# Simple initialization
client = AnamClient(
    api_key="your-api-key",
    persona_id="your-persona-id",
)

# Advanced initialization with full persona config
client = AnamClient(
    api_key="your-api-key",
    persona=PersonaConfig(
        persona_id="your-persona-id",
        name="My Assistant",
        system_prompt="You are a helpful assistant...",
        voice_id="emma",
        language_code="en",
    ),
    options=ClientOptions(
        disable_input_audio=True,  # Don't capture microphone
    ),
)
```

### Events

Register event handlers using the `@client.on()` decorator:

```python
from anam import AnamEvent

@client.on(AnamEvent.VIDEO_FRAME)
async def on_video(frame: VideoFrame):
    """Called for each video frame received."""
    pass

@client.on(AnamEvent.AUDIO_FRAME)
async def on_audio(frame: AudioFrame):
    """Called for each audio frame received."""
    pass

@client.on(AnamEvent.MESSAGE_RECEIVED)
async def on_message(message: Message):
    """Called when a chat message is received."""
    print(f"{message.role}: {message.content}")

@client.on(AnamEvent.CONNECTION_ESTABLISHED)
async def on_connected():
    """Called when the connection is established."""
    pass

@client.on(AnamEvent.CONNECTION_CLOSED)
async def on_closed(code: str, reason: str | None):
    """Called when the connection is closed."""
    pass
```

### Session

The `Session` object is returned by `client.connect()` and provides methods for interacting with the avatar:

```python
async with client.connect() as session:
    # Send a text message (simulates user speech)
    session.send_message("Hello, how are you?")
    
    # Interrupt the avatar if speaking
    session.interrupt()
    
    # Wait until the session ends
    await session.wait_until_closed()
```

### Frame Types

#### VideoFrame

```python
@dataclass
class VideoFrame:
    data: bytes           # Raw frame data
    width: int            # Frame width in pixels
    height: int           # Frame height in pixels
    timestamp: float      # Frame timestamp
    format: str           # Pixel format (default: "rgb24")

    def to_ndarray(self) -> np.ndarray:
        """Convert to numpy array (H, W, 3) in RGB format."""
```

#### AudioFrame

```python
@dataclass
class AudioFrame:
    data: bytes           # Raw audio data
    sample_rate: int      # Sample rate in Hz (typically 48000)
    channels: int         # Number of channels (typically 1)
    timestamp: float      # Frame timestamp
    format: str           # Sample format (default: "s16")

    def to_ndarray(self) -> np.ndarray:
        """Convert to numpy array of int16 samples."""

    def to_float32(self) -> np.ndarray:
        """Convert to float32 array normalized to [-1.0, 1.0]."""
```

## Examples

### Save Video and Audio

```python
import cv2
import wave
from anam import AnamClient, AnamEvent

client = AnamClient(api_key="...", persona_id="...")

video_writer = cv2.VideoWriter("output.mp4", ...)
audio_writer = wave.open("output.wav", "wb")

@client.on(AnamEvent.VIDEO_FRAME)
async def save_video(frame):
    # Convert RGB to BGR for OpenCV VideoWriter
    rgb_frame = frame.to_ndarray()
    bgr_frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
    video_writer.write(bgr_frame)

@client.on(AnamEvent.AUDIO_FRAME)
async def save_audio(frame):
    audio_writer.writeframes(frame.data)

async with client.connect() as session:
    await asyncio.sleep(30)  # Record for 30 seconds
```

### Display Video with OpenCV

```python
import cv2
from anam import AnamClient, AnamEvent

client = AnamClient(api_key="...", persona_id="...")
latest_frame = None

@client.on(AnamEvent.VIDEO_FRAME)
async def update_frame(frame):
    global latest_frame
    # Convert RGB to BGR for OpenCV display
    rgb_frame = frame.to_ndarray()
    latest_frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)

# Run display in main thread
async with client.connect() as session:
    while True:
        if latest_frame is not None:
            cv2.imshow("Avatar", latest_frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
```

## Configuration

### Environment Variables

```bash
export ANAM_API_KEY="your-api-key"
export ANAM_PERSONA_ID="your-persona-id"
```

### Client Options

```python
from anam import ClientOptions

options = ClientOptions(
    api_base_url="https://api.anam.ai",  # API base URL
    api_version="v1",                     # API version
    disable_input_audio=False,            # Disable microphone input
    ice_servers=None,                     # Custom ICE servers
)
```

### Persona Configuration

```python
from anam import PersonaConfig

persona = PersonaConfig(
    persona_id="your-persona-id",    # Required
    name="Assistant",                 # Display name
    avatar_id="anna_v2",             # Avatar to use
    voice_id="emma",                 # Voice to use
    system_prompt="You are...",      # Custom system prompt
    language_code="en",              # Language code
    llm_id="gpt-4",                  # LLM model
    max_session_length_seconds=300,  # Max session duration
)
```

## Error Handling

```python
from anam import AnamError, AuthenticationError, SessionError

try:
    async with client.connect() as session:
        await session.wait_until_closed()
except AuthenticationError as e:
    print(f"Invalid API key: {e}")
except SessionError as e:
    print(f"Session error: {e}")
except AnamError as e:
    print(f"Anam error [{e.code}]: {e.message}")
```

## Requirements

- Python 3.10+
- Dependencies are installed automatically:
  - `aiortc` - WebRTC implementation
  - `aiohttp` - HTTP client
  - `websockets` - WebSocket client
  - `numpy` - Array handling

Optional for display utilities:
- `opencv-python` - Video display
- `sounddevice` - Audio playback

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- [Anam AI Website](https://anam.ai)
- [Documentation](https://docs.anam.ai)
- [API Reference](https://docs.anam.ai/api)
