import asyncio
import csv
import logging
from datetime import datetime
from pathlib import Path

import pyodbc

from bot.config import LOCAL_TZ
from bot.utils.meal_calendar import AcademicBreak, MealCalendarConfig

logger = logging.getLogger(__name__)


class MealsService:
    """Handles data fetching, caching, and business logic for the meal system."""

    def __init__(self, bot, conn_str: str):
        """Initialize the service with the bot reference and a shared SQL Server connection string."""
        self.bot = bot
        self.conn_str = conn_str
        self.calendar_config = None

    def _rows_to_dicts(self, cursor: pyodbc.Cursor) -> list[dict]:
        """Convert pyodbc cursor rows into dicts matching Supabase's row format."""
        if not cursor.description:
            return []
        columns = [col[0] for col in cursor.description]
        results = []
        for row in cursor.fetchall():
            item = {}
            for col, val in zip(columns, row):
                if isinstance(val, datetime):
                    item[col] = val.isoformat()
                else:
                    item[col] = val
            results.append(item)
        return results

    async def initialize_meals(self):
        """Initialize the meal calendar, academic breaks, and parse the CSV menu."""
        def _init():
            with pyodbc.connect(self.conn_str) as conn:
                cursor = conn.cursor()

                # 1. Initialize the active calendar if it doesn't exist
                cursor.execute("SELECT TOP 1 id FROM dbo.meal_calendars")
                if not cursor.fetchone():
                    logger.info("meal_calendars empty. Initializing Spring 2026 term...")
                    # Anchored to Tuesday, Jan 20, 2026
                    cursor.execute("SET IDENTITY_INSERT dbo.meal_calendars ON;")
                    cursor.execute(
                        """
                        INSERT INTO dbo.meal_calendars (
                            id, term_name, semester_start, rotation_length_weeks, is_active
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (1, "Spring 2026", "2026-01-20T00:00:00Z", 4, 1),
                    )
                    cursor.execute("SET IDENTITY_INSERT dbo.meal_calendars OFF;")
                    conn.commit()

                # 2. Initialize academic breaks if empty
                cursor.execute("SELECT TOP 1 id FROM dbo.academic_breaks")
                if not cursor.fetchone():
                    logger.info("academic_breaks empty. Initializing Spring Break...")
                    # Pauses the rotation for the 9 days of Spring Break
                    cursor.execute("SET IDENTITY_INSERT dbo.academic_breaks ON;")
                    cursor.execute(
                        """
                        INSERT INTO dbo.academic_breaks (
                            id, calendar_id, [name], start_date, end_date, rotation_skip_days
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            1,
                            1,
                            "Spring Break",
                            "2026-03-14T00:00:00Z",
                            "2026-03-22T00:00:00Z",
                            9,
                        ),
                    )
                    cursor.execute("SET IDENTITY_INSERT dbo.academic_breaks OFF;")
                    conn.commit()

                # 3. Check if meals table is already populated
                cursor.execute("SELECT TOP 1 id FROM dbo.meals")
                if cursor.fetchone():
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
                                        (day, week, meal_type, dish_name)
                                    )

                # 6. Batch insert all generated meal records
                if all_meals:
                    cursor.executemany(
                        """
                        INSERT INTO dbo.meals ([day], week_number, meal_type, dish_name)
                        VALUES (?, ?, ?, ?)
                        """,
                        all_meals,
                    )
                    conn.commit()
                    logger.info(f"Successfully initialized {len(all_meals)} meals!")
                else:
                    logger.warning("CSV was read but no valid meals were found.")

        try:
            await asyncio.to_thread(_init)
        except Exception as e:
            logger.exception(f"Meal initialization failed: {e}")

    async def refresh_calendar_config(self) -> bool:
        """Queries SQL Server for the active meal calendar and caches it."""
        def _query():
            with pyodbc.connect(self.conn_str) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM dbo.meal_calendars WHERE is_active = 1"
                )
                calendars = self._rows_to_dicts(cursor)
                if not calendars:
                    return None, []

                active_cal = calendars[0]
                cursor.execute(
                    "SELECT * FROM dbo.academic_breaks WHERE calendar_id = ?",
                    (active_cal["id"],),
                )
                breaks_rows = self._rows_to_dicts(cursor)
                return active_cal, breaks_rows

        try:
            active_cal, breaks_rows = await asyncio.to_thread(_query)

            if not active_cal:
                print("Warning: No active meal calendar found in database.")
                return False

            breaks = [
                AcademicBreak(
                    name=b["name"],
                    start=datetime.fromisoformat(b["start_date"]).astimezone(LOCAL_TZ),
                    end=datetime.fromisoformat(b["end_date"]).astimezone(LOCAL_TZ),
                    rotation_skip_days=b["rotation_skip_days"],
                )
                for b in breaks_rows
            ]

            self.calendar_config = MealCalendarConfig(
                semester_start=datetime.fromisoformat(
                    active_cal["semester_start"]
                ).astimezone(LOCAL_TZ),
                rotation_length_weeks=active_cal["rotation_length_weeks"],
                breaks=breaks,
            )
            print("Successfully loaded Meal Calendar config from SQL Server.")
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
