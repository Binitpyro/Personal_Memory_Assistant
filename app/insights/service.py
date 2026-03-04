import logging
import os
from typing import Dict, Any, List
from app.storage.db import DatabaseManager

logger = logging.getLogger(__name__)

class InsightsService:
    def __init__(self, db: DatabaseManager):
        self.db = db

    async def get_stats(self) -> Dict[str, Any]:
        """Aggregates storage insights from the database."""
        stats: Dict[str, Any] = {
            "total_size_bytes": 0,
            "file_count": 0,
            "database_size_bytes": 0,
            "top_files": [],
            "cold_files": [],
            "type_breakdown": {},
            "error": None,
        }

        try:
            stats["database_size_bytes"] = os.path.getsize(self.db.db_path) if os.path.exists(self.db.db_path) else 0
            rows = await self.db.execute_query("SELECT SUM(size), COUNT(*) FROM files")
            if rows:
                stats["total_size_bytes"] = rows[0][0] or 0
                stats["file_count"] = rows[0][1] or 0

            rows = await self.db.execute_query(
                "SELECT path, size FROM files ORDER BY size DESC LIMIT 10"
            )
            stats["top_files"] = [{"path": r[0], "size": r[1]} for r in rows]

            rows = await self.db.execute_query(
                "SELECT path, size, usage_count FROM files ORDER BY usage_count ASC, size DESC LIMIT 15"
            )
            stats["cold_files"] = [{"path": r[0], "size": r[1], "usage_count": r[2]} for r in rows]

            rows = await self.db.execute_query(
                "SELECT type, COUNT(*), SUM(size) FROM files GROUP BY type"
            )
            stats["type_breakdown"] = {
                r[0]: {"count": r[1], "size": r[2]} for r in rows
            }

        except Exception as e:
            logger.error("Error fetching insights: %s", e)
            stats["error"] = "Failed to load insights. Check server logs for details."

        return stats

    async def get_filtered_files(self, type_filter: str) -> Dict[str, Any]:
        """Returns top and cold files filtered by extension type."""
        result: Dict[str, Any] = {"top_files": [], "cold_files": []}
        try:
            # Ensure type_filter matches stored format (usually lowercase, no dot)
            clean_type = type_filter.lower().lstrip('.')
            
            rows = await self.db.execute_query(
                "SELECT path, size FROM files WHERE LOWER(type) = ? ORDER BY size DESC LIMIT 15",
                (clean_type,),
            )
            result["top_files"] = [{"path": r[0], "size": r[1]} for r in rows]

            rows = await self.db.execute_query(
                "SELECT path, size, usage_count FROM files WHERE LOWER(type) = ? "
                "ORDER BY usage_count ASC, size DESC LIMIT 15",
                (clean_type,),
            )
            result["cold_files"] = [{"path": r[0], "size": r[1], "usage_count": r[2]} for r in rows]
        except Exception as e:
            logger.error("Error fetching filtered insights for type '%s': %s", type_filter, e)
            result["error"] = f"Failed to load insights for {type_filter}."
        return result
