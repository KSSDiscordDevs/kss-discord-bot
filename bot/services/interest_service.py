import logging

import discord

logger = logging.getLogger(__name__)


class InterestService:
    """Business logic for self-service access to opt-in interest channels.

    Access is granted with a per-member channel permission overwrite, which is the
    same mechanism RAs use today when they add someone to a channel by hand. No
    extra Discord roles are required.
    """

    @staticmethod
    def has_access(channel: discord.abc.GuildChannel, member: discord.Member) -> bool:
        """Return True if the member currently has an explicit view overwrite."""
        return channel.overwrites_for(member).view_channel is True

    async def grant_access(
        self, channel: discord.abc.GuildChannel, member: discord.Member
    ) -> None:
        """Give the member a view overwrite on the channel (idempotent)."""
        await channel.set_permissions(
            member,
            view_channel=True,
            reason="Joined via interest channel menu",
        )
        logger.info("%s joined #%s", member.display_name, channel.name)

    async def revoke_access(
        self, channel: discord.abc.GuildChannel, member: discord.Member
    ) -> None:
        """Remove the member's overwrite on the channel (idempotent)."""
        await channel.set_permissions(
            member,
            overwrite=None,
            reason="Left via interest channel menu",
        )
        logger.info("%s left #%s", member.display_name, channel.name)

    async def toggle_access(
        self, channel: discord.abc.GuildChannel, member: discord.Member
    ) -> bool:
        """Grant access if the member lacks it, otherwise revoke it.

        Returns True when the member was added and False when they were removed.
        Kept for callers that want toggle semantics; the dropdown UI no longer
        uses it because it surprised members who had been added by hand.
        """
        if self.has_access(channel, member):
            await self.revoke_access(channel, member)
            return False
        await self.grant_access(channel, member)
        return True
