const signalingUrl = "ws://localhost:8080";
let ws = new WebSocket(signalingUrl);
let pc = new RTCPeerConnection({
    iceServers: [
        { urls: "stun:stun.l.google.com:19302" }  // Sample STUN server
    ]  // Local connection only; no STUN/TURN servers
});

// Setup ICE candidate and track handlers immediately after creating the connection
pc.onicecandidate = event => {
    if (event.candidate) {
        console.log("Subscriber ICE candidate gathered:", event.candidate.candidate);
        ws.send(JSON.stringify({
            to: "publisher",
            candidate: event.candidate
        }));
        console.log("Sent ICE candidate to publisher");
    } else {
        console.log("Subscriber ICE candidate gathering completed.");
    }
};

pc.ontrack = (event) => {
    console.log("ontrack event fired with streams:", event.streams);
    const videoElement = document.getElementById('remoteVideo');
    if (event.streams && event.streams[0]) {
        videoElement.srcObject = event.streams[0];
        console.log("Assigned remote stream to video element.");
    } else {
        console.error("No streams received in ontrack event.");
    }
};

ws.onopen = () => {
    console.log("WebSocket connection opened for Subscriber");
    ws.send(JSON.stringify({ peer_id: "subscriber", origin: "subscriber.js" }));
};

ws.onerror = (error) => {
    console.error("WebSocket error observed:", error);
};

// Handle incoming WebSocket messages
ws.onmessage = async (message) => {
    let data = JSON.parse(message.data);
    
    if (data.type === 'offer') {
        console.log("Received SDP offer from publisher");

        await pc.setRemoteDescription(new RTCSessionDescription(data));
        console.log("Set remote description from publisher");

        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        ws.send(JSON.stringify({
            to: "publisher",
            type: answer.type,
            sdp: answer.sdp
        }));
        console.log("Sent SDP answer to publisher");
    }
    else if (data.candidate) {
        await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
        console.log("Added ICE candidate from publisher");
    }
};

// Check ICE gathering and connection states
pc.onicegatheringstatechange = () => {
    console.log("Subscriber ICE gathering state changed to:", pc.iceGatheringState);
};

pc.oniceconnectionstatechange = () => {
    console.log("Ice Connection state changed to:", pc.iceConnectionState);
};