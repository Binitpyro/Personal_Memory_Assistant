"""Vector-only rebuild: re-embed every chunk and summary, leaving text alone.

Extracted from ``scripts/reindex_embeddings.py`` so the script and the API route
share one implementation. A second copy of a rebuild loop is exactly the kind of
drift that produces a subtly wrong vector store, and the store is the thing this
code exists to keep correct.

What it does NOT touch: ``files``, ``chunks``, the FTS index, or query history.
Only ``chunk_embeddings`` and the LanceDB tables are rebuilt.
"""

from __future__ import annotations

import logging
import zlib
from typing import Any

import numpy as np

from app.config import settings
from app.indexing.summarizer import summary_embedding_text
from app.project_constants import chunk_embedding_text

logger = logging.getLogger(__name__)

_BATCH_SIZE = 5000


class ReembedError(RuntimeError):
    """A rebuild finished in a state that would silently degrade retrieval."""


def _decompress(blob: Any) -> str:
    if not blob:
        return ""
    if isinstance(blob, str):
        return blob
    try:
        return zlib.decompress(blob).decode("utf-8")
    except Exception:
        return str(blob)


async def _reembed_chunks(db, emb, lance, batch_size: int) -> tuple[int, int]:
    conn = db._get_conn()
    async with conn.execute("SELECT COUNT(*) FROM chunks") as cur:
        row = await cur.fetchone()
        total_chunks = row[0] if row else 0

    if total_chunks == 0:
        return 0, 0

    offset = 0
    processed = 0
    while True:
        async with conn.execute(
            """
            SELECT c.id, c.text_preview, f.path AS file_path, f.folder_tag
            FROM chunks c
            JOIN files f ON c.file_id = f.id
            ORDER BY c.id
            LIMIT ? OFFSET ?
            """,
            (batch_size, offset),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            break

        ids_int = [r[0] for r in rows]
        ids_str = [str(r[0]) for r in rows]
        # Must match the ingest-time form (service.py `_process_embed_stream_batch`).
        # r[2] is f.path; no offsets are selected here, which is exactly why the
        # strip is path-based rather than length-based.
        texts = [
            chunk_embedding_text(_decompress(r[1]), str(r[2]), settings.embed_chunk_prefix)
            for r in rows
        ]

        embeddings_list = await emb.embed_texts(texts)
        embeddings_np = [np.array(e, dtype=np.float32) for e in embeddings_list]

        await db.insert_chunk_embeddings_bulk(
            [
                (chunk_id, np.array(e, dtype=np.float16).tobytes())
                for chunk_id, e in zip(ids_int, embeddings_list, strict=True)
            ]
        )
        await lance.add_documents(
            ids_str,
            embeddings_np,
            [
                {"chunk_id": cid, "file_path": r[2], "folder_tag": r[3]}
                for cid, r in zip(ids_str, rows, strict=True)
            ],
        )

        processed += len(rows)
        logger.info("Re-embedded %d/%d chunks", processed, total_chunks)

        if len(rows) < batch_size:
            break
        offset += batch_size

    return processed, total_chunks


async def _rebuild_summaries(db, emb, lance, batch_size: int) -> int:
    """Rebuild ``pma_summaries`` after ``lance.clear_all()`` dropped it.

    ``clear_all()`` drops ``pma_chunks``, ``pma_summaries`` and ``query_cache``,
    but the chunk loop only repopulates ``pma_chunks``. Skipping this leaves the
    document-routing signal contributing nothing to retrieval while everything
    still appears to work, so it hard-fails rather than warns.
    """
    conn = db._get_conn()

    async with conn.execute(
        "SELECT COUNT(*) FROM files WHERE summary != '' AND summary NOT LIKE '[ERROR:%'"
    ) as cur:
        row = await cur.fetchone()
        expected_files = row[0] if row else 0

    written = 0
    offset = 0
    while expected_files:
        async with conn.execute(
            """
            SELECT id, path, folder_tag, summary
            FROM files
            WHERE summary != '' AND summary NOT LIKE '[ERROR:%'
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (batch_size, offset),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            break

        # De-scaffolded, matching service.py `_generate_summaries`. These two
        # loops HAD drifted: this one embedded the raw display string, so every
        # user-initiated rebuild silently replaced each summary vector with the
        # form summary_embedding_text measured as noise (recall 0.972 -> 0.819).
        # The module docstring above names this exact failure mode.
        embs = await emb.embed_texts([summary_embedding_text(r[1], str(r[3])) for r in rows])
        await lance.add_summaries_batch(
            [
                {
                    "doc_id": f"file_{r[0]}",
                    "embedding": e,
                    "metadata": {
                        "file_path": r[1],
                        "folder_tag": r[2] or "",
                        "is_folder_profile": "false",
                    },
                }
                for r, e in zip(rows, embs, strict=False)
            ]
        )
        written += len(rows)

        if len(rows) < batch_size:
            break
        offset += batch_size

    # Folder profiles live in the same table and were dropped too.
    async with conn.execute(
        "SELECT folder_tag, folder_path, profile_text FROM folder_profiles WHERE profile_text != ''"
    ) as cursor:
        profile_rows = await cursor.fetchall()

    if profile_rows:
        embs = await emb.embed_texts([str(r[2]) for r in profile_rows])
        await lance.add_summaries_batch(
            [
                {
                    "doc_id": f"folder_profile_{r[0]}",
                    "embedding": e,
                    "metadata": {
                        "file_path": r[1],
                        "folder_tag": r[0],
                        "is_folder_profile": "true",
                    },
                }
                for r, e in zip(profile_rows, embs, strict=False)
            ]
        )
        written += len(profile_rows)

    if expected_files and lance.count_rows("pma_summaries") == 0:
        raise ReembedError(
            f"pma_summaries is empty after rebuild, but {expected_files} files carry "
            "summaries. The document-routing signal would be dead."
        )

    return written


async def reembed_all(db, emb, lance, batch_size: int = _BATCH_SIZE) -> dict[str, int]:
    """Clear and rebuild every vector. Caller owns db/emb/lance lifecycle.

    Order matters and is asserted by tests: LanceDB is cleared first, then the
    SQLite mirror. ``clear_vectors_only`` touches ``chunk_embeddings`` only and
    its docstring is explicit that callers must clear LanceDB separately.
    """
    await lance.clear_all()
    await db.clear_vectors_only()

    chunks, total = await _reembed_chunks(db, emb, lance, batch_size)
    summaries = await _rebuild_summaries(db, emb, lance, batch_size)

    logger.info("Re-embed complete: %d/%d chunks, %d summaries", chunks, total, summaries)
    return {"chunks": chunks, "total_chunks": total, "summaries": summaries}
