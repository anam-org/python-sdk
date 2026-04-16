"""Test app for tool call lifecycle events.

Tests the ToolCallHandler registration and lifecycle callbacks
(on_start, on_complete, on_fail) with both client and server tools.

Supports either a pre-defined persona (ANAM_PERSONA_ID) or an ephemeral
persona (ANAM_AVATAR_ID + ANAM_VOICE_ID + ANAM_LLM_ID). For ephemeral
personas, tools can be defined inline via ANAM_TOOLS (JSON) or referenced
by ID via ANAM_TOOL_IDS.

Requirements:
    uv sync --extra display
    # or: pip install opencv-python sounddevice

Usage:
    # With persona_id (tools configured in Lab):
    export ANAM_API_KEY="your-api-key"
    export ANAM_PERSONA_ID="your-persona-id"
    uv run --extra display python scripts/tool_call_test.py

    # With ephemeral persona + inline tools:
    export ANAM_API_KEY="your-api-key"
    export ANAM_AVATAR_ID="your-avatar-id"
    export ANAM_VOICE_ID="your-voice-id"
    export ANAM_LLM_ID="your-llm-id"
    export ANAM_TOOLS='[{"name":"get_weather","description":"Get weather for a city","parameters":{"properties":{"city":{"type":"string"}},"required":["city"]}}]'
    export ANAM_TOOL_HANDLERS="auto:get_weather=sunny"
    uv run --extra display python scripts/tool_call_test.py

    # With ephemeral persona + pre-created tool IDs:
    export ANAM_TOOL_IDS="uuid-1,uuid-2"
    uv run --extra display python scripts/tool_call_test.py
"""

import asyncio
import json
import logging
import os
import sys
from collections.abc import Callable
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

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
)
logging.getLogger("anam").setLevel(logging.DEBUG)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiortc").setLevel(logging.WARNING)
logging.getLogger("aioice").setLevel(logging.WARNING)


# --- Tool Call Handlers ---


class LoggingHandler(ToolCallHandler):
    """Logs all tool call lifecycle events. Works for any tool type."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def on_start(self, payload: ToolCallStartedPayload) -> str | None:
        print(f"\n{'='*60}")
        print(f"TOOL STARTED: {self.name}")
        print(f"  tool_call_id: {payload.tool_call_id}")
        print(f"  tool_type:    {payload.tool_type}")
        print(f"  tool_subtype: {payload.tool_subtype}")
        print(f"  arguments:    {json.dumps(payload.arguments, indent=2)}")
        print(f"  timestamp:    {payload.timestamp}")
        print(f"{'='*60}")
        return None  # Don't auto-complete

    async def on_complete(self, payload: ToolCallCompletedPayload) -> None:
        print(f"\n{'='*60}")
        print(f"TOOL COMPLETED: {self.name}")
        print(f"  tool_call_id:   {payload.tool_call_id}")
        print(f"  result:         {payload.result}")
        print(f"  execution_time: {payload.execution_time:.1f}ms")
        print(f"  docs_accessed:  {payload.documents_accessed}")
        print(f"  timestamp:      {payload.timestamp}")
        print(f"{'='*60}")

    async def on_fail(self, payload: ToolCallFailedPayload) -> None:
        print(f"\n{'='*60}")
        print(f"TOOL FAILED: {self.name}")
        print(f"  tool_call_id:   {payload.tool_call_id}")
        print(f"  error_message:  {payload.error_message}")
        print(f"  execution_time: {payload.execution_time:.1f}ms")
        print(f"  timestamp:      {payload.timestamp}")
        print(f"{'='*60}")


class AutoCompleteHandler(ToolCallHandler):
    """Dummy handler that forces a TOOL_CALL_COMPLETED event for testing.

    For client tools only. Returning a string from on_start causes the SDK's
    ToolCallManager to immediately emit a local TOOL_CALL_COMPLETED event
    with that string as the result. This lets you verify the completed
    lifecycle path without needing a real tool implementation or engine
    round-trip.
    """

    def __init__(self, name: str, result: str) -> None:
        self.name = name
        self.result = result

    async def on_start(self, payload: ToolCallStartedPayload) -> str | None:
        print(f"\n  AUTO-COMPLETE [{self.name}]: returning '{self.result}'")
        print(f"    arguments: {json.dumps(payload.arguments, indent=2)}")
        return self.result

    async def on_complete(self, payload: ToolCallCompletedPayload) -> None:
        print(f"  AUTO-COMPLETE [{self.name}]: completed in {payload.execution_time:.1f}ms")
        print(f"    result: {payload.result}")


class AutoFailHandler(ToolCallHandler):
    """Dummy handler that forces a TOOL_CALL_FAILED event for testing.

    For client tools only. Raising an exception from on_start causes the
    SDK's ToolCallManager to immediately emit a local TOOL_CALL_FAILED event
    with the exception message as the error. This lets you verify the failure
    lifecycle path without needing a real tool error.
    """

    def __init__(self, name: str, error: str) -> None:
        self.name = name
        self.error = error

    async def on_start(self, _payload: ToolCallStartedPayload) -> str | None:
        print(f"\n  AUTO-FAIL [{self.name}]: raising error '{self.error}'")
        raise RuntimeError(self.error)

    async def on_fail(self, payload: ToolCallFailedPayload) -> None:
        print(f"  AUTO-FAIL [{self.name}]: failed in {payload.execution_time:.1f}ms")
        print(f"    error: {payload.error_message}")


class OnStartOnlyHandler(ToolCallHandler):
    """Dummy handler that only implements on_start (tests partial implementation).

    Verifies that the SDK handles handlers with missing on_complete/on_fail
    methods gracefully. Returns None so no auto-complete is triggered.
    """

    async def on_start(self, payload: ToolCallStartedPayload) -> str | None:
        print(f"\n  ON_START_ONLY: {payload.tool_name} started")
        return None


# Track registered handlers for unsubscribe testing
_unsubscribers: dict[str, Callable[[], None]] = {}


def register_handlers(client: AnamClient, tool_names: list[str]) -> None:
    """Register handlers based on user input.

    By default, registers a LoggingHandler for each tool name provided.
    Special prefixes change behavior:
      - "auto:<name>=<result>" -> AutoCompleteHandler
      - "fail:<name>=<error>" -> AutoFailHandler
      - "partial:<name>" -> OnStartOnlyHandler
    """
    for spec in tool_names:
        if spec.startswith("auto:"):
            # auto:tool_name=result_string
            rest = spec[5:]
            name, _, result = rest.partition("=")
            handler = AutoCompleteHandler(name, result or "ok")
            unsub = client.register_tool_call_handler(name, handler)
            _unsubscribers[name] = unsub
            print(f"  Registered AutoCompleteHandler for '{name}' -> '{result or 'ok'}'")
        elif spec.startswith("fail:"):
            rest = spec[5:]
            name, _, error = rest.partition("=")
            handler = AutoFailHandler(name, error or "handler error")
            unsub = client.register_tool_call_handler(name, handler)
            _unsubscribers[name] = unsub
            print(f"  Registered AutoFailHandler for '{name}' -> '{error or 'handler error'}'")
        elif spec.startswith("partial:"):
            name = spec[8:]
            handler = OnStartOnlyHandler()
            unsub = client.register_tool_call_handler(name, handler)
            _unsubscribers[name] = unsub
            print(f"  Registered OnStartOnlyHandler for '{name}'")
        else:
            handler = LoggingHandler(spec)
            unsub = client.register_tool_call_handler(spec, handler)
            _unsubscribers[spec] = unsub
            print(f"  Registered LoggingHandler for '{spec}'")


async def interactive_loop(session, client: AnamClient, display: VideoDisplay) -> None:
    """Interactive command loop with tool-testing commands."""
    print("\n" + "=" * 60)
    print("Tool Call Test App")
    print("=" * 60)
    print("Commands:")
    print("  m <message>               - Send message (trigger tool calls via LLM)")
    print("  t <text>                  - Talk (bypass LLM, direct TTS)")
    print("  i                         - Interrupt")
    print("  reg <tool1> [tool2] ...   - Register LoggingHandler(s)")
    print("  reg auto:<name>=<result>  - Register AutoCompleteHandler (client tools)")
    print("  reg fail:<name>=<error>   - Register AutoFailHandler (client tools)")
    print("  reg partial:<name>        - Register OnStartOnlyHandler")
    print("  unreg <name>              - Unsubscribe handler")
    print("  handlers                  - List registered handlers")
    print("  q                         - Quit")
    print("=" * 60)
    print("\nTip: Send a message that triggers a tool call to test the handlers.")
    print("     e.g., 'm What is the weather in Dublin?' (if weather tool is configured)\n")

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
                try:
                    await session.send_message(text)
                    print(f"Sent: {text}")
                except Exception as e:
                    print(f"Error: {e}")

            elif command == "t":
                if len(parts) < 2:
                    print("Usage: t <text>")
                    continue
                text = " ".join(parts[1:])
                try:
                    await session.send_talk_stream(text)
                    print(f"Talk: {text}")
                except Exception as e:
                    print(f"Error: {e}")

            elif command == "i":
                try:
                    await session.interrupt()
                    print("Interrupted")
                except Exception as e:
                    print(f"Error: {e}")

            elif command == "reg":
                if len(parts) < 2:
                    print("Usage: reg <tool_name> [tool_name2] ...")
                    print("       reg auto:<name>=<result>")
                    print("       reg fail:<name>=<error>")
                    print("       reg partial:<name>")
                    continue
                register_handlers(client, parts[1:])

            elif command == "unreg":
                if len(parts) < 2:
                    print("Usage: unreg <tool_name>")
                    continue
                name = parts[1]
                if name in _unsubscribers:
                    _unsubscribers[name]()
                    del _unsubscribers[name]
                    print(f"Unsubscribed handler for '{name}'")
                else:
                    print(f"No handler registered for '{name}'")

            elif command == "handlers":
                if _unsubscribers:
                    print("Registered handlers:")
                    for name in _unsubscribers:
                        print(f"  - {name}")
                else:
                    print("No handlers registered")

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

    # Tool call event listeners (in addition to handlers, these fire for ALL tools)
    @client.on(AnamEvent.TOOL_CALL_STARTED)
    async def on_tool_started(payload: ToolCallStartedPayload) -> None:
        print(f"\n[EVENT] TOOL_CALL_STARTED: {payload.tool_name} ({payload.tool_type})")

    @client.on(AnamEvent.TOOL_CALL_COMPLETED)
    async def on_tool_completed(payload: ToolCallCompletedPayload) -> None:
        print(
            f"[EVENT] TOOL_CALL_COMPLETED: {payload.tool_name} "
            f"({payload.execution_time:.1f}ms)"
        )

    @client.on(AnamEvent.TOOL_CALL_FAILED)
    async def on_tool_failed(payload: ToolCallFailedPayload) -> None:
        print(
            f"[EVENT] TOOL_CALL_FAILED: {payload.tool_name} - {payload.error_message} "
            f"({payload.execution_time:.1f}ms)"
        )

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
        interactive_task = asyncio.create_task(interactive_loop(session, client, display))

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
    persona_id = os.environ.get("ANAM_PERSONA_ID", "").strip().strip('"')
    api_base_url = os.environ.get("ANAM_API_BASE_URL", "https://api.anam.ai").strip().strip('"')

    # Support either persona_id or ephemeral config
    avatar_id = os.environ.get("ANAM_AVATAR_ID", "").strip().strip('"')
    voice_id = os.environ.get("ANAM_VOICE_ID", "").strip().strip('"')
    llm_id = os.environ.get("ANAM_LLM_ID", "").strip().strip('"')

    if not api_key:
        raise ValueError("Set ANAM_API_KEY environment variable")

    # Default client tool used when no ANAM_TOOLS or ANAM_TOOL_IDS are set
    default_tool = ClientToolConfig(
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
    )

    # Parse inline tool definitions from ANAM_TOOLS env var (JSON array)
    # Example: ANAM_TOOLS='[{"name":"get_weather","description":"Get weather","parameters":{"properties":{"city":{"type":"string"}},"required":["city"]}}]'
    tools = None
    tools_json = os.environ.get("ANAM_TOOLS", "").strip()
    if tools_json and not persona_id:
        raw_tools = json.loads(tools_json)
        tools = []
        for t in raw_tools:
            params = None
            if t.get("parameters"):
                params = ToolParametersConfig(
                    properties=t["parameters"]["properties"],
                    required=t["parameters"].get("required"),
                )
            tools.append(ClientToolConfig(
                name=t["name"],
                description=t["description"],
                parameters=params,
            ))
        print(f"Inline tools: {[t.name for t in tools]}")

    # Parse pre-created tool IDs from ANAM_TOOL_IDS env var (comma-separated)
    tool_ids = None
    tool_ids_raw = os.environ.get("ANAM_TOOL_IDS", "").strip()
    if tool_ids_raw:
        tool_ids = [s.strip() for s in tool_ids_raw.split(",") if s.strip()]
        print(f"Tool IDs: {tool_ids}")

    # Fall back to default get_weather tool if nothing else is configured
    if not tools and not tool_ids and not persona_id:
        tools = [default_tool]
        print(f"Using default tool: {default_tool.name}")

    if persona_id:
        print(f"Using persona_id: {persona_id}")
        client = AnamClient(
            api_key=api_key,
            persona_id=persona_id,
            options=ClientOptions(api_base_url=api_base_url),
        )
    elif avatar_id and voice_id and llm_id:
        persona_config = PersonaConfig(
            avatar_id=avatar_id,
            voice_id=voice_id,
            llm_id=llm_id,
            system_prompt=(
                "You are a helpful assistant. When the user asks you to do something "
                "that could involve a tool, use the appropriate tool. Keep responses short."
            ),
            tools=tools,
            tool_ids=tool_ids,
        )
        print(f"Using ephemeral persona: avatar={avatar_id}, voice={voice_id}, llm={llm_id}")
        client = AnamClient(
            api_key=api_key,
            persona_config=persona_config,
            options=ClientOptions(api_base_url=api_base_url),
        )
    else:
        raise ValueError(
            "Set either ANAM_PERSONA_ID, or all of ANAM_AVATAR_ID + ANAM_VOICE_ID + ANAM_LLM_ID"
        )

    # Pre-register handlers from env var (comma-separated tool specs)
    tool_specs = os.environ.get("ANAM_TOOL_HANDLERS", "").strip()
    if tool_specs:
        print("Pre-registering tool handlers from ANAM_TOOL_HANDLERS:")
        register_handlers(client, [s.strip() for s in tool_specs.split(",") if s.strip()])

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
