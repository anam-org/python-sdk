"""Create a new chat session"""
import asyncio
from typing import Dict, Optional

from dotenv import dotenv_values
from anam_python_sdk.api.clients import AnamClient
from anam_python_sdk.chat.streaming import StreamingClient


async def main():
    """Main control logic for creating a new chat session"""
    api_cfg: Dict[str, Optional[str]] = dotenv_values(".env")
    anam_client = AnamClient(cfg=api_cfg)

    # Only works with v1 api
    josh = anam_client.get_persona_by_name("josh")[0]
    if josh.id is None:
        raise ValueError("No persona ID found")

    streaming_client = StreamingClient(anam_client, josh.id)
    await streaming_client.start()

    # Keep the connection alive for a while
    await asyncio.sleep(60)

    # Stop the client
    await streaming_client.stop()

    # Previous code
    # doesn't work with v1 api (hardcoded endpoint)
    # session_cfg = client.start_session(josh.id)
    # print(session_cfg)

    # signaling_client = SignallingClient(session_cfg)
    # await signaling_client.connect()


if __name__ == "__main__":
    asyncio.run(main())
