import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.cogs.interests import InterestSelect
from bot.services.interest_service import InterestService


def make_channel(name="ice-skating", member_can_view=None):
    """Build a channel double whose overwrites_for() reports the given view state."""
    channel = MagicMock()
    channel.name = name
    channel.mention = f"#{name}"
    channel.overwrites_for.return_value = SimpleNamespace(view_channel=member_can_view)
    channel.set_permissions = AsyncMock()
    return channel


def make_interaction(channel):
    """Build a minimal interaction double wired to return the given channel."""
    return SimpleNamespace(
        user=SimpleNamespace(id=1234, display_name="Tester"),
        guild=SimpleNamespace(get_channel=MagicMock(return_value=channel)),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


TEST_INTERESTS = [
    {
        "channel_id": 111,
        "label": "Ice Skating",
        "description": "Skating trips",
        "emoji": "⛸️",
    }
]


class InterestServiceTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for join/leave toggling of interest channel access."""

    async def test_toggle_grants_access_when_member_lacks_it(self):
        service = InterestService()
        channel = make_channel(member_can_view=None)
        member = SimpleNamespace(display_name="Tester")

        joined = await service.toggle_access(channel, member)

        self.assertTrue(joined)
        channel.set_permissions.assert_awaited_once()
        _, kwargs = channel.set_permissions.await_args
        self.assertTrue(kwargs["view_channel"])

    async def test_toggle_revokes_access_when_member_has_it(self):
        service = InterestService()
        channel = make_channel(member_can_view=True)
        member = SimpleNamespace(display_name="Tester")

        joined = await service.toggle_access(channel, member)

        self.assertFalse(joined)
        channel.set_permissions.assert_awaited_once()
        _, kwargs = channel.set_permissions.await_args
        self.assertIsNone(kwargs["overwrite"])

    def test_has_access_false_for_explicit_deny(self):
        channel = make_channel(member_can_view=False)
        self.assertFalse(InterestService.has_access(channel, SimpleNamespace()))


class InterestSelectTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for the persistent dropdown callback."""

    def _make_select(self):
        with patch("bot.cogs.interests.INTEREST_CHANNELS", TEST_INTERESTS):
            select = InterestSelect(InterestService())
        select._values = ["111"]
        return select

    async def test_callback_joins_and_confirms(self):
        select = self._make_select()
        channel = make_channel(member_can_view=None)
        interaction = make_interaction(channel)

        await select.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        interaction.guild.get_channel.assert_called_once_with(111)
        message = interaction.followup.send.await_args.args[0]
        self.assertIn("now have access", message)

    async def test_callback_leaves_and_confirms(self):
        select = self._make_select()
        channel = make_channel(member_can_view=True)
        interaction = make_interaction(channel)

        await select.callback(interaction)

        message = interaction.followup.send.await_args.args[0]
        self.assertIn("left", message)

    async def test_callback_handles_missing_channel(self):
        select = self._make_select()
        interaction = make_interaction(channel=None)

        await select.callback(interaction)

        message = interaction.followup.send.await_args.args[0]
        self.assertIn("can't find that channel", message)

    async def test_callback_handles_forbidden(self):
        select = self._make_select()
        channel = make_channel(member_can_view=None)
        channel.set_permissions.side_effect = discord.Forbidden(
            MagicMock(status=403), "Missing Permissions"
        )
        interaction = make_interaction(channel)

        await select.callback(interaction)

        message = interaction.followup.send.await_args.args[0]
        self.assertIn("don't have permission", message)

    def test_options_built_from_config(self):
        select = self._make_select()
        self.assertEqual(len(select.options), 1)
        self.assertEqual(select.options[0].value, "111")
        self.assertEqual(select.options[0].label, "Ice Skating")
        self.assertEqual(select.custom_id, "persistent_interest_select")


if __name__ == "__main__":
    unittest.main()
