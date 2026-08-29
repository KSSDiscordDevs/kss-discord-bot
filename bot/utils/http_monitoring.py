import logging
import time


class RateLimitMonitorHandler(logging.Handler):
    """A logging handler that watches for discord.py's internal rate limit warnings."""

    def __init__(self, bot_instance):
        super().__init__()
        self.bot = bot_instance
        # The message that discord.py logs when it encounters a 429 response.
        self.rate_limit_message = "We are being rate limited."

    def emit(self, record: logging.LogRecord):
        """This method is called for every log record.

        If the record is a rate limit warning from discord.http, it updates the bot's state.
        """
        if record.name == "discord.http" and record.getMessage().startswith(
            self.rate_limit_message
        ):
            self.bot.last_rate_limit_timestamp = time.monotonic()


def install_http_monitoring_hook(bot):
    """Installs a custom handler to proactively detect HTTP 429 rate limits."""
    handler = RateLimitMonitorHandler(bot_instance=bot)
    logging.getLogger("discord.http").addHandler(handler)
