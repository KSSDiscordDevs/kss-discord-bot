import asyncio
import logging
from datetime import datetime, timezone

from bot.utils.db import get_mssql_connection

logger = logging.getLogger(__name__)


class RolesService:
    """Business logic and data access for role selection and directory updates."""

    def __init__(self, conn_str: str):
        """Store the shared SQL Server connection string."""
        self.conn_str = conn_str

    async def get_last_update(self, discord_id: int) -> str | None:
        """Fetches the ISO string of the user's last role update."""
        def _query():
            with get_mssql_connection(self.conn_str) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT last_role_update FROM dbo.residents WHERE discord_id = ?",
                    (str(discord_id),),
                )
                row = cursor.fetchone()
                if not row or row[0] is None:
                    return None
                val = row[0]
                return val.isoformat() if isinstance(val, datetime) else str(val)

        try:
            return await asyncio.to_thread(_query)
        except Exception as e:
            logger.exception(f"Failed to fetch last role update for {discord_id}: {e}")
            return None

    async def update_resident_status(
        self, discord_id: int, username: str, role_slug: str, email: str = None
    ) -> bool:
        """
        Upserts a resident's role status into SQL Server.
        If an email is provided (for alumni), it updates that field as well.
        Also timestamps the update to enforce the 30-day rate limit.
        """
        now_dt = datetime.now(timezone.utc)
        discord_id_str = str(discord_id)

        def _upsert():
            with get_mssql_connection(self.conn_str) as conn:
                cursor = conn.cursor()
                if email:
                    cursor.execute(
                        """
                        MERGE dbo.residents AS target
                        USING (SELECT ? AS discord_id) AS source
                        ON target.discord_id = source.discord_id
                        WHEN MATCHED THEN
                            UPDATE SET
                                username = ?,
                                community_role = ?,
                                last_role_update = ?,
                                email = ?
                        WHEN NOT MATCHED THEN
                            INSERT (discord_id, username, community_role, last_role_update, email)
                            VALUES (?, ?, ?, ?, ?);
                        """,
                        (
                            discord_id_str,
                            username,
                            role_slug,
                            now_dt,
                            email,
                            discord_id_str,
                            username,
                            role_slug,
                            now_dt,
                            email,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        MERGE dbo.residents AS target
                        USING (SELECT ? AS discord_id) AS source
                        ON target.discord_id = source.discord_id
                        WHEN MATCHED THEN
                            UPDATE SET
                                username = ?,
                                community_role = ?,
                                last_role_update = ?
                        WHEN NOT MATCHED THEN
                            INSERT (discord_id, username, community_role, last_role_update)
                            VALUES (?, ?, ?, ?);
                        """,
                        (
                            discord_id_str,
                            username,
                            role_slug,
                            now_dt,
                            discord_id_str,
                            username,
                            role_slug,
                            now_dt,
                        ),
                    )
                affected = cursor.rowcount > 0
                conn.commit()
                return affected

        try:
            return await asyncio.to_thread(_upsert)
        except Exception as e:
            logger.exception(f"Failed to update resident status for {username}: {e}")
            return False
