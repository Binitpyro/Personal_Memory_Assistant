"""rust_core audit fixes: A1-11 (UTF-16 text), A8-14 (sibling overlap), A8-07 (GIL)."""

import struct
import threading
import time
from itertools import pairwise

import pytest

rust_core = pytest.importorskip("rust_core")
pytestmark = pytest.mark.skipif(
    not hasattr(rust_core, "extract_text_files"), reason="rust_core imported as an empty namespace"
)


def _nodes(buf: bytes):
    # position[3] f32, radius f32, parent u32, flags u32, type_hash u32, pad u32
    return [struct.unpack_from("<4f4I", buf, o) for o in range(0, len(buf), 32)]


def _files(folders: int, per_folder: int):
    return [
        (f"root/dir{d}/f{f}.txt", float((f * 37 + d * 11) % 5000), "txt")
        for d in range(folders)
        for f in range(per_folder)
    ]


def test_utf16_and_utf32_text_is_extracted_not_binary(tmp_path):
    cases = {
        "ps_out.txt": b"\xff\xfe" + "kubernetes\r\nalice".encode("utf-16-le"),
        "be.txt": b"\xfe\xff" + "kubernetes\r\nalice".encode("utf-16-be"),
        "u32.txt": b"\xff\xfe\x00\x00" + "kubernetes\r\nalice".encode("utf-32-le"),
        "u8bom.txt": b"\xef\xbb\xbfkubernetes\r\nalice",
        "u8.txt": b"kubernetes\r\nalice",
    }
    for name, data in cases.items():
        (tmp_path / name).write_bytes(data)
    out = dict(rust_core.extract_text_files([str(tmp_path / n) for n in cases], 1 << 20))
    for name in cases:
        assert out[str(tmp_path / name)] == "kubernetes\r\nalice", name


def test_real_binary_is_still_binary(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00\x01\x02\x03binary\x00\x00")
    ((_, text),) = rust_core.extract_text_files([str(p)], 1 << 20)
    assert text.startswith("[BINARY:")


@pytest.mark.parametrize("folders,per_folder", [(4, 100), (12, 30)])
def test_sibling_spheres_do_not_overlap(folders, per_folder):
    nodes = _nodes(rust_core.get_spatial_binary(_files(folders, per_folder)))
    by_parent: dict[int, list] = {}
    for n in nodes:
        by_parent.setdefault(n[4], []).append(n)
    checked = 0
    for parent, sibs in by_parent.items():
        if parent == 0xFFFFFFFF:
            continue
        for i, a in enumerate(sibs):
            for b in sibs[i + 1 :]:
                d = sum((a[k] - b[k]) ** 2 for k in range(3)) ** 0.5
                assert d >= a[3] + b[3], f"overlap {d} < {a[3] + b[3]}"
                checked += 1
    assert checked >= folders * (folders - 1) // 2


def test_layout_is_deterministic():
    files = _files(6, 20)
    assert rust_core.get_spatial_binary(files) == rust_core.get_spatial_binary(files)


def test_layout_releases_the_gil():
    files = _files(8, 1500)
    stop = threading.Event()
    stamps: list[float] = []

    def ticker():
        while not stop.is_set():
            stamps.append(time.perf_counter())
            time.sleep(0.001)

    t = threading.Thread(target=ticker)
    t.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    rust_core.get_spatial_binary(files)
    t1 = time.perf_counter()
    stop.set()
    t.join()

    during = [s for s in stamps if t0 <= s <= t1]
    assert t1 - t0 > 0.3, f"layout too fast ({t1 - t0:.3f}s) for the test to mean anything"
    # With the GIL held the ticker gets ~0 turns; released it ticks throughout.
    assert len(during) > 20, f"only {len(during)} ticks in {t1 - t0:.2f}s: GIL held"
    gaps = [b - a for a, b in pairwise(during)]
    assert max(gaps) < 0.25, f"ticker stalled {max(gaps):.3f}s"
