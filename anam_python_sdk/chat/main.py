"""Create a new chat session"""
import asyncio
from typing import Dict, Optional

from dotenv import dotenv_values
from anam_python_sdk.lab.client import AnamLabClient
from anam_python_sdk.chat.signaling_client import SignallingClient


async def main():
    """Main control logic for creating a new chat session"""
    api_cfg: Dict[str, Optional[str]] = dotenv_values(".env")
    client = AnamLabClient(cfg=api_cfg)

    # Only works with v1 api
    josh = client.get_persona_by_name("josh")[0]
    if josh.id is None:
        raise ValueError("No persona ID found")

    # doesn't work with v1 api (hardcoded endpoint)
    session_cfg = client.start_session(josh.id)
    print(session_cfg)

    signaling_client = SignallingClient(session_cfg)
    await signaling_client.connect()

if __name__ == "__main__":
    asyncio.run(main())
