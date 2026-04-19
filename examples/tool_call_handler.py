"""Tool call handler example — client tool with result passback.

This example shows how to:
1. Define a client tool (get_weather) on an ephemeral persona
2. Register a ToolCallHandler to observe tool call lifecycle events
3. Return a result from ``on_start`` — the SDK sends it back to the engine
   over the data channel so the LLM can use it in its response

When the user asks about the weather, the LLM invokes the get_weather
tool. The handler returns a JSON string, which the SDK forwards to the
engine; the LLM then incorporates the result into its spoken reply.

Requirements:
    uv sync --extra display
    # or: pip install opencv-python sounddevice

Usage:
    export ANAM_API_KEY="your-api-key"
    export ANAM_AVATAR_ID="your-avatar-id"
    export ANAM_VOICE_ID="your-voice-id"
    export ANAM_LLM_ID="your-llm-id"
    uv run --extra display python examples/tool_call_handler.py
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from anam import AnamClient, AnamEvent, ClientOptions
from anam.types import (
    ClientToolConfig,
    PersonaConfig,
    ToolCallCompletedPayload,
    ToolCallFailedPayload,
    ToolCallHandler,
    ToolCallStartedPayload,
    ToolParametersConfig,
)

sys.path.insert(0, str(Path(__file__).parent.parent))
from examples.utils import AudioPlayer, VideoDisplay, async_input

load_dotenv()

REQUIRED_ENV_VARS = ["ANAM_API_KEY", "ANAM_AVATAR_ID", "ANAM_VOICE_ID", "ANAM_LLM_ID"]
missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
if missing:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logging.getLogger("anam").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiortc").setLevel(logging.WARNING)
logging.getLogger("aioice").setLevel(logging.WARNING)


class WeatherToolHandler(ToolCallHandler):
    """Handles get_weather tool calls on the client side.

    Logs the tool call lifecycle and returns a dummy weather result.
    In a real application you would call an external API here.
    """

    async def on_start(self, payload: ToolCallStartedPayload) -> str | None:
        city = payload.arguments.get("city", "unknown")
        print(f"\n[get_weather] Started — city={city!r}")
        # Returning a string sends the result back to the engine over the data
        # channel so the LLM can incorporate it into its response. Returning
        # None would acknowledge the call locally without sending a result.
        return json.dumps({
            "city": city,
            "temperature_celsius": 18,
            "condition": "Partly cloudy",
        })

    async def on_complete(self, payload: ToolCallCompletedPayload) -> None:
        print(f"[get_weather] Completed in {payload.execution_time:.0f}ms")

    async def on_fail(self, payload: ToolCallFailedPayload) -> None:
        print(f"[get_weather] Failed: {payload.error_message}")


async def interactive_loop(session, display: VideoDisplay) -> None:
    """Interactive command loop."""
    print("\n" + "=" * 60)
    print("Tool Call Handler Example")
    print("=" * 60)
    print("Commands:")
    print("  m <message>  - Send message (trigger tool calls via LLM)")
    print("  t <text>     - Talk (bypass LLM, direct TTS)")
    print("  i            - Interrupt")
    print("  q            - Quit")
    print()
    print("Try: 'm What is the weather in Dublin?'")
    print("=" * 60 + "\n")

    while True:
        try:
            user_input = await async_input(">> ")
            parts = user_input.strip().split()
            if not parts:
                continue

            command = parts[0].lower()

            if command == "q":
                print("Exiting...")
                display.stop()
                break
            elif command == "m":
                if len(parts) < 2:
                    print("Usage: m <message>")
                    continue
                text = " ".join(parts[1:])
                await session.send_message(text)
                print(f"Sent: {text}")
            elif command == "t":
                if len(parts) < 2:
                    print("Usage: t <text>")
                    continue
                text = " ".join(parts[1:])
                await session.send_talk_stream(text)
                print(f"Talk: {text}")
            elif command == "i":
                await session.interrupt()
                print("Interrupted")
            else:
                print(f"Unknown command: {command}")

        except KeyboardInterrupt:
            print("\nInterrupted by user")
            break
        except Exception as e:
            print(f"Error: {e}")


async def stream_session(
    client: AnamClient,
    display: VideoDisplay,
    audio_player: AudioPlayer,
) -> None:
    """Run the streaming session."""

    @client.on(AnamEvent.CONNECTION_ESTABLISHED)
    async def on_connected() -> None:
        print("Connected!")

    @client.on(AnamEvent.CONNECTION_CLOSED)
    async def on_closed(code: str, reason: str | None) -> None:
        print(f"Connection closed: {code} - {reason or 'User initiated'}")

    @client.on(AnamEvent.MESSAGE_STREAM_EVENT_RECEIVED)
    async def on_message_stream(event) -> None:
        if event.content_index == 0:
            role = "USER" if event.role.value == "user" else "AVATAR"
            print(f"\n[{role}] ", end="", flush=True)
        print(event.content, end="", flush=True)
        if event.end_of_speech:
            print()

    async def consume_video(session) -> None:
        try:
            async for frame in session.video_frames():
                display.update(frame)
        except Exception as e:
            logging.getLogger(__name__).error(f"Video error: {e}")

    async def consume_audio(session) -> None:
        try:
            async for frame in session.audio_frames():
                audio_player.add_frame(frame)
        except Exception as e:
            logging.getLogger(__name__).error(f"Audio error: {e}")

    async with client.connect() as session:
        print(f"Session: {session.session_id}")

        video_task = asyncio.create_task(consume_video(session))
        audio_task = asyncio.create_task(consume_audio(session))
        interactive_task = asyncio.create_task(interactive_loop(session, display))

        while display.is_running():
            if not session.is_active:
                break
            if interactive_task.done():
                break
            await asyncio.sleep(0.1)

        video_task.cancel()
        audio_task.cancel()
        if not interactive_task.done():
            interactive_task.cancel()

        for task in [video_task, audio_task, interactive_task]:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logging.getLogger(__name__).error(f"Task error: {e}")

        if session.is_active:
            try:
                await session.close()
            except Exception as e:
                logging.getLogger(__name__).error(f"Close error: {e}")


def main() -> None:
    api_key = os.environ.get("ANAM_API_KEY", "").strip().strip('"')
    avatar_id = os.environ.get("ANAM_AVATAR_ID", "").strip().strip('"')
    voice_id = os.environ.get("ANAM_VOICE_ID", "").strip().strip('"')
    llm_id = os.environ.get("ANAM_LLM_ID", "").strip().strip('"')
    avatar_model = os.environ.get("ANAM_AVATAR_MODEL")
    api_base_url = os.environ.get("ANAM_API_BASE_URL", "https://api.anam.ai").strip().strip('"')

    # Define the get_weather client tool inline on the ephemeral persona.
    # await_result=True tells the engine to wait for the handler's return value
    # and pass it back to the LLM as the tool result.
    get_weather_tool = ClientToolConfig(
        name="get_weather",
        description="Get the current weather for a city.",
        parameters=ToolParametersConfig(
            properties={
                "city": {
                    "type": "string",
                    "description": "The city to get weather for",
                },
            },
            required=["city"],
        ),
        await_result=True,
        tool_timeout_seconds=10,
    )

    persona_config = PersonaConfig(
        avatar_id=avatar_id,
        voice_id=voice_id,
        llm_id=llm_id,
        avatar_model=avatar_model,
        system_prompt=(
            "You are a helpful assistant. When the user asks about the weather "
            "in a city, use the get_weather tool. Keep responses short."
        ),
        tools=[get_weather_tool],
    )
    print(f"Using persona: avatar={avatar_id}, voice={voice_id}, llm={llm_id}")

    client = AnamClient(
        api_key=api_key,
        persona_config=persona_config,
        options=ClientOptions(api_base_url=api_base_url),
    )

    # Register the handler. When the LLM calls get_weather,
    # WeatherToolHandler.on_start() logs and returns a dummy result.
    client.register_tool_call_handler("get_weather", WeatherToolHandler())
    print("Registered WeatherToolHandler for 'get_weather'")

    display = VideoDisplay()
    audio_player = AudioPlayer()
    audio_player.start()

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
            print(f"Error: {e}")

    thread = threading.Thread(target=run_async, daemon=True)
    thread.start()

    try:
        display.run()
    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        display.stop()
        audio_player.stop()
        if not stream_task.done():
            stream_task.cancel()
        thread.join(timeout=2.0)
        if not loop.is_running():
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except RuntimeError:
                pass
            finally:
                try:
                    if not loop.is_closed():
                        loop.close()
                except RuntimeError:
                    pass


if __name__ == "__main__":
    main()
