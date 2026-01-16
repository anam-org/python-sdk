"""Video display example using OpenCV.

This example shows how to display the avatar video stream
in a window using OpenCV.

Requirements:
    uv sync --extra display
    # or: pip install opencv-python sounddevice

Usage:
    export ANAM_API_KEY="your-api-key"
    export ANAM_PERSONA_ID="your-persona-id"
    uv run --extra display python examples/video_display.py
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
from anam.types import AgentAudioInputConfig

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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Audio playback (optional)
try:
    import sounddevice as sd
    _audio_enabled = True
except ImportError:
    sd = None
    _audio_enabled = False
    logger.warning("sounddevice not installed, audio playback disabled")

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

    Raises:
        FileNotFoundError: If the WAV file doesn't exist.
        ValueError: If the WAV file format is incompatible.
    """
    if not wav_file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {wav_file_path}")

    logger.info(f"Reading audio file: {wav_file_path}")

    with wave.open(str(wav_file_path), "rb") as wf:
        sample_rate = wf.getframerate()
        num_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        num_frames = wf.getnframes()

        duration_sec = num_frames / sample_rate
        logger.info(
            f"WAV file: {sample_rate}Hz, {num_channels}ch, {sample_width} bytes/sample, "
            + f"{num_frames} frames ({duration_sec:.2f}s)"
        )

        # Verify format matches agent config
        config = agent.get_config()
        if num_channels != config.channels:
            logger.warning(
                f"Channel mismatch: file has {num_channels} channels, "
                + f"agent expects {config.channels}. Converting to mono."
            )

        # Calculate chunk size in frames
        chunk_size_frames = int(sample_rate * chunk_duration_ms / 1000.0)

        # Read and send chunks
        chunk_count = 0
        total_bytes_sent = 0

        while True:
            # Read chunk
            frames = wf.readframes(chunk_size_frames)

            if not frames:
                break

            # Convert to mono if needed
            if num_channels > 1:
                # Convert multi-channel to mono by averaging
                audio_array: np.ndarray = np.frombuffer(frames, dtype=np.int16)
                audio_array = audio_array.reshape(-1, num_channels)
                audio_array = np.mean(audio_array, axis=1).astype(np.int16)
                frames = audio_array.tobytes()

            # Send chunk
            await agent.send_audio_chunk(frames)
            chunk_count += 1
            total_bytes_sent += len(frames)

            logger.debug(f"Sent audio chunk {chunk_count}: {len(frames)} bytes")

            # Small delay between chunks to avoid overwhelming the connection
            await asyncio.sleep(0.01)

        logger.info(
            f"Finished sending audio: {chunk_count} chunks, "
            + f"{total_bytes_sent} bytes total"
        )


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

            # Write directly to stream
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


async def stream_session(
    client: AnamClient,
    display: VideoDisplay,
    audio_player: AudioPlayer,
) -> None:
    """Run the streaming session."""

    video_frames:int = 0

    # These functions are registered via decorators and called by the client
    # They appear unused to static analysis but are actually used at runtime
    @client.on(AnamEvent.VIDEO_FRAME)
    async def on_video(frame: VideoFrame) -> None:
        nonlocal video_frames
        video_frames += 1
        display.update(frame)
        if video_frames == 200:
            await session.send_message("Hello! Tell me a short joke.")
        if video_frames == 250:
            session.interrupt()
        if video_frames == 300:
            agent = session.create_agent_audio_input_stream(
                AgentAudioInputConfig(encoding="pcm_s16le", sample_rate=24000, channels=1)
            )
            # Read and send audio file in chunks
            wav_path = Path(__file__).parent.parent / "input.wav"
            await send_audio_file_chunked(agent, wav_path)
            await agent.end_sequence()

    @client.on(AnamEvent.AUDIO_FRAME)
    async def on_audio(frame: AudioFrame) -> None:
        audio_player.add_frame(frame)

    @client.on(AnamEvent.CONNECTION_ESTABLISHED)
    async def on_connected() -> None:
        logger.info("✓ Connected!")

    async with client.connect() as session:
        logger.info("Session: %s", session.session_id)
        logger.info("Press 'q' in the video window to quit")

        # Wait until display is closed or session ends
        while display.is_running():
            if not session.is_active:
                break
            await asyncio.sleep(0.1)


def main() -> None:
    """Main entry point."""
    # Get configuration from environment variables (loaded from .env file)
    api_key = os.environ.get("ANAM_API_KEY", "").strip().strip('"')
    persona_id = os.environ.get("ANAM_PERSONA_ID", "").strip().strip('"')
    api_base_url = os.environ.get("ANAM_API_BASE_URL", "https://api.anam.ai").strip().strip('"')

    if not api_key or not persona_id:
        raise ValueError(
            "Set ANAM_API_KEY and ANAM_PERSONA_ID environment variables"
        )

    # Create client
    client = AnamClient(
        api_key=api_key,
        persona_id=persona_id,
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
            logger.error("Error in async thread: %s", e)

    thread = threading.Thread(target=run_async, daemon=True)
    thread.start()

    try:
        # Run display in main thread (required by OpenCV on macOS)
        # This will block until 'q' is pressed
        display.run()

    except KeyboardInterrupt:
        logger.info("Interrupted")
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

