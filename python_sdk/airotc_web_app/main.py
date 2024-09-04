"""The entry point of your application"""
import asyncio
import logging
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiohttp import web
import json

logging.basicConfig(level=logging.INFO)

async def index(request):
    content = open('templates/index.html', 'r').read()
    print(content)
    return web.Response(
        content_type='text/html', text=content
    )

app = web.Application()
app.router.add_get('/', index)

async def main():
    # Create a WebRTC peer connection
    pc = RTCPeerConnection()

    # Define the signaling mechanism
    async def offer(request):
        params = await request.json()

        # Handles the WebRTC signaling process.
        # Receives an offer from a client, sets it as the remote description for 
        # the peer connection, creates an answer, and sends the answer back to the client.
        offer = RTCSessionDescription(
            sdp=params['sdp'],
            type=params['type']
        )


        await pc.setRemoteDescription(
            offer
        )
        answer = await pc.createAnswer()
        await pc.setLocalDescription(
            answer
        )

        return web.Response(
            content_type='application/json',
            text=json.dumps({
                'sdp': pc.localDescription.sdp, 
                'type': pc.localDescription.type
            })
        )

    app.router.add_post(
        '/offer', 
        offer
    )

    # Start the web server
    runner = web.AppRunner(app)
    print("Starting server")
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    await site.start()

    logging.info("Server started at http://localhost:8080")


# Run the main function
if __name__ == '__main__':
    # Starts the web server on localhost:8080. 
    # When you run this script, your server will be up and running, 
    # ready to handle WebRTC connections.
    asyncio.run(main())