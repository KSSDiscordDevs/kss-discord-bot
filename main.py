"""Thin runtime entrypoint for the Discord bot."""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

# Load .env BEFORE importing bot.app so os.getenv("MSSQL_CONN_STR") is populated
load_dotenv(Path(__file__).resolve().parent / ".env")

from bot.app import bot  # noqa: E402
from bot.web.keep_alive import keep_alive  # noqa: E402

LOG_FILE = "discord_bot.txt"


class _StreamToLogger:
    """Forward print() / stdout / stderr writes into Python's logging system."""

    def __init__(self, logger: logging.Logger, level: int):
        self.logger = logger
        self.level = level

    def write(self, message: str):
        message = message.rstrip()
        if message:
            self.logger.log(self.level, message)

    def flush(self):
        pass


def configure_logging():
    """Send all logs and stdout/stderr through a single RotatingFileHandler."""
    handler = RotatingFileHandler(
        filename=LOG_FILE,
        encoding="utf-8",
        maxBytes=5 * 1024 * 1024,  # 5 MB per file
        backupCount=5,             # Keeps discord_bot.txt.1 through .5
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)

    # Keep DEBUG for the handshake details; records propagate to the root handler.
    logging.getLogger("discord").setLevel(logging.DEBUG)

    # Route print() and uncaught stderr through the same single file handler
    # so no second file

if __name__ == "__main__":
    # 1. Configure single-handle rotating file logging before anything else starts
    configure_logging()

    # 2. Start the web server
    keep_alive()

    # 3. Run the bot
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logging.critical("FATAL: DISCORD_TOKEN is missing from environment variables!")
    else:
        # log_handler=None because logging is configured above
        bot.run(token, log_handler=None) 

