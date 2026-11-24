import asyncio
from unittest.mock import MagicMock

import pytest

from anam._agent_audio_input_stream import AgentAudioInputStream
from anam._signalling import SignallingClient
from anam.types import AgentAudioInputConfig, AgentAudioInputPayload


@pytest.mark.asyncio
async def test_sequence_numbers_unique_under_concurrency() -> None:
    release = asyncio.Event()
    sent: list[int | str] = []
    signalling = MagicMock(spec=SignallingClient)

    async def send_audio(payload: AgentAudioInputPayload) -> None:
        await release.wait()
        sent.append(payload.sequence_number)

    async def send_end() -> None:
        await release.wait()
        sent.append("end")

    signalling.send_agent_audio_input.side_effect = send_audio
    signalling.send_agent_audio_input_end.side_effect = send_end
    stream = AgentAudioInputStream(AgentAudioInputConfig(), signalling)

    tasks = [asyncio.create_task(stream.send_audio_chunk(b"\x00\x00")) for _ in range(10)]
    tasks.append(asyncio.create_task(stream.end_sequence()))
    tasks += [asyncio.create_task(stream.send_audio_chunk(b"\x00\x00")) for _ in range(2)]
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*tasks)

    chunk_seqs = [
        call.args[0].sequence_number for call in signalling.send_agent_audio_input.call_args_list
    ]
    assert chunk_seqs == list(range(10)) + [0, 1]
    assert stream.get_sequence_number() == 2
