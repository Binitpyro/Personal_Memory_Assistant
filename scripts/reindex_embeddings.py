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
import zlib
from pathlib import Path

import numpy as np

# Ensure project root is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reindex")


async def main() -> None:
    from app.config import settings
    from app.embeddings.service import EmbeddingService
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
    logger.info("Clearing existing LanceDB collection …")
    await lance.clear_all()

    logger.info("Clearing existing chunk_embeddings table …")
    conn = db._get_conn()
    await conn.execute("DELETE FROM chunk_embeddings")
    await conn.commit()

    # Count total chunks
    async with conn.execute("SELECT COUNT(*) FROM chunks") as cur:
        row = await cur.fetchone()
        total_chunks = row[0] if row else 0

    logger.info("Total chunks to re-embed: %d", total_chunks)
    if total_chunks == 0:
        logger.warning("No chunks found — nothing to do.")
        await db.close()
        return

    batch_size = 5000
    offset = 0
    processed = 0

    def _decompress(blob) -> str:
        if not blob:
            return ""
        if isinstance(blob, str):
            return blob
        try:
            return zlib.decompress(blob).decode("utf-8")
        except Exception:
            return str(blob)

    while True:
        async with conn.execute(
            """
            SELECT c.id, c.text_preview, f.path AS file_path, f.folder_tag
            FROM chunks c
            JOIN files f ON c.file_id = f.id
            LIMIT ? OFFSET ?
            """,
            (batch_size, offset),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            break

        n_rows = len(rows)
        ids_int = [r[0] for r in rows]
        ids_str = [str(r[0]) for r in rows]
        texts = [_decompress(r[1]) for r in rows]
        file_paths = [r[2] for r in rows]
        folder_tags = [r[3] for r in rows]

        logger.info(
            "Embedding batch offset=%d  (%d chunks, total so far: %d/%d) …",
            offset,
            n_rows,
            processed,
            total_chunks,
        )

        # Embed — uses the synchronous path because we're already in an async context
        embeddings_list: list[list[float]] = await embedding_svc.embed_texts(texts)
        embeddings_np = [np.array(e, dtype=np.float32) for e in embeddings_list]

        # ── Write to chunk_embeddings (portable BLOB store) ──────────────────────
        blob_data = [
            (chunk_id, np.array(emb, dtype=np.float16).tobytes())
            for chunk_id, emb in zip(ids_int, embeddings_list, strict=True)
        ]
        await db.insert_chunk_embeddings_bulk(blob_data)

        # ── Write to LanceDB (host-local vector index) ────────────────────────────
        lancedb_metas = [
            {"chunk_id": cid, "file_path": fp, "folder_tag": ft}
            for cid, fp, ft in zip(ids_str, file_paths, folder_tags, strict=True)
        ]
        await lance.add_documents(ids_str, embeddings_np, lancedb_metas)

        processed += n_rows
        logger.info("  … done  %d/%d", processed, total_chunks)

        if n_rows < batch_size:
            break
        offset += batch_size

    await db.close()
    logger.info(
        "Done! Re-embedded %d/%d chunks with model '%s'.",
        processed,
        total_chunks,
        settings.embedding_model,
    )


def cli_main():
    asyncio.run(main())

if __name__ == "__main__":
    cli_main()
