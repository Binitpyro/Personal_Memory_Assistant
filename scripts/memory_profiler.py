import tracemalloc
import asyncio
import os
import sys
from pathlib import Path
import logging

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.storage.db import DatabaseManager
from app.embeddings.service import EmbeddingService
from app.vector_store.lancedb_client import LanceDBClient
from app.indexing.service import IndexingService

logging.basicConfig(level=logging.INFO)

async def run_profiling():
    tracemalloc.start()
    print("Tracemalloc started. Taking baseline snapshot...")
    snapshot1 = tracemalloc.take_snapshot()

    # Initialize components
    db = DatabaseManager()
    await db.init_db(schema_path=settings.schema_path)
    
    embedding_service = EmbeddingService()
    embedding_service.load_model_background()
    
    lancedb_client = LanceDBClient()
    await asyncio.get_running_loop().run_in_executor(None, lancedb_client.connect)
    
    indexing_service = IndexingService(db, embedding_service, lancedb_client)
    
    # We will try to index the project directory itself as a test (or just the app directory)
    test_folder = str(Path(__file__).parent.parent / "app")
    
    print(f"Indexing test folder: {test_folder}")
    try:
        await indexing_service.index_folders([test_folder])
    except Exception as e:
        print(f"Indexing error: {e}")
        
    print("Taking post-indexing snapshot...")
    snapshot2 = tracemalloc.take_snapshot()
    
    # Compare snapshots
    top_stats = snapshot2.compare_to(snapshot1, 'lineno')
    
    print("[ Top 20 memory allocation differences ]")
    for stat in top_stats[:20]:
        print(stat)

if __name__ == "__main__":
    # Ensure Windows asyncio loop policy is correct
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_profiling())
