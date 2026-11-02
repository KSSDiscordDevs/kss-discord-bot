"""Owner-only debug commands for testing internal bot health systems."""

import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


class Debug(commands.Cog):
    """Owner-only commands for verifying rate-limit detection and suppression."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Option A: Test suppression logic only ─────────────────────────────────
    # Calls tracker.mark() directly, bypassing the log handler entirely.
    # Verifies that heartbeat_monitor consults the tracker and skips the ping.
    # Expected: next heartbeat tick (within 3 min) logs
    #   "Withholding gateway heartbeat: Discord answered HTTP 429 ..."
    @commands.command(name="debug_stamp_ratelimit")
    @commands.is_owner()
    async def debug_stamp_ratelimit(self, ctx: commands.Context):
        """[Option A] Mark the RateLimitTracker directly to test heartbeat suppression."""
        self.bot.rate_limits.mark()
        window_sec = self.bot.rate_limits.unhealthy_seconds
        window_min = int(window_sec // 60)
        await ctx.send(
            f"✅ `rate_limits.mark()` called. Tracker is now unhealthy for {window_min} min.\n"
            f"The next heartbeat tick (within ~3 min) should log:\n"
            f"> *Withholding gateway heartbeat: Discord answered HTTP 429 ...*"
        )

    # ── Option B: Test detection pipeline only ────────────────────────────────
    # Fires the exact DEBUG log record the webhook client emits on a 429,
    # then checks whether RateLimitMonitorHandler.emit() called tracker.mark().
    @commands.command(name="debug_probe_handler")
    @commands.is_owner()
    async def debug_probe_handler(self, ctx: commands.Context):
        """[Option B] Fire a fake webhook 429 log to test RateLimitMonitorHandler."""
        was_unhealthy_before = self.bot.rate_limits.is_unhealthy()
        seconds_before = self.bot.rate_limits.seconds_since_last()

        # Emit the exact DEBUG message discord.webhook.async_ logs on a 429
        # (no Via header path — the silent-raise case seen in production logs).
        logging.getLogger("discord.webhook.async_").debug(
            "Webhook ID %s with POST %s has returned status code 429",
            "TEST_PROBE",
            "https://discord.com/api/v10/fake/callback",
        )

        seconds_after = self.bot.rate_limits.seconds_since_last()
        # Handler fired if the tracker was just marked (seconds_since_last is
        # now a very small number, smaller than it was before the probe).
        handler_fired = (
            seconds_after is not None
            and (seconds_before is None or seconds_after < seconds_before)
        )

        if handler_fired:
            await ctx.send(
                "✅ Handler fired correctly.\n"
                "`RateLimitMonitorHandler.emit()` called `tracker.mark()`.\n"
                "Detection pipeline is working."
            )
        else:
            await ctx.send(
                "❌ Handler did **not** fire.\n"
                "`tracker.seconds_since_last()` was unchanged — check that:\n"
                "1. `install_http_monitoring_hook()` was called in `setup_hook`.\n"
                "2. The handler is attached to `discord.webhook.async_` logger.\n"
                "3. The `discord` logger level is DEBUG (set in `configure_logging()`).\n"
                f"Current tracker state: seconds_since_last={seconds_after}, "
                f"was_unhealthy_before={was_unhealthy_before}"
            )


async def setup(bot: commands.Bot):
    """Register the debug cog with the bot."""
    await bot.add_cog(Debug(bot))
