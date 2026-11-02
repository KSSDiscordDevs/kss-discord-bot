import asyncio
import logging

import discord
from discord import app_commands, ui
from discord.ext import commands
from postgrest import AsyncPostgrestClient

from bot.config import BOT_NAME

logger = logging.getLogger(__name__)


class FeedbackModal(ui.Modal, title=f"Improve {BOT_NAME}"):
    """Modal used to collect free-form feedback and persist it to Supabase."""

    suggestion = ui.TextInput(
        label="What should we add or change?",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Add a way to trade parking spots, or fix the meal schedule formatting...",
        required=True,
        max_length=1000,
    )

    def __init__(self, supabase_client: AsyncPostgrestClient):
        """Store the Supabase client used to save submitted feedback."""
        super().__init__()
        self.supabase = supabase_client

    async def on_submit(self, interaction: discord.Interaction):
        """Persist the submitted suggestion and acknowledge the user privately."""
        payload = {
            "user_id": str(interaction.user.id),
            "user_name": interaction.user.display_name,
            "content": self.suggestion.value,
        }

        await interaction.response.defer(ephemeral=True)

        try:
            await asyncio.wait_for(
                self.supabase.table("feedback").insert(payload).execute(),
                timeout=10,
            )
        except Exception:
            logger.exception(
                "Feedback submission failed",
                extra={"user_id": str(interaction.user.id)},
            )
            return await interaction.followup.send(
                "❌ Your feedback could not be submitted right now.",
                ephemeral=True,
            )

        await interaction.followup.send(
            f"Thanks {interaction.user.display_name}! Your feedback has been sent to the {BOT_NAME} dev team. 🚀",
            ephemeral=True,
        )


class Feedback(commands.Cog):
    """Slash command group for collecting user feedback."""

    def __init__(self, bot):
        """Initialize the cog and connect to Supabase."""
        self.bot = bot
        self.supabase: AsyncPostgrestClient = bot.supabase

    @app_commands.command(
        name="feedback",
        description=f"Submit feedback to the {BOT_NAME} dev team (hit enter, then a submission box will appear)",
    )
    async def feedback(self, interaction: discord.Interaction):
        """Open the feedback modal for the requesting user."""
        await interaction.response.send_modal(FeedbackModal(self.supabase))


async def setup(bot):
    """Register the feedback cog with the bot."""
    await bot.add_cog(Feedback(bot))
