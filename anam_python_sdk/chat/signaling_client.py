"""Talk with Anam's Websocket Server for Signaling"""
import json
import asyncio
from typing import Callable, Dict, Optional
import logging
import websockets
from enum import Enum, auto

class ActionType(Enum):
    HEARTBEAT = auto()
    ANSWER = auto()
    ICECANDIDATE = auto()
    # Add other action types as needed

class SignallingClient:
    def __init__(self, session_info: dict):
        self.session_info = session_info
        self.websocket_url = self._construct_websocket_url()
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        
        # Add a stream handler if you want to see logs in the console
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        
        self.logger.info(f"Initializing SignallingClient with websocket URL: {self.websocket_url}")
        self.session_id = session_info['sessionId']
        self.heartbeat_interval = session_info['clientConfig']['expectedHeartbeatIntervalSecs']
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.on_open_callback: Optional[Callable] = None
        self.on_message_callback: Optional[Callable] = None

    def _construct_websocket_url(self) -> str:
        engine_protocol = self.session_info['engineProtocol']
        engine_host = self.session_info['engineHost']
        signalling_endpoint = self.session_info['signallingEndpoint']
        session_id = self.session_info['sessionId']
        
        ws_protocol = 'wss:' if engine_protocol == 'https' else 'ws:'
        base_url = f"{ws_protocol}//{engine_host}{signalling_endpoint}"
        return f"{base_url}?session_id={session_id}"

    async def connect(self):
        self.logger.info(f"Attempting to connect to WebSocket: {self.websocket_url}")
        self.ws = await websockets.connect(self.websocket_url)
        self.logger.info("WebSocket connection established")
        await self._on_open()
        await self._handle_messages()

    async def _on_open(self):
        self.logger.info("WebSocket connection opened")
        self._start_heartbeat()
        if self.on_open_callback:
            await self.on_open_callback()

    async def _handle_messages(self):
        try:
            async for message in self.ws:
                self.logger.debug(f"Received message: {message}")
                await self._on_message(message)
        except websockets.exceptions.ConnectionClosed as e:
            self.logger.warning(f"WebSocket connection closed: {e.code} - {e.reason}")
            await self._on_close(e.code, e.reason)

    async def _on_message(self, message):
        if self.on_message_callback:
            self.logger.debug("Calling on_message_callback")
            message_dict = json.loads(message)
            try:
                action_type = ActionType[message_dict['actionType'].upper()]
                message_dict['actionType'] = action_type
            except KeyError:
                self.logger.warning(f"Unknown action type: {message_dict.get('actionType')}")
            await self.on_message_callback(message_dict)

    async def _on_close(self, close_code, close_reason):
        self.logger.info(f"WebSocket connection closed: {close_code} - {close_reason}")

    def _start_heartbeat(self):
        self.heartbeat_task = asyncio.create_task(self._send_heartbeat())

    async def _send_heartbeat(self):
        while True:
            heartbeat_message = {
                "actionType": ActionType.HEARTBEAT.name,
                "sessionId": self.session_id,
                "payload": ""
            }
            self.logger.debug("Sending heartbeat")
            await self.ws.send(json.dumps(heartbeat_message))
            await asyncio.sleep(self.heartbeat_interval)

    async def send_message(self, message: Dict):
        if self.ws and self.ws.open:
            if 'actionType' in message and isinstance(message['actionType'], ActionType):
                message['actionType'] = message['actionType'].name
            self.logger.debug(f"Sending message: {message}")
            await self.ws.send(json.dumps(message))
        else:
            self.logger.warning("WebSocket is not connected. Cannot send message.")

    def set_on_open_callback(self, callback: Callable):
        self.on_open_callback = callback

    def set_on_message_callback(self, callback: Callable):
        self.on_message_callback = callback

# Example usage
async def main():
    session_info = {
        'sessionId': '620a746f-08a8-4f10-8690-f212453cb752',
        'engineHost': 'engine-0-gcp-us-central1-a-gcp-prod-1.engine.anam.ai',
        'engineProtocol': 'https',
        'signallingEndpoint': '/ws',
        'clientConfig': {
            'expectedHeartbeatIntervalSecs': 5,
            # ... other client config ...
        }
    }
    client = SignallingClient(session_info)
    await client.connect()

if __name__ == "__main__":
    asyncio.run(main())
