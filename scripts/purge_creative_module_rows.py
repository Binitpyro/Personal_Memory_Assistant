"""
scripts/purge_creative_module_rows.py
──────────────────────────────────────
One-shot cleanup for the license-boundary strip of app/api/modules.py:
that router used to write Houdini scene data from the private Creative
module directly into Core's `files`/`chunks` tables (path prefix
"houdini://"), bypassing normal ingestion - the size column held
len(chunks) instead of a byte size, text_preview was stored
uncompressed (unlike every other row, see db.py's zlib_decompress
convention), and start_offset/end_offset were always 0. Those rows
are worthless now that the handlers that produced and read them are
gone, and their uncompressed text_preview would break the first
consumer that expects compressed bytes (e.g. any query path that
doesn't happen to go through the isinstance(blob, str) passthrough
that let this go unnoticed).

Deletes `files` rows with path LIKE 'houdini://%'; chunks cascade via
the FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE in
schema.sql, and the chunk_fts triggers fire on that cascade the same
way they would for a normal single-row delete, so FTS stays in sync.

Usage (from the project root with the venv active):
    python scripts/purge_creative_module_rows.py           # dry run - report only
    python scripts/purge_creative_module_rows.py --execute # actually delete
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("purge_creative_module_rows")

_PATH_PREFIX = "houdini://"


async def main(execute: bool) -> None:
    from app.config import settings
    from app.storage.db import DatabaseManager

    db = DatabaseManager(settings.db_path)
    await db.connect()

    conn = db._get_conn()
    async with conn.execute(
        "SELECT id, path, size, modified_at FROM files WHERE path LIKE ? || '%'",
        (_PATH_PREFIX,),
    ) as cursor:
        rows = list(await cursor.fetchall())

    if not rows:
        logger.info("No orphaned '%s*' rows found. Nothing to do.", _PATH_PREFIX)
        await db.close()
        return

    file_ids = [r["id"] for r in rows]
    async with conn.execute(
        f"SELECT COUNT(*) FROM chunks WHERE file_id IN ({','.join('?' * len(file_ids))})",  # nosec B608 # noqa: S608
        file_ids,
    ) as cursor:
        chunk_count_row = await cursor.fetchone()
    chunk_count = chunk_count_row[0] if chunk_count_row else 0

    logger.info(
        "Found %d orphaned Creative-module file(s), %d chunk row(s):", len(rows), chunk_count
    )
    for r in rows[:20]:
        logger.info("  file_id=%s path=%r modified_at=%s", r["id"], r["path"], r["modified_at"])
    if len(rows) > 20:
        logger.info("  … and %d more", len(rows) - 20)

    if not execute:
        logger.info("Dry run - no changes made. Re-run with --execute to delete these rows.")
        await db.close()
        return

    await conn.execute("DELETE FROM files WHERE path LIKE ? || '%'", (_PATH_PREFIX,))
    await conn.commit()
    logger.info("Deleted %d file(s) and their cascaded chunks.", len(rows))

    await db.close()


def cli_main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete the rows. Without this flag, only reports what would be deleted.",
    )
    args = parser.parse_args()
    asyncio.run(main(execute=args.execute))


if __name__ == "__main__":
    cli_main()
