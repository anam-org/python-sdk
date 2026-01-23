"""Shared utility functions and classes for examples."""

import asyncio
import logging
import wave
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import cv2
import numpy as np

from anam import AudioFrame, VideoFrame
from anam._agent_audio_input_stream import AgentAudioInputStream

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

        if sample_rate != 24000:
            raise ValueError(f"Sample rate must be 24000, got {sample_rate}")
        if num_channels != 1:
            raise ValueError(f"Mono audio only supported, number of channels must be 1, got {num_channels}")
        if sample_width != 2:
            raise ValueError(f"16-bit audio only supported, sample width must be 2, got {sample_width}")

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
        # Convert RGB to BGR for OpenCV display
        rgb_frame = frame.to_ndarray()
        self.frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)

    def run(self) -> None:
        """Run the display loop (call from main thread).

        Note: Key presses in the window are disabled to prevent hangs.
        Use the CLI 'q' command to quit instead.
        """
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)

        while self._running:
            if self.frame is not None:
                cv2.imshow(self.window_name, self.frame)

            # Process window events but don't check for key presses to prevent hangs
            cv2.waitKey(1)

        cv2.destroyAllWindows()

    def stop(self) -> None:
        """Stop the display."""
        self._running = False

    def is_running(self) -> bool:
        """Check if the display is running."""
        return self._running


class AudioPlayer:
    """Simple audio player using sounddevice."""

    def __init__(self, sample_rate: int = 48000, channels: int = 2) -> None:
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
            # Convert int16 to float32 for sounddevice - reshape to (samples, channels) for stereo playback
            audio_data = frame.to_ndarray().reshape(-1, frame.channels).astype(np.float32) / 32768.0
            _ = self.stream.write(audio_data)
        except Exception as e:
            logger = logging.getLogger(__name__)
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
