"""
This module provides a client for managing Anam chat sessions using WebRTC.

Classes:
    AnamChatClient: A client for managing Anam chat sessions using WebRTC.
    AudioStreamTrack: A custom audio stream track for handling audio data.

Exceptions:
    SessionStartError: Exception raised when a session fails to start.
    SessionDataError: Exception raised when session data is not found.
"""
import asyncio
import logging
from typing import Dict, Optional
import sounddevice as sd
import numpy as np

import av
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceCandidate,
    RTCDataChannel,
    RTCConfiguration,
    RTCIceServer
)
from aiortc.contrib.media import MediaRecorder, MediaStreamTrack
from anam_python_sdk.api.clients import AnamClient
from anam_python_sdk.chat.signaling import SignallingClient, ActionType


class AudioStreamTrack(MediaStreamTrack):
    """Audio stream track for handling audio data."""
    kind = "audio"

    def __init__(self, device_name):
        super().__init__()
        self.device_name = device_name
        self.stream = sd.InputStream(device=device_name, channels=1, samplerate=48000)
        self.stream.start()

    async def recv(self):
        """Continuously read audio data from the microphone and return it as an audio frame."""
        frame = av.AudioFrame.from_ndarray(
            self.stream.read(960)[0], format='s16', layout='mono'
        )
        frame.pts = None
        frame.time_base = av.Rational(1, 48000)
        return frame
        
class StreamingClient:
    """
    A client for managing Anam chat sessions using WebRTC.

    This class handles the setup and management of a WebRTC connection
    for audio and video communication with an Anam persona.
    """

    def __init__(self, lab_client: AnamClient, persona_id: str):
        """
        Initialize the StreamingClient.

        Args:
            lab_client (AnamClient): The Anam client for starting sessions.
            persona_id (str): The ID of the persona to chat with.
        """
        self.lab_client = lab_client
        self.persona_id = persona_id
        self.signalling_client: Optional[SignallingClient] = None
        self.peer_connection: Optional[RTCPeerConnection] = None
        self.session_data: Optional[Dict] = {}
        self.audio_recorder: Optional[MediaRecorder] = None
        self.video_recorder: Optional[MediaRecorder] = None
        self.data_channel: Optional[RTCDataChannel] = None
        self.logger = self._setup_logger()
        self.logger.info("Initializing AnamChatClient for persona_id: %s", persona_id)

    class SessionStartError(Exception):
        """Exception raised when a session fails to start."""

    class SessionDataError(Exception):
        """Exception raised when session data is not found."""

    class AudioSetupError(Exception):
        """Exception raised when audio setup fails."""

    class AudioTrackCreationError(Exception):
        """Exception raised when an AudioTrack cannot be created."""
        pass

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        # Add a stream handler if you want to see logs in the console
        handler = logging.StreamHandler()
        formatter = logging.Formatter('[%(asctime)s %(filename)s:%(lineno)s %(funcName)s] %(levelname)s: %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    async def start(self):
        """
        Start the chat session and initialize the WebRTC connection.

        Raises:
            Exception: If the session fails to start.
        """
        self.logger.info("Starting chat session")
        self.session_data = self.lab_client.start_session(self.persona_id)
        if not self.session_data:
            self.logger.error("Failed to start session")
            raise self.SessionStartError("Failed to start session")
        self.logger.info("Session started successfully")
        self.signalling_client = SignallingClient(self.session_data)
        self.signalling_client.set_on_open_callback(self.on_signalling_open)
        self.signalling_client.set_on_message_callback(self.on_signalling_message)

        await self.signalling_client.connect()

    async def on_signalling_open(self):
        """Callback for when the signalling connection is opened."""
        self.logger.info("Signalling connection opened")
        await self.setup_rtc_connection()

    async def on_signalling_message(self, message: Dict):
        """
        Handle incoming signalling messages.

        Args:
            message (Dict): The received signalling message.
        """
        action_type = ActionType(message['actionType'])
        self.logger.debug("Received signalling message: %s", action_type)

        if action_type == ActionType.ANSWER:
            await self.handle_answer(message['payload'])
        elif action_type == ActionType.ICECANDIDATE:
            await self.handle_ice_candidate(message['payload'])

    async def setup_rtc_connection(self):
        """Set up the WebRTC peer connection and media tracks."""
        self.logger.info("Setting up RTC connection")
        
        # Ensure that iceServers is correctly accessed from the session_data
        config = RTCConfiguration()
        config.iceServers = []
        for server in self.session_data.get('clientConfig', {}).get('iceServers', []):
            ice_server = RTCIceServer(
                urls=server['urls'],
                username=server.get('username'),
                credential=server.get('credential')
            )
            config.iceServers.append(ice_server)

        self.peer_connection = RTCPeerConnection(configuration=config)

        # # Set up data channel first
        self.data_channel = self.peer_connection.createDataChannel("chat")
        self.data_channel.on("open", self.on_data_channel_open)
        self.data_channel.on("message", self.on_data_channel_message)

        @self.peer_connection.on("datachannel")
        def on_datachannel(channel):
            self.logger.info("Data channel opened")
            channel.on("message", self.on_data_channel_message)

        # # Attempt to set up audio
        # audio_added = False
        # try:
        #     audio_added = await self.setup_audio()
        # except self.AudioSetupError:
        #     self.logger.error("Error setting up audio.")

        # if not audio_added:
        #     self.logger.warning("No audio input available. Proceeding with data channel only.")

        # Create and send offer
        self.logger.info("Creating and sending offer")
        offer_msg = await self.peer_connection.createOffer()
        await self.peer_connection.setLocalDescription(offer_msg)
        
        offer_message_payload = {
            "connectionDescription": {
                "sdp": offer_msg.sdp,
                "type": offer_msg.type
            },
            "userUid": self.session_data['sessionId']  # Note: This is using sessionId as userUid
        }
        offer_msg = {
            "actionType": ActionType.OFFER.value,
            "sessionId": self.session_data['sessionId'],
            "payload": offer_message_payload
        }
        self.logger.info("Sending offer message on Websocket: %s", offer_msg)
        await self.signalling_client.send_message(offer_msg)

    async def setup_audio(self):
        """Set up audio input."""
        devices = sd.query_devices()
        input_devices = [d for d in devices if d['max_input_channels'] > 0]
        
        if not input_devices:
            self.logger.error("No audio input devices found.")
            return False

        self.logger.info("Available audio input devices:")
        for i, device in enumerate(input_devices):
            self.logger.info("%d: %s", i, device['name'])

        # Automatically select the first audio device
        if input_devices:
            selected_device = input_devices[1]
            self.logger.info("Automatically selected audio device: %s", selected_device['name'])
        else:
            self.logger.warning("No audio input devices found.")
            return False

        # Create audio track
        try:
            audio_track = AudioStreamTrack(selected_device['name'])
            self.peer_connection.addTrack(audio_track)
            return True
        except Exception as e:
            # raise self.AudioTrackCreationError(f"Failed to create audio track: {str(e)}")
            self.logger.error("Error creating audio track: %s", e)
            return False

    def on_data_channel_open(self):
        """Callback for when the data channel is opened."""
        self.logger.info("Data channel opened")

    def on_data_channel_message(self, message):
        """
        Handle incoming messages on the data channel.

        Args:
            message: The received message.
        """
        self.logger.info("Received message: %s", message)
        
        # Here you can add custom logic to handle incoming messages

    async def handle_answer(self, answer_payload: Dict):
        """
        Handle the SDP answer from the remote peer.

        Args:
            answer_payload (Dict): The SDP answer payload.
        """
        answer = RTCSessionDescription(
            sdp=answer_payload['sdp'],
            type=answer_payload['type']
        )
        await self.peer_connection.setRemoteDescription(answer)

    async def handle_ice_candidate(self, candidate_payload: Dict):
        """
        Handle ICE candidates received from the remote peer.

        Args:
            candidate_payload (Dict): The ICE candidate payload.
        """
        self.logger.debug("Handling ICE candidate: %s", candidate_payload)
        
        # The Candidate answer is a spaced-string that needs to split and mapped 
        # to the RTCIceCandidate properties
        candidate_property_index = {
            1: 'component',
            2: 'protocol',
            3: 'priority',
            4: 'ip',
            5: 'port',
            6: 'foundation',
            7: 'type'
        }
        # Parse the candidate string
        candidate_parts = candidate_payload['candidate'].split(" ")
        # Log the candidate's untangled parts
        for i, v in candidate_property_index.items():
            self.logger.debug("Candidate part %s (%d): %s", v, i, candidate_parts[i])
        
        candidate = RTCIceCandidate(
            component=int(candidate_parts[1]),
            foundation=candidate_parts[0].split(':')[1],
            ip=candidate_parts[4],
            port=int(candidate_parts[5]),
            priority=int(candidate_parts[3]),
            protocol=candidate_parts[2],
            type=candidate_parts[7],
            sdpMid=candidate_payload.get('sdpMid', ''),
            sdpMLineIndex=candidate_payload.get('sdpMLineIndex', 0)
        )
        
        self.logger.info("Adding ICE candidate to peer connection: %s", candidate)
        await self.peer_connection.addIceCandidate(candidate)
        self.logger.info("ICE candidate added to peer connection.")

    async def send_message(self, message: str):
        """
        Send a message through the data channel.

        Args:
            message (str): The message to send.
        """
        if self.data_channel and self.data_channel.readyState == "open":
            self.data_channel.send(message)
            self.logger.info(f"Sent message: {message}")
        else:
            self.logger.warning("Data channel is not open. Message not sent.")

    async def stop(self):
        """Stop the chat session and clean up resources."""
        if self.audio_recorder:
            await self.audio_recorder.stop()
        if self.video_recorder:
            await self.video_recorder.stop()
        if self.peer_connection:
            await self.peer_connection.close()
        if self.signalling_client:
            await self.signalling_client.ws.close()
        if self.data_channel:
            self.data_channel.close()
