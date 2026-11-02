"""L2 b2: entrypoint PORT handling, dev.bat token provisioning, StartPMA java scope, gate parity."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _entry():
    spec = importlib.util.spec_from_file_location("pma_entry", ROOT / "__main__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_garbage_port_falls_back_to_settings_port(monkeypatch, capsys):
    entry = _entry()
    monkeypatch.setenv("PORT", "not-a-port")
    assert entry._port_from_env(8000) == 8000
    assert "PORT" in capsys.readouterr().err
    monkeypatch.setenv("PORT", "9123")
    assert entry._port_from_env(8000) == 9123
    monkeypatch.delenv("PORT")
    assert entry._port_from_env(8000) == 8000


def test_dev_bat_shares_one_token_between_backend_and_vite():
    bat = (ROOT / "scripts" / "dev.bat").read_text(encoding="utf-8")
    assert "set X_LOCAL_ACCESS_TOKEN=%PMA_DEV_TOKEN%" in bat
    assert "set VITE_DEV_TOKEN=%PMA_DEV_TOKEN%" in bat


def test_startpma_does_not_kill_every_java_process():
    bat = (ROOT / "scripts" / "StartPMA.bat").read_text(encoding="utf-8")
    assert "Stop-Process -Name java" not in bat
    assert "sonarqube" in bat.split("Terminating old SonarQube Java processes")[1][:400]


def test_local_rust_gate_matches_ci_clippy_flags():
    bat = (ROOT / "scripts" / "run_ci_checks.bat").read_text(encoding="utf-8")
    clippy = [ln for ln in bat.splitlines() if "cargo clippy" in ln]
    assert len(clippy) == 2
    for ln in clippy:
        assert "--all-targets" in ln and "-D warnings" in ln


def test_open_file_goes_through_path_only_command_not_shell_scope():
    tauri = ROOT / "frontend" / "src-tauri"
    caps = (tauri / "capabilities" / "default.json").read_text(encoding="utf-8")
    assert "shell:allow-open" not in caps
    lib = (tauri / "src" / "lib.rs").read_text(encoding="utf-8")
    assert "fn open_file(" in lib and "            open_file\n" in lib.replace("\r\n", "\n")
    ts = (ROOT / "frontend" / "src" / "utils" / "tauriShell.ts").read_text(encoding="utf-8")
    assert "invoke('open_file'" in ts and "@tauri-apps/plugin-shell" not in ts
