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
from collections import deque
from typing import Deque

import cv2
import numpy as np
from dotenv import load_dotenv

from anam import AnamClient, AnamEvent, AudioFrame, ClientOptions, VideoFrame

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Audio playback (optional)
try:
    import sounddevice as sd
    AUDIO_ENABLED = True
except ImportError:
    AUDIO_ENABLED = False
    logger.warning("sounddevice not installed, audio playback disabled")


class VideoDisplay:
    """Simple video display using OpenCV."""

    def __init__(self, window_name: str = "Anam Avatar"):
        self.window_name = window_name
        self.frame: np.ndarray | None = None
        self._running = True

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


class AudioPlayer:
    """Simple audio player using sounddevice."""

    def __init__(self, sample_rate: int = 48000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self.buffer: Deque[np.ndarray] = deque(maxlen=100)
        self.stream: sd.OutputStream | None = None
        self._running = False

    def start(self) -> None:
        """Start the audio stream."""
        if not AUDIO_ENABLED:
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
            self.stream.write(audio_data)
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

    @client.on(AnamEvent.VIDEO_FRAME)
    async def on_video(frame: VideoFrame) -> None:
        display.update(frame)

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
        while display._running:
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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stream_task = loop.create_task(
        stream_session(client, display, audio_player)
    )

    try:
        # Run display in main thread (required by OpenCV on macOS)
        # This will block until 'q' is pressed
        import threading

        def run_async() -> None:
            loop.run_until_complete(stream_task)

        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()

        # Run OpenCV display in main thread
        display.run()

    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        display.stop()
        audio_player.stop()
        stream_task.cancel()
        loop.close()


if __name__ == "__main__":
    main()

