"""Text-to-video example - send text and save avatar video response.

This example demonstrates a simple text-to-video function that:
1. Connects to an Anam session with a persona or avatar/voice config
2. Sends text via the talk() command for direct TTS
3. Records the avatar's video and audio response
4. Saves the output once speech ends (after a silence timeout)

This example does NOT require opencv - it uses PyAV (already a dependency)
for video encoding.

Usage:
    # With persona_id:
    export ANAM_API_KEY="your-api-key"
    export ANAM_PERSONA_ID="your-persona-id"
    uv run python examples/text_to_video.py "Hello, how are you today?"

    # With avatar_id + voice_id (creates ephemeral persona):
    export ANAM_API_KEY="your-api-key"
    export ANAM_AVATAR_ID="your-avatar-id"
    export ANAM_VOICE_ID="your-voice-id"
    export ANAM_AVATAR_MODEL="cara-3"    # optional, defaults to cara-3
    export ANAM_LLM_ID="your-llm-id"     # optional, uses default Anam LLM
    uv run python examples/text_to_video.py "Hello, how are you today?"
"""

import asyncio
import logging
import os
import sys
import time
import wave
from pathlib import Path

import av
import numpy as np
from dotenv import load_dotenv

from anam import AnamClient, AnamEvent, ClientOptions
from anam.client import Session

# Load environment variables from .env
_ = load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress verbose dependency logging
logging.getLogger("anam").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("aiortc").setLevel(logging.WARNING)


class VideoWriter:
    """Writes video frames to an MP4 file using PyAV."""

    def __init__(self, output_path: str, fps: int = 30) -> None:
        self.output_path = output_path
        self.fps = fps
        self.container: av.container.OutputContainer | None = None
        self.stream: av.video.stream.VideoStream | None = None
        self.frame_count = 0
        self._closed = False

    def add_frame(self, frame: av.video.frame.VideoFrame) -> None:
        """Add a video frame to the recording."""
        if self._closed:
            return

        # Initialize container on first frame
        if self.container is None:
            self.container = av.open(self.output_path, mode="w")
            self.stream = self.container.add_stream("libx264", rate=self.fps)
            self.stream.width = frame.width
            self.stream.height = frame.height
            self.stream.pix_fmt = "yuv420p"
            # Use CRF 18 for high quality (lower = better, 18 is visually lossless)
            # Use medium preset for good quality/speed balance
            self.stream.options = {"crf": "18", "preset": "medium"}
            logger.info(
                "Recording video to: %s (%dx%d)", self.output_path, frame.width, frame.height
            )

        # Convert to yuv420p for h264 encoding
        frame_yuv = frame.reformat(format="yuv420p")

        # Encode and write - let PyAV handle pts automatically
        for packet in self.stream.encode(frame_yuv):
            self.container.mux(packet)

        self.frame_count += 1

    def close(self) -> None:
        """Close the video file."""
        if self._closed:
            return
        self._closed = True

        if self.container and self.stream:
            # Flush encoder
            try:
                for packet in self.stream.encode():
                    self.container.mux(packet)
            except Exception as e:
                logger.warning("Error flushing encoder: %s", e)
            self.container.close()
            logger.info("Saved %d video frames to %s", self.frame_count, self.output_path)
        self.container = None
        self.stream = None


class AudioWriter:
    """Writes audio frames to a WAV file."""

    def __init__(self, output_path: str) -> None:
        self.output_path = output_path
        self.writer: wave.Wave_write | None = None
        self.frame_count = 0

    def add_frame(self, frame: av.audio.frame.AudioFrame) -> None:
        """Add an audio frame to the recording."""
        # Initialize writer on first frame
        if self.writer is None:
            self.writer = wave.open(self.output_path, "wb")
            self.writer.setnchannels(frame.layout.nb_channels)
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
            logger.info("Saved %d audio frames to %s", self.frame_count, self.output_path)
        self.writer = None


def mux_video_audio(video_path: str, audio_path: str, output_path: str) -> None:
    """Combine video and audio into a single MP4 file using ffmpeg.

    Args:
        video_path: Path to video-only MP4.
        audio_path: Path to WAV audio file.
        output_path: Path for combined output MP4.
    """
    import subprocess

    # Use ffmpeg to mux - more reliable than PyAV for this
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("ffmpeg error: %s", result.stderr)
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")

    logger.info("Muxed video+audio to %s", output_path)


async def text_to_video(
    text: str,
    output_path: str = "output.mp4",
    silence_timeout: float = 2.0,
    max_duration: float = 60.0,
    speech_timeout: float = 10.0,
    persona_id: str | None = None,
    avatar_id: str | None = None,
    voice_id: str | None = None,
    avatar_model: str | None = None,
) -> str:
    """Convert text to avatar video using the talk() command.

    The avatar will speak the provided text directly via the TTS pipeline.

    You can configure either:
    - persona_id: Use an existing persona
    - avatar_id + voice_id: Create an ephemeral persona with custom avatar/voice

    Args:
        text: The text for the avatar to speak.
        output_path: Path for the output video file (with audio).
        silence_timeout: Seconds of silence before stopping (after speech starts).
        max_duration: Maximum recording duration in seconds.
        speech_timeout: Seconds to wait for speech to start before giving up.
        persona_id: Persona ID to use.
        avatar_id: Avatar ID for ephemeral persona.
        voice_id: Voice ID for ephemeral persona.
        avatar_model: Avatar model version (e.g., 'cara-3').

    Returns:
        Path to the output video file.

    Raises:
        ValueError: If configuration is invalid.
        RuntimeError: If connection fails or no speech detected.
    """
    from anam.types import PersonaConfig

    # Get configuration from env if not provided
    api_key = os.environ.get("ANAM_API_KEY", "").strip().strip('"')
    api_base_url = os.environ.get("ANAM_API_BASE_URL", "https://api.anam.ai").strip().strip('"')

    if not api_key:
        raise ValueError("Set ANAM_API_KEY environment variable")

    # Use env vars as fallback for all config options
    if not persona_id:
        persona_id = os.environ.get("ANAM_PERSONA_ID", "").strip().strip('"')
    if not avatar_id:
        avatar_id = os.environ.get("ANAM_AVATAR_ID", "").strip().strip('"')
    if not voice_id:
        voice_id = os.environ.get("ANAM_VOICE_ID", "").strip().strip('"')
    if not avatar_model:
        avatar_model = os.environ.get("ANAM_AVATAR_MODEL", "cara-3").strip().strip('"')

    # Determine mode - prefer avatar_id + voice_id if both provided
    if avatar_id and voice_id:
        # Create ephemeral persona with custom avatar/voice
        # enable_audio_passthrough=False is required for /talk to work (uses TTS)
        # llm_id is required for ephemeral personas (default Anam LLM)
        llm_id = (
            os.environ.get("ANAM_LLM_ID", "0934d97d-0c3a-4f33-91b0-5e136a0ef466").strip().strip('"')
        )
        logger.info(
            "Using ephemeral persona (avatar_id=%s, voice_id=%s, model=%s)",
            avatar_id[:8] + "...",
            voice_id[:8] + "...",
            avatar_model,
        )
        persona_config = PersonaConfig(
            name="TextToVideo",
            avatar_id=avatar_id,
            avatar_model=avatar_model,
            voice_id=voice_id,
            llm_id=llm_id,
            enable_audio_passthrough=False,
        )
    elif persona_id:
        logger.info("Using persona mode (persona_id)")
        persona_config = PersonaConfig(persona_id=persona_id)
    else:
        raise ValueError(
            "Provide either persona_id or (avatar_id + voice_id). "
            "Set via args or environment variables."
        )

    # Create client
    client = AnamClient(
        api_key=api_key,
        persona_config=persona_config,
        options=ClientOptions(api_base_url=api_base_url),
    )

    # Temp files for video and audio (keep extensions for format detection)
    video_tmp = output_path.replace(".mp4", ".video.mp4")
    audio_tmp = output_path.replace(".mp4", ".audio.wav")

    # Create writers for temp files
    video_writer = VideoWriter(video_tmp)
    audio_writer = AudioWriter(audio_tmp)

    # Track audio timing to detect end of speech
    # We detect "silence" by checking if audio amplitude is below a threshold
    last_speech_time = 0.0
    speech_started = False
    silence_threshold = 500  # Amplitude threshold for 16-bit audio (adjust if needed)

    # Connection event handler
    @client.on(AnamEvent.CONNECTION_ESTABLISHED)
    async def on_connected() -> None:
        logger.info("Connected to Anam")

    async def consume_video_frames(session: Session) -> None:
        """Consume and record video frames."""
        try:
            async for frame in session.video_frames():
                video_writer.add_frame(frame)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error consuming video frames: %s", e)

    async def consume_audio_frames(session: Session) -> None:
        """Consume and record audio frames, tracking speech activity."""
        nonlocal last_speech_time, speech_started
        try:
            async for frame in session.audio_frames():
                audio_writer.add_frame(frame)

                # Check if this frame contains actual speech (not silence)
                # by measuring the RMS amplitude
                audio_data = frame.to_ndarray()
                rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))

                if rms > silence_threshold:
                    last_speech_time = time.monotonic()
                    if not speech_started:
                        speech_started = True
                        logger.info("Speech detected (RMS: %.0f)", rms)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error consuming audio frames: %s", e)

    logger.info("Connecting to Anam...")
    timed_out = False

    try:
        async with client.connect() as session:
            logger.info("Session started: %s", session.session_id)

            # Start consuming frames
            video_task = asyncio.create_task(consume_video_frames(session))
            audio_task = asyncio.create_task(consume_audio_frames(session))

            # Wait for connection to stabilize
            await asyncio.sleep(1.0)

            # Send the text directly to TTS (avatar speaks exactly this text)
            logger.info("Sending talk: %s", text[:50] + "..." if len(text) > 50 else text)
            await session.talk(text)

            # Wait for speech to start, then stop after silence timeout
            start_time = time.monotonic()
            timed_out = False

            while True:
                elapsed = time.monotonic() - start_time

                # Check max duration
                if elapsed > max_duration:
                    logger.info("Max duration reached (%.1fs)", max_duration)
                    break

                # Check for speech timeout (no speech detected after sending talk)
                if not speech_started and elapsed > speech_timeout:
                    logger.error("No speech detected after %.1fs, giving up", speech_timeout)
                    timed_out = True
                    break

                # Check for end of speech (silence after speech started)
                if speech_started:
                    silence_duration = time.monotonic() - last_speech_time
                    if silence_duration > silence_timeout:
                        logger.info("Silence detected (%.1fs), stopping", silence_duration)
                        break

                await asyncio.sleep(0.1)

            # Cancel frame consumption tasks
            video_task.cancel()
            audio_task.cancel()

            for task in [video_task, audio_task]:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    except KeyboardInterrupt:
        logger.info("Recording interrupted")
        timed_out = True
    finally:
        video_writer.close()
        audio_writer.close()

    # Clean up temp files on failure
    if timed_out or video_writer.frame_count == 0 or audio_writer.frame_count == 0:
        Path(video_tmp).unlink(missing_ok=True)
        Path(audio_tmp).unlink(missing_ok=True)
        if timed_out:
            raise RuntimeError("No speech detected - talk command may not be supported")
        raise RuntimeError("No frames recorded")

    # Mux video and audio into final output
    mux_video_audio(video_tmp, audio_tmp, output_path)

    # Clean up temp files
    Path(video_tmp).unlink(missing_ok=True)
    Path(audio_tmp).unlink(missing_ok=True)

    return output_path


async def main() -> None:
    """Main entry point."""
    # Get text from command line or use default
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = "Hello there stranger! Welcome to Anam AI. We hope you have fun building your project with us!"

    # Create output directory
    output_dir = Path("recordings")
    output_dir.mkdir(exist_ok=True)

    output_path = str(output_dir / "output.mp4")

    logger.info("Text to convert: %s", text)

    video_path = await text_to_video(
        text=text,
        output_path=output_path,
        silence_timeout=2.0,
    )

    logger.info("Done! Output: %s", video_path)


if __name__ == "__main__":
    asyncio.run(main())
