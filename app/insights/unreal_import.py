import json
from pathlib import Path
from typing import Any


def _first(data: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _extract_assets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        payload.get("Assets"),
        payload.get("assets"),
        payload.get("AssetData"),
        payload.get("asset_data"),
    ]
    registry = payload.get("AssetRegistry")
    if isinstance(registry, dict):
        candidates.extend(
            [
                registry.get("Assets"),
                registry.get("AssetData"),
            ]
        )
    for value in candidates:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _looks_character(asset_class: str, object_path: str, tags: dict[str, Any]) -> bool:
    needle = f"{asset_class} {object_path} {json.dumps(tags)}".lower()
    return any(word in needle for word in ["character", "hero", "enemy", "npc", "pawn"])


def _empty_unreal_counters() -> dict[str, int]:
    return {
        "map_count": 0,
        "character_blueprints": 0,
        "pawn_blueprints": 0,
        "skeletal_meshes": 0,
        "material_count": 0,
        "niagara_systems": 0,
        "environment_assets": 0,
    }


def _normalized_tags(asset: dict[str, Any]) -> dict[str, Any]:
    tags = _first(asset, ["TagsAndValues", "tags", "tags_and_values"], {})
    return tags if isinstance(tags, dict) else {}


def _increment_asset_counters(
    counters: dict[str, int],
    asset_class: str,
    object_path: str,
    tags: dict[str, Any],
) -> None:
    lower_class = asset_class.lower()
    lower_path = object_path.lower()

    if lower_class in {"world", "map"} or "/maps/" in lower_path or lower_path.endswith(".umap"):
        counters["map_count"] += 1
    if "skeletalmesh" in lower_class:
        counters["skeletal_meshes"] += 1
    if "material" in lower_class:
        counters["material_count"] += 1
    if "niagarasystem" in lower_class or "niagara" in lower_class:
        counters["niagara_systems"] += 1
    if "blueprint" in lower_class and _looks_character(asset_class, object_path, tags):
        counters["character_blueprints"] += 1
    if "pawn" in lower_class or ("blueprint" in lower_class and "pawn" in lower_path):
        counters["pawn_blueprints"] += 1
    if any(seg in lower_path for seg in ["/environment", "/props", "/foliage", "/landscape"]):
        counters["environment_assets"] += 1


def _build_profile_text(facts: dict[str, Any]) -> str:
    return (
        f"Project {facts['project_name']} uses Unreal Engine {facts['engine_version']}. "
        f"Detected {facts['total_assets']} assets including {facts['map_count']} maps, "
        f"{facts['environment_assets']} environment-related assets, "
        f"{facts['character_blueprints']} character blueprints, "
        f"{facts['pawn_blueprints']} pawn blueprints, "
        f"{facts['skeletal_meshes']} skeletal meshes, {facts['material_count']} materials, "
        f"and {facts['niagara_systems']} Niagara systems."
    )


def parse_unreal_metadata(json_path: str, folder_tag: str = "") -> dict[str, Any]:
    path = Path(json_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    project_name = _first(payload, ["ProjectName", "project_name", "Name", "name"], path.stem)
    project_path = _first(payload, ["ProjectPath", "project_path", "RootPath", "root_path"], "")
    folder_path = str(Path(project_path).resolve()) if project_path else str(path.parent.resolve())
    engine_version = _first(
        payload,
        ["EngineVersion", "engine_version", "EngineAssociation", "engine_association"],
        "unknown",
    )

    assets = _extract_assets(payload)

    total_assets = len(assets)
    counters = _empty_unreal_counters()

    for asset in assets:
        asset_class = str(_first(asset, ["AssetClass", "Class", "asset_class"], ""))
        object_path = str(
            _first(asset, ["ObjectPath", "PackagePath", "PackageName", "object_path"], "")
        )
        tags = _normalized_tags(asset)
        _increment_asset_counters(counters, asset_class, object_path, tags)

    facts = {
        "folder_path": folder_path,
        "folder_tag": folder_tag or str(project_name),
        "project_name": str(project_name),
        "engine_version": str(engine_version),
        "total_assets": total_assets,
        "map_count": counters["map_count"],
        "character_blueprints": counters["character_blueprints"],
        "pawn_blueprints": counters["pawn_blueprints"],
        "skeletal_meshes": counters["skeletal_meshes"],
        "material_count": counters["material_count"],
        "niagara_systems": counters["niagara_systems"],
        "environment_assets": counters["environment_assets"],
        "metadata_source": str(path.resolve()),
    }

    facts["profile_text"] = _build_profile_text(facts)
    return facts
