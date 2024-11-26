"""Create a new chat session"""
import asyncio
from typing import Dict, Optional

from dotenv import dotenv_values
from anam_python_sdk.api.client import AnamClient
from anam_python_sdk.api.model import Persona
from anam_python_sdk.chat.streaming import StreamingClient

async def main():
    """Main control logic for creating a new chat session"""
    api_cfg: Dict[str, Optional[str]] = dotenv_values(".env.dev")
    anam_client = AnamClient(cfg=api_cfg)

    # persona_name: str = "cara" # This is for production
    persona_name = "Eva"
    p: Persona = anam_client.get_persona_by_name(f"{persona_name}")[0]
    if p.id is None:
        raise ValueError("No persona ID found")

    streaming_client = StreamingClient(anam_client, p.id)
    await streaming_client.start()
    await streaming_client.send_message("Hello, how are you?")

    # Keep the connection alive for a while
    await asyncio.sleep(60)

    # Stop the client
    await streaming_client.stop()

if __name__ == "__main__":
    asyncio.run(main())
