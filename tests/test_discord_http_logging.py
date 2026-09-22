import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import discord

from bot.utils import discord_http_logging as logging_module


class DiscordHttpLoggingTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for Discord HTTP 429 logging instrumentation."""

    def setUp(self):
        self.original_request = discord.http.HTTPClient.request
        logging_module._installed = False
        logging_module._original_request = None
        logging_module._on_rate_limit = None

    def tearDown(self):
        discord.http.HTTPClient.request = self.original_request
        logging_module._installed = False
        logging_module._original_request = None
        logging_module._on_rate_limit = None

    def test_context_flags_cloudflare_block_when_via_header_missing(self):
        route = discord.http.Route("GET", "/users/{user_id}", user_id=1)
        cloudflare = SimpleNamespace(status=429, reason="Too Many Requests", headers={})
        discord_api = SimpleNamespace(
            status=429, reason="Too Many Requests", headers={"Via": "1.1 google"}
        )

        self.assertTrue(
            logging_module.build_rate_limit_log_context(
                route, cloudflare, discord.HTTPException(cloudflare, "<!doctype html>")
            )["likely_cloudflare_block"]
        )
        self.assertFalse(
            logging_module.build_rate_limit_log_context(
                route, discord_api, discord.HTTPException(discord_api, {"code": 0})
            )["likely_cloudflare_block"]
        )

    async def test_on_rate_limit_callback_fires_for_429_only(self):
        route = discord.http.Route("GET", "/users/{user_id}", user_id=1)
        calls = {"status": None}

        async def failing_request(_self, _route, *, files=None, form=None, **kwargs):
            response = SimpleNamespace(status=calls["status"], reason="", headers={})
            raise discord.HTTPException(response, "body")

        discord.http.HTTPClient.request = failing_request
        callback = MagicMock()
        logging_module.install_discord_http_rate_limit_logging(on_rate_limit=callback)

        calls["status"] = 403
        with self.assertRaises(discord.HTTPException):
            await discord.http.HTTPClient.request(MagicMock(), route)
        callback.assert_not_called()

        calls["status"] = 429
        with self.assertRaises(discord.HTTPException):
            await discord.http.HTTPClient.request(MagicMock(), route)
        callback.assert_called_once()

    def test_reinstall_only_updates_callback(self):
        first, second = MagicMock(), MagicMock()
        logging_module.install_discord_http_rate_limit_logging(on_rate_limit=first)
        wrapped = discord.http.HTTPClient.request
        logging_module.install_discord_http_rate_limit_logging(on_rate_limit=second)

        self.assertIs(discord.http.HTTPClient.request, wrapped)
        self.assertIs(logging_module._on_rate_limit, second)

    def test_build_rate_limit_log_context_includes_route_and_headers(self):
        route = discord.http.Route(
            "POST", "/interactions/{interaction_id}/{interaction_token}/callback"
        )
        response = SimpleNamespace(
            status=429,
            reason="Too Many Requests",
            headers={
                "Retry-After": "2",
                "X-RateLimit-Bucket": "bucket-1",
                "X-RateLimit-Scope": "user",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset-After": "2.000",
            },
        )
        exc = discord.HTTPException(response, "rate limited")

        context = logging_module.build_rate_limit_log_context(route, response, exc)

        self.assertEqual(context["method"], "POST")
        self.assertEqual(
            context["route_path"],
            "/interactions/{interaction_id}/{interaction_token}/callback",
        )
        self.assertEqual(context["retry_after"], "2")
        self.assertEqual(context["x_ratelimit_bucket"], "bucket-1")

    async def test_install_logs_429_with_route_path(self):
        route = discord.http.Route("POST", "/webhooks/{webhook_id}/{webhook_token}")
        response = SimpleNamespace(
            status=429,
            reason="Too Many Requests",
            headers={"Retry-After": "1.5"},
        )
        exc = discord.HTTPException(response, "rate limited")

        async def failing_request(_self, _route, *, files=None, form=None, **kwargs):
            raise exc

        discord.http.HTTPClient.request = failing_request

        with patch.object(logging_module, "logger") as logger_mock:
            logging_module.install_discord_http_rate_limit_logging()

            with self.assertRaises(discord.HTTPException):
                await discord.http.HTTPClient.request(MagicMock(), route)

        logger_mock.warning.assert_called_once()
        extra = logger_mock.warning.call_args.kwargs["extra"]
        self.assertEqual(extra["route_path"], "/webhooks/{webhook_id}/{webhook_token}")
        self.assertEqual(extra["status"], 429)
