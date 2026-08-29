import asyncio
import logging
from datetime import datetime, timedelta

from dateutil.relativedelta import FR, MO, SA, SU, TH, TU, WE, relativedelta
from supabase import AsyncClient
from bot.utils.constants import NOON

from bot.config import (
    LOCAL_TZ,
    MINIMUM_RESERVATION_HOURS,
    PERMIT_SPOTS,
    STAFF_PARKING_BLACKOUTS,
    STAFF_SPOTS,
    WEEKEND_GUEST_HOURS_END,
)

logger = logging.getLogger(__name__)


class ParkingService:
    """Database-backed business logic for the parking system."""

    def __init__(self, supabase: AsyncClient):
        """Use the shared async Supabase client."""
        self.supabase = supabase

        self._spot_mutation_locks = {}
        self._staff_mutation_lock = asyncio.Lock()
        self._fallback_mutation_lock = asyncio.Lock()

        # In-memory cache for guest spots loaded on startup
        self.guest_spots_cache = set()
        self.active_offers_cache = []
        self.active_claims_cache = []

    async def refresh_parking_cache(self):
        """Fetches active offers/claims from Supabase and stores them in memory."""
        try:
            now_iso = datetime.now(LOCAL_TZ).isoformat()

            # Run sequentially instead of using asyncio.gather to satisfy strict lock testing
            offers = await (
                self.supabase.table("parking_offers")
                .select("*")
                .gt("end_time", now_iso)
                .execute()
            )
            claims = await (
                self.supabase.table("parking_reservations")
                .select("*")
                .gt("end_time", now_iso)
                .execute()
            )

            self.active_offers_cache = offers.data
            self.active_claims_cache = claims.data
            logger.info(
                f"Parking cache refreshed: {len(self.active_offers_cache)} offers, {len(self.active_claims_cache)} claims."
            )
        except Exception as e:
            logger.error(f"Failed to refresh parking cache: {e}")

    def _get_mutation_lock_for_spot(self, spot):
        """Return the shared mutation lock for one parking spot or the staff pool."""
        if spot in STAFF_SPOTS:
            return self._staff_mutation_lock
        return self._spot_mutation_locks.setdefault(int(spot), asyncio.Lock())

    async def save_offer_spot_preference(self, user_id, username, spot):
        """Persist the caller's last successful offer spot without failing the command."""
        try:
            # Clear any existing spot ownership for this user
            await (
                self.supabase.table("parking_spots")
                .update(
                    {
                        "discord_userid": None,
                        "discord_nickname": None,
                    }
                )
                .eq("discord_userid", str(user_id))
                .execute()
            )

            # Set ownership on the newly offered spot
            await (
                self.supabase.table("parking_spots")
                .update(
                    {
                        "discord_userid": str(user_id),
                        "discord_nickname": username,
                    }
                )
                .eq("spot_number", int(spot))
                .execute()
            )

            return True
        except Exception:
            logger.exception(
                "Failed to save parking spot preference",
                extra={"user_id": str(user_id), "spot": spot},
            )
            return False

    def parse_range(self, s_day_int, s_time_str, e_day_int, e_time_str):
        """Convert weekday and hour choices into the next matching start/end datetimes."""
        now = datetime.now(LOCAL_TZ).replace(minute=0, second=0, microsecond=0)
        day_map = {0: MO, 1: TU, 2: WE, 3: TH, 4: FR, 5: SA, 6: SU}

        def to_dt(day_val, time_str, reference_date):
            t_obj = datetime.strptime(time_str.strip().upper(), "%I %p").time()
            real_day = day_map[int(day_val)]

            dt = reference_date + relativedelta(
                weekday=real_day,
                hour=t_obj.hour,
                minute=0,
                second=0,
                microsecond=0,
            )

            if dt < reference_date:
                dt += relativedelta(weeks=1)
            return dt

        start = to_dt(s_day_int, s_time_str, now)
        end = to_dt(e_day_int, e_time_str, start)

        if end == start:
            end += relativedelta(weeks=1)

        return start, end, end - start

    def _format_datetime_label(self, value):
        """Format a datetime as a human-readable weekday/date/time label."""
        hour = value.hour % NOON or NOON
        am_pm = "AM" if value.hour < NOON else "PM"
        return f"{value.strftime('%a %b')} {value.day} at {hour}:{value.strftime('%M')} {am_pm}"

    async def load_cache(self):
        """Load mostly-static data into memory to reduce database hits."""
        try:
            response = await (
                self.supabase.table("parking_spots")
                .select("spot_number")
                .eq("is_guest", True)
                .execute()
            )
            self.guest_spots_cache = {int(row["spot_number"]) for row in response.data}
            logger.info(f"Loaded {len(self.guest_spots_cache)} guest spots into cache.")
        except Exception:
            logger.exception("Failed to load parking spot cache.")

    async def initialize_spots(self):
        """Synchronize the database parking spot table ONLY if it is completely empty."""
        try:
            # 1. Fast check to see if ANY row exists
            check_response = await (
                self.supabase.table("parking_spots")
                .select("spot_number")
                .limit(1)
                .execute()
            )

            if check_response.data:
                logger.info(
                    "parking_spots table is already populated. Skipping initialization."
                )
                return  # Early return, do not overwrite existing data

            logger.info("parking_spots table is empty. Initializing default spots...")

            all_configs = []

            # 2. Format standard spots
            for spot in PERMIT_SPOTS:
                all_configs.append(
                    {
                        "spot_number": spot,
                        "spot_type": "resident",
                        "is_guest": False,
                    }
                )

            # 3. Format staff spots
            for spot in STAFF_SPOTS:
                all_configs.append(
                    {
                        "spot_number": spot,
                        "spot_type": "staff",
                        "is_guest": False,
                    }
                )

            # 4. Batch insert all new spots
            await self.supabase.table("parking_spots").insert(all_configs).execute()
            logger.info(f"Successfully initialized {len(all_configs)} parking spots!")

        except Exception:
            logger.exception("Parking spot initialization failed")

    def is_blackout(self, start, end):
        """Return whether any hour in the requested window falls inside staff blackout time."""
        curr = start
        while curr < end:
            d, h = curr.weekday(), curr.hour
            for blackout_day, blackout_start, blackout_end in STAFF_PARKING_BLACKOUTS:
                if d == blackout_day and blackout_start <= h < blackout_end:
                    return True
            curr += timedelta(hours=1)
        return False

    def get_staff_availability_windows(self, start_time: datetime, end_time: datetime):
        """Return a list of non-blackout time windows for the specified range."""
        windows = []
        current_window_start = None

        hour_iterator = start_time
        while hour_iterator < end_time:
            is_in_blackout = self.is_blackout(
                hour_iterator, hour_iterator + timedelta(hours=1)
            )

            if not is_in_blackout and current_window_start is None:
                current_window_start = hour_iterator
            elif is_in_blackout and current_window_start is not None:
                windows.append({"start": current_window_start, "end": hour_iterator})
                current_window_start = None

            hour_iterator += timedelta(hours=1)

        if current_window_start is not None:
            windows.append({"start": current_window_start, "end": end_time})

        return windows

    async def get_parking_data(self, now, cutoff):
        """Fetch all raw parking data from the IN-MEMORY cache (0ms latency)."""
        now_iso = now.isoformat()
        cutoff_iso = cutoff.isoformat()

        # Filter the local memory exactly like Supabase would
        valid_offers = [
            o
            for o in self.active_offers_cache
            if o["end_time"] > now_iso and o["start_time"] < cutoff_iso
        ]
        valid_claims = [
            c
            for c in self.active_claims_cache
            if c["end_time"] > now_iso and c["start_time"] < cutoff_iso
        ]

        return valid_offers, valid_claims, list(self.guest_spots_cache)

    async def create_offers(self, user_id, username, spot, base_start, base_end, weeks):
        """Create one or more weekly parking offers and return a user-facing confirmation."""
        async with self._get_mutation_lock_for_spot(spot):
            try:
                all_offers = []
                for i in range(weeks):
                    start = base_start + timedelta(weeks=i)
                    end = base_end + timedelta(weeks=i)

                    existing = await (
                        self.supabase.table("parking_offers")
                        .select("*")
                        .eq("spot_number", int(spot))
                        .lt("start_time", end.isoformat())
                        .gt("end_time", start.isoformat())
                        .execute()
                    )

                    if not existing.data:
                        all_offers.append(
                            {
                                "spot_number": int(spot),
                                "owner_id": str(user_id),
                                "owner_discord_username": username,
                                "start_time": start.isoformat(),
                                "end_time": end.isoformat(),
                            }
                        )

                if not all_offers:
                    return False, "❌ This spot is already offered for those times."

                await self.supabase.table("parking_offers").insert(all_offers).execute()

                start_label = self._format_datetime_label(base_start)
                end_label = self._format_datetime_label(base_end)
                recur_msg = f" for the next **{weeks} weeks**" if weeks > 1 else ""
                success_msg = (
                    f"📢 **Spot {spot}** listed{recur_msg}\n"
                    f"Start: {start_label}\n"
                    f"End: {end_label}"
                )

                await self.refresh_parking_cache()

                return True, success_msg
            except Exception as e:
                return False, f"❌ Database error: {e}"

    async def claim_resident_spot(self, user_id, username, spot, start, end):
        """Reserve a guest spot or a resident spot covered by an existing offer."""
        async with self._get_mutation_lock_for_spot(spot):
            conflict = await (
                self.supabase.table("parking_reservations")
                .select("*")
                .eq("spot_number", int(spot))
                .lt("start_time", end.isoformat())
                .gt("end_time", start.isoformat())
                .execute()
            )

            if conflict.data:
                return False, f"❌ Spot {spot} is already reserved."

            offer_id = None
            if int(spot) not in self.guest_spots_cache:
                offer = await (
                    self.supabase.table("parking_offers")
                    .select("id")
                    .eq("spot_number", int(spot))
                    .lte("start_time", start.isoformat())
                    .gte("end_time", end.isoformat())
                    .execute()
                )

                if not offer.data:
                    return False, f"❌ Spot {spot} isn't offered for that window."
                offer_id = offer.data[0]["id"]

            (
                await self.supabase.table("parking_reservations")
                .insert(
                    {
                        "spot_number": int(spot),
                        "claimer_id": str(user_id),
                        "claimer_discord_username": username,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                        "offer_id": offer_id,
                    }
                )
                .execute()
            )

            start_label = self._format_datetime_label(start)
            end_label = self._format_datetime_label(end)

            await self.refresh_parking_cache()

            return (
                True,
                f"✅ **Spot {spot}** reserved!\nStart: {start_label}\nEnd: {end_label}",
            )

    async def claim_staff_spot(self, user_id, username, start, end):
        """Assign the first available staff spot for a requested window."""
        async with self._staff_mutation_lock:
            if self.is_blackout(start, end):
                return False, "❌ Blackout hours active."

            conflicts = await (
                self.supabase.table("parking_reservations")
                .select("spot_number")
                .in_("spot_number", STAFF_SPOTS)
                .lt("start_time", end.isoformat())
                .gt("end_time", start.isoformat())
                .execute()
            )

            occupied = [int(row["spot_number"]) for row in conflicts.data]

            if len(occupied) >= len(STAFF_SPOTS):
                return False, "❌ Staff spots are full."

            assigned = (
                STAFF_SPOTS[0] if STAFF_SPOTS[0] not in occupied else STAFF_SPOTS[1]
            )
            await (
                self.supabase.table("parking_reservations")
                .insert(
                    {
                        "spot_number": assigned,
                        "claimer_id": str(user_id),
                        "claimer_discord_username": username,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                    }
                )
                .execute()
            )

            start_label = self._format_datetime_label(start)
            end_label = self._format_datetime_label(end)

            await self.refresh_parking_cache()

            return (
                True,
                f"✅ Staff Spot reserved!\nStart: {start_label}\nEnd: {end_label}",
            )

    async def cancel_action(self, user_id, action_type, record_id):
        """Cancel one selected offer or reservation and return any affected user mentions."""
        try:
            table_name = (
                "parking_offers" if action_type == "offer" else "parking_reservations"
            )
            response = await (
                self.supabase.table(table_name)
                .select("spot_number")
                .eq("id", str(record_id))
                .execute()
            )

            spot = int(response.data[0]["spot_number"]) if response.data else None
        except Exception:
            spot = None

        lock = (
            self._get_mutation_lock_for_spot(spot)
            if spot is not None
            else self._fallback_mutation_lock
        )

        async with lock:
            now_iso = datetime.now(LOCAL_TZ).isoformat()

            if action_type == "offer":
                target = await (
                    self.supabase.table("parking_offers")
                    .select("*")
                    .eq("owner_id", str(user_id))
                    .eq("id", str(record_id))
                    .gt("end_time", now_iso)
                    .execute()
                )

                if not target.data:
                    return False, "No matching offers.", None

                offer = target.data[0]
                claims = await (
                    self.supabase.table("parking_reservations")
                    .select("claimer_id")
                    .eq("offer_id", str(record_id))
                    .execute()
                )
                await (
                    self.supabase.table("parking_reservations")
                    .delete()
                    .eq("offer_id", str(record_id))
                    .execute()
                )
                await (
                    self.supabase.table("parking_offers")
                    .delete()
                    .eq("id", str(record_id))
                    .execute()
                )
                pings = list({f"<@{c['claimer_id']}>" for c in claims.data})

                spot_label = (
                    "Staff Spot"
                    if offer["spot_number"] in STAFF_SPOTS
                    else f"Spot {offer['spot_number']}"
                )
                return True, f"🔄 {spot_label} offer withdrawn.", pings

            target = await (
                self.supabase.table("parking_reservations")
                .select("*")
                .eq("claimer_id", str(user_id))
                .eq("id", str(record_id))
                .gt("end_time", now_iso)
                .execute()
            )

            if not target.data:
                return False, "No matching claims.", None

            reservation = target.data[0]
            await (
                self.supabase.table("parking_reservations")
                .delete()
                .eq("id", str(record_id))
                .execute()
            )
            spot_label = (
                "Staff Spot"
                if reservation["spot_number"] in STAFF_SPOTS
                else f"Spot {reservation['spot_number']}"
            )

            await self.refresh_parking_cache()

            return True, f"🔄 Reservation for {spot_label} cancelled.", None

    async def get_user_activity(self, user_id):
        """Fetch active offers and reservations for a specific user instantly from memory."""
        now_iso = datetime.now(LOCAL_TZ).isoformat()
        user_str = str(user_id)

        # Filter the global cache instead of querying Supabase
        user_offers = [
            offer
            for offer in self.active_offers_cache
            if offer["owner_id"] == user_str and offer["end_time"] > now_iso
        ]

        user_claims = [
            claim
            for claim in self.active_claims_cache
            if claim["claimer_id"] == user_str and claim["end_time"] > now_iso
        ]

        return user_offers, user_claims

    async def get_cancel_autocomplete_data(self, user_id, now):
        """Fetch active offers and reservations for cancel autocomplete instantly from memory."""
        now_iso = now.isoformat()
        user_str = str(user_id)

        # Filter the global cache instead of querying Supabase
        user_offers = [
            offer
            for offer in self.active_offers_cache
            if offer["owner_id"] == user_str and offer["end_time"] > now_iso
        ]

        user_claims = [
            claim
            for claim in self.active_claims_cache
            if claim["claimer_id"] == user_str and claim["end_time"] > now_iso
        ]

        return user_offers, user_claims

    async def get_claim_autocomplete_data(self, now):
        """Fetch guest spots, active offers, and active claims for claim autocomplete instantly from memory."""
        now_iso = now.isoformat()

        # Filter the global cache
        valid_offers = [
            offer for offer in self.active_offers_cache if offer["end_time"] > now_iso
        ]

        valid_claims = [
            claim for claim in self.active_claims_cache if claim["end_time"] > now_iso
        ]

        guest_spots = [{"spot_number": spot} for spot in self.guest_spots_cache]

        return guest_spots, valid_offers, valid_claims

    async def get_guest_spot_list(self) -> str:
        """Return the formatted string directly from the local cache."""
        if not self.guest_spots_cache:
            return "None"
        return ", ".join(map(str, sorted(self.guest_spots_cache)))

    def get_merged_availability(
        self, now, cutoff, raw_offers, raw_claims, is_guest=False, is_resident=True
    ):
        """Merge offer windows, subtract claims, and return a status header plus free blocks."""
        if is_guest:
            merged_windows = [{"start": now.replace(hour=0), "end": cutoff}]
        else:
            raw_sorted = sorted(raw_offers, key=lambda x: x["start"])
            if not raw_sorted:
                merged_windows = []
            else:
                merged_windows = []
                curr = raw_sorted[0].copy()
                for next_window in raw_sorted[1:]:
                    if next_window["start"] <= curr["end"]:
                        curr["end"] = max(curr["end"], next_window["end"])
                    else:
                        merged_windows.append(curr)
                        curr = next_window.copy()
                merged_windows.append(curr)

        blocks = []
        for window in merged_windows:
            window_start, window_end = (
                max(window["start"], now),
                min(window["end"], cutoff),
            )
            if window_start >= window_end:
                continue

            pointer = window_start
            relevant_claims = sorted(
                [
                    claim
                    for claim in raw_claims
                    if not (
                        claim["end"] <= window_start or claim["start"] >= window_end
                    )
                ],
                key=lambda x: x["start"],
            )

            for claim in relevant_claims:
                claim_start = max(claim["start"], window_start)
                if (claim_start - pointer) >= timedelta(
                    hours=MINIMUM_RESERVATION_HOURS
                ):
                    blocks.append((pointer, claim_start))
                pointer = max(pointer, claim["end"])

            if (window_end - pointer) >= timedelta(hours=MINIMUM_RESERVATION_HOURS):
                blocks.append((pointer, window_end))

        current_claim = next(
            (claim for claim in raw_claims if claim["start"] <= now < claim["end"]),
            None,
        )
        active_block = next(
            (block for block in blocks if block[0] <= now < block[1]), None
        )
        next_block = next((block for block in blocks if block[0] > now), None)

        if active_block:
            if active_block[1] >= cutoff:
                if not is_resident:
                    # Staff spots reset at 12 AM (Sun-Thu) or 2 AM (Fri-Sat)
                    reset_time_string = (
                        "Sun 2 AM"
                        if (
                            now.weekday() == FR.weekday
                            or now.weekday() == SA.weekday
                            or (
                                now.weekday() == SU.weekday
                                and now.hour < WEEKEND_GUEST_HOURS_END
                            )
                        )
                        else "12 AM"
                    )
                    header = f"🟢 Available Now (until {reset_time_string})"
                else:
                    header = "🟢 Available Now (All Week)"
            else:
                header = (
                    f"🟢 Available Now (until {active_block[1].strftime('%a %I%p')})"
                )
        elif current_claim:
            if next_block:
                header = f"🔴 Busy (Next: {next_block[0].strftime('%a %I%p')})"
            else:
                header = f"🔴 Busy until {current_claim['end'].strftime('%a %I%p')}"
        elif next_block:
            header = f"🕒 Unavailable (Next: {next_block[0].strftime('%a %I%p')})"
        else:
            header = "❌ Not Offered"

        return header, blocks

    def get_staff_cutoff(self, now: datetime) -> datetime:
        """Calculate the actual cutoff datetime for the current staff parking window."""
        # If it's Friday or Saturday, the window ends at 2 AM on Sunday.
        if now.weekday() in [FR.weekday, SA.weekday]:
            days_until_sunday = SU.weekday - now.weekday()
            return (now + timedelta(days=days_until_sunday)).replace(
                hour=WEEKEND_GUEST_HOURS_END, minute=0, second=0, microsecond=0
            )

        # If it's Sunday before 2 AM, the window also ends at 2 AM on Sunday.
        if now.weekday() == SU.weekday and now.hour < WEEKEND_GUEST_HOURS_END:
            return now.replace(
                hour=WEEKEND_GUEST_HOURS_END, minute=0, second=0, microsecond=0
            )

        # Otherwise (Sun-Thu), the window ends at midnight of the next day.
        return (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
