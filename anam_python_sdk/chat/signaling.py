"""Talk with Anam's Websocket Server for Signaling"""
import json
import asyncio
from typing import Callable, Dict, Optional, List
import logging
import websockets
from enum import Enum
import wave
import numpy as np
from aiortc import RTCPeerConnection, MediaStreamTrack
from av import AudioFrame

class ActionType(Enum):
    """Enumeration of action types for signaling."""
    OFFER = 'offer'
    ANSWER = 'answer'
    ICECANDIDATE = 'icecandidate'
    ENDSESSION = 'endsession'
    HEARTBEAT = 'heartbeat'
    WARNING = 'warning'

class SignallingClient:
    """Handles signaling operations for the chat system."""

    def __init__(self, session_info: dict):
        self._validate_session_info(session_info)
        self.session_info = session_info
        self.websocket_url = self._construct_websocket_url()
        self.logger = self._setup_logger()
        self.logger.debug(
            "Initializing SignallingClient with websocket URL: %s", self.websocket_url
        )
        self.session_id = session_info['sessionId']
        self.heartbeat_interval = session_info['clientConfig']['expectedHeartbeatIntervalSecs']
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.on_open_callback: Optional[Callable] = None
        self.on_message_callback: Optional[Callable] = None

    def _validate_session_info(self, session_info: Dict):
        """Validate that all required parameters are present in the session_info dictionary."""
        required_keys = [
            'sessionId',
            'engineHost',
            'engineProtocol',
            'signallingEndpoint',
            'clientConfig',
        ]
        required_client_config_keys = [
            'expectedHeartbeatIntervalSecs',
        ]

        missing_keys = [key for key in required_keys if key not in session_info]
        if missing_keys:
            raise ValueError(f"Missing required keys in session_info: {', '.join(missing_keys)}")

        if 'clientConfig' in session_info:
            missing_client_config_keys = [
                key for key in required_client_config_keys
                if key not in session_info['clientConfig']
            ]
            if missing_client_config_keys:
                raise ValueError(f"Missing required keys in session_info['clientConfig']: {', '.join(missing_client_config_keys)}")
        else:
            raise ValueError("Missing 'clientConfig' in session_info")

    def _construct_websocket_url(self) -> str:
        """Builds a Webscocket URL to connect with a Persona using a session's reponse. 

        Returns:
            str: The websocket URL. 
        """
        engine_protocol = self.session_info['engineProtocol']
        engine_host = self.session_info['engineHost']
        signalling_endpoint = self.session_info['signallingEndpoint']
        session_id = self.session_info['sessionId']
        
        ws_protocol = 'wss:' if engine_protocol == 'https' else 'ws:'
        base_url = f"{ws_protocol}//{engine_host}{signalling_endpoint}"
        return f"{base_url}?session_id={session_id}"

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        
        # Add a stream handler if you want to see logs in the console
        handler = logging.StreamHandler()
        formatter = logging.Formatter('[%(asctime)s %(filename)s:%(lineno)s %(funcName)s] %(levelname)s: %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        return logger

    async def connect(self):
        """Establish a WebSocket connection to the specified URL."""
        self.logger.debug("Attempting to connect to WebSocket: %s", self.websocket_url)
        self.ws = await websockets.connect(self.websocket_url)
        self.logger.debug("WebSocket connection established")
        await self._on_open()
        await self._handle_messages()

    async def _on_open(self):
        self.logger.debug("WebSocket connection opened")
        self._start_heartbeat()
        if self.on_open_callback:
            await self.on_open_callback()

    async def _handle_messages(self):
        if self.ws is None:
            self.logger.error("WebSocket connection is not established")
            return
        try:
            async for message in self.ws:
                self.logger.debug(
                    "Handling message: %s", message
                )
                await self._on_message(message)
        except websockets.exceptions.ConnectionClosed as e:
            self.logger.warning(
                "WebSocket connection closed: %s - %s",
                e.code,
                e
            )
            await self._on_close(e.code, e.reason)

    async def _on_message(self, message):
        """Get ActionType from message (as Enum) and call on_message_callback"""
        if self.on_message_callback:
            self.logger.debug("Calling on_message_callback")
            message_dict = json.loads(message)
            try:
                # Upcast string to Enum
                action_type = ActionType[message_dict['actionType'].upper()]
                message_dict['actionType'] = action_type
            except KeyError:
                self.logger.warning(
                    "Unknown action type: %s",
                    message_dict.get('actionType')
                )
            await self.on_message_callback(message_dict)

    async def _on_close(self, code, reason):
        self.logger.warning("WebSocket connection closed: %s - %s", code, reason)
        await self._reconnect()

    async def _reconnect(self):
        """Attempt to reconnect the WebSocket connection."""
        self.logger.info("Attempting to reconnect WebSocket...")
        retry_interval = 5  # seconds
        while True:
            try:
                await self.connect()
                self.logger.info("Reconnected to WebSocket successfully.")
                break
            except Exception as e:
                self.logger.error("Reconnection failed: %s. Retrying in %d seconds...", e, retry_interval)
                await asyncio.sleep(retry_interval)

    def _start_heartbeat(self):
        self.heartbeat_task = asyncio.create_task(self._send_heartbeat())

    async def _send_heartbeat(self):
        """Continuously send heartbeat messages to the server."""
        while True:
            if self.ws is None or not self.ws.open:
                self.logger.warning("WebSocket is not connected. Cannot send heartbeat.")
                await asyncio.sleep(self.heartbeat_interval)
                continue
            heartbeat_message = {
                "actionType": ActionType.HEARTBEAT.value,
                "sessionId": self.session_id,
                "payload": ""
            }
            self.logger.debug("Sending heartbeat", heartbeat_message)
            await self.ws.send(json.dumps(heartbeat_message))
            await asyncio.sleep(self.heartbeat_interval)

    async def send_message(self, message: Dict):
        """Send a message through the WebSocket connection."""
        if self.ws and self.ws.open:
            if 'actionType' in message and isinstance(message['actionType'], ActionType):
                message['actionType'] = message['actionType'].name
            
            # Skip ICECANDIDATE logs for now.
            if not message['actionType'] == ActionType.ICECANDIDATE.value:
                self.logger.debug("Sent message: %s", message)
            await self.ws.send(json.dumps(message))
        else:
            self.logger.warning("WebSocket is not connected. Cannot send message.")

    def set_on_open_callback(self, callback: Callable):
        """Set the callback function to be called when the connection is opened."""
        self.on_open_callback = callback

    def set_on_message_callback(self, callback: Callable):
        """Set the callback function for handling incoming messages."""
        self.on_message_callback = callback

    async def send_offer(self, offer, user_uid):
        if self.ws is None or not self.ws.open:
            self.logger.error("WebSocket is not connected. Cannot send offer.")
            return

        offer_message_payload = {
            "connectionDescription": {
                "sdp": offer.sdp,
                "type": offer.type
            },
            "userUid": user_uid
        }
        
        offer_msg = {
            "actionType": ActionType.OFFER.value,
            "sessionId": self.session_id,
            "payload": offer_message_payload
        }
        # Already logged in _on_message
        # self.logger.debug("Sending offer message on WebSocket: %s", offer_msg)
        await self.send_message(offer_msg)

# Example usage
async def main():
    """Execute the main asynchronous function."""
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
