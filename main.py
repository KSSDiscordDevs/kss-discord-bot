"""Thin runtime entrypoint for the Discord bot."""

import logging
from logging.handlers import RotatingFileHandler
import os
import sys

from bot.app import bot
from bot.web.keep_alive import keep_alive

LOG_FILE = "discord_bot.txt"


def configure_output_buffering():
    """Redirect stdout/stderr to the durable log file with line buffering."""
    log_stream = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_stream
    sys.stderr = log_stream


def configure_logging():
    """Send the bot's own INFO+ logs and discord.py's DEBUG logs to a rotating file."""
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


if __name__ == "__main__":
    # 1. Route output and logging to discord_bot.txt before anything else starts
    configure_output_buffering()
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
