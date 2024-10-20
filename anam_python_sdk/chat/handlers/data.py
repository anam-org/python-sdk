"""Handler for the data channel."""

import logging

class DataHandler:
    """Handler for the data channel."""
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def on_data_channel_open(self):
        """Callback for when the data channel is opened."""
        self.logger.debug("Data channel opened")

    def on_data_channel_message(self, message):
        """Callback for when a message is received on the data channel."""
        self.logger.debug("Received message on data channel: %s", message)
