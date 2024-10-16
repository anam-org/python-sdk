import logging

class TextHandler:
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def on_data_channel_message(self, message):
        self.logger.debug(f"Received message on data channel: {message}")

