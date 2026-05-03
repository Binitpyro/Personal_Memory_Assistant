import asyncio
import logging
import os
from typing import Any

from app.storage.db import DatabaseManager

logger = logging.getLogger(__name__)


class InsightsService:
    def __init__(self, db: DatabaseManager):
        self.db = db

    async def get_dashboard_insights(self) -> dict[str, Any]:
        """Aggregates storage insights from the database.
        P1-1: All 4 queries run concurrently via asyncio.gather instead of sequentially.
        """
        stats: dict[str, Any] = {
            "total_size_bytes": 0,
            "file_count": 0,
            "database_size_bytes": 0,
            "top_files": [],
            "cold_files": [],
            "type_breakdown": {},
            "error": None,
        }

        try:
            db_path = getattr(self.db, "db_path", None)
            stats["database_size_bytes"] = (
                os.path.getsize(db_path) if db_path and os.path.exists(db_path) else 0
            )

            summary_rows, top_rows, cold_rows, type_rows = await asyncio.gather(
                self.db.execute_query("SELECT SUM(size), COUNT(*) FROM files"),
                self.db.execute_query("SELECT path, size FROM files ORDER BY size DESC LIMIT 10"),
                self.db.execute_query(
                    "SELECT path, size, usage_count FROM files "
                    "ORDER BY usage_count ASC, size DESC LIMIT 15"
                ),
                self.db.execute_query("SELECT type, COUNT(*), SUM(size) FROM files GROUP BY type"),
            )

            if summary_rows:
                stats["total_size_bytes"] = summary_rows[0][0] or 0
                stats["file_count"] = summary_rows[0][1] or 0

            stats["top_files"] = [{"path": r[0], "size": r[1]} for r in top_rows]
            stats["cold_files"] = [
                {"path": r[0], "size": r[1], "usage_count": r[2]} for r in cold_rows
            ]
            stats["type_breakdown"] = {r[0]: {"count": r[1], "size": r[2] or 0} for r in type_rows}

        except Exception as e:
            logger.error("Error fetching insights: %s", e)
            stats["error"] = "Failed to load insights. Check server logs for details."

        return stats

    async def get_insights_for_extension(self, type_filter: str) -> dict[str, Any]:
        """Returns top and cold files filtered by extension type."""
        result: dict[str, Any] = {"top_files": [], "cold_files": []}
        try:
            # Robustly handle extension format: ensure it has exactly one leading dot
            clean_type = type_filter.lower().lstrip(".")
            clean_type = f".{clean_type}"

            rows = await self.db.execute_query(
                "SELECT path, size FROM files WHERE type = ? ORDER BY size DESC LIMIT 15",
                (clean_type,),
            )
            result["top_files"] = [{"path": r[0], "size": r[1]} for r in rows]

            rows = await self.db.execute_query(
                "SELECT path, size, usage_count FROM files WHERE type = ? "
                "ORDER BY usage_count ASC, size DESC LIMIT 15",
                (clean_type,),
            )
            result["cold_files"] = [{"path": r[0], "size": r[1], "usage_count": r[2]} for r in rows]
        except Exception as e:
            logger.error("Error fetching filtered insights for type '%s': %s", type_filter, e)
            result["error"] = f"Failed to load insights for {type_filter}."
        return result
