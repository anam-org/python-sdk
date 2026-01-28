# Anam AI Python SDK

Official Python SDK for [Anam AI](https://anam.ai) - Real-time AI avatar streaming.

[![PyPI version](https://badge.fury.io/py/anam.svg)](https://badge.fury.io/py/anam)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Installation

```bash
# Using uv (recommended)
uv add anam

# With optional display utilities (for testing)
uv add anam --extra display

# Using pip
pip install anam

# With optional display utilities (for testing)
pip install anam[display]
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
                samples = frame.to_ndarray()  # int16 samples (1D array, interleaved for stereo)
                # Determine mono/stereo from frame layout
                channel_type = "mono" if frame.layout.nb_channels == 1 else "stereo"
                print(f"Audio: {samples.size} samples ({channel_type}) @ {frame.sample_rate}Hz")
        
        # Run both streams concurrently until session closes
        await asyncio.gather(
            consume_video(),
            consume_audio(),
        )

asyncio.run(main())
```

## Features

- 🎥 **Real-time Audio/Video streaming** - Receive synchronized audio/video frames from the avatar (as PyAV AudioFrame/VideoFrame objects)
- 💬 **Two-way communication** - Send text messages (like transcribed user speech) and receive generated responses
- 📝 **Real-time transcriptions** - Receive incremental message stream events for user and persona text as it's generated
- 📚 **Message history tracking** - Automatic conversation history with incremental updates
- 🎤 **Audio-passthrough** - Send TTS generated audio input and receive rendered synchronized audio/video avatar
- 🗣️ **Direct text-to-speech** - Send text directly to TTS for immediate speech output (bypasses LLM processing)
- 🎯 **Async iterator API** - Clean, Pythonic async/await patterns for continuous stream of audio/video frames
- 🎯 **Event-driven API** - Simple decorator-based event handlers for discrete events
- 📝 **Fully typed** - Complete type hints for IDE support
- 🔒 **Server-side ready** - Designed for server-side Python applications (e.g. for use in a web application)

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

Frames are **PyAV objects** (VideoFrame/AudioFrame) containing synchronized **decoded audio (PCM) and video (RGB) samples** from the avatar, delivered over WebRTC and extracted by aiortc. All PyAV frame attributes are accessible (samples, format, layout, etc.). Access the frames via **async iterators** and **run both iterators concurrently**, e.g. using `asyncio.gather()`:

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
from anam import AnamEvent, MessageRole, MessageStreamEvent

@client.on(AnamEvent.CONNECTION_ESTABLISHED)
async def on_connected():
    """Called when the connection is established."""
    print("✅ Connected!")

@client.on(AnamEvent.CONNECTION_CLOSED)
async def on_closed(code: str, reason: str | None):
    """Called when the connection is closed."""
    print(f"Connection closed: {code} - {reason or 'No reason'}")

@client.on(AnamEvent.MESSAGE_STREAM_EVENT_RECEIVED)
async def on_message_stream_event(event: MessageStreamEvent):
    """Called for each incremental chunk of transcribed text or persona response.
    
    This event fires for both user transcriptions and persona responses as they stream in.
    This can be used for real-time captions or transcriptions.
    """
    if event.role == MessageRole.USER:
        # User transcription (from their speech)
        if event.content_index == 0:
            print(f"👤 User: ", end="", flush=True)
        print(event.content, end="", flush=True)
        if event.end_of_speech:
            print()  # New line when transcription completes
    else:
        # Persona response
        if event.content_index == 0:
            print(f"🤖 Persona: ", end="", flush=True)
        print(event.content, end="", flush=True)
        if event.end_of_speech:
            status = "✓" if not event.interrupted else "✗ INTERRUPTED"
            print(f" {status}")

@client.on(AnamEvent.MESSAGE_RECEIVED)
async def on_message(message: Message):
    """Called when a complete message is received (after end_of_speech).
    
    This is fired after MESSAGE_STREAM_EVENT_RECEIVED with end_of_speech=True.
    Useful for backward compatibility or when you only need complete messages.
    """
    print(f"{message.role}: {message.content}")

@client.on(AnamEvent.MESSAGE_HISTORY_UPDATED)
async def on_message_history_updated(messages: list[Message]):
    """Called when the message history is updated (after a message completes).
    
    The messages list contains the complete conversation history.
    """
    print(f"📝 Conversation history: {len(messages)} messages")
    for msg in messages:
        print(f"  {msg.role}: {msg.content[:50]}...")
```

### Session

The `Session` object is returned by `client.connect()` and provides methods for interacting with the avatar:

```python
async with client.connect() as session:
    # Send a text message (simulates user speech)
    await session.send_message("Hello, how are you?")
    
    # Send text directly to TTS (bypasses LLM)
    await session.talk("This will be spoken immediately")
    
    # Stream text to TTS incrementally (for streaming scenarios)
    await session.send_talk_stream(
        content="Hello",
        start_of_speech=True,
        end_of_speech=False,
    )
    await session.send_talk_stream(
        content=" world!",
        start_of_speech=False,
        end_of_speech=True,
    )
    
    # Interrupt the avatar if speaking
    await session.interrupt()
    
    # Get message history
    history = client.get_message_history()
    for msg in history:
        print(f"{msg.role}: {msg.content}")
    
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
- [API Reference](https://docs.anam.ai/api-reference)
