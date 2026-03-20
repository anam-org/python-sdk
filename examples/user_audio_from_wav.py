"""Stream a WAV file as user audio and print speech lifecycle events.

This example sends a local 16-bit PCM WAV file through `session.send_user_audio()`
to exercise the backend's user-audio / VAD path.

Usage:
    export ANAM_API_KEY="your-api-key"
    export ANAM_PERSONA_ID="your-persona-id"
    uv run python examples/user_audio_from_wav.py path/to/input.wav

    # Or use an ephemeral persona:
    export ANAM_API_KEY="your-api-key"
    export ANAM_AVATAR_ID="your-avatar-id"
    export ANAM_VOICE_ID="your-voice-id"
    export ANAM_LLM_ID="your-llm-id"  # optional
    export ANAM_AVATAR_MODEL="cara-3"  # optional
    uv run python examples/user_audio_from_wav.py path/to/input.wav
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import wave
from contextlib import suppress
from pathlib import Path

from dotenv import load_dotenv

from anam import (
    AnamClient,
    AnamEvent,
    ClientOptions,
    MessageRole,
    MessageStreamEvent,
    PersonaConfig,
)
from anam.client import Session

_ = load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

for noisy_logger in ("anam", "websockets", "aiohttp", "aiortc", "aioice"):
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

REQUIRED_ENV_VARS = [
    "ANAM_API_KEY",
    "ANAM_AVATAR_ID",
    "ANAM_LLM_ID",
    "ANAM_VOICE_ID",
]

missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
if missing:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

def _build_persona_config() -> PersonaConfig:
    """Build a persona configuration from environment variables."""
    avatar_id = os.environ.get("ANAM_AVATAR_ID", "").strip().strip('"')
    voice_id = os.environ.get("ANAM_VOICE_ID", "").strip().strip('"')
    llm_id = os.environ.get("ANAM_LLM_ID", "").strip().strip('"')
    avatar_model = os.environ.get("ANAM_AVATAR_MODEL", "").strip().strip('"') or None

    return PersonaConfig(
        avatar_id=avatar_id,
        voice_id=voice_id,
        llm_id=llm_id,
        avatar_model=avatar_model,
        enable_audio_passthrough=False,
    )

def _format_ids(correlation_ids: list[str | None]) -> str:
    """Format correlation IDs for log output."""
    if not correlation_ids:
        return "none"
    return ", ".join("None" if correlation_id is None else correlation_id for correlation_id in correlation_ids)


def _compact_text(text: str) -> str:
    """Collapse message whitespace so console output stays on one line."""
    return " ".join(text.split())


def _append_unique(values: list[str | None], value: str | None) -> None:
    """Append an ID once while preserving order."""
    if value not in values:
        values.append(value)


def _pending_ids(started: list[str | None], observed: list[str | None]) -> list[str | None]:
    """Return correlation IDs that started but have not yet been observed."""
    observed_set = set(observed)
    return [correlation_id for correlation_id in started if correlation_id not in observed_set]


async def _wait_for_pending_ids(
    label: str,
    started: list[str | None],
    observed: list[str | None],
    *,
    timeout: float,
    log: callable,
) -> None:
    """Wait until every started correlation ID appears in the observed list."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        pending = _pending_ids(started, observed)
        if not pending:
            return
        if asyncio.get_running_loop().time() >= deadline:
            await log(f"⚠️ Timed out waiting for {label}: {_format_ids(pending)}")
            return
        await asyncio.sleep(0.05)


async def _stream_wav_file_realtime(
    session: Session,
    wav_path: Path,
    *,
    chunk_duration_ms: int = 20,
) -> tuple[float, int, int]:
    """Send a WAV file as user audio in real time.

    Returns:
        Tuple of duration in seconds, sample rate, and channel count.
    """
    if not wav_path.exists():
        raise FileNotFoundError(f"File not found: {wav_path}")

    with wave.open(str(wav_path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        num_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        total_frames = wav_file.getnframes()

        if sample_width != 2:
            raise ValueError(
                f"Expected 16-bit PCM WAV input (sample width 2), got {sample_width}"
            )
        if num_channels not in (1, 2):
            raise ValueError(f"Expected mono or stereo WAV input, got {num_channels} channels")

        frames_per_chunk = max(1, int(sample_rate * chunk_duration_ms / 1000.0))
        duration_seconds = total_frames / sample_rate

        logger.info(
            "Streaming %s (sample_rate=%dHz, channels=%d, duration=%.2fs)",
            wav_path.name,
            sample_rate,
            num_channels,
            duration_seconds,
        )

        while True:
            chunk = wav_file.readframes(frames_per_chunk)
            if not chunk:
                break

            session.send_user_audio(
                audio_bytes=chunk,
                sample_rate=sample_rate,
                num_channels=num_channels,
            )
            await asyncio.sleep(chunk_duration_ms / 1000.0)

    return duration_seconds, sample_rate, num_channels


async def _send_silence_until_cancelled(
    session: Session,
    *,
    sample_rate: int,
    num_channels: int,
    chunk_duration_ms: int = 20,
) -> None:
    """Keep sending silent audio chunks until the task is cancelled."""
    frames_per_chunk = max(1, int(sample_rate * chunk_duration_ms / 1000.0))
    silence_chunk = b"\x00" * frames_per_chunk * 2 * num_channels

    try:
        while True:
            session.send_user_audio(
                audio_bytes=silence_chunk,
                sample_rate=sample_rate,
                num_channels=num_channels,
            )
            await asyncio.sleep(chunk_duration_ms / 1000.0)
    except asyncio.CancelledError:
        return


async def main() -> None:
    api_key = os.environ.get("ANAM_API_KEY", "").strip().strip('"')
    api_base_url = os.environ.get("ANAM_API_BASE_URL", "https://api.anam.ai").strip().strip('"')
    wav_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("input.wav")

    if not api_key:
        raise ValueError("Set ANAM_API_KEY environment variable")

    client = AnamClient(
        api_key=api_key,
        persona_config=_build_persona_config(),
        options=ClientOptions(api_base_url=api_base_url),
    )

    session_ready = asyncio.Event()
    first_user_speech_started = asyncio.Event()
    assistant_response_received = asyncio.Event()
    started_ids: list[str | None] = []
    ended_ids: list[str | None] = []
    transcript_ids: list[str | None] = []
    message_buffers: dict[str, str] = {}
    console_lock = asyncio.Lock()

    async def log(message: str) -> None:
        async with console_lock:
            print(message, flush=True)

    @client.on(AnamEvent.CONNECTION_ESTABLISHED)
    async def on_connected() -> None:
        await log("✅ Connected")

    @client.on(AnamEvent.SESSION_READY)
    async def on_session_ready() -> None:
        session_ready.set()
        await log("✅ Session ready for user audio")

    @client.on(AnamEvent.CONNECTION_CLOSED)
    async def on_closed(code: str, reason: str | None) -> None:
        await log(f"Connection closed: {code} - {reason or 'No reason'}")

    @client.on(AnamEvent.USER_SPEECH_STARTED)
    async def on_user_speech_started(correlation_id: str | None) -> None:
        _append_unique(started_ids, correlation_id)
        first_user_speech_started.set()
        await log(f"🎙️ USER_SPEECH_STARTED: {correlation_id}")

    @client.on(AnamEvent.USER_SPEECH_ENDED)
    async def on_user_speech_ended(correlation_id: str | None) -> None:
        _append_unique(ended_ids, correlation_id)
        await log(f"🛑 USER_SPEECH_ENDED: {correlation_id}")

    @client.on(AnamEvent.MESSAGE_STREAM_EVENT_RECEIVED)
    async def on_message_stream_event(event: MessageStreamEvent) -> None:
        message_buffers[event.id] = message_buffers.get(event.id, "") + event.content
        if not event.end_of_speech:
            return

        message = _compact_text(message_buffers.pop(event.id, ""))
        status = "interrupted" if event.interrupted else "complete"
        role = "user" if event.role == MessageRole.USER else "assistant"
        role_emoji = "👤" if event.role == MessageRole.USER else "🤖"
        status_emoji = "✗" if event.interrupted else "✓"
        await log(f"{role_emoji} {role} [{event.correlation_id}] ({status_emoji} {status}): {message}")

        if event.role == MessageRole.USER:
            _append_unique(transcript_ids, event.correlation_id)
        else:
            assistant_response_received.set()

    session: Session | None = None
    try:
        session = await client.connect_async()
        await log(f"Session: {session.session_id}")

        try:
            await asyncio.wait_for(session_ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            await log("Timed out waiting for SESSION_READY. Continuing anyway.")

        streaming_client = client._streaming_client
        if streaming_client and await streaming_client.wait_for_data_channel(timeout=10.0):
            await log("✅ Data channel ready")
        else:
            await log("⚠️ Data channel did not open before streaming")

        await log("⏳ Waiting 2 seconds before sending user audio...")
        await asyncio.sleep(2.0)

        wav_duration, sample_rate, num_channels = await _stream_wav_file_realtime(session, wav_path)
        await log(f"📤 Finished sending {wav_duration:.2f}s of WAV input. Sending trailing silence...")

        silence_task = asyncio.create_task(
            _send_silence_until_cancelled(
                session,
                sample_rate=sample_rate,
                num_channels=num_channels,
            )
        )
        try:
            try:
                await asyncio.wait_for(first_user_speech_started.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                await log("⚠️ Timed out waiting for USER_SPEECH_STARTED")

            await _wait_for_pending_ids(
                "USER_SPEECH_ENDED",
                started_ids,
                ended_ids,
                timeout=20.0,
                log=log,
            )
            await _wait_for_pending_ids(
                "final user transcripts",
                started_ids,
                transcript_ids,
                timeout=20.0,
                log=log,
            )
            if not assistant_response_received.is_set():
                try:
                    await asyncio.wait_for(assistant_response_received.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    await log("⚠️ Timed out waiting for assistant output")
        finally:
            silence_task.cancel()
            with suppress(asyncio.CancelledError):
                await silence_task
    finally:
        if session and session.is_active:
            try:
                await asyncio.wait_for(session.close(), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                await log("⚠️ Timed out closing session cleanly. Exiting anyway.")


if __name__ == "__main__":
    asyncio.run(main())
