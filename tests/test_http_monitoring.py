import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.utils import discord_http_logging as logging_module
from bot.utils.http_monitoring import (
    RateLimitMonitorHandler,
    RateLimitTracker,
    heartbeat_skip_reason,
    install_http_monitoring_hook,
)

# bot.app builds a Bot at import time and needs a guild id from the environment.
os.environ.setdefault("GUILD_ID", "1")
from bot.app import Bot, bot as app_bot, on_app_command_error  # noqa: E402

UNHEALTHY_SECONDS = 600


class FakeClock:
    """Deterministic monotonic clock for the tracker tests."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


def cloudflare_429(
    body: str = "<!doctype html>You are being rate limited",
) -> discord.HTTPException:
    """Build the exception discord.py raises for a Cloudflare IP block (no Via header)."""
    response = SimpleNamespace(status=429, reason="Too Many Requests", headers={})
    return discord.HTTPException(response, body)


def http_error(status: int) -> discord.HTTPException:
    response = SimpleNamespace(status=status, reason="", headers={"Via": "1.1 google"})
    return discord.HTTPException(response, {"message": "nope", "code": 0})


class RateLimitTrackerTests(unittest.TestCase):
    def test_healthy_until_marked(self):
        tracker = RateLimitTracker(UNHEALTHY_SECONDS, clock=FakeClock())
        self.assertFalse(tracker.is_unhealthy())
        self.assertIsNone(tracker.seconds_since_last())

    def test_unhealthy_right_after_mark(self):
        clock = FakeClock()
        tracker = RateLimitTracker(UNHEALTHY_SECONDS, clock=clock)
        tracker.mark()
        clock.advance(5)
        self.assertTrue(tracker.is_unhealthy())
        self.assertEqual(tracker.seconds_since_last(), 5)

    def test_recovers_after_window(self):
        clock = FakeClock()
        tracker = RateLimitTracker(UNHEALTHY_SECONDS, clock=clock)
        tracker.mark()
        clock.advance(UNHEALTHY_SECONDS - 1)
        self.assertTrue(tracker.is_unhealthy())
        clock.advance(1)
        self.assertFalse(tracker.is_unhealthy())


class RateLimitMonitorHandlerTests(unittest.TestCase):
    """The log handler catches JSON 429s that discord.py retries and logs."""

    def _record(self, name: str, msg: str) -> logging.LogRecord:
        return logging.LogRecord(name, logging.WARNING, __file__, 1, msg, None, None)

    def test_marks_on_discord_http_rate_limit_warning(self):
        tracker = RateLimitTracker(UNHEALTHY_SECONDS, clock=FakeClock())
        RateLimitMonitorHandler(tracker).emit(
            self._record(
                "discord.http",
                "We are being rate limited. POST /channels responded with 429.",
            )
        )
        self.assertTrue(tracker.is_unhealthy())

    def test_ignores_other_loggers_and_messages(self):
        tracker = RateLimitTracker(UNHEALTHY_SECONDS, clock=FakeClock())
        handler = RateLimitMonitorHandler(tracker)
        handler.emit(self._record("discord.gateway", "We are being rate limited."))
        handler.emit(self._record("discord.http", "Some other warning"))
        self.assertFalse(tracker.is_unhealthy())


class HeartbeatSkipReasonTests(unittest.TestCase):
    def _tracker(self, marked: bool) -> RateLimitTracker:
        tracker = RateLimitTracker(UNHEALTHY_SECONDS, clock=FakeClock())
        if marked:
            tracker.mark()
        return tracker

    def test_none_when_healthy(self):
        self.assertIsNone(
            heartbeat_skip_reason(
                is_closed=False,
                latency=0.05,
                tracker=self._tracker(False),
                latency_threshold=1.0,
            )
        )

    def test_closed_connection(self):
        reason = heartbeat_skip_reason(
            is_closed=True,
            latency=0.05,
            tracker=self._tracker(False),
            latency_threshold=1.0,
        )
        self.assertIn("closed", reason)

    def test_high_latency(self):
        reason = heartbeat_skip_reason(
            is_closed=False,
            latency=2.5,
            tracker=self._tracker(False),
            latency_threshold=1.0,
        )
        self.assertIn("latency", reason)

    def test_recent_rate_limit(self):
        reason = heartbeat_skip_reason(
            is_closed=False,
            latency=0.05,
            tracker=self._tracker(True),
            latency_threshold=1.0,
        )
        self.assertIn("429", reason)


class CloudflareBlockDetectionTests(unittest.IsolatedAsyncioTestCase):
    """A 429 raised by the HTTP layer (Cloudflare block) must mark the tracker."""

    def setUp(self):
        self.original_request = discord.http.HTTPClient.request
        self.http_logger = logging.getLogger("discord.http")
        self.original_handlers = list(self.http_logger.handlers)
        logging_module._installed = False
        logging_module._original_request = None
        logging_module._on_rate_limit = None

    def tearDown(self):
        discord.http.HTTPClient.request = self.original_request
        self.http_logger.handlers = self.original_handlers
        logging_module._installed = False
        logging_module._original_request = None
        logging_module._on_rate_limit = None

    async def _install_with_failing_request(self, exc, tracker):
        async def failing_request(_self, _route, *, files=None, form=None, **kwargs):
            raise exc

        discord.http.HTTPClient.request = failing_request
        install_http_monitoring_hook(SimpleNamespace(rate_limits=tracker))

    async def test_raised_cloudflare_429_marks_tracker(self):
        tracker = RateLimitTracker(UNHEALTHY_SECONDS, clock=FakeClock())
        await self._install_with_failing_request(cloudflare_429(), tracker)
        route = discord.http.Route("GET", "/users/{user_id}", user_id=1)

        with self.assertRaises(discord.HTTPException):
            await discord.http.HTTPClient.request(MagicMock(), route)

        self.assertTrue(tracker.is_unhealthy())

    async def test_raised_403_does_not_mark_tracker(self):
        tracker = RateLimitTracker(UNHEALTHY_SECONDS, clock=FakeClock())
        await self._install_with_failing_request(http_error(403), tracker)
        route = discord.http.Route("GET", "/users/{user_id}", user_id=1)

        with self.assertRaises(discord.HTTPException):
            await discord.http.HTTPClient.request(MagicMock(), route)

        self.assertFalse(tracker.is_unhealthy())

    def test_log_handler_is_attached_to_discord_http(self):
        tracker = RateLimitTracker(UNHEALTHY_SECONDS, clock=FakeClock())
        install_http_monitoring_hook(SimpleNamespace(rate_limits=tracker))
        self.assertTrue(
            any(
                isinstance(h, RateLimitMonitorHandler)
                for h in self.http_logger.handlers
            )
        )


class ApiHealthProberTests(unittest.IsolatedAsyncioTestCase):
    """The 5-minute probe must treat any 429 as unhealthy, not swallow it."""

    def _bot(self, fetch_user_side_effect=None) -> SimpleNamespace:
        """A stand-in for the Bot exposing only what the probe coroutine reads.

        Bot.api_health_prober is a tasks.Loop; its .coro is the plain function,
        so it can be driven with any object as ``self``.
        """
        return SimpleNamespace(
            rate_limits=RateLimitTracker(UNHEALTHY_SECONDS, clock=FakeClock()),
            is_closed=lambda: False,
            user=SimpleNamespace(id=1),
            fetch_user=AsyncMock(side_effect=fetch_user_side_effect),
        )

    async def test_cloudflare_429_marks_unhealthy(self):
        probe_bot = self._bot(fetch_user_side_effect=cloudflare_429())
        await Bot.api_health_prober.coro(probe_bot)
        self.assertTrue(probe_bot.rate_limits.is_unhealthy())

    async def test_success_stays_healthy(self):
        probe_bot = self._bot()
        await Bot.api_health_prober.coro(probe_bot)
        self.assertFalse(probe_bot.rate_limits.is_unhealthy())

    async def test_non_429_error_stays_healthy(self):
        probe_bot = self._bot(fetch_user_side_effect=http_error(500))
        await Bot.api_health_prober.coro(probe_bot)
        self.assertFalse(probe_bot.rate_limits.is_unhealthy())


class AppCommandErrorTests(unittest.IsolatedAsyncioTestCase):
    """A 429 while answering a slash command is the user-facing symptom; mark it."""

    def setUp(self):
        self.original_tracker = app_bot.rate_limits
        app_bot.rate_limits = RateLimitTracker(UNHEALTHY_SECONDS, clock=FakeClock())

    def tearDown(self):
        app_bot.rate_limits = self.original_tracker

    def _interaction(self):
        return SimpleNamespace(
            command=SimpleNamespace(name="today"),
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_429_on_command_response_marks_tracker(self):
        error = discord.app_commands.CommandInvokeError(
            SimpleNamespace(name="today"), cloudflare_429()
        )
        interaction = self._interaction()

        await on_app_command_error(interaction, error)

        self.assertTrue(app_bot.rate_limits.is_unhealthy())
        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("rate limiting", message)

    async def test_429_while_sending_error_reply_marks_tracker(self):
        error = discord.app_commands.CommandInvokeError(
            SimpleNamespace(name="today"), RuntimeError("boom")
        )
        interaction = self._interaction()
        interaction.response.send_message = AsyncMock(side_effect=cloudflare_429())

        await on_app_command_error(interaction, error)

        self.assertTrue(app_bot.rate_limits.is_unhealthy())


if __name__ == "__main__":
    unittest.main()
