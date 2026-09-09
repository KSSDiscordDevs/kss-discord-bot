import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import INTEREST_CHANNELS
from bot.services.interest_service import InterestService

logger = logging.getLogger(__name__)

# --- UI Components ---


class InterestSelect(discord.ui.Select):
    """Persistent dropdown that toggles access to an opt-in interest channel."""

    def __init__(self, service: InterestService):
        self.service = service
        options = [
            discord.SelectOption(
                label=entry["label"],
                description=entry.get("description"),
                value=str(entry["channel_id"]),
                emoji=entry.get("emoji"),
            )
            for entry in INTEREST_CHANNELS
        ]
        super().__init__(
            placeholder="Select a channel to join or leave...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="persistent_interest_select",
        )

    async def callback(self, interaction: discord.Interaction):
        # Defer immediately so the permission edit can't hit the 3-second timeout.
        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(int(self.values[0]))
        if channel is None:
            return await interaction.followup.send(
                "❌ I can't find that channel. It may have been deleted, or I may not "
                "have permission to view it. Please ask an RA or Server Admin.",
                ephemeral=True,
            )

        try:
            joined = await self.service.toggle_access(channel, interaction.user)
        except discord.Forbidden:
            logger.warning("Missing permission to edit overwrites on #%s", channel.name)
            return await interaction.followup.send(
                f"⚠️ I don't have permission to manage access to {channel.mention}. "
                "Please ask an RA or Server Admin.",
                ephemeral=True,
            )

        if joined:
            msg = f"✅ You now have access to {channel.mention}. Select it again to leave."
        else:
            msg = f"👋 You have left {channel.mention}. Select it again to rejoin."

        await interaction.followup.send(msg, ephemeral=True)


class InterestView(discord.ui.View):
    """A persistent view holding the interest channel select menu."""

    def __init__(self, service: InterestService):
        super().__init__(timeout=None)
        self.add_item(InterestSelect(service))


# --- The Cog ---


class Interests(commands.Cog):
    """Self-service join/leave for opt-in interest channels."""

    def __init__(self, bot):
        self.bot = bot
        self.service = InterestService()

    async def cog_load(self):
        """Register the persistent view so the menu keeps working across restarts."""
        if not INTEREST_CHANNELS:
            logger.warning("INTEREST_CHANNELS is empty; interest menu disabled.")
            return
        self.bot.add_view(InterestView(self.service))
        logger.info("Persistent InterestView loaded.")

    @app_commands.command(
        name="spawn_interest_menu",
        description="[Admin] Spawns the persistent interest channel menu in the current channel.",
    )
    @app_commands.default_permissions(administrator=True)
    async def spawn_interest_menu(self, interaction: discord.Interaction):
        """Admin command to drop the persistent UI into a designated channel."""
        await interaction.response.defer(ephemeral=True)

        if not INTEREST_CHANNELS:
            return await interaction.followup.send(
                "⚠️ No interest channels are configured. Add entries to "
                "`INTEREST_CHANNELS` in `bot/config.py` first.",
                ephemeral=True,
            )

        channel_list = "\n".join(
            f"{entry.get('emoji', '•')} <#{entry['channel_id']}>"
            for entry in INTEREST_CHANNELS
        )
        embed = discord.Embed(
            title="Interest Channels",
            description=(
                "Pick a channel from the dropdown to join it. Pick it again to leave.\n\n"
                f"{channel_list}"
            ),
            color=discord.Color.teal(),
        )

        await interaction.channel.send(embed=embed, view=InterestView(self.service))
        await interaction.followup.send("✅ Menu spawned successfully.", ephemeral=True)


async def setup(bot):
    """Register the interests cog with the bot."""
    await bot.add_cog(Interests(bot))
