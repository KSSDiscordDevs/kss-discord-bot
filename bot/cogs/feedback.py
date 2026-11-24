import asyncio
import logging

import discord
import pyodbc
from discord import app_commands, ui
from discord.ext import commands

from bot.config import BOT_NAME

logger = logging.getLogger(__name__)


class FeedbackModal(ui.Modal, title=f"Improve {BOT_NAME}"):
    """Modal used to collect free-form feedback and persist it to SQL Server."""

    suggestion = ui.TextInput(
        label="What should we add or change?",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Add a way to trade parking spots, or fix the meal schedule formatting...",
        required=True,
        max_length=1000,
    )

    def __init__(self, conn_str: str):
        """Store the SQL Server connection string used to save submitted feedback."""
        super().__init__()
        self.conn_str = conn_str

    async def on_submit(self, interaction: discord.Interaction):
        """Persist the submitted suggestion and acknowledge the user privately."""
        payload = {
            "user_id": str(interaction.user.id),
            "user_name": interaction.user.display_name,
            "content": self.suggestion.value,
        }

        await interaction.response.defer(ephemeral=True)

        def _insert():
            with pyodbc.connect(self.conn_str) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO dbo.feedback (user_id, user_name, content)
                    VALUES (?, ?, ?)
                    """,
                    (
                        payload["user_id"],
                        payload["user_name"],
                        payload["content"],
                    ),
                )
                conn.commit()

        try:
            await asyncio.wait_for(
                asyncio.to_thread(_insert),
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
        """Initialize the cog and store the SQL Server connection string."""
        self.bot = bot
        self.supabase: str = bot.supabase

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
