import sys
import asyncio
import threading
from unittest.mock import MagicMock, patch

# Setup mock modules before importing app.embeddings.finetune
mock_sentence_transformers = MagicMock()
mock_torch = MagicMock()

# Stub classes needed from sentence_transformers
mock_sentence_transformers.SentenceTransformer = MagicMock()
mock_sentence_transformers.InputExample = MagicMock()
mock_sentence_transformers.losses = MagicMock()

# Stub classes needed from torch.utils.data
mock_dataloader = MagicMock()
mock_torch_data = MagicMock()
mock_torch_data.DataLoader = mock_dataloader
mock_torch.utils = MagicMock()
mock_torch.utils.data = mock_torch_data

sys.modules["sentence_transformers"] = mock_sentence_transformers
sys.modules["torch"] = mock_torch
sys.modules["torch.utils"] = mock_torch.utils
sys.modules["torch.utils.data"] = mock_torch.utils.data

import pytest
from app.storage.db import DatabaseManager
from app.embeddings.finetune import finetune, main

def run_in_thread(coro):
    result = None
    exception = None
    def target():
        nonlocal result, exception
        try:
            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(coro)
            loop.close()
        except Exception as e:
            exception = e
    t = threading.Thread(target=target)
    t.start()
    t.join()
    if exception:
        raise exception
    return result

@pytest.fixture(autouse=True)
def patch_asyncio_run():
    with patch("app.embeddings.finetune.asyncio.run", side_effect=run_in_thread):
        yield

@pytest.mark.asyncio
async def test_finetune_insufficient_data(tmp_path):
    db_path = str(tmp_path / "test_pma_insufficient.db")
    db = DatabaseManager(db_path)
    await db.connect()
    await db.init_db()
    await db.close()

    result = finetune(db_path=db_path, epochs=1)
    assert result == ""

@pytest.mark.asyncio
async def test_finetune_success(tmp_path):
    db_path = str(tmp_path / "test_pma_success.db")
    db = DatabaseManager(db_path)
    await db.connect()
    await db.init_db()

    # Pre-populate db with 12 chunks for file_id=1 to trigger training pair generation >= 10
    await db.execute_write(
        "INSERT INTO files (id, path, type, size, modified_at) VALUES (1, 'a.py', '.py', 100, 'now')"
    )
    for i in range(12):
        await db.execute_write(
            f"INSERT INTO chunks (file_id, text_preview, start_offset, end_offset) VALUES (1, 'chunk {i} text content', {i*10}, {i*10+9})"
        )

    await db.close()

    mock_model = MagicMock()
    mock_model.fit = MagicMock()

    with patch("app.embeddings.finetune.SentenceTransformer", return_value=mock_model):
        result = finetune(db_path=db_path, epochs=1, output_dir="mock_output_dir")
        assert result == "mock_output_dir"
        assert mock_model.fit.called

def test_main_method():
    with patch("app.embeddings.finetune.finetune", return_value="mock_output") as mock_finetune:
        with patch("sys.argv", ["pma-finetune", "--base-model", "my-model", "--output", "my-out", "--db", "my-db", "--epochs", "3", "--batch-size", "8"]):
            main()
            mock_finetune.assert_called_once_with(
                base_model="my-model",
                output_dir="my-out",
                db_path="my-db",
                epochs=3,
                batch_size=8
            )

def test_main_method_skipped():
    with patch("app.embeddings.finetune.finetune", return_value="") as mock_finetune:
        with patch("sys.argv", ["pma-finetune"]):
            main()
            mock_finetune.assert_called_once()
