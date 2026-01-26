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
from anam import AnamClient
from av.video.frame import VideoFrame
from av.audio.frame import AudioFrame

async def main():
    # Create client with your API key and persona
    client = AnamClient(
        api_key="your-api-key",
        persona_id="your-persona-id",
    )

    # Connect and stream
    async with client.connect() as session:
        print(f"Connected! Session: {session.session_id}")
        
        # Consume video and audio frames concurrently
        async def consume_video():
            async for frame in session.video_frames():
                img = frame.to_ndarray(format="rgb24")  # numpy array (H, W, 3) in RGB format - use "bgr24" for OpenCV
                print(f"Video: {frame.width}x{frame.height}")
        
        async def consume_audio():
            async for frame in session.audio_frames():
                samples = frame.to_ndarray()  # int16 samples
                print(f"Audio: {len(samples)} samples @ {frame.sample_rate}Hz")
        
        # Run both streams concurrently until session closes
        await asyncio.gather(
            consume_video(),
            consume_audio(),
        )

asyncio.run(main())
```

## Features

- 🎥 **Real-time video streaming** - Receive avatar video frames as PyAV VideoFrame objects via async iterators
- 🔊 **Real-time audio streaming** - Receive avatar audio frames asPyAV AudioFrame objects via async iterators  
- 💬 **Two-way communication** - Send text messages and receive responses
- 🎯 **Async iterator API** - Clean, Pythonic async/await patterns
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
    persona_config=PersonaConfig(
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

### Video and Audio Frames

Frames are PyAV objects (VideoFrame/AudioFrame) delivered via async iterators. **Run both iterators concurrently** using `asyncio.gather()`:

```python
async with client.connect() as session:
    async def process_video():
        async for frame in session.video_frames():
            img = frame.to_ndarray(format="rgb24")  # RGB numpy array
            # Process frame...
    
    async def process_audio():
        async for frame in session.audio_frames():
            samples = frame.to_ndarray()  # int16 samples
            # Process frame...
    
    # Both streams run concurrently
    await asyncio.gather(process_video(), process_audio())
```

### Events

Register callbacks for connection and message events using the `@client.on()` decorator:

```python
from anam import AnamEvent

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
    await session.send_message("Hello, how are you?")
    
    # Interrupt the avatar if speaking
    await session.interrupt()
    
    # Wait until the session ends
    await session.wait_until_closed()
```



## Examples

### Save Video and Audio

```python
import cv2
import wave
import asyncio
from anam import AnamClient

client = AnamClient(api_key="...", persona_id="...")

video_writer = cv2.VideoWriter("output.mp4", ...)
audio_writer = wave.open("output.wav", "wb")

async def save_video(session):
    async for frame in session.video_frames():
        # Read frame as BGR for OpenCV VideoWriter
        bgr_frame = frame.to_ndarray(format="bgr24")
        video_writer.write(bgr_frame)

async def save_audio(session):
    async for frame in session.audio_frames():
        # Initialize writer on first frame
        if audio_writer.getnframes() == 0:
            audio_writer.setnchannels(frame.layout.nb_channels)
            audio_writer.setsampwidth(2)  # 16-bit
            audio_writer.setframerate(frame.sample_rate)
        # Write audio data (convert to int16 and get bytes)
        audio_writer.writeframes(frame.to_ndarray().tobytes())

async with client.connect() as session:
    # Record for 30 seconds
    await asyncio.wait_for(
        asyncio.gather(save_video(session), save_audio(session)),
        timeout=30.0,
    )
```

### Display Video with OpenCV

```python
import cv2
import asyncio
from anam import AnamClient

client = AnamClient(api_key="...", persona_id="...")
latest_frame = None

async def update_frame(session):
    global latest_frame
    async for frame in session.video_frames():
        # Read frame as BGR for OpenCV display
        latest_frame = frame.to_ndarray(format="bgr24")

async def main():
    async with client.connect() as session:
        # Start frame consumer
        frame_task = asyncio.create_task(update_frame(session))
        
        # Display loop
        while True:
            if latest_frame is not None:
                cv2.imshow("Avatar", latest_frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        frame_task.cancel()

asyncio.run(main())
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
