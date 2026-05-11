import os
import tempfile
from pathlib import Path

from app.scanner.scanner import ScanResult, scan_folder


def test_fast_scan():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some dummy files
        for i in range(3):
            with open(os.path.join(tmpdir, f"test{i}.txt"), "w") as f:
                f.write("hello word dummy")

        # Test scandir (by skipping MFT)
        result = scan_folder(Path(tmpdir), {".txt"})
        assert isinstance(result, ScanResult)
        assert len(result.files) >= 3
        # Ensure 'test0.txt' is in the results
        assert any("test0.txt" in str(f) for f in result.files)
