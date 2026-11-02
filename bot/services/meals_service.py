import csv
import logging
from datetime import datetime
from pathlib import Path

from postgrest import AsyncPostgrestClient

from bot.config import LOCAL_TZ
from bot.utils.meal_calendar import AcademicBreak, MealCalendarConfig

logger = logging.getLogger(__name__)


class MealsService:
    """Handles data fetching, caching, and business logic for the meal system."""

    def __init__(self, bot, supabase: AsyncPostgrestClient):
        """Initialize the service with the bot reference and a shared Supabase client."""
        self.bot = bot
        self.supabase = supabase
        self.calendar_config = None

    async def initialize_meals(self):
        """Initialize the meal calendar, academic breaks, and parse the CSV menu."""
        try:
            # 1. Initialize the active calendar if it doesn't exist
            calendar_check = (
                await self.supabase.table("meal_calendars")
                .select("id")
                .limit(1)
                .execute()
            )

            if not calendar_check.data:
                logger.info("meal_calendars empty. Initializing Spring 2026 term...")
                # Anchored to Tuesday, Jan 20, 2026
                await (
                    self.supabase.table("meal_calendars")
                    .insert(
                        {
                            "id": 1,
                            "term_name": "Spring 2026",
                            "semester_start": "2026-01-20T00:00:00Z",
                            "rotation_length_weeks": 4,
                            "is_active": True,
                        }
                    )
                    .execute()
                )

            # 2. Initialize academic breaks if empty
            break_check = (
                await self.supabase.table("academic_breaks")
                .select("id")
                .limit(1)
                .execute()
            )

            if not break_check.data:
                logger.info("academic_breaks empty. Initializing Spring Break...")
                # Pauses the rotation for the 9 days of Spring Break
                await (
                    self.supabase.table("academic_breaks")
                    .insert(
                        {
                            "id": 1,
                            "calendar_id": 1,
                            "name": "Spring Break",
                            "start_date": "2026-03-14T00:00:00Z",
                            "end_date": "2026-03-22T00:00:00Z",
                            "rotation_skip_days": 9,
                        }
                    )
                    .execute()
                )

            # 3. Check if meals table is already populated
            meal_check = (
                await self.supabase.table("meals").select("id").limit(1).execute()
            )

            if meal_check.data:
                logger.info("Meals table is already populated. Skipping CSV parsing.")
                return

            logger.info("Meals table is empty. Parsing meal_menu.csv...")

            # 4. Locate the CSV file
            # Adjust the .parent chain based on where this file lives in your project
            base_dir = Path(__file__).resolve().parent.parent.parent
            csv_path = base_dir / "docs" / "meal_menu.csv"

            if not csv_path.exists():
                logger.error(f"Meal menu CSV not found at {csv_path}")
                return

            all_meals = []

            # 5. Read and normalize the CSV data to match the new schema
            with open(csv_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)

                for row in reader:
                    day = row["Day"].strip()

                    # Loop through all 4 weeks and both meal types
                    for week in range(1, 5):
                        for meal_type in ["Lunch", "Dinner"]:
                            column_name = f"Week {week} - {meal_type}"
                            dish_name = row.get(column_name, "").strip()

                            # Only add if the cell is not blank
                            if dish_name:
                                all_meals.append(
                                    {
                                        "day": day,
                                        "week_number": week,
                                        "meal_type": meal_type,
                                        "dish_name": dish_name,
                                    }
                                )

            # 6. Batch insert all generated meal records
            if all_meals:
                await self.supabase.table("meals").insert(all_meals).execute()
                logger.info(f"Successfully initialized {len(all_meals)} meals!")
            else:
                logger.warning("CSV was read but no valid meals were found.")

        except Exception as e:
            logger.exception(f"Meal initialization failed: {e}")

    async def refresh_calendar_config(self) -> bool:
        """Queries Supabase for the active meal calendar and caches it."""
        try:
            # Now using the injected client via self.supabase
            response = (
                await self.supabase.table("meal_calendars")
                .select("*, academic_breaks(*)")
                .eq("is_active", True)
                .execute()
            )

            if not response.data:
                print("Warning: No active meal calendar found in database.")
                return False

            active_cal = response.data[0]

            breaks = [
                AcademicBreak(
                    name=b["name"],
                    start=datetime.fromisoformat(b["start_date"]).astimezone(LOCAL_TZ),
                    end=datetime.fromisoformat(b["end_date"]).astimezone(LOCAL_TZ),
                    rotation_skip_days=b["rotation_skip_days"],
                )
                for b in active_cal.get("academic_breaks", [])
            ]

            self.calendar_config = MealCalendarConfig(
                semester_start=datetime.fromisoformat(
                    active_cal["semester_start"]
                ).astimezone(LOCAL_TZ),
                rotation_length_weeks=active_cal["rotation_length_weeks"],
                breaks=breaks,
            )
            print("Successfully loaded Meal Calendar config from Supabase.")
            return True

        except Exception as e:
            print(f"Error fetching calendar config: {e}")
            return False

    def get_meal_from_cache(self, week: int, day: str, meal_type: str) -> str:
        """Filters the cached menu data for the specific meal."""
        meal_type = meal_type.lower()

        # Accessing the cache from the bot object where it's stored
        for meal in getattr(self.bot, "meal_cache", []):
            if (
                meal["week_number"] == week
                and meal["day"].strip() == day
                and meal["meal_type"] == meal_type
            ):
                return meal["dish_name"]

        return "No meal scheduled"

    def get_active_break_name(self, current_date: datetime) -> str | None:
        """Return the current academic break label, if one is configured."""
        if not self.calendar_config:
            return None

        for break_window in self.calendar_config.breaks:
            if break_window.start <= current_date <= break_window.end:
                return break_window.name

        return None

    def calculate_rotation_week(self, current_date: datetime) -> int:
        """Return the active meal rotation week for the configured calendar."""
        if not self.calendar_config:
            return 1  # Fallback if DB fails to load

        days_since_start = (current_date - self.calendar_config.semester_start).days

        for break_window in self.calendar_config.breaks:
            if current_date > break_window.end:
                days_since_start -= break_window.rotation_skip_days

        return (
            (max(0, days_since_start) // 7) % self.calendar_config.rotation_length_weeks
        ) + 1
