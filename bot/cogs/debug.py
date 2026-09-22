"""Owner-only debug commands for testing internal bot health systems."""

import logging
import time

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


class Debug(commands.Cog):
    """Owner-only commands for verifying rate-limit detection and suppression."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Option A: Test suppression logic only ─────────────────────────────────
    # Directly stamps last_rate_limit_timestamp, bypassing the handler entirely.
    # Verifies that heartbeat_monitor reads the timestamp and skips the ping.
    # Expected: next heartbeat tick (within 3 min) logs
    #   "Bot was rate-limited within the last 5 minutes. Skipping healthy heartbeat."
    @commands.command(name="debug_stamp_ratelimit")
    @commands.is_owner()
    async def debug_stamp_ratelimit(self, ctx: commands.Context):
        """[Option A] Stamp last_rate_limit_timestamp directly to test heartbeat suppression."""
        self.bot.last_rate_limit_timestamp = time.monotonic()
        window_min = self.bot.RATE_LIMIT_UNHEALTHY_SECONDS // 60
        await ctx.send(
            f"✅ `last_rate_limit_timestamp` stamped.\n"
            f"The next heartbeat tick (within ~3 min) should log:\n"
            f"> *Bot was rate-limited within the last {window_min} minutes. Skipping healthy heartbeat.*\n"
            f"The timestamp will clear automatically after {window_min} min."
        )

    # ── Option B: Test detection pipeline only ────────────────────────────────
    # Fires the exact DEBUG log record the webhook client emits on a 429,
    # then checks whether the handler stamped last_rate_limit_timestamp.
    # Verifies RateLimitMonitorHandler.emit() fires correctly.
    @commands.command(name="debug_probe_handler")
    @commands.is_owner()
    async def debug_probe_handler(self, ctx: commands.Context):
        """[Option B] Fire a fake webhook 429 log to test RateLimitMonitorHandler."""
        before = self.bot.last_rate_limit_timestamp

        # Emit the exact DEBUG message discord.webhook.async_ logs on a 429
        # (no Via header path — the silent-raise case seen in production logs).
        logging.getLogger("discord.webhook.async_").debug(
            "Webhook ID %s with POST %s has returned status code 429",
            "TEST_PROBE",
            "https://discord.com/api/v10/fake/callback",
        )

        after = self.bot.last_rate_limit_timestamp
        handler_fired = after != before

        if handler_fired:
            await ctx.send(
                "✅ Handler fired correctly.\n"
                "`last_rate_limit_timestamp` was updated by `RateLimitMonitorHandler.emit()`.\n"
                "Detection pipeline is working."
            )
        else:
            await ctx.send(
                "❌ Handler did **not** fire.\n"
                "`last_rate_limit_timestamp` was unchanged — check that:\n"
                "1. `install_http_monitoring_hook()` was called in `setup_hook`.\n"
                "2. The handler is attached to `discord.webhook.async_`.\n"
                "3. The handler level is `DEBUG` (not `WARNING`)."
            )


async def setup(bot: commands.Bot):
    """Register the debug cog with the bot."""
    await bot.add_cog(Debug(bot))
