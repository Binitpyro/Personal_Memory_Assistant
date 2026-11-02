"""Opt-in GPU embedding: worker protocol, client failure modes, service routing,
signature, persisted setting, pins. No GPU, no network, no model files: the
client runs against a stdlib fake worker, the service against mocks."""

import ast
import asyncio
import hashlib
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.config import Settings, settings
from app.embeddings.gpu import client as gpu_client
from app.embeddings.gpu import registry, worker
from app.embeddings.gpu.client import GpuEmbedClient
from app.embeddings.service import EmbeddingService

_FAKE_WORKER = r"""
import base64, json, struct, sys, time
mode = sys.argv[1]
for line in sys.stdin:
    msg = json.loads(line)
    if msg["type"] == "hello":
        ep = "CPUExecutionProvider" if mode == "cpu" else "DmlExecutionProvider"
        print(json.dumps({"type": "ready", "protocol": msg["protocol"], "ep": ep, "dim": 4}), flush=True)
    elif msg["type"] == "embed":
        if mode == "crash":
            sys.exit(3)
        if mode == "hang":
            time.sleep(30)
        n = msg["rows"] * 4 - (1 if mode == "short" else 0)
        b64 = base64.b64encode(struct.pack("<%df" % n, *([0.5] * n))).decode()
        print(json.dumps({"type": "vecs", "id": msg["id"], "b64": b64}), flush=True)
    elif msg["type"] == "shutdown":
        break
"""


@pytest.fixture
def fake_client(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(_FAKE_WORKER, encoding="utf-8")
    made: list[GpuEmbedClient] = []

    def make(mode: str, **kw) -> GpuEmbedClient:
        c = GpuEmbedClient([sys.executable, "-I", "-u", str(script), mode], "model.onnx", **kw)
        made.append(c)
        return c

    yield make
    for c in made:
        c.close()


_IDS = np.array([[101, 7, 102], [101, 8, 0]], dtype=np.int64)
_MASK = np.array([[1, 1, 1], [1, 1, 0]], dtype=np.int64)


# ── protocol ─────────────────────────────────────────────────────────────


def test_worker_and_client_speak_the_same_protocol():
    assert worker.PROTOCOL == gpu_client.PROTOCOL


def test_worker_top_level_imports_are_stdlib_only():
    """It runs by path under -I in a venv with no `app`; numpy/onnxruntime are
    imported inside main() so this module also imports in Core's tests."""
    tree = ast.parse(Path(worker.__file__).read_text(encoding="utf-8"))
    names = [
        a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names
    ] + [n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module]
    assert names and all(n in sys.stdlib_module_names for n in names), names


# ── client ───────────────────────────────────────────────────────────────


def test_client_round_trip_reuses_one_worker(fake_client):
    c = fake_client("ok")
    out = c.embed(_IDS, _MASK)
    assert out is not None and out.shape == (2, 4)
    assert np.allclose(out, 0.5)
    pid = c._proc.pid
    assert c.embed(_IDS, _MASK) is not None
    assert c._proc.pid == pid
    assert c.status() == {
        "running": True,
        "degraded": False,
        "last_error": "",
        "ep": "DmlExecutionProvider",
    }


@pytest.mark.parametrize(
    ("mode", "kw", "error"),
    [
        ("crash", {}, "exited"),
        ("hang", {"call_timeout_s": 0.5}, "no reply"),
        ("cpu", {}, "CPUExecutionProvider"),
        ("short", {}, "floats"),
    ],
)
def test_client_failure_returns_none_and_degrades(fake_client, mode, kw, error):
    c = fake_client(mode, **kw)
    assert c.embed(_IDS, _MASK) is None
    assert c.degraded and error in c.last_error
    assert c._proc is None  # killed, not left holding VRAM
    # Degraded stays on CPU without respawning a worker per batch.
    c._spawn = MagicMock()
    assert c.embed(_IDS, _MASK) is None
    c._spawn.assert_not_called()


def test_client_retires_idle_worker_and_respawns(fake_client):
    c = fake_client("ok", idle_s=0.2)
    assert c.embed(_IDS, _MASK) is not None
    deadline = time.monotonic() + 5
    while c._proc is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert c._proc is None
    assert c.embed(_IDS, _MASK) is not None and not c.degraded


# ── service routing ──────────────────────────────────────────────────────


def _service_with_mocks(gpu):
    svc = EmbeddingService("model")
    svc._embedding_dim = 4
    session = MagicMock()
    session.run.side_effect = lambda _o, feed: [np.ones((feed["input_ids"].shape[0], 3, 4))]
    svc._session = session

    def encode_batch(texts):
        return [
            MagicMock(ids=[1, 2, 3], attention_mask=[1, 1, 1], type_ids=[0, 0, 0]) for _ in texts
        ]

    svc._tokenizer = MagicMock(encode_batch=encode_batch)
    svc._gpu = gpu
    return svc, session


_GPU_ROW = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)


def _fake_gpu(works: bool):
    gpu = MagicMock()
    gpu.embed.side_effect = lambda ids, _m: np.tile(_GPU_ROW, (len(ids), 1)) if works else None
    return gpu


def test_documents_go_to_the_gpu_and_queries_stay_on_cpu():
    gpu = _fake_gpu(works=True)
    svc, session = _service_with_mocks(gpu)
    docs = asyncio.run(svc.embed_texts(["a", "bb", "ccc"]))
    assert np.allclose(docs, _GPU_ROW) and session.run.call_count == 0
    query = asyncio.run(svc.embed_texts(["just one"]))
    assert np.allclose(query, 0.5)  # CPU path: mean of ones, normalised
    assert gpu.embed.call_count == 1 and session.run.call_count == 1


def test_gpu_failure_falls_back_to_cpu_for_that_batch():
    svc, session = _service_with_mocks(_fake_gpu(works=False))
    out = asyncio.run(svc.embed_texts(["a", "bb", "ccc"]))
    assert np.allclose(out, 0.5) and session.run.call_count >= 1


# ── load: model file, signature, client gating ───────────────────────────


def _load(tmp_path, monkeypatch, device, *, installed=False, expected=None):
    monkeypatch.setattr(settings, "embedding_device", device)
    d = tmp_path / "model"
    (d / "onnx").mkdir(parents=True)
    (d / "tokenizer.json").write_text("{}", encoding="utf-8")
    for name in ("model_quantized.onnx", "model.onnx", "model_fp16.onnx"):
        (d / "onnx" / name).write_text("fake", encoding="utf-8")
    sess = MagicMock()
    sess.get_inputs.return_value = []
    sess.run.return_value = [np.ones((1, 8, 4))]
    svc = EmbeddingService("m")
    with (
        patch("tokenizers.Tokenizer.from_file", return_value=MagicMock()),
        patch("onnxruntime.InferenceSession", return_value=sess) as ctor,
        patch.object(registry, "is_installed", return_value=installed),
    ):
        svc._load_onnx_model(d, expected or {}, repo_id="r", revision="v")
    return svc, Path(ctor.call_args.args[0]).name


def test_cpu_mode_is_unchanged(tmp_path, monkeypatch):
    svc, loaded = _load(tmp_path, monkeypatch, "cpu")
    assert loaded == "model_quantized.onnx"
    assert svc.model_signature.endswith(":onnx/model_quantized.onnx")
    assert svc._gpu is None and svc.gpu_status() is None


def test_gpu_mode_keeps_int8_for_queries_and_changes_the_signature(tmp_path, monkeypatch):
    """INT8 stays resident (an fp32 query session cost ~90 MB idle); the
    signature still changes, because stored document vectors become fp16."""
    svc, loaded = _load(tmp_path, monkeypatch, "gpu")
    assert loaded == "model_quantized.onnx"
    assert svc.model_signature.endswith(":onnx/model_quantized.onnx:gpu-fp16")


def test_gpu_mode_without_install_degrades_instead_of_failing(tmp_path, monkeypatch):
    svc, _ = _load(tmp_path, monkeypatch, "gpu", installed=False)
    assert svc._gpu is None
    status = svc.gpu_status()
    assert status["degraded"] and "not installed" in status["last_error"]


def test_gpu_mode_refuses_an_unpinned_fp16(tmp_path, monkeypatch):
    svc, _ = _load(tmp_path, monkeypatch, "gpu", installed=True)
    assert svc._gpu is None and "checksum" in svc.gpu_status()["last_error"]


def test_gpu_mode_builds_a_client_for_a_verified_fp16(tmp_path, monkeypatch):
    fake = hashlib.sha256(b"fake").hexdigest()
    expected = {
        "onnx/model_quantized.onnx": {"sha256": fake},
        registry.FP16: {"sha256": fake},
        "tokenizer.json": {"sha256": hashlib.sha256(b"{}").hexdigest()},
    }
    svc, _ = _load(tmp_path, monkeypatch, "gpu", installed=True, expected=expected)
    assert isinstance(svc._gpu, GpuEmbedClient)


# ── setting, persistence, pins ───────────────────────────────────────────


def test_device_defaults_to_cpu():
    assert Settings.model_fields["embedding_device"].default == "cpu"


def test_device_persists_and_applies_only_at_load(monkeypatch):
    monkeypatch.setattr(settings, "embedding_device", "cpu")
    registry.persist_device("gpu")
    assert settings.embedding_device == "cpu"  # a switch waits for restart
    registry.load_persisted_device()
    assert settings.embedding_device == "gpu"


def test_garbage_persisted_device_is_ignored(monkeypatch):
    from app.settings_store import SettingsStore

    monkeypatch.setattr(settings, "embedding_device", "cpu")
    SettingsStore.save({"embedding": {"device": "tpu"}})
    registry.load_persisted_device()
    assert settings.embedding_device == "cpu"


def test_enable_refuses_when_not_installed(monkeypatch):
    from app.embeddings.gpu import api

    monkeypatch.setattr(registry, "is_installed", lambda: False)
    res = asyncio.run(api.enable(api.EnablePayload(on=True)))
    assert res == {"ok": False, "error_code": "NOT_INSTALLED"}
    assert registry.saved_device() is None


def test_pins_are_exact_and_carry_no_cpu_onnxruntime():
    assert all("==" in p for p in registry.PINNED_DEPS)
    names = {p.split("==")[0] for p in registry.PINNED_DEPS}
    assert "onnxruntime-directml" in names and "onnxruntime" not in names
