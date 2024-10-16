import pygame
import numpy as np
import logging
import asyncio

class VideoHandler:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.video_surface = None

    def handle_video_track(self, track):
        self.logger.debug("Setting up video display")
        pygame.init()
        self.video_surface = pygame.display.set_mode((640, 480))
        pygame.display.set_caption("Anam Video Chat")

        async def display_video():
            while True:
                frame = await track.recv()
                if frame:
                    self.logger.debug(f"Received video frame: pts={frame.pts}, "
                                      f"format={frame.format.name}, size={frame.width}x{frame.height}")
                    img = frame.to_ndarray(format="bgr24")
                    img = np.rot90(img)
                    surface = pygame.surfarray.make_surface(img)
                    self.video_surface.blit(surface, (0, 0))
                    pygame.display.flip()
                await asyncio.sleep(0.01)

        asyncio.create_task(display_video())
