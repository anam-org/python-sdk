"""Signaling server for the Python SDK."""
import asyncio
import websockets
import json

clients = {}

async def signaling_handler(websocket, path):
    peer_id = None
    for peer_id, ws in clients.items():
        print(f"Peer {peer_id} connected on websocket {ws}")
    try:
        async for message in websocket:
            data = json.loads(message)
            print(f"Message: {data}")
            if "peer_id" in data:
                peer_id = data["peer_id"]
                clients[peer_id] = websocket
                print(f"Peer {peer_id} connected")

            if "to" in data:
                target_ws = clients.get(data["to"])
                print("Target WS: ", target_ws)
                if target_ws:
                    await target_ws.send(message)
                else:
                    print(f"Peer {data['to']} not connected")
            else:
                print("Received message without 'to' field")

    finally:
        if peer_id and peer_id in clients:
            del clients[peer_id]
            print(f"Peer {peer_id} disconnected")


async def signaling_server():
    async with websockets.serve(signaling_handler, "localhost", 8080):
        await asyncio.Future()  # Run forever

if __name__ == "__main__":
    asyncio.run(signaling_server())