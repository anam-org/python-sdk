"""Recording example - save video/audio to files.

This example demonstrates how to save the avatar's video and audio
streams to files for later processing.

Usage:
    # Set environment variables in .env or shell:
    export ANAM_API_KEY="your-api-key"
    export ANAM_PERSONA_ID="your-persona-id"
    
    # Run with display extras:
    uv run --extra display python examples/save_recording.py
"""

import asyncio
import os
import logging
import wave
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv

from anam import AnamClient, AnamEvent, ClientOptions, VideoFrame, AudioFrame

# Load environment variables from .env
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class VideoRecorder:
    """Records video frames to an MP4 file."""

    def __init__(self, output_path: str, fps: float = 30.0):
        self.output_path = output_path
        self.fps = fps
        self.writer: cv2.VideoWriter | None = None
        self.frame_count = 0

    def add_frame(self, frame: VideoFrame) -> None:
        """Add a video frame to the recording."""
        img = frame.to_ndarray()

        # Initialize writer on first frame
        if self.writer is None:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self.writer = cv2.VideoWriter(
                self.output_path,
                fourcc,
                self.fps,
                (frame.width, frame.height),
            )
            logger.info("Recording video to: %s", self.output_path)

        self.writer.write(img)
        self.frame_count += 1

    def close(self) -> None:
        """Close the video file."""
        if self.writer:
            self.writer.release()
            self.writer = None
            logger.info("Saved %d video frames", self.frame_count)


class AudioRecorder:
    """Records audio frames to a WAV file."""

    def __init__(
        self,
        output_path: str,
        sample_rate: int = 24000,
        channels: int = 2,
    ):
        self.output_path = output_path
        self.sample_rate = sample_rate
        self.channels = channels
        self.writer: wave.Wave_write | None = None
        self.frame_count = 0

    def add_frame(self, frame: AudioFrame) -> None:
        """Add an audio frame to the recording."""
        # Initialize writer on first frame
        if self.writer is None:
            self.writer = wave.open(self.output_path, 'wb')
            self.writer.setnchannels(frame.channels)
            self.writer.setsampwidth(2)  # 16-bit
            self.writer.setframerate(frame.sample_rate)
            logger.info("Recording audio to: %s", self.output_path)

        audio_data = frame.to_ndarray()
        self.writer.writeframes(audio_data.tobytes())
        self.frame_count += 1

    def close(self) -> None:
        """Close the audio file."""
        if self.writer:
            self.writer.close()
            self.writer = None
            logger.info("Saved %d audio frames", self.frame_count)


async def main() -> None:
    """Main entry point."""
    # Get configuration (strip quotes that might be in .env)
    api_key = os.environ.get("ANAM_API_KEY", "").strip().strip('"')
    persona_id = os.environ.get("ANAM_PERSONA_ID", "").strip().strip('"')
    api_base_url = os.environ.get("ANAM_API_BASE_URL", "https://api.anam.ai").strip().strip('"')


    if not api_key or not persona_id:
        raise ValueError(
            "Set ANAM_API_KEY and ANAM_PERSONA_ID environment variables"
        )

    # Create output directory
    output_dir = Path("recordings")
    output_dir.mkdir(exist_ok=True)

    # Create recorders
    video_recorder = VideoRecorder(str(output_dir / "avatar_video.mp4"))
    audio_recorder = AudioRecorder(str(output_dir / "avatar_audio.wav"))

    # Create client with input audio disabled (server-side recording)
    client = AnamClient(
        api_key=api_key,
        persona_id=persona_id,
        options=ClientOptions(disable_input_audio=True, api_base_url=api_base_url),
    )

    @client.on(AnamEvent.VIDEO_FRAME)
    async def on_video(frame: VideoFrame) -> None:
        video_recorder.add_frame(frame)

    @client.on(AnamEvent.AUDIO_FRAME)
    async def on_audio(frame: AudioFrame) -> None:
        audio_recorder.add_frame(frame)

    @client.on(AnamEvent.CONNECTION_ESTABLISHED)
    async def on_connected() -> None:
        logger.info("✓ Connected - recording started")

    # Record for specified duration
    duration_seconds = 30
    logger.info("Recording for %d seconds...", duration_seconds)

    try:
        async with client.connect() as session:
            await asyncio.sleep(duration_seconds)
    except KeyboardInterrupt:
        logger.info("Recording interrupted")
    finally:
        video_recorder.close()
        audio_recorder.close()

    logger.info("Recording complete! Files saved to: %s", output_dir)


if __name__ == "__main__":
    asyncio.run(main())

