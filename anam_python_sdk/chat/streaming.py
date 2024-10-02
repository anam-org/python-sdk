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
import pygame
from aiortc.contrib.media import MediaPlayer

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
        try:
            self.stream = sd.InputStream(device=device_name, channels=1, samplerate=48000, blocksize=960)
            self.stream.start()
        except sd.PortAudioError as e:
            logging.error(f"PortAudio error when initializing device {device_name}: {str(e)}")
            raise

    async def recv(self):
        """Continuously read audio data from the microphone and return it as an audio frame."""
        try:
            frame = av.AudioFrame.from_ndarray(
                self.stream.read(960)[0], format='s16', layout='mono'
            )
            frame.pts = None
            frame.time_base = av.Rational(1, 48000)
            return frame
        except Exception as e:
            logging.error(f"Error reading audio data: {str(e)}")
            raise

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
        self.audio_player = None
        self.video_surface = None
        self.connection_received_answer = False
        self.remote_ice_candidate_buffer = []

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
        self.session_data = self.lab_client.start_session(
            self.persona_id
        )
        if not self.session_data:
            self.logger.error("Failed to start session")
            raise self.SessionStartError("Failed to start session")
        self.logger.info("Session started successfully")
        
        # Initialize the signalling client
        self.signalling_client = SignallingClient(self.session_data)
        
        # Setup RTCPeerConnection once the websocket connection is opened.
        self.signalling_client.set_on_open_callback(
            self.on_signalling_open
        )
        # Setup message handling: different action types.
        self.signalling_client.set_on_message_callback(
            self.on_signalling_message
        )

        # Connect to the signalling server
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
            self.connection_received_answer = True
            await self.flush_remote_ice_candidate_buffer()
        elif action_type == ActionType.ICECANDIDATE:
            candidate = self.create_ice_candidate(message['payload'])
            if self.connection_received_answer:
                await self.peer_connection.addIceCandidate(candidate)
            else:
                self.remote_ice_candidate_buffer.append(candidate)

    async def flush_remote_ice_candidate_buffer(self):
        for candidate in self.remote_ice_candidate_buffer:
            await self.peer_connection.addIceCandidate(candidate)
        self.remote_ice_candidate_buffer.clear()

    def create_ice_candidate(self, payload):
        """
        Create an RTCIceCandidate object from the payload.

        Args:
            payload (Dict): The ICE candidate payload.

        Returns:
            RTCIceCandidate: The created ICE candidate object.
        """
        self.logger.debug("Creating ICE candidate from payload: %s", payload)
        
        candidate_parts = payload['candidate'].split(" ")
        
        candidate = RTCIceCandidate(
            component=int(candidate_parts[1]),
            foundation=candidate_parts[0].split(':')[1],
            ip=candidate_parts[4],
            port=int(candidate_parts[5]),
            priority=int(candidate_parts[3]),
            protocol=candidate_parts[2],
            type=candidate_parts[7],
            sdpMid=payload.get('sdpMid', ''),
            sdpMLineIndex=payload.get('sdpMLineIndex', 0)
        )
        
        self.logger.debug("Created ICE candidate: %s", candidate)
        return candidate

    async def setup_rtc_connection(self):
        self.logger.info("Setting up RTC connection")
        
        # Configure ICE servers (from Anam session data)
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

        # Set up data channel
        # self.data_channel = self.peer_connection.createDataChannel("chat")
        # self.data_channel.on("open", self.on_data_channel_open)
        # self.data_channel.on("message", self.on_data_channel_message)

        # @self.peer_connection.on("datachannel")
        # def on_datachannel(channel):
        #     self.logger.info("Data channel opened")
        #     channel.on("message", self.on_data_channel_message)

        # Set up audio
        audio_success = await self.setup_audio()
        if not audio_success:
            self.logger.warning("Failed to set up audio. Continuing without audio.")

        @self.peer_connection.on("track")
        def on_track(track):
            self.logger.info(f"Received {track.kind} track")
            if track.kind == "audio":
                self.handle_audio_track(track)
            elif track.kind == "video":
                self.handle_video_track(track)

        # Create and send offer
        self.logger.info("Creating and sending offer")
        offer = await self.peer_connection.createOffer()
        
        # Log the SDP for debugging
        self.logger.debug(f"Offer SDP:\n{offer.sdp}")
        await self.peer_connection.setLocalDescription(offer)
        user_uid = self.session_data['sessionId']  # Using sessionId as userUid
        await self.signalling_client.send_offer(
            self.peer_connection.localDescription, 
            user_uid
        )

    async def setup_audio(self):
        try:
            audio_track = AudioStreamTrack(device_name=None)  # Use default device
            self.peer_connection.addTrack(audio_track)
            self.logger.info("Audio track added successfully")
            return True
        except Exception as e:
            self.logger.error(f"Failed to set up audio: {str(e)}")
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
        self.logger.info("Setting remote description with answer: %s", answer_payload)
        answer = RTCSessionDescription(
            sdp=answer_payload['sdp'],
            type=answer_payload['type']
        )
        await self.peer_connection.setRemoteDescription(answer)

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
        if self.audio_player:
            self.audio_player.stop()
        pygame.quit()

    def handle_audio_track(self, track):
        """Set up audio playback for the given track."""
        self.logger.info("Setting up audio playback")
        self.audio_player = MediaPlayer(track)
        self.audio_player.start()

    def handle_video_track(self, track):
        self.logger.info("Setting up video display")
        pygame.init()
        self.video_surface = pygame.display.set_mode((640, 480))
        pygame.display.set_caption("Anam Video Chat")

        async def display_video():
            while True:
                frame = await track.recv()
                if frame:
                    img = frame.to_ndarray(format="bgr24")
                    img = np.rot90(img)
                    surface = pygame.surfarray.make_surface(img)
                    self.video_surface.blit(surface, (0, 0))
                    pygame.display.flip()
                await asyncio.sleep(0.01)

        asyncio.create_task(display_video())

    async def init_peer_connection_and_send_offer(self):
        await self.init_peer_connection()

        if not self.peer_connection:
            self.logger.error("StreamingClient - init_peer_connection_and_send_offer: peer connection is not initialized")
            return

        # Create offer and set local description
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
        await self.signalling_client.send_message(offer_msg)

    async def init_peer_connection(self):
        self.peer_connection = RTCPeerConnection(configuration=RTCConfiguration(iceServers=self.ice_servers))
        
        # Set up event handlers
        @self.peer_connection.on("icecandidate")
        def on_ice_candidate(event):
            if event.candidate:
                self.signalling_client.send_ice_candidate(event.candidate)

        @self.peer_connection.on("track")
        def on_track(track):
            self.handle_track(track)

        # Set up data channel
        self.data_channel = self.peer_connection.createDataChannel("chat")
        self.data_channel.on("open", self.on_data_channel_open)
        self.data_channel.on("message", self.on_data_channel_message)

        # Add transceivers
        self.peer_connection.addTransceiver("video", direction="recvonly")
        self.peer_connection.addTransceiver("audio", direction="sendrecv")

        # Set up audio (if needed)
        await self.setup_audio()