import asyncio
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.api.deps import (
    get_db, get_emb, get_chroma, ensure_indexing
)
from app.storage.db import DatabaseManager
from app.api.limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

class IndexRequest(BaseModel):
    folders: List[str] = Field(..., max_length=50)

    @property
    def validated_folders(self) -> List[str]:
        import os
        import sys

        # System directories that must never be indexed
        BLOCKED_ROOTS: tuple[str, ...] = (
            "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
            "/etc", "/proc", "/sys", "/dev", "/boot",
        )

        cleaned = []
        for f in self.folders:
            p = f.strip().strip('"').strip("'")
            if not p:
                continue
            # Resolve symlinks and normalise separators
            try:
                resolved = os.path.realpath(os.path.normpath(p))
            except Exception:
                continue
            # Block path-traversal sequences in the original input
            if ".." in p.replace("\\", "/").split("/"):
                logger.warning("Rejected folder with traversal sequence: %s", p)
                continue
            # Block sensitive system directories
            r_lower = resolved.lower()
            if any(r_lower.startswith(b.lower()) for b in BLOCKED_ROOTS):
                logger.warning("Rejected blocked system path: %s", resolved)
                continue
            # Must exist and be a directory
            if os.path.isdir(resolved):
                cleaned.append(resolved)
            else:
                logger.debug("Folder not found or not a directory: %s", resolved)
        return cleaned

class UnrealImportRequest(BaseModel):
    json_path: str = Field(..., min_length=1)
    folder_tag: Optional[str] = Field(None)
    @property
    def validated_json_path(self) -> str:
        import os
        return os.path.realpath(self.json_path.strip().strip('"').strip("'"))

@router.post("/start")
@limiter.limit("3/minute")
async def index_start(
    request: Request,
    payload: IndexRequest, 
    background_tasks: BackgroundTasks, 
    db: DatabaseManager = Depends(get_db), 
    emb=Depends(get_emb), 
    chroma=Depends(get_chroma)
):
    folders = payload.validated_folders
    if not folders: 
        return JSONResponse(status_code=400, content={"error": "No valid folder paths provided."})
    
    indexing_service_cls, _ = ensure_indexing()
    service = indexing_service_cls(db, emb, chroma)
    
    from app.main import _file_tree_cache, _insights_cache
    _file_tree_cache["data"] = None
    _insights_cache["data"] = None
    
    async def _index_then_compact():
        await service.index_folders(folders)
        try:
            await db.vacuum()
            logger.info("Auto-compact completed after indexing.")
        except Exception as e:
            logger.warning("Auto-compact after indexing failed: %s", e)
            
    background_tasks.add_task(_index_then_compact)
    return {"message": "Indexing started"}

@router.get("/status")
async def index_status(db: DatabaseManager = Depends(get_db)):
    _, progress = ensure_indexing()
    file_count, chunk_count = await db.get_counts()
    percentage = int((progress.processed_files / progress.total_files) * 100) if progress.total_files > 0 else 0
    return {
        "status": progress.status, 
        "files_indexed": file_count, 
        "chunks_indexed": chunk_count, 
        "progress_percent": percentage, 
        "scan_method": progress.scan_method, 
        "processed_files": progress.processed_files, 
        "total_files": progress.total_files
    }

@router.get("/progress-stream")
async def progress_stream(db: DatabaseManager = Depends(get_db)):
    _, progress = ensure_indexing()
    async def event_generator():
        while True:
            _, chunk_count = await db.get_counts()
            pct = int((progress.processed_files / progress.total_files) * 100) if progress.total_files > 0 else 0
            data = {
                "status": progress.status,
                "total_files": progress.total_files,
                "processed_files": progress.processed_files,
                "total_chunks": chunk_count,
                "skipped_files": progress.skipped_files,
                "new_files": progress.new_files,
                "changed_files": progress.changed_files,
                "current_file": progress.current_file,
                "scan_method": progress.scan_method,
                "scan_duration_ms": progress.scan_duration_ms,
                "progress_percent": pct
            }
            yield {"event": "progress", "data": json.dumps(data)}
            if progress.status != "running": break
            await asyncio.sleep(0.5)
    return EventSourceResponse(event_generator())

@router.post("/cleanup")
async def cleanup_stale(db: DatabaseManager = Depends(get_db)):
    try:
        from app.main import _file_tree_cache, _insights_cache
        cleaned = await db.cleanup_stale_files()
        _file_tree_cache["data"] = _insights_cache["data"] = None
        return {"message": f"Cleaned {len(cleaned)} stale file(s).", "cleaned_paths": cleaned}
    except Exception as e:
        logger.error("Cleanup failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Cleanup failed."})

@router.post("/clear")
async def clear_index(db: DatabaseManager = Depends(get_db), chroma=Depends(get_chroma)):
    from app.main import _file_tree_cache, _insights_cache
    res = await db.clear_all()
    await chroma.clear_all()
    _file_tree_cache["data"] = _insights_cache["data"] = None
    return res

@router.get("/export")
async def export_index(db: DatabaseManager = Depends(get_db)):
    try:
        file_count, chunk_count = await db.get_counts()
        files = await db.get_all_files()
        return {"file_count": file_count, "chunk_count": chunk_count, "files": [dict(f) for f in files]}
    except Exception as e:
        logger.error("Export failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Export failed."})

@router.post("/remove-folder")
async def remove_folder_index(request: IndexRequest, db: DatabaseManager = Depends(get_db), chroma=Depends(get_chroma)):
    from app.main import _file_tree_cache, _insights_cache
    folders = request.validated_folders
    if not folders:
        return JSONResponse(status_code=400, content={"error": "No valid folder paths provided."})
    from app.vectors import remove_folder_from_chroma
    try:
        file_ids_removed = await db.remove_folder(folders[0])
        await remove_folder_from_chroma(chroma.collection, file_ids_removed)
        _file_tree_cache["data"] = _insights_cache["data"] = None
        return {"message": f"Removed {folders[0]}", "files_removed": len(file_ids_removed)}
    except Exception as e:
        logger.error("Failed to remove folder: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/unreal-import")
async def unreal_import(request: UnrealImportRequest, db: DatabaseManager = Depends(get_db), emb=Depends(get_emb), chroma=Depends(get_chroma)):
    try:
        from app.insights.unreal_import import parse_unreal_metadata
        jpath = request.validated_json_path
        tag = request.folder_tag or "unreal_import"
        stats = parse_unreal_metadata(jpath)
        if stats:
            await db.upsert_unreal_facts({
                "folder_path": jpath, "folder_tag": tag, "project_name": stats.get("ProjectName",""),
                "engine_version": stats.get("EngineVersion",""), "total_assets": stats.get("AssetCount",0),
                "map_count": stats.get("MapCount",0), "character_blueprints": stats.get("CharacterBPCount",0),
                "pawn_blueprints": stats.get("PawnBPCount",0), "skeletal_meshes": stats.get("SkeletalMeshCount",0),
                "material_count": stats.get("MaterialCount",0), "niagara_systems": stats.get("NiagaraCount",0),
                "environment_assets": stats.get("EnvAssetCount",0), "metadata_source": jpath
            })
            
        import pyarrow as pa
        from sentence_transformers import SentenceTransformer
        import os
        model: SentenceTransformer = emb.model
        doc = f"Unreal Engine Metadata Import: {stats}"
        embedding = model.encode(doc).tolist()
        batch_ids = [f"unreal_{os.path.basename(jpath)}"]
        batch_metas = [{"source": jpath}]
        batch_docs = [doc]
        batch_embs = [embedding]
        
        record_batch = pa.RecordBatch.from_arrays(
            [
                pa.array(batch_ids, type=pa.string()),
                pa.array(batch_embs, type=pa.list_(pa.float32(), emb.dimension)),
                pa.array(batch_metas),
                pa.array(batch_docs, type=pa.string())
            ],
            names=['id', 'vector', 'metadata', 'text']
        )
        chroma.collection.add(record_batch)
        
        from app.main import _file_tree_cache, _insights_cache
        _file_tree_cache["data"] = _insights_cache["data"] = None
        return {"message": "Unreal Engine metadata imported successfully.", "stats": stats}
    except Exception as e:
        logger.error("Unreal import failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})
