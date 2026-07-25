import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

# Ensure app is in path
sys.path.append(str(Path(__file__).parent.parent.absolute()))

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
            except Exception as e:
                print(f"Failed to get memory info: {e}")

            # Print current RSS periodically
            print(
                f"[MemoryMonitor] Current RSS: {rss / 1024 / 1024:.2f} MB | Peak: {self.peak_rss / 1024 / 1024:.2f} MB",
                flush=True,
            )
            time.sleep(self.interval)


async def run_benchmark():
    temp_dir = tempfile.gettempdir()
    temp_db_path = os.path.join(temp_dir, "temp_pma_metadata.db")
    temp_lancedb_dir = os.path.join(temp_dir, "temp_lancedb_data")
    corpus_dir = "tests/fixtures/perf_corpus"

    # Ensure cleanup of any previous run
    for p in [temp_db_path, temp_db_path + "-shm", temp_db_path + "-wal"]:
        if Path(p).exists():
            try:
                Path(p).unlink()
            except Exception as e:
                print(f"Could not unlink {p}: {e}")
    if Path(temp_lancedb_dir).exists():
        try:
            shutil.rmtree(temp_lancedb_dir)
        except Exception as e:
            print(f"Could not rmtree {temp_lancedb_dir}: {e}")

    # Configure settings
    settings.db_path = temp_db_path
    settings.lancedb_persist_dir = temp_lancedb_dir
    Path("tests/fixtures").mkdir(parents=True, exist_ok=True)

    # Initialize components
    db = DatabaseManager(temp_db_path)
    await db.connect()
    await db.init_db()

    lancedb_client = LanceDBClient(persist_directory=temp_lancedb_dir)
    lancedb_client.connect()

    embedding_service = EmbeddingService()
    print("Loading embedding model...")
    # Synchronously load embedding model
    embedding_service.load_model()

    indexing_service = IndexingService(
        db=db, embedding_service=embedding_service, lancedb_client=lancedb_client
    )

    # Start memory monitoring
    monitor = MemoryMonitor(interval=0.1)
    monitor.start()

    print("Starting ingestion benchmark...")
    t0 = time.perf_counter()

    # Run the indexing
    await indexing_service.index_folders([corpus_dir])

    duration = time.perf_counter() - t0
    monitor.stop()

    # Gather results
    # IndexingService progress updates processed_files and total_chunks
    total_files = progress.processed_files
    total_chunks = progress.total_chunks
    peak_rss_mb = monitor.peak_rss / (1024 * 1024)
    chunks_per_sec = total_chunks / duration if duration > 0 else 0

    print("\n================ BENCHMARK RESULTS ================")
    print(f"Wall-clock Ingestion Time : {duration:.2f} seconds")
    print(f"Total Files Processed      : {total_files}")
    print(f"Total Chunks Processed     : {total_chunks}")
    print(f"Throughput                 : {chunks_per_sec:.2f} chunks/sec")
    print(f"Peak RSS Memory            : {peak_rss_mb:.2f} MB")
    print("===================================================\n")

    # Cleanup database connections
    await db.close()

    # Clean up physical files
    await asyncio.sleep(1.0)  # Wait for file handles to close
    for p in [temp_db_path, temp_db_path + "-shm", temp_db_path + "-wal"]:
        if Path(p).exists():
            try:
                Path(p).unlink()
            except Exception as e:
                print(f"Could not delete temp db file {p}: {e}")
    if Path(temp_lancedb_dir).exists():
        try:
            shutil.rmtree(temp_lancedb_dir)
        except Exception as e:
            print(f"Could not delete temp LanceDB directory {temp_lancedb_dir}: {e}")

    # Write a JSON report
    import json

    report = {
        "wall_clock_seconds": duration,
        "total_files": total_files,
        "total_chunks": total_chunks,
        "chunks_per_second": chunks_per_sec,
        "peak_rss_mb": peak_rss_mb,
    }
    with open("tests/fixtures/perf_corpus/benchmark_results.json", "w") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    if not Path("tests/fixtures/perf_corpus").exists():
        print(
            "Corpus directory tests/fixtures/perf_corpus does not exist. Run scripts/generate_perf_corpus.py first."
        )
        sys.exit(1)
    asyncio.run(run_benchmark())
