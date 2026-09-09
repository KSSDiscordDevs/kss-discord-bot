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

    async def toggle_access(
        self, channel: discord.abc.GuildChannel, member: discord.Member
    ) -> bool:
        """Grant access if the member lacks it, otherwise revoke it.

        Returns True when the member was added and False when they were removed.
        """
        if self.has_access(channel, member):
            await channel.set_permissions(
                member,
                overwrite=None,
                reason="Left via interest channel menu",
            )
            logger.info("%s left #%s", member.display_name, channel.name)
            return False

        await channel.set_permissions(
            member,
            view_channel=True,
            reason="Joined via interest channel menu",
        )
        logger.info("%s joined #%s", member.display_name, channel.name)
        return True
