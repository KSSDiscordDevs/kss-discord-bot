"""Detect Discord HTTP 429s (rate limits and Cloudflare blocks) and gate the heartbeat.

Discord answers with HTTP 429 in two different ways, and discord.py treats them
differently:

1. A normal rate limit comes back as JSON with a ``retry_after``. discord.py
   sleeps and retries internally and logs a WARNING on the ``discord.http``
   logger. :class:`RateLimitMonitorHandler` watches for those log lines.
2. A Cloudflare block on the host's IP comes back as an HTML page with no
   ``Via`` header. discord.py raises :class:`discord.HTTPException` straight
   away with no log line at all. The request wrapper in
   :mod:`bot.utils.discord_http_logging` catches that and calls back into the
   tracker.

Interaction callbacks (slash-command responses) go through a separate
``discord.webhook.async_`` HTTP client, not ``discord.http``. The handler is
also attached to that logger so per-interaction 429s are not missed.

All paths feed one :class:`RateLimitTracker`, and the gateway heartbeat is
withheld while the tracker reports unhealthy so Healthchecks.io can alert.
"""

import logging
import time
from typing import Callable

from bot.utils.discord_http_logging import install_discord_http_rate_limit_logging

# ── discord.http WARNING prefixes ─────────────────────────────────────────────
# Verified from discord.http.HTTPClient.request source:
#
#   Global rate limit (retrying):
#     'Global rate limit has been hit. Retrying in %.2f seconds.'
#
#   Per-route 429 (retrying):
#     'We are being rate limited. %s %s responded with 429. Retrying in %.2f seconds.'
#
#   Per-route 429 (max_ratelimit_timeout exceeded → raises RateLimited):
#     'We are being rate limited. %s %s responded with 429. Timeout of %.2f was too long, erroring instead.'
#
_HTTP_RATE_LIMIT_PREFIXES: tuple[str, ...] = (
    "Global rate limit has been hit.",
    "We are being rate limited.",
)

# ── discord.webhook.async_ log patterns ───────────────────────────────────────
# WARNING — discord.py retries the 429 (Via header present):
#   'Webhook ID %s is rate limited. Retrying in %.2f seconds.'
#
# DEBUG — emitted for every response including no-Via-header 429s that are
#          raised immediately as HTTPException (the Cloudflare-block path on
#          the webhook client, which is not covered by the request wrapper):
#   'Webhook ID %s with %s %s has returned status code %s'
#
_WEBHOOK_WARNING_SUFFIX = "is rate limited."
_WEBHOOK_DEBUG_429_SUFFIX = "has returned status code 429"


class RateLimitTracker:
    """Remember when Discord last answered with HTTP 429."""

    def __init__(
        self,
        unhealthy_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.unhealthy_seconds = unhealthy_seconds
        self._clock = clock
        self._last_marked: float | None = None

    def mark(self) -> None:
        """Record that a 429 was just observed."""
        self._last_marked = self._clock()

    def seconds_since_last(self) -> float | None:
        """Seconds since the last 429, or None if none has been seen."""
        if self._last_marked is None:
            return None
        return self._clock() - self._last_marked

    def is_unhealthy(self) -> bool:
        """True while a 429 was seen within the unhealthy window."""
        since = self.seconds_since_last()
        return since is not None and since < self.unhealthy_seconds


class RateLimitMonitorHandler(logging.Handler):
    """Mark the tracker for every 429 discord.py logs on either HTTP client.

    Covers:
    - Global rate limits              (discord.http WARNING)
    - Per-route 429s discord.py retries (discord.http WARNING)
    - Webhook / interaction-callback 429s that discord.py retries
                                      (discord.webhook.async_ WARNING)
    - Webhook 429s raised immediately (no Via header)
                                      (discord.webhook.async_ DEBUG status line)
    """

    def __init__(self, tracker: RateLimitTracker):
        # Level must be DEBUG so the webhook status-code line is not filtered
        # out before emit() is reached.
        super().__init__(level=logging.DEBUG)
        self.tracker = tracker

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "discord.http":
            msg = record.getMessage()
            if any(msg.startswith(p) for p in _HTTP_RATE_LIMIT_PREFIXES):
                self.tracker.mark()

        elif record.name == "discord.webhook.async_":
            msg = record.getMessage()
            # WARNING: "Webhook ID <id> is rate limited. Retrying in X seconds."
            if record.levelno == logging.WARNING and _WEBHOOK_WARNING_SUFFIX in msg:
                self.tracker.mark()
            # DEBUG: "Webhook ID <id> with POST <url> has returned status code 429"
            elif record.levelno == logging.DEBUG and msg.endswith(_WEBHOOK_DEBUG_429_SUFFIX):
                self.tracker.mark()


def heartbeat_skip_reason(
    *,
    is_closed: bool,
    latency: float,
    tracker: RateLimitTracker,
    latency_threshold: float,
) -> str | None:
    """Return why the gateway heartbeat should be withheld, or None if healthy."""
    if is_closed:
        return "gateway connection is closed"
    if latency > latency_threshold:
        return f"gateway latency is {latency:.2f}s (threshold {latency_threshold:.2f}s)"
    if tracker.is_unhealthy():
        return (
            f"Discord answered HTTP 429 {tracker.seconds_since_last():.0f}s ago "
            f"(unhealthy for {tracker.unhealthy_seconds:.0f}s after a 429)"
        )
    return None


def install_http_monitoring_hook(bot) -> None:
    """Wire all 429 detection paths to ``bot.rate_limits``.

    Three sources feed the tracker:
    1. Log handler on discord.http — JSON rate limits discord.py retries.
    2. Log handler on discord.webhook.async_ — interaction-callback 429s
       (both retried and raised-immediately paths).
    3. Request wrapper in discord_http_logging — raised 429s on discord.http
       (Cloudflare blocks and over-long retry windows).
    """
    handler = RateLimitMonitorHandler(bot.rate_limits)
    logging.getLogger("discord.http").addHandler(handler)
    logging.getLogger("discord.webhook.async_").addHandler(handler)
    install_discord_http_rate_limit_logging(on_rate_limit=bot.rate_limits.mark)
