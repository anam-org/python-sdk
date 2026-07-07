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

async def main():
    # Create client with your API key and persona_id (for pre-defined personas)
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
- 🤖 **Audio-passthrough** - Send TTS generated audio input and receive rendered synchronized audio/video avatar
- 🗣️ **Direct text-to-speech** - Send text directly to TTS for immediate speech output (bypasses LLM processing)
- 🎤 **Real-time user audio input** - Send raw audio samples (e.g. from microphone) to Anam for processing (turnkey solution: STT → LLM → TTS → Avatar)
- 📤 **Direct egress** - (experimental) Publish the avatar's synchronised audio + video directly to a 3rd party video network provider. (currently supported providers: Daily)
- 📡 **Async iterator API** - Clean, Pythonic async/await patterns for continuous stream of audio/video frames
- 🎯 **Event-driven API** - Simple decorator-based event handlers for discrete events
- 📝 **Fully typed** - Complete type hints for IDE support
- 🔒 **Server-side ready** - Designed for server-side Python applications (e.g. for backend pipelines)

## Video Quality Notes (Server-to-Server)

The Python SDK is primarily intended for server-side use with a high-capacity network connection. In this setup, adaptive bitrate (ABR) is often not required. By default, `SessionOptions` uses `video_quality="high"` which disables ABR and pins the video quality to the highest available rendition.


Omitting `session_options` or setting `video_quality="high"` achieves this: 
```python
from anam import SessionOptions
session_options = SessionOptions(video_quality="high")
async with client.connect(session_options=session_options) as session:
```

This sends `sessionOptions.videoQuality="high"` to the API and pins the video bitrate for the session to the highest available bitrate.

If you want to use ABR, set `video_quality="auto"` instead:

```python
from anam import SessionOptions
session_options = SessionOptions(video_quality="auto")
async with client.connect(session_options=session_options) as session:
```

Currently, only `"high"` or `"auto"` are supported `video_quality` values.

## Direct Egress (Daily)

> [!WARNING]
> Direct Egress is experimental and only supported for Cara-4 avatars.
> The transport and signalling path will change in upcoming alpha releases,
> including our backend support. Expect breaking changes between alphas.

Instead of consuming avatar frames over the SDK's WebRTC connection, Anam can publish the avatar's synchronised audio + video directly to a 3rd party real-time media network layer (e.g. WebRTC). The SDK's connection stays open for signalling; media goes straight from Anam to your channel/room/SFU/etc. Supported 3rd party networks: Daily.

```python
from anam import (
    AnamClient,
    EgressDailyOptions,
    EgressOptions,
    PersonaConfig,
    SessionOptions,
)

client = AnamClient(
    api_key="your-api-key",
    persona_config=PersonaConfig(
        avatar_id="your-avatar-id",
        enable_audio_passthrough=True,
    ),
)

session_options = SessionOptions(
    egress=EgressOptions(
        mode="daily",
        daily=EgressDailyOptions(
            room_url="https://your-domain.daily.co/your-room",
            token="meeting-token-minted-by-you",  # optional for public rooms
            user_name="anam-avatar",              # optional
        ),
    ),
)

async with client.connect(session_options=session_options) as session:
    # The avatar is now publishing into your Daily room.
    # Drive it via the normal SDK surface — e.g. send TTS audio:
    await session.wait_until_closed()
```

**Daily tokens.** Mint meeting tokens through your own Daily app (Daily REST API or a server you control); the SDK never does this for you. A Daily meeting token is bound to the room it was minted for, so the `token` you pass must be minted for the same `room_url` — passing a token minted for a different room will fail at join time with a 403. For public rooms (no token required) you can omit `token` entirely.

## API Reference

### AnamClient

The main client class for connecting to Anam AI.

```python
from anam import AnamClient, ClientOptions, DirectorNotes, PersonaConfig

# Simple initialization for pre-defined personas - all other parameters are ignored except enable_audio_passthrough
client = AnamClient(
    api_key="your-api-key",
    persona_id="your-persona-id",
)

# Advanced initialization with full (ephemeral) persona config - ideal for programmatic configuration.
# Use avatar_id instead of persona_id.
client = AnamClient(
    api_key="your-api-key",
    persona_config=PersonaConfig(
        avatar_id="your-avatar-id",
        voice_id="your-voice-id",
        llm_id="your-llm-id",
        name="My Assistant",
        system_prompt="You are a helpful assistant...",
        avatar_model="cara-4",
        language_code="en",
        director_notes=DirectorNotes(
            preset_style="warm",
            expressivity=0.7,
        ),
        enable_audio_passthrough=False,
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

### User Audio Input

User audio input is real-time audio such as microphone audio.
User audio is 16-bit PCM samples, mono or stereo, with any sample rate. In order to process the audio correctly, the sample rate needs to be provided.
The audio is forwarded in real-time as a WebRTC audio track. In order to reduce latency, any audio provided before the WebRTC audio track is created will be dropped.

### TTS audio (Audio Passthrough)

TTS audio is generated by a TTS engine, and should be provided in chunks through the `send_audio_chunk` method. The audio can be a byte array or base64 encoded strings (the SDK will convert to base64). The audio is sent to the backend at maximum upload speed. Sample rate and channels need to be provided through the `AgentAudioInputConfig` object. When TTS audio finishes (e.g. at the end of a turn), call `end_sequence()` to signal completion. Without this, the backend keeps waiting for more chunks and the avatar will freeze. 

For best performance, we suggest using 24kHz mono audio. The provided audio is returned in-sync with the avatar without any resampling. Sample rates lower than 24kHz will result in poor avatar performance. Sample rates higher than 24kHz might impact latency without any noticeable improvement in audio quality.

### Events

Register callbacks for connection and message events using the `@client.on()` decorator:

```python
from anam import AnamEvent, Message, MessageRole, MessageStreamEvent

@client.on(AnamEvent.CONNECTION_ESTABLISHED)
async def on_connected():
    """Called when the connection is established."""
    print("✅ Connected!")

@client.on(AnamEvent.CONNECTION_CLOSED)
async def on_closed(code: str, reason: str | None):
    """Called when the connection is closed."""
    print(f"Connection closed: {code} - {reason or 'No reason'}")

@client.on(AnamEvent.USER_SPEECH_STARTED)
async def on_user_speech_started(correlation_id: str | None):
    """Called when VAD detects user speech before transcription is available."""
    print(f"🎙️ User started speaking ({correlation_id})")

@client.on(AnamEvent.USER_SPEECH_ENDED)
async def on_user_speech_ended(correlation_id: str | None):
    """Called when VAD detects the user has stopped speaking."""
    print(f"🛑 User stopped speaking ({correlation_id})")

@client.on(AnamEvent.MESSAGE_STREAM_EVENT_RECEIVED)
async def on_message_stream_event(event: MessageStreamEvent):
    """Called for each incremental chunk of transcribed text or persona response.
    
    This event fires for both user transcriptions and persona responses as they stream in.
    This can be used for real-time captions or transcriptions.
    """
    correlation_id = event.correlation_id

    if event.role == MessageRole.USER:
        # User transcription (from their speech)
        if event.content_index == 0:
            print(f"👤 User ({correlation_id}): ", end="", flush=True)
        print(event.content, end="", flush=True)
        if event.end_of_speech:
            print()  # New line when transcription completes
    else:
        # Persona response
        if event.content_index == 0:
            print(f"🤖 Persona ({correlation_id}): ", end="", flush=True)
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
    talk_stream = session.create_talk_stream()
    await talk_stream.send("Hello", end_of_speech=False)
    await talk_stream.send(" world!", end_of_speech=True)
    
    # Interrupt the avatar if speaking
    await session.interrupt()

    # Runtime director-note cue (turn-scoped)
    await session.send_director_note_cue("curious", at_seconds=0.5)
    
    # Get message history
    history = client.get_message_history()
    for msg in history:
        print(f"{msg.role}: {msg.content}")
    
    # Wait until the session ends
    await session.wait_until_closed()
```

### Director Notes

Director notes control how the avatar performs a conversation (how something is said, not just what is said). Set session defaults on `PersonaConfig(director_notes=...)`, then send runtime `director_note_cue` messages with `session.send_director_note_cue(...)`.
For supported styles/cues and timing semantics (`at_seconds` vs `in_seconds`), see the official docs: [Director Notes](https://anam.ai/docs/personas/director-notes).
The parameters `at_seconds` and `in_seconds` only support positive finite numbers, a finite guardrail is in place to prevent invalid JSON serialization at session start. All other invalid values will log a warning and be ignored.

#### Initial Director Notes

Set director notes directly on `PersonaConfig` to apply them from session start:

```python
from anam import DirectorNotes, PersonaConfig

persona = PersonaConfig(
    avatar_id="your-avatar-id",
    director_notes=DirectorNotes(
        preset_style="warm",
        expressivity=0.7,
    ),
)
```
The initial director notes are the avatar's default presence for the session.

#### Runtime Director Note Cues

Use `session.send_director_note_cue(...)` to adjust delivery style during the active turn.

```python
import asyncio

async with client.connect() as session:
    cues = ["warm", "playful", "concerned", "laughter"]

    for cue in cues:
        await session.send_director_note_cue(cue, at_seconds=3.0)
        await asyncio.sleep(8)
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

### Interactive Persona Session With Message History

Use `examples/persona_interactive_video.py` for a full interactive session with
video, audio, live captions, and conversation history output.

This example supports:

- `m <message>` to send a user message into the conversation
- `c` to toggle live captions from `MESSAGE_STREAM_EVENT_RECEIVED`
- `h` to toggle conversation history printing on session close using `client.get_message_history()`

```bash
uv sync --extra display

export ANAM_API_KEY="your-api-key"
export ANAM_AVATAR_ID="your-avatar-id"
export ANAM_VOICE_ID="your-voice-id"
export ANAM_LLM_ID="your-llm-id"
uv run --extra display python examples/persona_interactive_video.py
```

Example interaction:

```text
>> c
Captions enabled
>> h
Conversation history enabled
>> m Hello there
>> m Can you summarize what I just asked?
>> q
Conversation transcript:
========================
User: Hello there
Persona: Hi! Nice to meet you.
User: Can you summarize what I just asked?
Persona: You asked me to summarize your previous message.
```

### Test User Audio Events From WAV

Use the WAV example to exercise the real user-audio/VAD path and verify
`USER_SPEECH_STARTED`, `USER_SPEECH_ENDED`, and transcript `correlation_id`
matching without requiring microphone support.

```bash
export ANAM_API_KEY="your-api-key"
export ANAM_PERSONA_ID="your-persona-id"
uv run python examples/user_audio_from_wav.py path/to/input.wav
```


## Configuration

### Environment Variables

```bash
export ANAM_API_KEY="your-api-key"
export ANAM_AVATAR_ID="your-avatar-id"
export ANAM_VOICE_ID="your-voice-id"
export ANAM_LLM_ID="your-llm-id"
```

### Client Options

```python
from anam import ClientOptions

options = ClientOptions(
    api_base_url="https://api.anam.ai",   # API base URL
    api_version="v1",                     # API version
    ice_servers=None,                     # Custom ICE servers for WebRTC delivery
)
```

### Persona types

There are two types of personas:

- Pre-defined personas: use `persona_id` only. Other parameters are ignored except `enable_audio_passthrough`.
- Ephemeral personas: use `avatar_id`, `voice_id`, `llm_id`, `avatar_model`, `system_prompt`, `language_code` and `enable_audio_passthrough`.

#### Pre-defined personas

Pre-defined personas are built in [lab.anam.ai](https://lab.anam.ai) and combine avatar, voice and LLM. They cannot be changed after creation. 
They are quick to set up for demos but offer less flexibility for production use.

```python
client = AnamClient(
    api_key="your-api-key",
    persona_id="your-persona-id",
)
```

#### Ephemeral personas

Ephemeral personas give you full control over components at startup. Configure avatar, voice, LLM, and other options at [lab.anam.ai](https://lab.anam.ai) (avatars, voices, LLMs).
They are ideal for production environments where you need to control the components at startup.

```python
from anam import DirectorNotes, PersonaConfig

# Ephemeral: specify avatar_id, voice_id, and optionally llm_id, avatar_model
persona = PersonaConfig(
    avatar_id="your-avatar-id",       # From https://lab.anam.ai/avatars (do not use persona_id)
    voice_id="your-voice-id",         # From https://lab.anam.ai/voices
    llm_id="your-llm-id",             # From https://lab.anam.ai/llms (optional)
    avatar_model="cara-4",            # Video frame model (optional)
    system_prompt="You are...",       # See https://docs.anam.ai/concepts/prompting-guide
    director_notes=DirectorNotes(     # Optional: Initial director notes 
        preset_style="warm",
        expressivity=0.7,
    ),
    enable_audio_passthrough=False,
)
```

### Avatar model

Set `avatar_model` on ephemeral `PersonaConfig`. If your avatar does not support Cara-4, use `avatar_model="cara-3"` or if you require Cara-4, generate a new avatar in [lab.anam.ai](https://lab.anam.ai). New avatars will support the "cara-4" model.

### Orchestration

Orchestration is the process of running a pipeline with different components to transform user audio into a response (STT -> LLM -> TTS -> Avatar). 
Anam allows two types of orchestration:

- **Anam's orchestration**: Anam receives user audio (or text messages) and runs the pipeline, with a default or custom LLM. 
- **Custom orchestration**: Anam's orchestration is bypassed by directly providing TTS audio. The TTS audio is passed through directly to the avatar, without being added to the context or message history. This can be achieved by setting `enable_audio_passthrough=True`. See [TTS audio (Audio Passthrough)](#tts-audio-audio-passthrough) for more details.


### LLM options

Anam's orchestration layer allows you to choose between default LLMs or running your own custom LLMs:

- **Default LLMs**: Use Anam-provided models when you do not run your own.
- **Custom LLMs**: Anam connects to your LLM server-to-server. Add and test the connection at [lab.anam.ai/llms](https://lab.anam.ai/llms).
- **`CUSTOMER_CLIENT_V1`**: Your LLM is not directly connected to Anam. Use `MESSAGE_STREAM_EVENT_RECEIVED` to forward messages and send responses via talk stream (or `enable_audio_passthrough=True` for TTS). Higher latency; useful for niche use cases but not recommended for general applications.

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
  - `pyav` - Video and audio handling

Optional for display utilities:
- `opencv-python` - Video display
- `sounddevice` - Audio playback

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- [Anam AI Website](https://anam.ai)
- [Documentation](https://docs.anam.ai)
- [API Reference](https://docs.anam.ai/api-reference)
