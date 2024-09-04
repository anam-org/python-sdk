
import asyncio
from anam_sdk import AnamClient
from aiortc.contrib.media import MediaPlayer

async def main():
    client = AnamClient(
        'your-api-key', 
        'your-persona-id'
    )
    await client.start_session()

    # Add an audio stream
    audio_player = MediaPlayer('/path/to/audio/file')
    client.add_audio_stream(audio_player.audio)

    # Add a video stream
    video_player = MediaPlayer('/path/to/video/file')
    client.add_video_stream(video_player.video)

    # Keep the connection open
    await asyncio.sleep(30)

    # Close the session
    await client.close()

asyncio.run(main())