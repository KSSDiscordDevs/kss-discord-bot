"""Logging helpers for Discord HTTP rate-limit diagnostics."""

from __future__ import annotations

import logging
from typing import Any, Callable

import discord
import discord.http

logger = logging.getLogger(__name__)

_installed = False
_original_request = None
_on_rate_limit: Callable[[], None] | None = None

HTTP_TOO_MANY_REQUESTS = 429


def build_rate_limit_log_context(
    route: Any, response: Any, exc: discord.HTTPException
) -> dict[str, Any]:
    """Build structured log metadata for a Discord HTTP 429 response."""
    headers = getattr(response, "headers", {}) or {}
    return {
        "method": getattr(route, "method", None),
        "route_path": getattr(route, "path", None),
        "route_key": getattr(route, "key", None),
        "route_metadata": getattr(route, "metadata", None),
        "channel_id": str(getattr(route, "channel_id", "")) or None,
        "guild_id": str(getattr(route, "guild_id", "")) or None,
        "webhook_id": str(getattr(route, "webhook_id", "")) or None,
        "status": getattr(exc, "status", None),
        "discord_error_code": getattr(exc, "code", None),
        "retry_after": headers.get("Retry-After"),
        "x_ratelimit_bucket": headers.get("X-RateLimit-Bucket"),
        "x_ratelimit_scope": headers.get("X-RateLimit-Scope"),
        "x_ratelimit_remaining": headers.get("X-RateLimit-Remaining"),
        "x_ratelimit_reset_after": headers.get("X-RateLimit-Reset-After"),
        "x_ratelimit_global": headers.get("X-RateLimit-Global"),
        # Discord's own API responses always carry a Via header; Cloudflare's
        # "You are being rate limited" HTML page does not. A 429 without Via is a
        # block on this host's IP address, not a per-route rate limit.
        "likely_cloudflare_block": not headers.get("Via"),
    }


async def _traced_request(self, route, *, files=None, form=None, **kwargs):
    """Wrap Discord HTTP requests so raised 429s are logged and reported.

    discord.py handles JSON rate limits internally (sleep and retry). The only
    429s that reach this wrapper as exceptions are the ones discord.py gives up
    on, which in practice means Cloudflare IP blocks and over-long retry windows.
    """
    try:
        return await _original_request(self, route, files=files, form=form, **kwargs)
    except discord.HTTPException as exc:
        if exc.status == HTTP_TOO_MANY_REQUESTS:
            logger.warning(
                "Discord HTTP 429 raised for %s %s",
                getattr(route, "method", "?"),
                getattr(route, "path", "?"),
                extra=build_rate_limit_log_context(
                    route, getattr(exc, "response", None), exc
                ),
            )
            if _on_rate_limit is not None:
                try:
                    _on_rate_limit()
                except Exception:
                    logger.exception("Rate limit callback failed")
        raise


def install_discord_http_rate_limit_logging(
    on_rate_limit: Callable[[], None] | None = None,
):
    """Install the Discord HTTP 429 wrapper once per process.

    ``on_rate_limit`` is called (with no arguments) every time a 429 is raised
    through the wrapper. Calling this again only updates the callback.
    """
    global _installed, _original_request, _on_rate_limit

    _on_rate_limit = on_rate_limit
    if _installed:
        return

    _original_request = discord.http.HTTPClient.request
    discord.http.HTTPClient.request = _traced_request
    _installed = True
