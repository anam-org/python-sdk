"""
This module provides a client for managing Anam chat sessions using WebRTC.

Classes:
    AnamChatClient: A client for managing Anam chat sessions using WebRTC.

Exceptions:
    SessionStartError: Exception raised when a session fails to start.
    SessionDataError: Exception raised when session data is not found.
"""

import asyncio
import logging
from typing import Dict, Optional

from aiortc import (
    RTCConfiguration,
    RTCDataChannel,
    RTCIceCandidate,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)

from anam_python_sdk.api.clients import AnamClient
from anam_python_sdk.chat.handlers.audio import AudioHandler, AudioStreamTrack
from anam_python_sdk.chat.handlers.data import DataHandler
from anam_python_sdk.chat.handlers.video import VideoHandler
from anam_python_sdk.chat.signaling import ActionType, SignallingClient


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

        # Logger
        self.logger = self._setup_logger()
        self.logger.debug("Initializing StreamingClient for persona_id: %s", persona_id)

        # WebRTC Connection
        self.signalling_client: Optional[SignallingClient] = None
        self.peer_connection: Optional[RTCPeerConnection] = None
        self.session_data: Optional[Dict] = {}
        self.data_channel: Optional[RTCDataChannel] = None
        self.connection_received_answer = False
        self.remote_ice_candidate_buffer = []
        self.remote_description_set = False

        # Event Handlers
        self.audio_handler = AudioHandler(self.logger)
        self.video_handler = VideoHandler(self.logger)
        self.data_handler = DataHandler(self.logger)

    class SessionStartError(Exception):
        """Exception raised when a session fails to start."""

    class SessionDataError(Exception):
        """Exception raised when session data is not found."""

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        # Add a stream handler if you want to see logs in the console
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "[%(asctime)s %(filename)s:%(lineno)s %(funcName)s] %(levelname)s: %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    async def start(self):
        """
        Start the chat session and initialize the WebRTC connection.

        Raises:
            Exception: If the session fails to start.
        """
        self.logger.debug("Starting chat session")
        self.session_data = self.lab_client.start_session(self.persona_id)
        if not self.session_data:
            self.logger.error("Failed to start session")
            raise self.SessionStartError("Failed to start session")
        self.logger.debug("Session started successfully")

        # Initialize the signalling client
        self.signalling_client = SignallingClient(self.session_data)

        # Setup RTCPeerConnection once the websocket connection is opened.
        self.signalling_client.set_on_open_callback(self.on_signalling_open)
        # Setup message handling: different action types.
        self.signalling_client.set_on_message_callback(self.on_signalling_message)

        # Connect to the signalling server
        await self.signalling_client.connect()

    async def stop(self):
        """Stop the chat session and clean up resources."""
        if self.data_channel:
            self.data_channel.close()

        if self.peer_connection:
            await self.peer_connection.close()
        if self.signalling_client:
            await self.signalling_client.ws.close()

    async def on_signalling_open(self):
        """Callback for when the signalling connection is opened."""
        self.logger.debug("Signalling connection opened")
        await self.setup_rtc_connection()

    async def on_signalling_message(self, message: Dict):
        """
        Handle incoming signalling messages.

        Args:
            message (Dict): The received signalling message.
        """
        action_type = ActionType(message["actionType"])
        self.logger.debug("Received signalling message: %s", action_type)

        if action_type == ActionType.ANSWER:
            await self.handle_answer(message["payload"])
            self.connection_received_answer = True
            await self.flush_remote_ice_candidate_buffer()
        elif action_type == ActionType.ICECANDIDATE:
            candidate = self.create_ice_candidate(message["payload"])
            self.logger.debug(
                "Received new ice candidate with state: %s and candidate: %s",
                self.peer_connection.iceConnectionState,
                candidate,
            )
            if self.connection_received_answer:
                await self.peer_connection.addIceCandidate(candidate)
            else:
                self.remote_ice_candidate_buffer.append(candidate)

    async def flush_remote_ice_candidate_buffer(self):
        """Flush any remaining ICE candidates in the buffer."""
        for candidate in self.remote_ice_candidate_buffer:
            await self.peer_connection.addIceCandidate(candidate)
        self.remote_ice_candidate_buffer.clear()

    # Setup Helpers
    async def setup_rtc_connection(self):
        """
        Set up the RTC (Real-Time Communication) connection.

        This method configures the RTCPeerConnection with ICE servers from the Anam session,
        sets up event handlers for incoming tracks, initializes data, audio, and video channels, creates an offer, and sends it through the signalling client.

        The method performs the following steps:
        1. Configures ICE servers using data from the Anam session object.
        2. Creates an RTCPeerConnection with the configured ICE servers.
        3. Sets up an event handler for incoming tracks (audio and video).
        4. Initializes data, audio, and video channels.
        5. Creates an offer for the peer connection.
        6. Sets the local description of the peer connection.
        7. Sends the offer through the signalling client.

        Raises:
            Warning: If any of the channel setups (data, audio, video) fail.

        Note:
            This method is crucial for establishing the WebRTC connection and should be
            called before any media transmission can occur.
        """
        self.logger.debug("Setting up RTC connection")

        # Configure ICE servers (from Anam session object)
        config = RTCConfiguration()
        config.iceServers = []
        for server in self.session_data.get("clientConfig", {}).get("iceServers", []):
            ice_server = RTCIceServer(
                urls=server["urls"],
                username=server.get("username"),
                credential=server.get("credential"),
            )
            config.iceServers.append(ice_server)

        self.peer_connection = RTCPeerConnection(configuration=config)

        # Log ICE connection state changes
        @self.peer_connection.on("iceconnectionstatechange")
        def on_ice_connection_state_change():
            state = self.peer_connection.iceConnectionState
            self.logger.debug(f"ICE connection state changed: {state}")
            if state == "failed":
                self.logger.error(
                    "ICE connection failed. Check network configuration and ICE server settings."
                )
            elif state == "disconnected":
                self.logger.warning(
                    "ICE connection disconnected. Attempting to reconnect..."
                )
            elif state == "completed":
                self.logger.debug("ICE connection completed successfully.")

        @self.peer_connection.on("track")
        def on_track(track):
            self.logger.debug("Received %s track", track.kind)
            if track.kind == "audio":
                asyncio.create_task(self.audio_handler.handle_avatar_audio(track))
            elif track.kind == "video":
                self.video_handler.handle_video_track(track)

        # Setup Tracks
        # data_success: bool = await self.setup_data_channel()
        audio_success: bool = await self.setup_audio_channel()
        # video_success: bool = await self.setup_video_channel()

        # if not audio_success or not data_success or not video_success:
        #     self.logger.warning(
        #         "Failed to set up tracks. Audio: %s, Data: %s, Video: %s",
        #         str(audio_success),
        #         str(data_success),
        #         str(video_success)
        #     )

        # Create and send offer
        self.logger.debug("Creating and sending offer")
        offer = await self.peer_connection.createOffer()

        # self.logger.debug("Original Offer SDP:\n %s", offer.sdp)

        # Modify the SDP to use a single set of ICE credentials
        new_session_desc = RTCSessionDescription(
            sdp=self.modify_sdp(offer.sdp), type="offer"
        )
        # self.logger.debug("Modified Offer SDP:\n  %s", offer.sdp)
        await self.peer_connection.setLocalDescription(new_session_desc)

        if self.signalling_client and self.session_data:
            # Almost always false 
            # self.logger.debug(
            #     "Local Decription SDP = new  SDP? %s",
            #     self.peer_connection.localDescription.sdp == new_session_desc.sdp,
            # )

            # self.logger.debug("Original Offer: %s", offer.sdp.split('\r\n'))
            # self.logger.debug("Mofified SDP: %s", new_session_desc.sdp.split('\r\n'))
            # self.logger.debug("Current Local Description: %s", self.peer_connection.localDescription.sdp.split("\r\n"))
            await self.signalling_client.send_offer(
                # self.peer_connection.localDescription,
                offer=new_session_desc,
                # Using sessionId as userUid
                user_uid=self.session_data.get("sessionId", ""),
            )
        else:
            self.logger.debug(
                "Cannot send offer: peer connection or session data not available. "
            )

    async def setup_audio_channel(self) -> bool:
        """
        Set up the WebRTC audio channel for sending and receiving audio.

        This method adds an audio transceiver to the peer connection in 'sendrecv' mode,
        allowing the client to send and receive audio.

        Returns:
            bool: True if the audio channel is set up successfully, False otherwise.
        """
        self.logger.debug("Attempting to set up Audio channel")
        if self.peer_connection:

            # Setup track for sending audio
            audio_track = AudioStreamTrack(
                device_name=None, audio_handler=self.audio_handler  # Use default device
            )

            # Automatically send/receive
            self.peer_connection.addTrack(audio_track)
            self.logger.debug("Audio channel added successfully in sendrecv mode")
            return True
        else:
            self.logger.error("Failed to set up audio.")
            return False

    async def setup_video_channel(self) -> bool:
        """
        Set up the WebRTC video channel for receiving video.

        This method adds a video transceiver to the peer connection in 'recvonly' mode,
        allowing the client to receive video from the remote peer but not send any.

        Returns:
            bool: True if the video channel is set up successfully, False otherwise.
        Returns:
            bool: _description_
        """

        if self.peer_connection:
            self.peer_connection.addTransceiver("video", direction="recvonly")
            self.logger.debug("Video channel added successfully")
            return True
        else:
            self.logger.error("Failed to set up video channel.")
            return False

    async def setup_data_channel(self):
        """
        Set up the WebRTC data channel for chat communication.

        This method creates a data channel on the peer connection for sending and receiving chat messages.
        It also sets up event handlers for the on 'open' and 'message' events on the data channel.
        """
        if self.peer_connection:  # Set up data channel
            self.data_channel = self.peer_connection.createDataChannel(
                label="chat", ordered=True
            )
            self.data_channel.on("open", self.data_handler.on_data_channel_open)
            self.data_channel.on("message", self.data_handler.on_data_channel_message)

            @self.peer_connection.on("datachannel")
            def on_datachannel(channel):
                self.logger.debug("Data channel opened")
                channel.on("message", self.data_handler.on_data_channel_message)

            self.logger.debug("Data channel added successfully")
            return True

        self.logger.debug("Data channel cannot be setup, no peer connection. ")
        return False

    # SIGNALING
    async def handle_answer(self, answer_payload: Dict):
        """
        Handle the SDP answer from the remote peer.

        Args:
            answer_payload (Dict): The SDP answer payload.
        """
        if not self.remote_description_set:
            self.logger.debug(
                "Setting remote description with answer: %s", answer_payload
            )
            answer = RTCSessionDescription(
                sdp=answer_payload["sdp"], type=answer_payload["type"]
            )
            await self.peer_connection.setRemoteDescription(answer)
            self.remote_description_set = True
        else:
            self.logger.warning(
                "Remote description already set, ignoring additional answer."
            )

    async def send_message(self, message: str):
        """
        Send a message through the data channel.

        Args:
            message (str): The message to send.
        """
        if self.data_channel and self.data_channel.readyState == "open":
            self.data_channel.send(message)
            self.logger.debug(f"Sent message: {message}")
        else:
            self.logger.warning("Data channel is not open. Message not sent.")

    # Utilities
    def modify_sdp(self, sdp):
        """
        Modify the SDP to ensure consistent ICE credentials across media sections.

        This function only replaces ice-ufrag and ice-pwd in all media sections
        with the values from the first media section that has them.

        Args:
            sdp (str): The original SDP string.

        Returns:
            str: The modified SDP string with consistent ICE credentials.
        """
        lines = sdp.split("\r\n")
        modified_lines = []
        first_ice_ufrag = None
        first_ice_pwd = None
        in_media_section = False

        for line in lines:
            if line.startswith("m="):
                in_media_section = True

            if in_media_section:
                if first_ice_ufrag is None and line.startswith("a=ice-ufrag:"):
                    first_ice_ufrag = line
                elif first_ice_pwd is None and line.startswith("a=ice-pwd:"):
                    first_ice_pwd = line

                if line.startswith("a=ice-ufrag:") and first_ice_ufrag:
                    modified_lines.append(first_ice_ufrag)
                elif line.startswith("a=ice-pwd:") and first_ice_pwd:
                    modified_lines.append(first_ice_pwd)
                else:
                    modified_lines.append(line)
            else:
                modified_lines.append(line)

        if not first_ice_ufrag or not first_ice_pwd:
            self.logger.warning(
                "Could not find ice-ufrag and ice-pwd in any media section."
            )
            return sdp

        modified_sdp = "\r\n".join(modified_lines)
        return modified_sdp

    def create_ice_candidate(self, payload):
        """
        Create an RTCIceCandidate object from the payload.

        Args:
            payload (Dict): The ICE candidate payload.

        Returns:
            RTCIceCandidate: The created ICE candidate object.
        """
        self.logger.debug("Creating ICE candidate from payload: %s", payload)

        candidate_parts = payload["candidate"].split(" ")

        candidate = RTCIceCandidate(
            component=int(candidate_parts[1]),
            foundation=candidate_parts[0].split(":")[1],
            ip=candidate_parts[4],
            port=int(candidate_parts[5]),
            priority=int(candidate_parts[3]),
            protocol=candidate_parts[2],
            type=candidate_parts[7],
            sdpMid=payload.get("sdpMid", ""),
            sdpMLineIndex=payload.get("sdpMLineIndex", 0),
        )

        self.logger.debug("Created ICE candidate: %s", candidate)
        return candidate
