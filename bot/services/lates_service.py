import logging
import os

import aiohttp

from bot.config import HOUSE_ROLE_CONFIG, LATES_VIEW_GROUPS

logger = logging.getLogger(__name__)


class LatesService:
    """Business logic and data access for late-plate commands."""

    def __init__(self, supabase):
        """Store the shared Supabase client and initialize the memory cache."""
        self.supabase = supabase
        self.lates_cache = []

    async def refresh_lates_cache(self):
        """Fetches all active late plates from Supabase and stores them in memory."""
        try:
            res = await self.supabase.table("lates").select("*").execute()
            self.lates_cache = res.data or []
            logger.info(
                f"Lates cache refreshed: {len(self.lates_cache)} total lates loaded."
            )
        except Exception as e:
            logger.error(f"Failed to refresh lates cache: {e}")

    @staticmethod
    def get_user_house(member):
        """Return the caller's house role slug, if present."""
        role_names = {role.name.lower() for role in member.roles}
        for key, role_name in HOUSE_ROLE_CONFIG.items():
            if role_name in role_names:
                return key
        return None

    async def perform_cleanup(self, day_to_clean=None):
        """Delete temporary lates for a specific day or for all days."""
        try:
            query = self.supabase.table("lates").delete().eq("is_permanent", False)
            if day_to_clean:
                query = query.eq("day_of_week", day_to_clean)

            res = await query.execute()
            count = len(res.data) if res.data else 0

            ping_url = os.getenv("MIDNIGHT_CLEANUP_URL")
            if ping_url:
                async with aiohttp.ClientSession() as session:
                    await session.get(ping_url)

            # Keep memory synchronized after cleanup
            await self.refresh_lates_cache()
            return count
        except Exception:
            logger.exception(
                "Late cleanup failed", extra={"day_to_clean": day_to_clean}
            )
            return None

    async def get_visible_lates(self, house, day, meal):
        """Return lates visible to the caller's house grouping instantly from memory."""
        # Find the group the user's house belongs to and get all roles in that group.
        target_roles = [
            HOUSE_ROLE_CONFIG.get(house)
        ]  # Default to just the user's own house
        for group in LATES_VIEW_GROUPS:
            if house in group:
                target_roles = [
                    HOUSE_ROLE_CONFIG.get(h) for h in group if h in HOUSE_ROLE_CONFIG
                ]
                break

        return [
            late
            for late in self.lates_cache
            if late["day_of_week"] == day
            and late["meal"] == meal
            and late["role"] in target_roles
        ]

    async def create_late(self, user_id, display_name, house, day, meal, is_permanent):
        """Create a new late request unless the same request already exists."""
        user_str = str(user_id)

        # 1. 0ms local duplicate check before hitting the database
        for late in self.lates_cache:
            if (
                late["user_id"] == user_str
                and late["day_of_week"] == day
                and late["meal"] == meal
            ):
                return False, "duplicate"

        # 2. Proceed with database write
        payload = {
            "user_id": user_str,
            "nickname": display_name,
            "role": house,
            "meal": meal,
            "day_of_week": day,
            "is_permanent": is_permanent,
        }
        await self.supabase.table("lates").insert(payload).execute()

        # 3. Synchronize cache
        await self.refresh_lates_cache()
        return True, payload

    async def get_autocomplete_lates(self, user_id):
        """Return a caller's lates for autocomplete display instantly from memory."""
        user_str = str(user_id)
        return [
            {
                "day_of_week": late["day_of_week"],
                "meal": late["meal"],
                "is_permanent": late["is_permanent"],
            }
            for late in self.lates_cache
            if late["user_id"] == user_str
        ]

    async def clear_late(self, user_id, day, meal):
        """Delete one late request for the caller."""
        res = await (
            self.supabase.table("lates")
            .delete()
            .eq("user_id", str(user_id))
            .eq("day_of_week", day)
            .eq("meal", meal)
            .execute()
        )

        success = bool(res.data)
        if success:
            await self.refresh_lates_cache()

        return success

    async def get_user_lates(self, user_id):
        """Fetch all active late requests for one caller instantly from memory."""
        user_str = str(user_id)
        return [late for late in self.lates_cache if late["user_id"] == user_str]
