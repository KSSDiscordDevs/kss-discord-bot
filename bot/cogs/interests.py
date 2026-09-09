import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import INTEREST_CHANNELS
from bot.services.interest_service import InterestService

logger = logging.getLogger(__name__)

PERMISSION_ERROR_MSG = (
    "⚠️ I don't have permission to manage access to {mention}. "
    "Please ask an RA or Server Admin."
)

# --- UI Components ---


class JoinLeaveView(discord.ui.View):
    """Ephemeral Join / Leave buttons shown after a member picks a channel.

    The dropdown used to toggle access directly, which removed members who had
    been added by hand and thought they were joining. Explicit buttons make the
    action unambiguous, and the button for the member's current state is disabled.
    """

    def __init__(
        self,
        service: InterestService,
        channel: discord.abc.GuildChannel,
        has_access: bool,
    ):
        super().__init__(timeout=120)
        self.service = service
        self.channel = channel
        self.join_button.disabled = has_access
        self.leave_button.disabled = not has_access

    async def _finish(self, interaction: discord.Interaction, content: str):
        """Replace the ephemeral prompt with a result and drop the buttons."""
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=content, view=None)
        self.stop()

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, emoji="✅")
    async def join_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            await self.service.grant_access(self.channel, interaction.user)
        except discord.Forbidden:
            logger.warning(
                "Missing permission to edit overwrites on #%s", self.channel.name
            )
            return await self._finish(
                interaction, PERMISSION_ERROR_MSG.format(mention=self.channel.mention)
            )
        await self._finish(
            interaction, f"✅ You now have access to {self.channel.mention}."
        )

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.danger, emoji="👋")
    async def leave_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            await self.service.revoke_access(self.channel, interaction.user)
        except discord.Forbidden:
            logger.warning(
                "Missing permission to edit overwrites on #%s", self.channel.name
            )
            return await self._finish(
                interaction, PERMISSION_ERROR_MSG.format(mention=self.channel.mention)
            )
        await self._finish(
            interaction,
            f"👋 You have left {self.channel.mention}. Use the menu again any time to rejoin.",
        )


class InterestSelect(discord.ui.Select):
    """Persistent dropdown listing the opt-in interest channels."""

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
            placeholder="Pick a channel to join or leave...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="persistent_interest_select",
        )

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.guild.get_channel(int(self.values[0]))
        if channel is None:
            await interaction.response.send_message(
                "❌ I can't find that channel. It may have been deleted, or I may not "
                "have permission to view it. Please ask an RA or Server Admin.",
                ephemeral=True,
            )
            await self._reset_menu(interaction)
            return

        has_access = self.service.has_access(channel, interaction.user)
        status = (
            f"You currently **have access** to {channel.mention}."
            if has_access
            else f"You currently **don't have access** to {channel.mention}."
        )
        await interaction.response.send_message(
            f"{status} What would you like to do?",
            view=JoinLeaveView(self.service, channel, has_access),
            ephemeral=True,
        )
        await self._reset_menu(interaction)

    async def _reset_menu(self, interaction: discord.Interaction):
        """Re-render the shared dropdown so the same option can be picked again.

        Discord keeps the last selection highlighted and does not fire a new
        interaction when the same value is re-selected, which is what made members
        unable to rejoin after leaving. Editing the message with a fresh view
        clears the selection for everyone.
        """
        if interaction.message is None:
            return
        try:
            await interaction.message.edit(view=InterestView(self.service))
        except discord.HTTPException:
            logger.exception("Failed to reset the interest menu")


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
                "Pick a channel from the dropdown, then choose **Join** or **Leave**.\n\n"
                f"{channel_list}"
            ),
            color=discord.Color.teal(),
        )

        await interaction.channel.send(embed=embed, view=InterestView(self.service))
        await interaction.followup.send("✅ Menu spawned successfully.", ephemeral=True)


async def setup(bot):
    """Register the interests cog with the bot."""
    await bot.add_cog(Interests(bot))
