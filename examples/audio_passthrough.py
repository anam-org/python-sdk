"""Interactive video session example with CLI controls.

This example shows how use Anam as an avatar provider where
the avatar is rendered on existing TTS audio. The video is displayed
in a window using OpenCV

Requirements:
    uv sync --extra display
    # or: pip install opencv-python sounddevice

Usage:
    export ANAM_API_KEY="your-api-key"
    export ANAM_AVATAR_ID="your-avatar-id"
    uv run --extra display python examples/audio_passthrough.py
"""

import asyncio
import logging
import os
import wave
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import cv2
import numpy as np
from dotenv import load_dotenv

from anam import AnamClient, AnamEvent, AudioFrame, ClientOptions, VideoFrame
from anam._agent_audio_input_stream import AgentAudioInputStream
from anam.types import AgentAudioInputConfig, PersonaConfig

if TYPE_CHECKING:
    import sounddevice as sd

    class OutputStreamProtocol(Protocol):
        """Protocol for sounddevice OutputStream."""

        def start(self) -> None:
            """Start the stream."""
            ...

        def stop(self) -> None:
            """Stop the stream."""
            ...

        def close(self) -> None:
            """Close the stream."""
            ...

        def write(self, data: np.ndarray) -> bool:
            """Write audio data."""
            ...

# Load environment variables
_ = load_dotenv()

# Configure logging - reduced verbosity
logging.basicConfig(
    level=logging.WARNING,  # Reduced from INFO to WARNING
    format="%(levelname)s: %(message)s",  # Simplified format
)
logger = logging.getLogger(__name__)

# Suppress verbose logging from dependencies
logging.getLogger("anam").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

# Audio playback
try:
    import sounddevice as sd
    _audio_enabled = True
except ImportError:
    sd = None
    _audio_enabled = False

AUDIO_ENABLED = _audio_enabled


async def send_audio_file_chunked(
    agent: AgentAudioInputStream,
    wav_file_path: Path,
    chunk_duration_ms: int = 500,
) -> None:
    """Read a WAV file and send it in chunks through the agent audio input stream.

    Args:
        agent: The agent audio input stream to send chunks to.
        wav_file_path: Path to the WAV file to read.
        chunk_duration_ms: Duration of each chunk in milliseconds (default: 500ms).
    """
    if not wav_file_path.exists():
        print(f"❌ File not found: {wav_file_path}")
        return

    with wave.open(str(wav_file_path), "rb") as wf:
        sample_rate = wf.getframerate()
        num_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        num_frames = wf.getnframes()

        # Read all frames
        all_frames = wf.readframes(num_frames)

        # Verify format matches agent config
        config = agent.get_config()
        if num_channels != config.channels:
            # Convert multi-channel to mono by averaging
            audio_array: np.ndarray = np.frombuffer(all_frames, dtype=np.int16)
            audio_array = audio_array.reshape(-1, num_channels)
            audio_array = np.mean(audio_array, axis=1).astype(np.int16)
            frames = audio_array.tobytes()
        else:
            frames = all_frames

        # Calculate chunk size in frames
        chunk_size_frames = int(sample_rate * chunk_duration_ms / 1000.0)
        sample_bytes = sample_width * (1 if num_channels != config.channels else num_channels)
        chunk_bytes = chunk_size_frames * sample_bytes

        chunk_count = 0
        idx = 0

        while idx < len(frames):
            chunk = frames[idx : idx + chunk_bytes]
            idx += chunk_bytes

            if not chunk:
                break

            await agent.send_audio_chunk(chunk)
            chunk_count += 1

            # Small delay between chunks
            await asyncio.sleep(0.01)

        await agent.end_sequence()
        print(f"✅ Sent {chunk_count} audio chunks from {wav_file_path.name}")


class VideoDisplay:
    """Simple video display using OpenCV."""

    def __init__(self, window_name: str = "Anam Avatar") -> None:
        self.window_name: str = window_name
        self.frame: np.ndarray | None = None
        self._running: bool = True

    def update(self, frame: VideoFrame) -> None:
        """Update the displayed frame."""
        self.frame = frame.to_ndarray()

    def run(self) -> None:
        """Run the display loop (call from main thread)."""
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)

        while self._running:
            if self.frame is not None:
                cv2.imshow(self.window_name, self.frame)

            # Check for 'q' key to quit
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                self._running = False
                break

        cv2.destroyAllWindows()

    def stop(self) -> None:
        """Stop the display."""
        self._running = False

    def is_running(self) -> bool:
        """Check if the display is running."""
        return self._running


class AudioPlayer:
    """Simple audio player using sounddevice."""

    def __init__(self, sample_rate: int = 48000, channels: int = 1) -> None:
        self.sample_rate: int = sample_rate
        self.channels: int = channels
        self.buffer: deque[np.ndarray] = deque(maxlen=100)
        self.stream: OutputStreamProtocol | None = None
        self._running: bool = False

    def start(self) -> None:
        """Start the audio stream."""
        if not AUDIO_ENABLED or sd is None:
            return

        self.stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='float32',
            blocksize=1024,
            latency='low',
        )
        self.stream.start()
        self._running = True

    def add_frame(self, frame: AudioFrame) -> None:
        """Add an audio frame to the buffer."""
        if not self._running or not self.stream:
            return

        try:
            # Convert int16 to float32 for sounddevice
            audio_data = frame.to_ndarray().astype(np.float32) / 32768.0
            _ = self.stream.write(audio_data)
        except Exception as e:
            logger.error("Audio playback error: %s", e)

    def stop(self) -> None:
        """Stop the audio stream."""
        self._running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None


async def async_input(prompt: str = "") -> str:
    """Get user input asynchronously without blocking."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)


async def interactive_loop(session, display: VideoDisplay) -> None:
    """Interactive command loop."""
    print("\n" + "="*60)
    print("Interactive Session Started!")
    print("="*60)
    print("Available commands:")
    print("  f [filename]  - Send audio file (defaults to input.wav)")
    print("  i             - Interrupt current audio")
    print("  q             - Quit and stop session")
    print("="*60 + "\n")

    while True:
        try:
            # Get user input in a non-blocking way
            user_input = await async_input(">> ")

            parts = user_input.strip().split()
            if not parts:
                continue

            command = parts[0].lower()

            if command == "q":
                print("Exiting...")
                display.stop()
                break

            elif command == "f":
                # Default to input.wav if no filename provided
                wav_file = parts[1] if len(parts) > 1 else "input.wav"
                wav_path = Path(wav_file)
                if wav_path.exists():
                    print(f"Sending audio from {wav_file}...")
                    agent = session.create_agent_audio_input_stream(
                        AgentAudioInputConfig(encoding="pcm_s16le", sample_rate=24000, channels=1)
                    )
                    await send_audio_file_chunked(agent, wav_path)
                else:
                    print(f"❌ File not found: {wav_file}")

            elif command == "i":
                session.interrupt()
                print("✅ Interrupt sent")

            else:
                print(f"❌ Unknown command: {command}")
                print("Available commands: f [filename], t <text>, i, q")

        except KeyboardInterrupt:
            print("\nInterrupted by user")
            break
        except Exception as e:
            print(f"❌ Error: {e}")


async def stream_session(
    client: AnamClient,
    display: VideoDisplay,
    audio_player: AudioPlayer,
) -> None:
    """Run the streaming session."""

    # These functions are registered via decorators and called by the client
    @client.on(AnamEvent.VIDEO_FRAME)
    async def on_video(frame: VideoFrame) -> None:
        display.update(frame)

    @client.on(AnamEvent.AUDIO_FRAME)
    async def on_audio(frame: AudioFrame) -> None:
        audio_player.add_frame(frame)

    @client.on(AnamEvent.CONNECTION_ESTABLISHED)
    async def on_connected() -> None:
        print("✅ Connected!")

    @client.on(AnamEvent.CONNECTION_CLOSED)
    async def on_closed(code: str, reason: str | None) -> None:
        print(f"Connection closed: {code} - {reason or 'No reason'}")

    async with client.connect() as session:
        print(f"Session: {session.session_id}")
        print("Press 'q' in the video window or type 'q' in CLI to quit")

        # Start interactive loop
        interactive_task = asyncio.create_task(interactive_loop(session, display))

        # Wait until display is closed, session ends, or interactive loop exits
        while display.is_running():
            if not session.is_active:
                break
            if interactive_task.done():
                break
            await asyncio.sleep(0.1)

        # Cancel interactive task if still running
        if not interactive_task.done():
            interactive_task.cancel()
            try:
                await interactive_task
            except asyncio.CancelledError:
                pass


def main() -> None:
    """Main entry point."""
    # Get configuration from environment variables (loaded from .env file)
    api_key = os.environ.get("ANAM_API_KEY", "").strip().strip('"')
    avatar_id = os.environ.get("ANAM_AVATAR_ID", "").strip().strip('"')
    api_base_url = os.environ.get("ANAM_API_BASE_URL", "https://api.anam.ai").strip().strip('"')

    if not api_key or not avatar_id:
        raise ValueError(
            "Set ANAM_API_KEY and ANAM_AVATAR_ID environment variables"
        )


    # Create persona config
    persona_config = PersonaConfig(
        avatar_id=avatar_id,
        enable_audio_passthrough=True,
    )

    # Create client
    client = AnamClient(
        api_key=api_key,
        persona=persona_config,
        options=ClientOptions(disable_input_audio=True, api_base_url=api_base_url),
    )

    # Create display and audio player
    display = VideoDisplay()
    audio_player = AudioPlayer()

    # Start audio
    audio_player.start()

    # Run streaming in background
    import threading

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stream_task = loop.create_task(
        stream_session(client, display, audio_player)
    )

    def run_async() -> None:
        try:
            loop.run_until_complete(stream_task)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ Error in async thread: {e}")

    thread = threading.Thread(target=run_async, daemon=True)
    thread.start()

    try:
        # Run display in main thread (required by OpenCV on macOS)
        # This will block until 'q' is pressed
        display.run()

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        display.stop()
        audio_player.stop()

        # Cancel the async task
        if not stream_task.done():
            stream_task.cancel()

        # Stop the event loop gracefully from thread-safe context
        if loop.is_running():
            def stop_loop() -> None:
                loop.stop()

            _ = loop.call_soon_threadsafe(stop_loop)

        # Wait for thread to finish (with timeout)
        thread.join(timeout=2.0)

        # Only close the loop if it's not running
        if not loop.is_running():
            try:
                # Cancel any remaining tasks
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                # Run until all tasks are cancelled
                if pending:
                    _ = loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except RuntimeError:
                # Loop might already be closed or in invalid state
                pass
            finally:
                try:
                    if not loop.is_closed():
                        loop.close()
                except RuntimeError:
                    # Loop might already be closed
                    pass


if __name__ == "__main__":
    main()
