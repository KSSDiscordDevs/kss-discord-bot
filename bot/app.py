"""Bot application setup, command registration, and startup hooks."""

import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from supabase import AsyncClient, create_async_client

from bot.config import EXTENSIONS, GUILD_ID, MY_GUILD
from bot.utils.database import ensure_tables_exist
from bot.utils.http_monitoring import install_http_monitoring_hook
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
        self.last_rate_limit_timestamp: float | None = None
        self.RATE_LIMIT_UNHEALTHY_SECONDS = 300  # 5 minutes

    async def setup_hook(self):
        """Load configured extensions and sync slash commands to the development guild."""
        install_http_monitoring_hook(self)
        print("Installed proactive rate limit monitor.")

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

    @tasks.loop(minutes=3.0)
    async def heartbeat_monitor(self):
        try:
            # 1. Check if the bot's internal websocket is closed
            if self.is_closed():
                print("Bot connection is closed. Skipping heartbeat.")
                return None

            # 2. (Optional) Check latency to ensure it's not severely lagging
            if self.latency > 1.0:  # Latency is over 1000ms
                print(f"High gateway latency ({self.latency}s). Skipping heartbeat.")
                return None

            # NEW: Check for recent HTTP rate limiting
            if (
                self.last_rate_limit_timestamp
                and (time.monotonic() - self.last_rate_limit_timestamp)
                < self.RATE_LIMIT_UNHEALTHY_SECONDS
            ):
                print(
                    f"Bot was rate-limited within the last {self.RATE_LIMIT_UNHEALTHY_SECONDS / 60:.0f} "
                    f"minutes. Skipping healthy heartbeat."
                )
                return None

            # 3. If everything is healthy, ping the healthcheck URL
            ping_url = os.getenv("GATEWAY_HEALTHCHECK_URL")
            if ping_url:
                async with aiohttp.ClientSession() as session:
                    await session.get(ping_url)

                print("Heartbeat sent successfully.")
        except Exception:
            logger.exception("Discord Gateway health check failed")
            return None

    @heartbeat_monitor.before_loop
    async def before_heartbeat(self):
        # Wait until the bot is fully logged in before starting the loop
        await self.wait_until_ready()

    @tasks.loop(minutes=5.0)
    async def api_health_prober(self):
        """Periodically make a benign API call to proactively check for rate-limiting."""
        try:
            if self.is_closed():
                # Don't run if the bot is disconnected.
                return

            # A lightweight, read-only request that has no side-effects.
            # Fetching the bot's own user object is a safe and reliable check.
            await self.fetch_user(self.user.id)
            print("API health probe successful.")
        except discord.HTTPException as e:
            # The RateLimitMonitorHandler will have already caught a 429.
            # This log is for other potential HTTP issues during the probe.
            if e.status != 429:
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
        and original.status == 429
    ):
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
    except discord.HTTPException:
        logger.warning(
            "Failed to send app command error response",
            extra={"command": command_name, "user_id": user_id},
        )
