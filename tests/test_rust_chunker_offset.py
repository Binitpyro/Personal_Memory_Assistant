import pytest
import rust_core

def test_rust_chunker_cjk_offsets():
    text = "中文测试：这是一个用来验证Rust和Python字符偏移量是否完全一致的测试字符串。"
    chunk_size = 10
    overlap = 3
    prefix = "[TEST] "
    base_offset = 5
    
    chunks = rust_core.create_chunks(text, chunk_size, overlap, prefix, base_offset)
    
    assert len(chunks) > 0
    for chunk in chunks:
        start = chunk["start_offset"] - base_offset
        end = chunk["end_offset"] - base_offset
        preview = chunk["text_preview"]
        
        # Verify text_preview matches prefix + sliced text using Python string slicing
        expected_text = prefix + text[start:end]
        assert preview == expected_text, f"Mismatch: preview='{preview}', expected='{expected_text}'"
        assert len(text[start:end]) == (chunk["end_offset"] - chunk["start_offset"])
