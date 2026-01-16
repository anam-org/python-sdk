"""Basic streaming example for Anam AI SDK.

This example demonstrates how to connect to an Anam avatar and
receive video/audio frames.

Usage:
    # Set env vars in .env file or export them:
    export ANAM_API_KEY="your-api-key"
    export ANAM_PERSONA_ID="your-persona-id"

    # Run:
    uv run python examples/basic_stream.py
"""

import asyncio
import logging
import os
from pathlib import Path

from anam import AnamClient, AnamEvent, AudioFrame, ClientOptions, Message, VideoFrame


def load_env() -> None:
    """Load environment variables from .env file if it exists."""
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    # Strip quotes from value
                    value = value.strip().strip('"').strip("'")
                    if key not in os.environ:  # Don't override existing
                        os.environ[key] = value

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point."""
    # Load .env file if present
    load_env()

    # Get configuration from environment
    api_key = os.environ.get("ANAM_API_KEY")
    persona_id = os.environ.get("ANAM_PERSONA_ID")

    if not api_key:
        raise ValueError("ANAM_API_KEY environment variable is required")
    if not persona_id:
        raise ValueError("ANAM_PERSONA_ID environment variable is required")

    # Create client (disable input audio for server-side usage)
    client = AnamClient(
        api_key=api_key,
        persona_id=persona_id,
        options=ClientOptions(disable_input_audio=True),
    )

    # Track frame counts
    video_frames = 0
    audio_frames = 0

    # Register event handlers
    # These functions are registered via decorators and called by the client
    # They appear unused to static analysis but are actually used at runtime
    @client.on(AnamEvent.VIDEO_FRAME)
    async def on_video(frame: VideoFrame) -> None:
        nonlocal video_frames
        video_frames += 1
        if video_frames % 30 == 0:  # Log every 30 frames (~1 second at 30fps)
            logger.info(
                "Video: %d frames, %dx%d",
                video_frames,
                frame.width,
                frame.height,
            )

    @client.on(AnamEvent.AUDIO_FRAME)
    async def on_audio(frame: AudioFrame) -> None:
        nonlocal audio_frames
        audio_frames += 1
        if audio_frames % 50 == 0:  # Log periodically
            logger.info(
                "Audio: %d frames, %dHz, %d channels",
                audio_frames,
                frame.sample_rate,
                frame.channels,
            )

    @client.on(AnamEvent.MESSAGE_RECEIVED)
    async def on_message(message: Message) -> None:
        logger.info("Message [%s]: %s", message.role.value, message.content)

    @client.on(AnamEvent.CONNECTION_ESTABLISHED)
    async def on_connected() -> None:
        logger.info("✓ Connection established!")

    @client.on(AnamEvent.CONNECTION_CLOSED)
    async def on_closed(code: str, reason: str | None) -> None:
        logger.info("Connection closed: %s - %s", code, reason or "No reason")

    # Connect and stream
    logger.info("Connecting to Anam...")

    try:
        async with client.connect() as session:
            logger.info("Session started: %s", session.session_id)

            # Send a message to trigger the avatar to respond
            logger.info("Sending message to avatar...")
            await session.send_message("Hello! Tell me a short joke.")

            # Keep the session alive for 30 seconds
            logger.info("Streaming for 30 seconds...")
            await asyncio.sleep(30)

            # Or wait until the session is closed by the server:
            # await session.wait_until_closed()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error("Error: %s", e)
        raise

    logger.info(
        "Session ended. Total frames: video=%d, audio=%d",
        video_frames,
        audio_frames,
    )


if __name__ == "__main__":
    asyncio.run(main())

