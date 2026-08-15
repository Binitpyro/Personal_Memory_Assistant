import asyncio
import contextlib
import logging
import sys
import tracemalloc
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.embeddings.service import EmbeddingService
from app.storage.db import DatabaseManager
from app.vector_store.lancedb_client import LanceDBClient

logging.basicConfig(level=logging.INFO)


async def run_profiling():
    tracemalloc.start()
    print("Tracemalloc started.")

    # Initialize components
    db = DatabaseManager()
    await db.init_db(schema_path=settings.schema_path)

    embedding_service = EmbeddingService()
    embedding_service.load_model()

    lancedb_client = LanceDBClient()
    await asyncio.get_running_loop().run_in_executor(None, lancedb_client.connect)
    with contextlib.suppress(Exception):
        # Three empty lists, not one: add_documents takes ids/embeddings/
        # metadatas and returns early on empty ids. The one-argument call raised
        # TypeError, which contextlib.suppress swallowed, so this table warm-up
        # had silently never run.
        await lancedb_client.add_documents([], [], [])

    dummy_text_512 = "word " * 512
    for b_size in [64, 512]:
        print(f"Testing embedding batch_size={b_size} at max sequence length 512...", flush=True)
        texts = [dummy_text_512] * b_size
        snap_before = tracemalloc.take_snapshot()
        embs = await embedding_service.embed_texts(texts, batch_size=b_size)
        snap_after = tracemalloc.take_snapshot()
        diff = snap_after.compare_to(snap_before, "filename")
        total_allocated = sum(stat.size for stat in diff)
        print(
            f"Batch {b_size} @ seq_len 512 produced {len(embs)} vectors ({len(embs[0])} dim). Memory delta: {total_allocated / (1024 * 1024):.2f} MB",
            flush=True,
        )

    print("Memory profiling complete.", flush=True)


if __name__ == "__main__":
    # Ensure Windows asyncio loop policy is correct
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_profiling())
