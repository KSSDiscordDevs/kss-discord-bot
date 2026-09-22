import logging
import time


# ── discord.http ──────────────────────────────────────────────────────────────
# WARNING-level messages emitted by HTTPClient.request on a 429:
#
#   Global rate limit:
#     'Global rate limit has been hit. Retrying in %.2f seconds.'
#
#   Per-route 429 (will retry):
#     'We are being rate limited. %s %s responded with 429. Retrying in %.2f seconds.'
#
#   Per-route 429 (max_ratelimit_timeout exceeded → raises RateLimited):
#     'We are being rate limited. %s %s responded with 429. Timeout of %.2f was too long, erroring instead.'
#
_HTTP_RATE_LIMIT_PREFIXES: tuple[str, ...] = (
    "Global rate limit has been hit.",
    "We are being rate limited.",
)

# ── discord.webhook.async_ ────────────────────────────────────────────────────
# WARNING-level message emitted when an interaction-callback / webhook POST
# receives a 429 and will be retried:
#
#   'Webhook ID %s is rate limited. Retrying in %.2f seconds.'
#
# DEBUG-level message emitted for *every* response (including 429s that raise
# immediately because the response has no Via header):
#
#   'Webhook ID %s with %s %s has returned status code %s'
#
# We watch both so we catch the silent raise path too.
_WEBHOOK_RATE_LIMIT_WARNING_PREFIX = "Webhook ID"
_WEBHOOK_RATE_LIMIT_WARNING_SUFFIX = "is rate limited."
_WEBHOOK_STATUS_DEBUG_PREFIX = "Webhook ID"
_WEBHOOK_STATUS_429_SUFFIX = "has returned status code 429"


class RateLimitMonitorHandler(logging.Handler):
    """Detects every 429 discord.py encounters, across both HTTP and webhook layers.

    Covers:
    - Global rate limits    (discord.http WARNING)
    - Per-route rate limits (discord.http WARNING)
    - Webhook / interaction-callback 429s that discord.py retries
                            (discord.webhook.async_ WARNING)
    - Webhook 429s that discord.py raises immediately (no Via header)
                            (discord.webhook.async_ DEBUG — status code log)
    """

    def __init__(self, bot_instance):
        super().__init__(level=logging.DEBUG)  # Must be DEBUG to catch webhook status logs
        self.bot = bot_instance

    def emit(self, record: logging.LogRecord) -> None:
        """Stamp last_rate_limit_timestamp whenever any 429 is detected."""
        if record.name == "discord.http":
            msg = record.getMessage()
            if any(msg.startswith(p) for p in _HTTP_RATE_LIMIT_PREFIXES):
                self.bot.last_rate_limit_timestamp = time.monotonic()

        elif record.name == "discord.webhook.async_":
            msg = record.getMessage()
            # WARNING path: "Webhook ID <id> is rate limited. Retrying in X seconds."
            if (
                record.levelno == logging.WARNING
                and msg.startswith(_WEBHOOK_RATE_LIMIT_WARNING_PREFIX)
                and _WEBHOOK_RATE_LIMIT_WARNING_SUFFIX in msg
            ):
                self.bot.last_rate_limit_timestamp = time.monotonic()

            # DEBUG path: "Webhook ID <id> with POST <url> has returned status code 429"
            # This fires for 429s that raise HTTPException immediately (no Via header).
            elif record.levelno == logging.DEBUG and msg.endswith(_WEBHOOK_STATUS_429_SUFFIX):
                self.bot.last_rate_limit_timestamp = time.monotonic()


def install_http_monitoring_hook(bot) -> None:
    """Attach the rate-limit handler to all relevant discord.py loggers."""
    handler = RateLimitMonitorHandler(bot_instance=bot)
    # HTTP client (slash command responses, REST calls)
    logging.getLogger("discord.http").addHandler(handler)
    # Webhook client (interaction callbacks, webhook POSTs)
    logging.getLogger("discord.webhook.async_").addHandler(handler)
