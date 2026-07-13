import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

sys.path.append(str(Path(__file__).parent.parent.absolute()))

# Also import generator methods
import scripts.generate_perf_corpus as gen
from app.config import settings
from app.embeddings.service import EmbeddingService
from app.indexing.service import IndexingService, progress
from app.storage.db import DatabaseManager
from app.vector_store.lancedb_client import LanceDBClient


class MemoryMonitor:
    def __init__(self, interval=0.1):
        self.interval = interval
        self.peak_rss = 0
        self.running = False
        self._thread = None

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join()

    def _monitor(self):
        process = psutil.Process()
        while self.running:
            try:
                rss = process.memory_info().rss
                if rss > self.peak_rss:
                    self.peak_rss = rss
            except Exception:  # noqa: S110
                pass
            print(
                f"[MemoryMonitor] Current RSS: {rss / 1024 / 1024:.2f} MB | Peak: {self.peak_rss / 1024 / 1024:.2f} MB",  # noqa: E501
                flush=True,
            )
            time.sleep(self.interval)


async def run_indexing_pass(name, indexing_service, monitor):
    print(f"Starting {name} ingestion benchmark...")

    # reset progress stats
    progress.processed_files = 0
    progress.total_chunks = 0

    monitor.peak_rss = 0
    monitor.start()

    t0 = time.perf_counter()
    await indexing_service.index_folders(["tests/fixtures/perf_corpus"])
    duration = time.perf_counter() - t0

    monitor.stop()

    total_files = progress.processed_files
    total_chunks = progress.total_chunks
    peak_rss_mb = monitor.peak_rss / (1024 * 1024)
    chunks_per_sec = total_chunks / duration if duration > 0 else 0

    print(f"\n================ {name.upper()} BENCHMARK RESULTS ================")
    print(f"Wall-clock Ingestion Time : {duration:.2f} seconds")
    print(f"Total Files Processed      : {total_files}")
    print(f"Total Chunks Processed     : {total_chunks}")
    print(f"Throughput                 : {chunks_per_sec:.2f} chunks/sec")
    print(f"Peak RSS Memory            : {peak_rss_mb:.2f} MB")
    print("===================================================\n")

    return {
        "wall_clock_seconds": duration,
        "total_files": total_files,
        "total_chunks": total_chunks,
        "chunks_per_second": chunks_per_sec,
        "peak_rss_mb": peak_rss_mb,
    }


async def run_benchmark():
    temp_dir = tempfile.gettempdir()
    temp_db_path = os.path.join(temp_dir, "temp_pma_metadata.db")
    temp_lancedb_dir = os.path.join(temp_dir, "temp_lancedb_data")
    corpus_dir = Path("tests/fixtures/perf_corpus")

    # Cleanup previous DB runs
    for p in [temp_db_path, temp_db_path + "-shm", temp_db_path + "-wal"]:
        if Path(p).exists():
            Path(p).unlink()
    if Path(temp_lancedb_dir).exists():
        shutil.rmtree(temp_lancedb_dir)

    # Guarantee clean 5031 file corpus
    if corpus_dir.exists():
        shutil.rmtree(corpus_dir)
    gen.main()

    # Configure settings
    settings.db_path = temp_db_path
    settings.lancedb_persist_dir = temp_lancedb_dir

    # Init components
    db = DatabaseManager(temp_db_path)
    await db.connect()
    await db.init_db()

    lancedb_client = LanceDBClient(persist_directory=temp_lancedb_dir)
    lancedb_client.connect()

    embedding_service = EmbeddingService()
    embedding_service.load_model()

    indexing_service = IndexingService(
        db=db, embedding_service=embedding_service, lancedb_client=lancedb_client
    )

    monitor = MemoryMonitor(interval=0.05)

    # 1. Cold Run
    _ = await run_indexing_pass("Cold", indexing_service, monitor)

    # Wait for the background wal_checkpoint to finish before proceeding to avoid overlap
    from app import state

    if state.bg_tasks:
        await asyncio.gather(*state.bg_tasks, return_exceptions=True)
        state.bg_tasks.clear()

    # 2. Add 150 files for Incremental Run
    print("Generating 150 additional files for incremental benchmark...")
    for i in range(5001, 5051):
        with open(corpus_dir / f"text_{i}.txt", "w", encoding="utf-8") as f:
            f.write(gen.generate_text_content(num_paragraphs=3))
    for i in range(1001, 1051):
        with open(corpus_dir / f"doc_{i}.md", "w", encoding="utf-8") as f:
            f.write(gen.generate_text_content(num_paragraphs=3))
    for i in range(501, 551):
        gen.create_docx(corpus_dir / f"spec_{i}.docx", gen.generate_text_content(num_paragraphs=2))

    # 3. Incremental Run
    _ = await run_indexing_pass("Incremental", indexing_service, monitor)

    # Wait for background wal_checkpoint from incremental run to finish before closing DB
    if state.bg_tasks:
        await asyncio.gather(*state.bg_tasks, return_exceptions=True)
        state.bg_tasks.clear()

    # Cleanup
    await db.close()
    await asyncio.sleep(1.0)
    for p in [temp_db_path, temp_db_path + "-shm", temp_db_path + "-wal"]:
        if Path(p).exists():
            Path(p).unlink()
    if Path(temp_lancedb_dir).exists():
        shutil.rmtree(temp_lancedb_dir)


if __name__ == "__main__":
    asyncio.run(run_benchmark())
