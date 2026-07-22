import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings


@pytest.mark.asyncio
async def test_system_config(client):
    response = await client.get("/api/system/config")
    assert response.status_code == 200
    data = response.json()
    assert "app_version" in data
    assert "embedding_model" in data
    assert "gemini_model" in data
    assert "gemini_max_output_tokens" in data
    assert "dev_mode" in data


@pytest.mark.asyncio
async def test_get_drive_fs_type():
    from app.api.system import get_drive_fs_type

    # 1. Non-Windows system
    with patch("app.api.system.plat.system", return_value="Darwin"):
        assert get_drive_fs_type("C:") == "Unknown"

    # 2. Windows system, success
    with patch("app.api.system.plat.system", return_value="Windows"):
        mock_kernel32 = MagicMock()
        # GetVolumeInformationW returns True, file_system_name_buffer is filled
        mock_kernel32.GetVolumeInformationW = (
            lambda drive, vol, vol_len, serial, max_len, flags, fs, fs_len: (
                setattr(fs, "value", "NTFS") or True
            )
        )
        with patch("ctypes.windll.kernel32", mock_kernel32, create=True):
            assert get_drive_fs_type("C:") == "NTFS"

        # 3. Windows system, error
        mock_kernel32_fail = MagicMock()
        mock_kernel32_fail.GetVolumeInformationW.side_effect = Exception("OS Error")
        with patch("ctypes.windll.kernel32", mock_kernel32_fail, create=True):
            assert get_drive_fs_type("C:") == "Unknown"


@pytest.mark.asyncio
async def test_get_drive_info(client):
    # Test on Darwin/Linux
    with patch("app.api.system.plat.system", return_value="Darwin"):
        response = await client.get("/api/system/drive_info")
        assert response.status_code == 200
        assert response.json()["fs_type"] == "Unknown"

    # Test on Windows
    with patch("app.api.system.plat.system", return_value="Windows"):  # noqa: SIM117
        with patch("app.api.system.get_drive_fs_type", return_value="exFAT"):
            response = await client.get("/api/system/drive_info")
            assert response.status_code == 200
            data = response.json()
            assert data["fs_type"] == "exFAT"
            assert data["is_portable_fs"] is True


@pytest.mark.asyncio
async def test_get_os_string():
    from app.api.system import _get_os_string

    # macOS
    with patch("app.api.system.plat.system", return_value="Darwin"):  # noqa: SIM117
        with patch("app.api.system.plat.mac_ver", return_value=("14.1", None, None)):
            assert "macOS 14.1" in _get_os_string()

    # Linux
    with patch("app.api.system.plat.system", return_value="Linux"):  # noqa: SIM117
        with patch("app.api.system.plat.release", return_value="6.2.0-generic"):
            assert "Linux 6.2.0-generic" in _get_os_string()

    # Windows 11 Build >= 22000
    with patch("app.api.system.plat.system", return_value="Windows"):  # noqa: SIM117
        with patch("app.api.system.plat.release", return_value="10"):
            with patch("app.api.system.plat.version", return_value="10.0.22621"):
                assert _get_os_string() == "Windows 11 (Build 22621)"

    # Windows 10 Build < 22000
    with patch("app.api.system.plat.system", return_value="Windows"):  # noqa: SIM117
        with patch("app.api.system.plat.release", return_value="10"):
            with patch("app.api.system.plat.version", return_value="10.0.19045"):
                assert _get_os_string() == "Windows 10 (Build 19045)"

    # Windows exception fallback
    with patch("app.api.system.plat.system", return_value="Windows"):  # noqa: SIM117
        with patch("app.api.system.plat.release", return_value="10"):
            with patch("app.api.system.plat.version", side_effect=Exception("API fail")):
                assert _get_os_string() == "Windows 10"


@pytest.mark.asyncio
async def test_get_volumes():
    from app.api.system import _get_volumes

    # Non-Windows, successful disk usage
    with patch("app.api.system.plat.system", return_value="Darwin"):
        with patch("shutil.disk_usage", return_value=(100 * 1024**3, 40 * 1024**3, 60 * 1024**3)):
            vols = _get_volumes()
            assert len(vols) == 1
            assert vols[0]["letter"] == "/"
            assert vols[0]["total_gb"] == 100.0

        # Non-Windows exception
        with patch("shutil.disk_usage", side_effect=Exception("Disk error")):
            assert _get_volumes() == []

    # Windows
    with patch("app.api.system.plat.system", return_value="Windows"):

        def mock_exists(path):
            return path == "C:\\" or path == "D:\\"

        def mock_usage(path):
            if path == "C:\\":
                return (200 * 1024**3, 50 * 1024**3, 150 * 1024**3)
            raise PermissionError("Access denied")

        with patch("os.path.exists", side_effect=mock_exists):  # noqa: SIM117
            with patch("shutil.disk_usage", side_effect=mock_usage):
                vols = _get_volumes()
                # Should list C:, and skip D: due to PermissionError
                assert len(vols) == 1
                assert vols[0]["letter"] == "C:"
                assert vols[0]["total_gb"] == 200.0


@pytest.mark.asyncio
async def test_system_info_endpoint(client):
    with patch("app.api.system.plat.system", return_value="Windows"):
        mock_shell32 = MagicMock()
        mock_shell32.IsUserAnAdmin.return_value = 1
        with patch("ctypes.windll.shell32", mock_shell32, create=True):
            response = await client.get("/api/system/info")
            assert response.status_code == 200
            data = response.json()
            assert data["is_admin"] is True
            assert data["scan_method"] == "MFT (fast)"

    with patch("app.api.system.plat.system", return_value="Darwin"):
        response = await client.get("/api/system/info")
        assert response.status_code == 200
        data = response.json()
        assert data["is_admin"] is False
        assert data["scan_method"] == "scandir"


@pytest.mark.asyncio
async def test_enable_split_brain(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    # Case 1: .env doesn't exist
    response = await client.post("/api/system/enable-split-brain")
    assert response.status_code == 200
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "PMA_LANCEDB_MODE=split_brain\n" in env_content

    # Case 2: .env exists and contains setting already
    (tmp_path / ".env").write_text("PMA_LANCEDB_MODE=local\nVAR=1\n", encoding="utf-8")
    response = await client.post("/api/system/enable-split-brain")
    assert response.status_code == 200
    env_content2 = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "PMA_LANCEDB_MODE=split_brain\n" in env_content2
    assert "VAR=1\n" in env_content2

    # Case 3: write fails (PermissionError)
    with patch("builtins.open", side_effect=PermissionError("Access denied")):
        response = await client.post("/api/system/enable-split-brain")
        assert response.status_code == 403
        assert "Failed to write to .env file" in response.json()["detail"]


@pytest.mark.asyncio
async def test_purge_host_cache(client, tmp_path):
    # Setup
    old_mode = settings.lancedb_mode
    old_dir = settings.lancedb_persist_dir

    # Case 1: lancedb_mode is not split_brain
    settings.lancedb_mode = "local"
    response = await client.post("/api/system/purge-host-cache")
    assert response.status_code == 400
    assert "purge-host-cache is only available in split_brain mode." in response.json()["detail"]

    # Case 2: split_brain mode, folder doesn't exist
    settings.lancedb_mode = "split_brain"
    settings.lancedb_persist_dir = str(tmp_path / "missing_cache_dir")
    response = await client.post("/api/system/purge-host-cache")
    assert response.status_code == 200
    assert "No host cache directory found" in response.json()["message"]

    # Case 3: split_brain mode, folder exists, deleted successfully
    cache_dir = tmp_path / "active_cache_dir"
    cache_dir.mkdir()
    (cache_dir / "file.txt").touch()
    settings.lancedb_persist_dir = str(cache_dir)
    response = await client.post("/api/system/purge-host-cache")
    assert response.status_code == 200
    assert "Host cache purged" in response.json()["message"]
    assert not cache_dir.exists()

    # Reset
    settings.lancedb_mode = old_mode
    settings.lancedb_persist_dir = old_dir


@pytest.mark.asyncio
async def test_compact_db_endpoints(client):
    # 1. Non-zero worker check (maintenance skips)
    with patch.dict(os.environ, {"UVICORN_WORKER_ID": "1"}):
        response = await client.post("/api/system/compact-db")
        assert (
            response.json()["message"] == "Compaction can only be triggered by the primary worker."
        )

    # 2. Primary worker, starts compaction
    with patch.dict(os.environ, {"UVICORN_WORKER_ID": "0"}):  # noqa: SIM117
        with patch("app.api.system.get_vacuum_lock") as mock_lock_getter:
            mock_lock = MagicMock()
            mock_lock.locked.return_value = False
            mock_lock_getter.return_value = mock_lock

            response = await client.post("/api/system/compact-db")
            assert response.json()["message"] == "Compaction started in background."

            # Lock is already locked (subsequent trigger)
            mock_lock.locked.return_value = True
            response2 = await client.post("/api/system/compact-db")
            assert response2.json()["message"] == "Compaction already in progress."


@pytest.mark.asyncio
async def test_compact_status_endpoint(client):
    response = await client.get("/api/system/compact-db/status")
    assert response.status_code == 200
    data = response.json()
    assert "is_running" in data
    assert "last_run" in data
    assert "error" in data


@pytest.mark.asyncio
async def test_clear_cache_endpoint(client):
    response = await client.post("/api/system/clear-cache")
    assert response.status_code == 200
    assert response.json()["message"] == "Caches cleared."


@pytest.mark.asyncio
async def test_demo_seed_endpoint(client, tmp_path):
    # Folder doesn't exist
    with patch("app.api.system.os.path.isdir", return_value=False):
        response = await client.post("/api/demo/seed")
        assert response.status_code == 400
        assert "demo_data folder not found." in response.json()["error"]

    # Folder exists
    with patch("app.api.system.os.path.isdir", return_value=True):  # noqa: SIM117
        with patch("app.api.system.ensure_indexing") as mock_ensure:
            mock_service_cls = MagicMock()
            mock_service_instance = MagicMock()
            mock_service_instance.index_folders = AsyncMock()
            mock_service_cls.return_value = mock_service_instance
            mock_ensure.return_value = (mock_service_cls, None)

            response = await client.post("/api/demo/seed")
            assert response.status_code == 200
            assert "Demo indexing started" in response.json()["message"]


@pytest.mark.asyncio
async def test_pick_folder_mocked(client):
    # Case 1: Dialog returns path
    with patch("app.api.system.asyncio.get_running_loop") as mock_loop:
        mock_executor = AsyncMock(return_value="/my/selected/folder")
        mock_loop.return_value.run_in_executor = mock_executor

        response = await client.get("/api/pick/folder")
        assert response.status_code == 200
        assert response.json()["path"] == "/my/selected/folder"

    # Case 2: Dialog raises Exception (e.g. GUI library missing or closed)
    with patch("app.api.system.asyncio.get_running_loop") as mock_loop:
        mock_executor = AsyncMock(side_effect=Exception("tkinter error"))
        mock_loop.return_value.run_in_executor = mock_executor

        response = await client.get("/api/pick/folder")
        assert response.status_code == 200
        assert response.json()["path"] == ""
        assert "Use the native Tauri dialog instead." in response.json()["error"]
