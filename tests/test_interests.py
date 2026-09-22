import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.cogs.interests import (
    InterestSelect,
    Interests,
    JoinLeaveView,
    channel_display,
)
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
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
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
    """Unit tests for granting and revoking interest channel access."""

    async def test_grant_sets_view_overwrite(self):
        channel = make_channel()
        await InterestService().grant_access(channel, SimpleNamespace(display_name="T"))
        _, kwargs = channel.set_permissions.await_args
        self.assertTrue(kwargs["view_channel"])

    async def test_revoke_clears_overwrite(self):
        channel = make_channel(member_can_view=True)
        await InterestService().revoke_access(
            channel, SimpleNamespace(display_name="T")
        )
        _, kwargs = channel.set_permissions.await_args
        self.assertIsNone(kwargs["overwrite"])

    async def test_toggle_grants_then_revokes(self):
        service = InterestService()
        member = SimpleNamespace(display_name="T")
        self.assertTrue(
            await service.toggle_access(make_channel(member_can_view=None), member)
        )
        self.assertFalse(
            await service.toggle_access(make_channel(member_can_view=True), member)
        )

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

    async def test_select_does_not_change_permissions_directly(self):
        """Picking a channel must only prompt; the buttons do the actual change."""
        select = self._make_select()
        channel = make_channel(member_can_view=True)
        interaction = make_interaction(channel)

        await select.callback(interaction)

        channel.set_permissions.assert_not_awaited()

    async def test_select_prompts_with_join_enabled_when_no_access(self):
        select = self._make_select()
        interaction = make_interaction(make_channel(member_can_view=None))

        await select.callback(interaction)

        args, kwargs = interaction.response.send_message.await_args
        self.assertIn("don't have access", args[0])
        self.assertTrue(kwargs["ephemeral"])
        view = kwargs["view"]
        self.assertIsInstance(view, JoinLeaveView)
        self.assertFalse(view.join_button.disabled)
        self.assertTrue(view.leave_button.disabled)

    async def test_select_prompts_with_leave_enabled_when_has_access(self):
        select = self._make_select()
        interaction = make_interaction(make_channel(member_can_view=True))

        await select.callback(interaction)

        args, kwargs = interaction.response.send_message.await_args
        self.assertIn("have access", args[0])
        view = kwargs["view"]
        self.assertTrue(view.join_button.disabled)
        self.assertFalse(view.leave_button.disabled)

    async def test_select_resets_shared_menu_after_use(self):
        """The dropdown is re-rendered so the same option can be picked again."""
        select = self._make_select()
        interaction = make_interaction(make_channel())

        with patch("bot.cogs.interests.INTEREST_CHANNELS", TEST_INTERESTS):
            await select.callback(interaction)

        interaction.message.edit.assert_awaited_once()
        self.assertIn("view", interaction.message.edit.await_args.kwargs)

    async def test_select_handles_missing_channel(self):
        select = self._make_select()
        interaction = make_interaction(channel=None)

        with patch("bot.cogs.interests.INTEREST_CHANNELS", TEST_INTERESTS):
            await select.callback(interaction)

        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("can't find that channel", message)
        interaction.message.edit.assert_awaited_once()

    def test_options_built_from_config(self):
        select = self._make_select()
        self.assertEqual(len(select.options), 1)
        self.assertEqual(select.options[0].value, "111")
        self.assertEqual(select.options[0].label, "Ice Skating")
        self.assertEqual(select.custom_id, "persistent_interest_select")


class JoinLeaveViewTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for the ephemeral Join / Leave buttons."""

    async def test_join_button_grants_and_confirms(self):
        channel = make_channel(member_can_view=None)
        view = JoinLeaveView(InterestService(), channel, has_access=False)
        interaction = make_interaction(channel)

        await view.join_button.callback(interaction)

        _, kwargs = channel.set_permissions.await_args
        self.assertTrue(kwargs["view_channel"])
        content = interaction.response.edit_message.await_args.kwargs["content"]
        self.assertIn("now have access", content)
        self.assertIsNone(interaction.response.edit_message.await_args.kwargs["view"])

    async def test_leave_button_revokes_and_confirms(self):
        channel = make_channel(member_can_view=True)
        view = JoinLeaveView(InterestService(), channel, has_access=True)
        interaction = make_interaction(channel)

        await view.leave_button.callback(interaction)

        _, kwargs = channel.set_permissions.await_args
        self.assertIsNone(kwargs["overwrite"])
        content = interaction.response.edit_message.await_args.kwargs["content"]
        self.assertIn("left", content)

    async def test_join_button_handles_forbidden(self):
        channel = make_channel(member_can_view=None)
        channel.set_permissions.side_effect = discord.Forbidden(
            MagicMock(status=403), "Missing Permissions"
        )
        view = JoinLeaveView(InterestService(), channel, has_access=False)
        interaction = make_interaction(channel)

        await view.join_button.callback(interaction)

        content = interaction.response.edit_message.await_args.kwargs["content"]
        self.assertIn("don't have permission", content)


class ChannelDisplayTests(unittest.IsolatedAsyncioTestCase):
    """Channel references must stay readable for members without access."""

    def test_mention_when_member_has_access(self):
        channel = make_channel(name="ice-skating")
        self.assertEqual(channel_display(channel, True), "#ice-skating")

    def test_plain_name_when_member_lacks_access(self):
        channel = make_channel(name="ice-skating")
        self.assertEqual(channel_display(channel, False), "**#ice-skating**")

    async def test_prompt_uses_plain_name_without_access(self):
        with patch("bot.cogs.interests.INTEREST_CHANNELS", TEST_INTERESTS):
            select = InterestSelect(InterestService())
        select._values = ["111"]
        channel = make_channel(name="kss-study", member_can_view=None)
        channel.mention = "<#111>"
        interaction = make_interaction(channel)

        await select.callback(interaction)

        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("**#kss-study**", message)
        self.assertNotIn("<#111>", message)

    async def test_leave_confirmation_uses_plain_name(self):
        channel = make_channel(name="kss-study", member_can_view=True)
        channel.mention = "<#111>"
        view = JoinLeaveView(InterestService(), channel, has_access=True)
        interaction = make_interaction(channel)

        await view.leave_button.callback(interaction)

        content = interaction.response.edit_message.await_args.kwargs["content"]
        self.assertIn("**#kss-study**", content)
        self.assertNotIn("<#111>", content)

    async def test_spawned_embed_never_uses_mentions(self):
        channel = make_channel(name="ice-skating")
        interaction = make_interaction(channel)
        interaction.response.defer = AsyncMock()
        interaction.channel = SimpleNamespace(send=AsyncMock())
        cog = Interests(bot=SimpleNamespace(supabase=None, add_view=MagicMock()))

        with patch("bot.cogs.interests.INTEREST_CHANNELS", TEST_INTERESTS):
            await cog.spawn_interest_menu.callback(cog, interaction)

        embed = interaction.channel.send.await_args.kwargs["embed"]
        self.assertIn("**Ice Skating** (#ice-skating)", embed.description)
        self.assertNotIn("<#", embed.description)


if __name__ == "__main__":
    unittest.main()
