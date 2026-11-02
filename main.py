"""Thin runtime entrypoint for the Discord bot."""

import logging
import os
import sys

from bot.app import bot
from bot.web.keep_alive import keep_alive


def configure_output_buffering():
    """Line-buffer stdout/stderr so log lines reach the host as they happen.

    Hosts capture output through a pipe, so Python block-buffers print() unless
    output is explicitly line-buffered.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass


def configure_logging():
    """Send the bot's own INFO+ logs and discord.py's DEBUG logs to one handler."""
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
    )

    # Attaching the handler to the root logger means bot.* loggers (INFO and up)
    # actually reach the log stream. Previously only "discord" had a handler, so
    # every logger.info() in the bot's own code was dropped.
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # Keep DEBUG for the handshake details; records propagate to the root handler.
    logging.getLogger("discord").setLevel(logging.DEBUG)

    # Supabase's HTTP client logs one INFO line per query; keep those quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


if __name__ == "__main__":
    # 1. Make output and logging usable before anything else starts
    configure_output_buffering()
    configure_logging()

    # 2. Start the web server
    keep_alive()

    # 3. Run the bot
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("FATAL: DISCORD_TOKEN is missing from environment variables!")
    else:
        # log_handler=None because logging is configured above
        bot.run(token, log_handler=None)
