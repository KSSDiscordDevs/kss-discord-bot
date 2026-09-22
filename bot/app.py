"""Bot application setup, command registration, and startup hooks."""

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from supabase import AsyncClient, create_async_client

from bot.config import (
    API_HEALTH_PROBE_MINUTES,
    EXTENSIONS,
    GATEWAY_HEARTBEAT_MINUTES,
    GUILD_ID,
    HIGH_GATEWAY_LATENCY_SECONDS,
    MY_GUILD,
    RATE_LIMIT_UNHEALTHY_SECONDS,
)
from bot.utils.database import ensure_tables_exist
from bot.utils.discord_http_logging import HTTP_TOO_MANY_REQUESTS
from bot.utils.http_monitoring import (
    RateLimitTracker,
    heartbeat_skip_reason,
    install_http_monitoring_hook,
)
from discord.ext import tasks
import requests
import aiohttp

load_dotenv()
logger = logging.getLogger(__name__)


class Bot(commands.Bot):
    """Main Discord bot class responsible for startup, sync, and shared caches."""

    def __init__(self):
        """Configure intents, create shared clients, and initialize local caches."""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

        self.supabase: AsyncClient | None = None
        self.meal_cache = []
        self._ready_once = False
        # Marked whenever Discord answers a REST call with HTTP 429, whether that is
        # a normal JSON rate limit or a Cloudflare block on this host's IP. The
        # gateway heartbeat is withheld while unhealthy so Healthchecks.io alerts.
        self.rate_limits = RateLimitTracker(RATE_LIMIT_UNHEALTHY_SECONDS)

    async def setup_hook(self):
        """Load configured extensions and sync slash commands to the development guild."""
        install_http_monitoring_hook(self)
        logger.info("Installed HTTP 429 / Cloudflare block monitor.")

        try:
            db_url = os.environ.get("SUPABASE_DB_URL")
            if db_url:
                print("Verifying database schema...")
                await ensure_tables_exist(db_url)
            else:
                print("WARNING: SUPABASE_DB_URL not found. Skipping schema creation.")
        except Exception:
            logger.exception("Failed to verify database schema")

        try:
            url = os.environ.get("SUPABASE_URL")
            key = os.environ.get("SUPABASE_SERVICE_KEY")
            if url and key:
                self.supabase = await create_async_client(url, key)
                print("Async Supabase client initialized")
            else:
                print(
                    "WARNING: SUPABASE_URL or SUPABASE_SERVICE_KEY not found. "
                    "Skipping Supabase client initialization."
                )
        except Exception:
            logger.exception("Failed to initialize Supabase client")

        for extension in EXTENSIONS:
            try:
                await self.load_extension(extension)
                print(f"Loaded {extension}")
            except Exception as e:
                print(f"Failed to load {extension}: {e}")

        self.heartbeat_monitor.start()
        self.api_health_prober.start()

    @tasks.loop(minutes=GATEWAY_HEARTBEAT_MINUTES)
    async def heartbeat_monitor(self):
        """Ping Healthchecks.io only while the bot can actually serve commands."""
        try:
            reason = heartbeat_skip_reason(
                is_closed=self.is_closed(),
                latency=self.latency,
                tracker=self.rate_limits,
                latency_threshold=HIGH_GATEWAY_LATENCY_SECONDS,
            )
            if reason:
                logger.warning("Withholding gateway heartbeat: %s", reason)
                return None

            ping_url = os.getenv("GATEWAY_HEALTHCHECK_URL")
            if ping_url:
                async with aiohttp.ClientSession() as session:
                    await session.get(ping_url)

                logger.info("Heartbeat sent successfully.")
        except Exception:
            logger.exception("Discord Gateway health check failed")
            return None

    @heartbeat_monitor.before_loop
    async def before_heartbeat(self):
        # Wait until the bot is fully logged in before starting the loop
        await self.wait_until_ready()

    @tasks.loop(minutes=API_HEALTH_PROBE_MINUTES)
    async def api_health_prober(self):
        """Make one harmless REST call so an HTTP-level block is noticed even when idle."""
        try:
            if self.is_closed():
                # Don't run if the bot is disconnected.
                return

            was_unhealthy = self.rate_limits.is_unhealthy()

            # A lightweight, read-only request that has no side-effects.
            # Fetching the bot's own user object is a safe and reliable check.
            await self.fetch_user(self.user.id)

            if was_unhealthy:
                logger.info(
                    "API health probe recovered; Discord REST is answering again."
                )
            else:
                logger.info("API health probe successful.")
        except discord.HTTPException as e:
            if e.status == HTTP_TOO_MANY_REQUESTS:
                # Cloudflare/HTML 429s are raised straight through by discord.py with
                # no "We are being rate limited." log line, so the log handler never
                # sees them. Mark here explicitly (the request wrapper also marks,
                # but this keeps the probe self-sufficient).
                self.rate_limits.mark()
                logger.warning(
                    "API health probe got HTTP 429; withholding gateway heartbeat "
                    "for %ss so Healthchecks.io alerts.",
                    RATE_LIMIT_UNHEALTHY_SECONDS,
                    extra={"status": e.status, "code": e.code},
                )
            else:
                logger.warning(
                    "API health probe failed with non-429 HTTP error.",
                    extra={"status": e.status, "code": e.code},
                )
        except Exception:
            logger.exception("Unhandled error in API health prober task.")

    @api_health_prober.before_loop
    async def before_api_health_prober(self):
        await self.wait_until_ready()

    async def on_ready(self):
        """Cache startup data, initialize parking, and publish bot presence."""
        if self._ready_once:
            print(f"Reconnected as {self.user.name}")
            return

        self._ready_once = True
        await self.change_presence(
            activity=discord.CustomActivity(
                name="Custom Status", state="Enter /help to see what I can do!"
            )
        )

        try:
            response = await self.supabase.table("meals").select("*").execute()
            self.meal_cache = response.data
            print(f"Cached {len(self.meal_cache)} meals")
        except Exception as e:
            print(f"Failed to cache meals: {e}")

        parking_cog = self.get_cog("Parking")
        if parking_cog:
            await parking_cog.initialize_parking_spots()
            print("Parking spots initialized")

        print(f"{self.user.name} is online in Champaign!")


bot = Bot()


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    """Handle slash-command cooldowns and Discord-side 429 failures consistently."""
    import math

    command_name = getattr(getattr(interaction, "command", None), "name", "unknown")
    user_id = str(interaction.user.id)
    message_to_send = "Something went wrong while running that command."  # Default

    if isinstance(error, app_commands.CommandOnCooldown):
        retry_after = max(1, math.ceil(error.retry_after))
        logger.info(
            "App command hit cooldown",
            extra={
                "command": command_name,
                "user_id": user_id,
                "retry_after_seconds": retry_after,
            },
        )
        message_to_send = (
            f"Please wait {retry_after}s before using `/{command_name}` again."
        )
    elif (
        isinstance(original := getattr(error, "original", error), discord.HTTPException)
        and original.status == HTTP_TOO_MANY_REQUESTS
    ):
        # A 429 on an interaction response is the user-facing symptom of a rate
        # limit or Cloudflare block, so withhold the gateway heartbeat.
        bot.rate_limits.mark()
        logger.warning(
            "Discord rate limited app command response",
            extra={
                "command": command_name,
                "user_id": user_id,
                "status": original.status,
                "discord_error_code": original.code,
            },
        )
        message_to_send = "Discord is rate limiting the bot right now. Please wait a few seconds and try again."
    else:  # Unhandled error
        logger.error(
            "Unhandled app command error",
            extra={"command": command_name, "user_id": user_id},
            exc_info=(
                type(original),
                original,
                original.__traceback__,
            ),
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message_to_send, ephemeral=True)
        else:
            await interaction.response.send_message(message_to_send, ephemeral=True)
    except discord.HTTPException as exc:
        if exc.status == HTTP_TOO_MANY_REQUESTS:
            bot.rate_limits.mark()
        logger.warning(
            "Failed to send app command error response",
            extra={"command": command_name, "user_id": user_id, "status": exc.status},
        )
