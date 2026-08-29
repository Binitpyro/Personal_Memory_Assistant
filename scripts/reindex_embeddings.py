"""
scripts/reindex_embeddings.py
─────────────────────────────
Re-embeds all existing chunks and rebuilds the LanceDB vector cache
and the ``chunk_embeddings`` SQLite table from scratch.

Run this whenever you change ``embedding_model`` in config/.env — it keeps
the LanceDB host cache and the portable SQLite BLOB store consistent with
each other and with the current model's vector space.

Usage (from the project root with the venv active):
    python scripts/reindex_embeddings.py

Notes:
- SQLite file metadata (``files``, ``chunks``, etc.) is NOT touched.
- Only ``chunk_embeddings`` and the LanceDB collection are rebuilt.
- Progress is printed to stdout so you can watch it run.
- Works in both ``portable`` and ``split_brain`` LanceDB modes.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reindex")


async def main() -> None:
    from app.config import settings
    from app.embeddings.service import EmbeddingService
    from app.indexing.reembed import ReembedError, reembed_all
    from app.storage.db import DatabaseManager
    from app.vector_store.lancedb_client import LanceDBClient

    db = DatabaseManager(settings.db_path)
    await db.connect()

    embedding_svc = EmbeddingService(settings.embedding_model)
    logger.info("Loading model: %s …", settings.embedding_model)
    embedding_svc.load_model()
    logger.info("Model loaded (optimal_batch_size=%d).", embedding_svc.optimal_batch_size)

    lance = LanceDBClient(str(settings.lancedb_persist_dir))
    lance.connect()
    logger.info("Clearing vectors and rebuilding …")
    try:
        result = await reembed_all(db, embedding_svc, lance)
    except ReembedError as err:
        logger.error("%s Aborting.", err)
        await db.close()
        sys.exit(1)

    await db.close()
    logger.info(
        "Done! Re-embedded %d/%d chunks and %d summaries with model '%s'.",
        result["chunks"],
        result["total_chunks"],
        result["summaries"],
        settings.embedding_model,
    )


def cli_main():
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
