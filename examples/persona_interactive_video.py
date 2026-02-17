"""Interactive video session example with CLI controls.

This example shows how to display the avatar video stream
in a window using OpenCV while providing CLI controls for
interactive session management, where talk commands will be
spoken directly by the avatar, while text messages mimic
the transcibed audio.

The persona config creates and ephemeral persona with enable_audio_passthrough=False to disable TTS ingest.
The avatar will respond directly to text messages and talk commands will be spoken verbatim.

Requirements:
    uv sync --extra display
    # or: pip install opencv-python sounddevice

Usage:
    export ANAM_API_KEY="your-api-key"
    export ANAM_AVATAR_ID="your-avatar-id"
    export ANAM_VOICE_ID="your-voice-id"
    export ANAM_LLM_ID="your-llm-id"
    export ANAM_AVATAR_MODEL="model-name"     # optional, e.g. "cara-3"
    uv run --extra display python examples/persona_interactive_video.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from anam import AnamClient, AnamEvent, ClientOptions
from anam.types import MessageRole, PersonaConfig

# Add parent directory to path to allow importing from examples
sys.path.insert(0, str(Path(__file__).parent.parent))
from examples.utils import AudioPlayer, VideoDisplay, async_input

# Load environment variables
_ = load_dotenv()

REQUIRED_ENV_VARS = [
    "ANAM_API_KEY",
    "ANAM_AVATAR_ID",
    "ANAM_LLM_ID",
    "ANAM_VOICE_ID",
]
missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
if missing:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

# Configure logging - reduced verbosity
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",  # Simplified format
)
logger = logging.getLogger(__name__)

# Suppress verbose logging from dependencies
logging.getLogger("anam").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("aiortc").setLevel(logging.WARNING)
logging.getLogger("aioice").setLevel(logging.WARNING)

# Global state for captions toggle
show_captions = False
print_conversation_history = False


async def interactive_loop(session, display: VideoDisplay) -> None:
    """Interactive command loop."""
    global show_captions
    global print_conversation_history
    print("\n" + "=" * 60)
    print("Interactive Session Started!")
    print("=" * 60)
    print("Available commands:")
    print("  m <message>  - Send text message (user input for the conversation.)")
    print("  t <text>     - Send talk command (bypasses LLM and sends text to TTS) using REST API)")
    print(
        "  ts <text>    - Send talk stream (bypasses LLM and sends text to TTS) using WebSocket (lower latency)"
    )
    print("  i            - Interrupt current audio")
    print("  c            - Toggle live captions. Default: disabled")
    print("  h            - Toggle conversation history at session end. Default: disabled.")
    print("  q            - Quit and stop session")
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
                # Stop display immediately
                display.stop()
                break

            elif command == "c":
                show_captions = not show_captions
                print(f"Captions {'enabled' if show_captions else 'disabled'}")

            elif command == "h":
                print_conversation_history = not print_conversation_history
                print(
                    f"Conversation history {'enabled' if print_conversation_history else 'disabled'}"
                )

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

            elif command == "t" or command == "ts":
                # Get the rest of the input as the talk (stream) command
                if len(parts) < 2:
                    print(
                        "❌ Please provide talk (stream) command. Usage: t|ts <text to be spoken>"
                    )
                    continue
                message_text = " ".join(parts[1:])
                try:
                    if command == "t":
                        await session.talk(message_text)
                    elif command == "ts":
                        await session.send_talk_stream(
                            message_text,
                            start_of_speech=True,
                            end_of_speech=True,
                            correlation_id=None,
                        )
                    print(f"✅ Sent talk (stream) command: {message_text}")
                except Exception as e:
                    print(f"❌ Error sending talk (stream) command: {e}")

            elif command == "i":
                try:
                    await session.interrupt()
                    print("✅ Interrupt sent")
                except Exception as e:
                    print(f"❌ Error sending interrupt: {e}")

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
    global show_captions

    # Register connection event handlers
    @client.on(AnamEvent.CONNECTION_ESTABLISHED)
    async def on_connected() -> None:
        print("✅ Connected!")

    @client.on(AnamEvent.CONNECTION_CLOSED)
    async def on_closed(code: str, reason: str | None) -> None:
        global print_conversation_history
        if print_conversation_history:
            print("Conversation transcript:")
            print("=" * 24)
            print(
                "\n".join(
                    [
                        f"{m.role.value.capitalize()}: {m.content}"
                        for m in client.get_message_history()
                    ]
                )
            )
        print(f"Connection closed: {code} - {reason or 'User initiated'}")

    # Register message stream event handlers
    @client.on(AnamEvent.MESSAGE_STREAM_EVENT_RECEIVED)
    async def on_message_stream_event(event) -> None:
        """Handle incremental message stream events."""
        global show_captions
        if show_captions:
            role_emoji = "👤" if event.role == MessageRole.USER else "🤖"
            role_name = "USER" if event.role == MessageRole.USER else "PERSONA"

            if event.content_index == 0:
                # content_index 0 denotes the start of a new message
                print(f"{role_emoji} {role_name}: ", end="", flush=True)
            # Show incremental updates (you can customize this)
            print(f"{event.content}", end="", flush=True)
            if event.end_of_speech:
                # end_of_speech is fired when the message is complete
                status = "✓" if not event.interrupted else "✗ INTERRUPTED"
                print(f"{status}\n")

    @client.on(AnamEvent.MESSAGE_HISTORY_UPDATED)
    async def on_message_history_updated(messages) -> None:
        """Handle message history updates."""
        logger.debug(f"\n📝 Message history updated: {len(messages)} messages total")

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

        # Explicitly close the session to ensure RTCPeerConnection.close() is properly awaited
        if session.is_active:
            try:
                await session.close()
            except Exception as e:
                logger.error(f"Error closing session: {e}")


def main() -> None:
    """Main entry point."""
    # Get configuration from environment variables (loaded from .env file)
    api_key = os.environ.get("ANAM_API_KEY", "").strip().strip('"')
    avatar_id = os.environ.get("ANAM_AVATAR_ID", "").strip().strip('"')
    voice_id = os.environ.get("ANAM_VOICE_ID", "").strip().strip('"')
    avatar_model = os.environ.get("ANAM_AVATAR_MODEL")
    llm_id = os.environ.get("ANAM_LLM_ID", "").strip().strip('"')
    api_base_url = os.environ.get("ANAM_API_BASE_URL", "https://api.anam.ai").strip().strip('"')

    if not api_key or not avatar_id or not voice_id:
        # These are required for an ephemeral persona configuration.
        raise ValueError(
            "Set ANAM_API_KEY, ANAM_AVATAR_ID, ANAM_LLM_ID and ANAM_VOICE_ID environment variables"
        )

    system_prompt = "You are a helpful and creative assistant. Respond in a conversational tone with short sentences and do not use special characters or emojis. Start you first message with 'Hello developer, Welcome to Anam. What can I help you with today?'"

    # Ephemeral persona configuration (using Anam's orchestration: STT, LLM, TTS).
    # Configure components at https://lab.anam.ai (avatars, voices, LLMs).
    #
    # Persona types:
    #   - Ephemeral (avatar_id + voice_id + ...): full control over components at startup.
    #   - Pre-defined (persona_id only): uses a persona from Lab; other params ignored except enable_audio_passthrough.
    #     Simpler for demos; limited for production (source control, adaptivity).
    #
    # Component IDs:
    #   - avatar_id: the "face" (https://lab.anam.ai/avatars). Do not use persona_id as avatar_id.
    #   - voice_id: voice for TTS (https://lab.anam.ai/voices).
    #   - llm_id: LLM for reasoning (https://lab.anam.ai/llms).
    #   - avatar_model: video frame model (e.g. "cara-3").
    #   - system_prompt: primes the LLM. See https://docs.anam.ai/concepts/prompting-guide
    #
    # enable_audio_passthrough:
    #   - False (default): Anam renders TTS; bot audio is added to context and message history.
    #   - True: TTS audio is passed through directly; not added to context/history.
    #   Typically leave False when using Anam's orchestration.
    #
    # LLM options:
    #   - Default LLMs: Anam-provided models when you do not run your own.
    #   - Custom LLMs: Anam connects to your LLM server-to-server. Add LLM and test connection at https://lab.anam.ai/llms.
    #   - CUSTOMER_CLIENT_V1: your LLM is not directly connected. Use MESSAGE_STREAM_EVENT_RECEIVED
    #     to forward messages and send responses via talk stream (or enable_audio_passthrough=True for TTS).
    #     Higher latency; not recommended for production.

    persona_config = PersonaConfig(
        avatar_id=avatar_id,
        voice_id=voice_id,
        llm_id=llm_id,
        avatar_model=avatar_model,
        system_prompt=system_prompt,
        enable_audio_passthrough=False,
    )
    print(f"Using personaConfig: {persona_config}")
    # Create client
    client = AnamClient(
        api_key=api_key,
        persona_config=persona_config,
        options=ClientOptions(api_base_url=api_base_url),
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
