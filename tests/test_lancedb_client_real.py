import pytest
import numpy as np
import pyarrow as pa
import tempfile
import shutil
from unittest.mock import MagicMock
from app.vector_store.lancedb_client import LanceDBClient, _normalize_rows, _clean_value, _arrow_table_to_search_result

@pytest.fixture
def real_lancedb_dir():
    # Force creation in standard temp directory (typically C: drive which is NTFS)
    temp_dir = tempfile.mkdtemp(prefix="lancedb_test_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)

def test_normalize_rows():
    rows = [{"a": 1}, {"b": 2}]
    normalized = _normalize_rows(rows)
    assert len(normalized) == 2
    assert normalized[0]["a"] == 1
    assert normalized[0]["b"] is None
    assert normalized[1]["a"] is None
    assert normalized[1]["b"] == 2
    assert _normalize_rows([]) == []

def test_clean_value():
    assert _clean_value(float('nan')) == 0.0
    assert _clean_value(float('inf')) == 0.0
    assert _clean_value(1.5) == 1.5
    assert _clean_value("string") == "string"

def test_arrow_table_to_search_result_none():
    res = _arrow_table_to_search_result(None)
    assert res["ids"] == [[]]

def test_arrow_table_to_search_result_empty_table():
    empty_tbl = pa.Table.from_pydict({"id": [], "vector": []}, schema=pa.schema([
        ("id", pa.string()),
        ("vector", pa.list_(pa.float32(), 384))
    ]))
    res = _arrow_table_to_search_result(empty_tbl)
    assert res["ids"] == [[]]

def test_arrow_table_to_search_result_pandas_fallback():
    # Mocking a non-pa.Table object to hit the else branch
    mock_table = MagicMock()
    mock_pandas = MagicMock()
    mock_pandas.to_dict.return_value = [
        {"id": "doc1", "vector": [0.1], "_distance": 0.05, "folder_tag": "tag1"},
        {"id": "doc2", "vector": [0.2], "_distance": float('nan')}
    ]
    mock_table.to_pandas.return_value = mock_pandas

    res = _arrow_table_to_search_result(mock_table)
    assert res["ids"] == [["doc1", "doc2"]]
    assert res["distances"] == [[0.05, 0.0]]
    # row2 has only id, vector, _distance so it gets filtered out of metadata to {}
    assert res["metadatas"] == [[{"folder_tag": "tag1"}, {}]]

    # Empty records in fallback
    mock_pandas.to_dict.return_value = []
    res_empty = _arrow_table_to_search_result(mock_table)
    assert res_empty["ids"] == [[]]

@pytest.mark.asyncio
async def test_lancedb_client_full_lifecycle(real_lancedb_dir):
    client = LanceDBClient(persist_directory=real_lancedb_dir)

    # Initial state
    assert client.db is None
    client.connect()
    # Call connect again to test the quick-return branch
    client.connect()
    assert client.db is not None

    # get_all_ids and get_max_id on empty db
    assert client.get_all_ids() == set()
    assert client.get_max_id() == 0

    # Add documents
    embeddings = [np.array([0.1] * 384, dtype=np.float32), np.array([0.2] * 384, dtype=np.float32)]
    metadatas = [
        {"file_path": "a.txt", "text": "hello standard text", "folder_tag": "folder_a"},
        {"file_path": "b.txt", "text": "another standard document", "folder_tag": "folder_b"}
    ]
    await client.add_documents(["1", "2"], embeddings, metadatas)

    # Try adding empty docs
    await client.add_documents([], [], [])

    # get_all_ids
    assert client.get_all_ids() == {"1", "2"}

    # get_max_id
    assert client.get_max_id() == 2

    # semantic_search
    query_emb = [0.1] * 384
    res = await client.semantic_search(query_emb, k=2)
    assert len(res["ids"][0]) == 2
    assert "1" in res["ids"][0]

    # semantic_search with filters
    res_filtered = await client.semantic_search(query_emb, k=2, where_filter={"folder_tag": "folder_a", "file_path": "a.txt"})
    assert res_filtered["ids"][0] == ["1"]

    res_filtered_numeric = await client.semantic_search(query_emb, k=2, where_filter={"folder_tag": "folder_a", "id": 1})
    assert res_filtered_numeric["ids"][0] == ["1"]

    # add_summaries_batch
    summaries = [
        {
            "doc_id": "summary1",
            "embedding": [0.5] * 384,
            "metadata": {"folder_tag": "folder_a", "summary_text": "summary content 1"}
        }
    ]
    await client.add_summaries_batch(summaries)
    # Empty call
    await client.add_summaries_batch([])

    # search_summaries
    res_sums = await client.search_summaries([0.5]*384, k=1, where_filter={"folder_tag": "folder_a"})
    assert res_sums["ids"][0] == ["summary1"]

    # delete_documents
    await client.delete_documents(["1"])
    # Empty list
    await client.delete_documents([])
    assert client.get_all_ids() == {"2"}

    # delete_folder
    await client.delete_folder("folder_b")
    assert client.get_all_ids() == set()

    # Query Cache testing
    cache_emb = np.array([0.9] * 384, dtype=np.float32)
    await client.add_query_cache(cache_emb, "query standard", "response standard", 12345.67)
    
    # Search cache matching threshold
    res_cache = await client.search_cache([0.9] * 384, threshold=0.99)
    assert res_cache is not None
    assert res_cache["query_text"] == "query standard"
    assert res_cache["response_text"] == "response standard"

    # Search cache not matching threshold
    res_cache_fail = await client.search_cache([-0.9] * 384, threshold=0.99)
    assert res_cache_fail is None

    # Search cache on non-existent table (if table name was wrong, but here query_cache exists)
    # Let's drop query_cache to test table None path in search_cache
    client.db.drop_table("query_cache")
    assert await client.search_cache([0.9] * 384) is None

    # clear_all
    await client.clear_all()
    assert "pma_chunks" not in client.db.list_tables()
    assert "pma_summaries" not in client.db.list_tables()
