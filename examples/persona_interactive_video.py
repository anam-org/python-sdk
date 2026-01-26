"""Interactive video session example with CLI controls.

This example shows how to display the avatar video stream
in a window using OpenCV while providing CLI controls for
interactive session management, where text messages mimic
the transcibed audio and wav files can be sent as TTS audio.

Requirements:
    uv sync --extra display
    # or: pip install opencv-python sounddevice

Usage:
    export ANAM_API_KEY="your-api-key"
    export ANAM_PERSONA_ID="your-persona-id"
    uv run --extra display python examples/persona_interactive_video.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from anam import AnamClient, AnamEvent, ClientOptions
from anam.types import AgentAudioInputConfig, PersonaConfig

# Add parent directory to path to allow importing from examples
sys.path.insert(0, str(Path(__file__).parent.parent))
from examples.utils import AudioPlayer, VideoDisplay, async_input, send_audio_file_chunked

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


async def interactive_loop(session, display: VideoDisplay) -> None:
    """Interactive command loop."""
    print("\n" + "=" * 60)
    print("Interactive Session Started!")
    print("=" * 60)
    print("Available commands:")
    print("  f [filename]  - Send audio file (defaults to input.wav)")
    print("  m <message>   - Send text message (user input for the conversation.)")
    print("  t <text>      - Send talk command text (bypasses LLM and sends text directly to TTS.)")
    print("  i             - Interrupt current audio")
    print("  q             - Quit and stop session")
    print("=" * 60 + "\n")

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

            elif command == "m":
                # Get the rest of the input as the message text
                if len(parts) < 2:
                    print("❌ Please provide text to send. Usage: m <message to the engine>")
                    continue
                message_text = " ".join(parts[1:])
                try:
                    await session.send_message(message_text)
                    print(f"✅ Sent message: {message_text}")
                except Exception as e:
                    print(f"❌ Error sending message: {e}")

            elif command == "t":
                # Get the rest of the input as the message text
                if len(parts) < 2:
                    print("❌ Please provide talk command. Usage: t <text to be spoken>")
                    continue
                message_text = " ".join(parts[1:])
                try:
                    await session.talk(message_text)
                    print(f"✅ Sent talk command: {message_text}")
                except Exception as e:
                    print(f"❌ Error sending talk command: {e}")

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

    # Register connection event handlers
    @client.on(AnamEvent.CONNECTION_ESTABLISHED)
    async def on_connected() -> None:
        print("✅ Connected!")

    @client.on(AnamEvent.CONNECTION_CLOSED)
    async def on_closed(code: str, reason: str | None) -> None:
        print(f"Connection closed: {code} - {reason or 'No reason'}")

    async def consume_video_frames(session) -> None:
        """Consume video frames from iterator."""
        try:
            async for frame in session.video_frames():
                display.update(frame)
        except Exception as e:
            logger.error(f"Error consuming video frames: {e}")

    async def consume_audio_frames(session) -> None:
        """Consume audio frames from iterator."""
        try:
            async for frame in session.audio_frames():
                audio_player.add_frame(frame)
        except Exception as e:
            logger.error(f"Error consuming audio frames: {e}")

    async with client.connect() as session:
        print(f"Session: {session.session_id}")
        print("Type 'q' in CLI to quit")

        # Start consuming video and audio frames
        video_task = asyncio.create_task(consume_video_frames(session))
        audio_task = asyncio.create_task(consume_audio_frames(session))

        # Start interactive loop
        interactive_task = asyncio.create_task(interactive_loop(session, display))

        # Wait until display is closed, session ends, or interactive loop exits
        while display.is_running():
            if not session.is_active:
                break
            if interactive_task.done():
                break
            await asyncio.sleep(0.1)

        # Cancel all tasks
        video_task.cancel()
        audio_task.cancel()
        if not interactive_task.done():
            interactive_task.cancel()

        # Wait for tasks to complete cancellation
        for task in [video_task, audio_task, interactive_task]:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error in task: {e}")


def main() -> None:
    """Main entry point."""
    # Get configuration from environment variables (loaded from .env file)
    api_key = os.environ.get("ANAM_API_KEY", "").strip().strip('"')
    persona_id = os.environ.get("ANAM_PERSONA_ID", "").strip().strip('"')
    api_base_url = os.environ.get("ANAM_API_BASE_URL", "https://api.anam.ai").strip().strip('"')

    if not api_key or not persona_id:
        raise ValueError("Set ANAM_API_KEY and ANAM_PERSONA_ID environment variables")

    # Create persona config
    persona_config = PersonaConfig(
        persona_id=persona_id,
        enable_audio_passthrough=True,
    )

    # Create client
    client = AnamClient(
        api_key=api_key,
        persona_config=persona_config,
        options=ClientOptions(disable_input_audio=False, api_base_url=api_base_url),
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

    stream_task = loop.create_task(stream_session(client, display, audio_player))

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
                    _ = loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
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
