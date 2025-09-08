"""Create a new chat session"""
import asyncio
from typing import Dict, Optional

from dotenv import load_dotenv
import os
from anam_python_sdk.api.client import AnamClient
from anam_python_sdk.api.model import Persona
from anam_python_sdk.chat.streaming import StreamingClient

async def main():
    """Main control logic for creating a new chat session"""
    load_dotenv(".env")
    api_cfg = {
        "ANAM_API_KEY": os.getenv("ANAM_API_KEY"),
        "ANAM_API_HOST": os.getenv("ANAM_API_HOST", "https://api.anam.ai"),
        "ANAM_API_VERSION": os.getenv("ANAM_API_VERSION", "v1")
    }
    anam_client = AnamClient(cfg=api_cfg)

    # hard coded persona id
    persona_id = "67fb9278-e049-4937-9af1-d771b88b6875"

    streaming_client = StreamingClient(anam_client, persona_id)
    await streaming_client.start()

    # Keep the connection alive for a while
    await asyncio.sleep(60)

    # Stop the client
    await streaming_client.stop()

if __name__ == "__main__":
    asyncio.run(main())
