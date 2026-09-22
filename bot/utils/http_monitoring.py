"""Detect Discord HTTP 429s (rate limits and Cloudflare blocks) and gate the heartbeat.

Discord answers with HTTP 429 in two different ways, and discord.py treats them
differently:

1. A normal rate limit comes back as JSON with a ``retry_after``. discord.py
   sleeps and retries internally and logs ``"We are being rate limited."`` on
   the ``discord.http`` logger. :class:`RateLimitMonitorHandler` watches for that
   log line.
2. A Cloudflare block on the host's IP comes back as an HTML page with no
   ``Via`` header. discord.py raises :class:`discord.HTTPException` straight
   away with no log line at all. The request wrapper in
   :mod:`bot.utils.discord_http_logging` catches that and calls back into the
   tracker.

Both paths feed one :class:`RateLimitTracker`, and the gateway heartbeat is
withheld while the tracker reports unhealthy so Healthchecks.io can alert.
"""

import logging
import time
from typing import Callable

from bot.utils.discord_http_logging import install_discord_http_rate_limit_logging

RATE_LIMIT_LOG_PREFIX = "We are being rate limited."


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
    """Mark the tracker when discord.py logs a JSON rate limit it is retrying."""

    def __init__(self, tracker: RateLimitTracker):
        super().__init__()
        self.tracker = tracker

    def emit(self, record: logging.LogRecord):
        if record.name == "discord.http" and record.getMessage().startswith(
            RATE_LIMIT_LOG_PREFIX
        ):
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
    """Wire both 429 detection paths to ``bot.rate_limits``."""
    logging.getLogger("discord.http").addHandler(
        RateLimitMonitorHandler(bot.rate_limits)
    )
    install_discord_http_rate_limit_logging(on_rate_limit=bot.rate_limits.mark)
