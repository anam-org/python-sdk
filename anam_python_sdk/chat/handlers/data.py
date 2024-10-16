import logging

class DataHandler:
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def on_data_channel_open(self):
        """Callback for when the data channel is opened."""
        self.logger.debug("Data channel opened")

    def on_data_channel_message(self, message):
        self.logger.debug(f"Received message on data channel: {message}")

