"""A8-05: the MSI must carry the sidecar zip where lib.rs looks for it.

The mapping lives in an overlay config, not tauri.conf.json: tauri-build copies
`bundle.resources` on every build, so a base-config entry for a zip that only
Build-Exe.bat produces would break cargo check/clippy/test everywhere else.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "frontend" / "src-tauri"


def _conf(name: str) -> dict:
    return json.loads((TAURI / name).read_text(encoding="utf-8"))


def test_base_config_declares_no_resources():
    assert _conf("tauri.conf.json")["bundle"]["resources"] == {}


def test_bundle_overlay_maps_build_exe_zip_to_where_lib_rs_looks():
    resources = _conf("tauri.bundle.conf.json")["bundle"]["resources"]
    assert resources == {"../../dist/PMA-sidecar.zip": "python/PMA-sidecar.zip"}
    # the producer and the consumer agree on the name and location
    assert 'dist\\PMA-sidecar.zip" -C' in (ROOT / "scripts" / "Build-Exe.bat").read_text(
        encoding="utf-8"
    )
    assert 'join("python").join("PMA-sidecar.zip")' in (TAURI / "src" / "lib.rs").read_text(
        encoding="utf-8"
    )
