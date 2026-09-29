"""Central runtime configuration for the Discord bot."""

import os
from pathlib import Path

import discord
import pytz
from dotenv import load_dotenv

load_dotenv()

# --- Core Bot Settings ---
BASE_DIR = Path(__file__).resolve().parent.parent
GUILD_ID = os.getenv("GUILD_ID")
MY_GUILD = discord.Object(id=GUILD_ID)
BOT_NAME = "Felipe"
EXTENSIONS = [
    "bot.cogs.meals",
    "bot.cogs.lates",
    "bot.cogs.parking",
    "bot.cogs.general",
    "bot.cogs.feedback",
    "bot.cogs.roles",
    "bot.cogs.interests",
    "bot.cogs.debug",
    # "bot.cogs.gemini_chat",
]
LOCAL_TZ = pytz.timezone("America/Chicago")

# --- Lates System Settings ---
# Role names should be lowercase. These are used to identify a user's house.
HOUSE_ROLE_CONFIG = {
    "koinonian": "koinonian",
    "stratfordite": "stratfordite",
    "suttonite": "suttonite",
}
# Defines which houses can see each other's late plates.
# Each tuple is a group. Houses in the same group can see each other's lates.
LATES_VIEW_GROUPS = [
    ("koinonian",),  # Koinonian can only see their own lates
    ("stratfordite", "suttonite"),  # Stratford and Sutton can see each other's lates
]


# --- Parking System Settings ---
PERMIT_SPOTS = list(range(1, 34)) + list(range(41, 47))
STAFF_SPOTS = [998, 999]

# Blackout periods for staff parking reservations.
WEEKEND_GUEST_HOURS_END = 2  # Guest hours end at 2 AM
SUNDAY_STAFF_BLACKOUT_END_HOUR = 14  # 2 PM
# Each tuple is (weekday, start_hour, end_hour), where end_hour is exclusive.
# Weekdays are Monday=0 to Sunday=6.
STAFF_PARKING_BLACKOUTS = [
    # Monday-Friday, before 5 PM (00:00 - 16:59)
    *[(day, 0, 17) for day in range(5)],
    # Sunday, 2 AM - 2 PM (02:00 - 13:59)
    (6, WEEKEND_GUEST_HOURS_END, SUNDAY_STAFF_BLACKOUT_END_HOUR),
]

MINIMUM_RESERVATION_HOURS = 1
MAXIMUM_RESERVATION_DAYS = 3
MAXIMUM_RESERVATION_HOURS = MAXIMUM_RESERVATION_DAYS * 24

MINIMUM_OFFER_HOURS = 2
MAXIMUM_OFFER_DAYS = 7

CLAIM_SPOT_MAX_AUTOCOMPLETE_CHOICES = 5
CANCEL_SPOT_MAX_AUTOCOMPLETE_CHOICES = 25
PARKING_STATUS_CACHE_TTL_SECONDS = 15

# --- Roles System Settings ---
KOINONIAN_ROLE_ID = 1402659975045578793
SUTTONITE_ROLE_ID = 1402660183225798666
STRATFORDITE_ROLE_ID = 1402660051008622734
ALUMNI_ROLE_ID = 1407422324847673397

RA_LOG_CHANNEL_ID = 1501386295140679750

# --- Interest Channels Settings ---
# Opt-in channels that members can join/leave themselves via the dropdown spawned
# by /spawn_interest_menu. Access is granted with a per-member channel permission
# overwrite, so no extra roles are needed. The bot must be able to view each
# channel listed here. Up to 25 entries (Discord select menu limit).
INTEREST_CHANNELS = [
    {
        "channel_id": 1446951268495917240,
        "label": "Ice Skating",
        "description": "Skating trips and meetups",
        "emoji": "⛸️",
    },
    {
        "channel_id": 1547371525336858664,
        "label": "Study",
        "description": "Group study",
        "emoji": "📒",
    },
    {
        "channel_id": 1554169487052513401,
        "label": "Tim Keller Discussion Group",
        "description": "Tim Keller Discussion Group",
        "emoji": "📖🎙",
    },
    {
        "channel_id": 1554304468965793864,
        "label": "Professional Development",
        "description": "Professional Development Help and Advice",
        "emoji": "🤝",
    },
    # Add more channels here as they are created, e.g.:
    # {"channel_id": 0, "label": "Intramurals", "description": "IM sports teams", "emoji": "🏀"},
    # {"channel_id": 0, "label": "Study", "description": "Study sessions", "emoji": "📚"},
]

# --- Monitoring Settings ---
# The gateway heartbeat pings Healthchecks.io every GATEWAY_HEARTBEAT_MINUTES while
# the bot is healthy. The API probe makes one harmless REST call every
# API_HEALTH_PROBE_MINUTES so an HTTP-level block (a Discord rate limit or a
# Cloudflare ban on this host's shared IP) is noticed even when nobody is running
# commands. Any HTTP 429 withholds the heartbeat for RATE_LIMIT_UNHEALTHY_SECONDS,
# which is deliberately longer than one probe interval so back-to-back failed
# probes keep it withheld and Healthchecks.io raises an alert.
GATEWAY_HEARTBEAT_MINUTES = 3.0
API_HEALTH_PROBE_MINUTES = 5.0
RATE_LIMIT_UNHEALTHY_SECONDS = int(API_HEALTH_PROBE_MINUTES * 60 * 2)
HIGH_GATEWAY_LATENCY_SECONDS = 1.0

# --- Discord API Settings ---
DISCORD_EMBED_FIELD_VALUE_LIMIT = 1024
TRUNCATION_SUFFIX = "..."
TRUNCATION_LIMIT = DISCORD_EMBED_FIELD_VALUE_LIMIT - len(TRUNCATION_SUFFIX)
