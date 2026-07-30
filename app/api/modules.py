"""API Router for external sidecar modules communicating with PMA Core.
Provides a secure WebSocket endpoint for real-time bidirectional messaging.
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/modules", tags=["modules"])


async def _handle_creative_ingest(data: dict[str, Any]) -> dict[str, Any]:
    from app.api.deps import get_db

    db_mgr = await get_db()
    conn = db_mgr._get_conn()

    project_name = data.get("project_name") or "Untitled"
    hip_file = data.get("hip_file") or f"{project_name}.hip"
    chunks = data.get("chunks", [])
    houdini_version = data.get("houdini_version", "")
    platform = data.get("platform", "")
    folder_tag = f"houdini:{project_name}"
    synth_file_path = f"houdini://{project_name}"

    summary = f"Houdini {houdini_version} on {platform} - {hip_file}"

    await conn.execute(
        """
        INSERT INTO files (path, size, modified_at, type, folder_tag, summary, sha256)
        VALUES (?, ?, datetime('now'), 'houdini_hip', ?, ?, '')
        ON CONFLICT(path) DO UPDATE SET
            size=excluded.size,
            modified_at=excluded.modified_at,
            folder_tag=excluded.folder_tag,
            summary=excluded.summary
        """,
        (synth_file_path, len(chunks), folder_tag, summary),
    )
    await conn.commit()

    async with conn.execute(
        "SELECT id FROM files WHERE path = ?", (synth_file_path,)
    ) as cursor:
        file_row = await cursor.fetchone()
    file_id = file_row["id"] if file_row else 0

    await conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
    await conn.commit()

    rows = []
    for c in chunks:
        node_path = c.get("node_path", "")
        node_type = c.get("node_type", "")
        content_parts = []
        if c.get("comment"):
            content_parts.append(f"comment: {c['comment']}")
        if c.get("vex_snippet"):
            content_parts.append(f"vex: {c['vex_snippet']}")
        if c.get("non_default_parms"):
            parms_str = ", ".join(f"{k}={v}" for k, v in c["non_default_parms"].items())
            content_parts.append(f"parms: {parms_str}")
        if c.get("solver_parms"):
            solver_str = ", ".join(f"{k}={v}" for k, v in c["solver_parms"].items())
            content_parts.append(f"solver_parms: {solver_str}")
        if c.get("render_parms"):
            render_str = ", ".join(f"{k}={v}" for k, v in c["render_parms"].items())
            content_parts.append(f"render_parms: {render_str}")
        if c.get("connections"):
            conn_dict = c["connections"]
            inputs = conn_dict.get("inputs", [])
            outputs = conn_dict.get("outputs", [])
            if inputs:
                content_parts.append(f"inputs: {', '.join(inputs)}")
            if outputs:
                content_parts.append(f"outputs: {', '.join(outputs)}")
        if c.get("errors"):
            content_parts.append(f"errors: {'; '.join(c['errors'])}")
        if c.get("warnings"):
            content_parts.append(f"warnings: {'; '.join(c['warnings'])}")

        content = f"[{node_type}] {node_path}\n" + "\n".join(content_parts)
        rows.append((file_id, 0, 0, content))

    if rows:
        await conn.executemany(
            "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) VALUES (?, ?, ?, ?)",
            rows,
        )
        await conn.commit()

    return {"status": "success", "indexed": len(chunks)}


async def _handle_creative_query(data: dict[str, Any]) -> dict[str, Any]:
    from app.api.deps import get_db, get_llm

    db_mgr = await get_db()
    question = data.get("question", "")
    project_name = data.get("project_name")
    folder_tag = f"houdini:{project_name}" if project_name else None

    sql = """
        SELECT c.text_preview AS content, f.path, f.folder_tag
        FROM chunk_fts
        JOIN chunks c ON c.id = chunk_fts.rowid
        JOIN files f ON f.id = c.file_id
        WHERE chunk_fts MATCH ?
    """
    params: list[Any] = [question]

    if folder_tag:
        sql += " AND f.folder_tag = ?"
        params.append(folder_tag)
    else:
        sql += " AND f.folder_tag LIKE 'houdini:%'"

    sql += " LIMIT 5"

    results = []
    try:
        async with db_mgr._get_read_conn() as conn:
            async with conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                results = [dict(r) for r in rows]
    except Exception as e:
        logger.warning("FTS query failed, attempting LIKE fallback: %s", e)

    # Fallback to LIKE scan if FTS returned nothing or failed
    if not results:
        like_sql = """
            SELECT c.text_preview AS content, f.path, f.folder_tag
            FROM chunks c
            JOIN files f ON f.id = c.file_id
            WHERE c.text_preview LIKE ?
        """
        like_params: list[Any] = [f"%{question}%"]
        if folder_tag:
            like_sql += " AND f.folder_tag = ?"
            like_params.append(folder_tag)
        else:
            like_sql += " AND f.folder_tag LIKE 'houdini:%'"
        like_sql += " LIMIT 5"

        try:
            async with db_mgr._get_read_conn() as conn:
                async with conn.execute(like_sql, like_params) as cursor:
                    rows = await cursor.fetchall()
                    results = [dict(r) for r in rows]
        except Exception as ex:
            logger.warning("LIKE fallback query failed: %s", ex)

    if not results:
        return {
            "status": "success",
            "answer": "I couldn't find relevant data in the indexed scene.",
            "sources": [],
        }

    context = "\n\n".join(
        f"Snippet:\n{r['content']}" for r in results
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a Houdini technical assistant. Answer the artist's question "
                "using ONLY the provided scene context. If unknown, say so plainly."
            ),
        },
        {"role": "user", "content": f"Scene context:\n{context}\n\nQuestion: {question}"},
    ]

    llm = get_llm()
    answer = await llm.generate_raw(messages)
    sources = [r["path"] for r in results]
    return {"status": "success", "answer": answer, "sources": sources}


async def _handle_creative_cross_query(data: dict[str, Any]) -> dict[str, Any]:
    from app.api.deps import get_db, get_llm

    db_mgr = await get_db()
    question = data.get("question", "")

    sql = """
        SELECT c.text_preview AS content, f.path, f.folder_tag, f.summary
        FROM chunk_fts
        JOIN chunks c ON c.id = chunk_fts.rowid
        JOIN files f ON f.id = c.file_id
        WHERE chunk_fts MATCH ? AND f.folder_tag LIKE 'houdini:%'
        LIMIT 10
    """
    results = []
    try:
        async with db_mgr._get_read_conn() as conn:
            async with conn.execute(sql, [question]) as cursor:
                rows = await cursor.fetchall()
                results = [dict(r) for r in rows]
    except Exception as e:
        logger.warning("Cross FTS query failed, attempting LIKE fallback: %s", e)

    if not results:
        like_sql = """
            SELECT c.text_preview AS content, f.path, f.folder_tag, f.summary
            FROM chunks c
            JOIN files f ON f.id = c.file_id
            WHERE c.text_preview LIKE ? AND f.folder_tag LIKE 'houdini:%'
            LIMIT 10
        """
        try:
            async with db_mgr._get_read_conn() as conn:
                async with conn.execute(like_sql, [f"%{question}%"]) as cursor:
                    rows = await cursor.fetchall()
                    results = [dict(r) for r in rows]
        except Exception as ex:
            logger.warning("Cross LIKE query failed: %s", ex)

    if not results:
        return {
            "status": "success",
            "answer": "No matching data across indexed projects.",
            "sources": [],
        }

    context = "\n\n".join(
        f"[Project: {r['folder_tag'].replace('houdini:', '')}]\n{r['content']}"
        for r in results
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a Houdini technical assistant. The artist is searching across "
                "past projects. Always cite which project file each piece of information came from."
            ),
        },
        {"role": "user", "content": f"Cross-project context:\n{context}\n\nQuestion: {question}"},
    ]

    llm = get_llm()
    answer = await llm.generate_raw(messages)
    sources = [
        {"project": r["folder_tag"].replace("houdini:", ""), "path": r["path"]}
        for r in results
    ]
    return {"status": "success", "answer": answer, "sources": sources}


async def _handle_creative_list_projects() -> dict[str, Any]:
    from app.api.deps import get_db

    db_mgr = await get_db()
    sql = """
        SELECT folder_tag, path, size AS node_count, modified_at, summary
        FROM files
        WHERE folder_tag LIKE 'houdini:%'
        ORDER BY modified_at DESC
    """
    async with db_mgr._get_read_conn() as conn:
        async with conn.execute(sql) as cursor:
            rows = await cursor.fetchall()
            projects = [
                {
                    "project_name": r["folder_tag"].replace("houdini:", ""),
                    "hip_file": r["summary"],
                    "node_count": r["node_count"],
                    "last_indexed": r["modified_at"],
                }
                for r in rows
            ]
    return {"status": "success", "projects": projects}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = Query(None)):
    """
    Secure WebSocket endpoint for module health and handshake messaging.
    Requires a valid X_LOCAL_ACCESS_TOKEN passed either in the header
    'X-Local-Access-Token' or as a query parameter 'token'.
    """
    expected_token = os.environ.get("X_LOCAL_ACCESS_TOKEN")
    if not expected_token:
        logger.error("X_LOCAL_ACCESS_TOKEN not set on server. WebSocket connection refused.")
        await websocket.close(code=1008)  # Policy Violation
        return

    provided_token = websocket.headers.get("x-local-access-token")
    if not provided_token:
        provided_token = token

    if not provided_token or not secrets.compare_digest(provided_token, expected_token):
        logger.warning("Unauthorized WebSocket connection attempt refused.")
        await websocket.close(code=1008)  # Policy Violation
        return

    await websocket.accept()
    logger.info("Secure module WebSocket client connected successfully.")

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.warning("Malformed WS frame: %s", e)
                await websocket.send_json({"status": "error", "message": "Invalid JSON payload"})
                continue

            action = data.get("action")

            try:
                if action == "ping":
                    await websocket.send_json({"status": "pong"})
                elif action == "creative_ingest":
                    res = await _handle_creative_ingest(data)
                    await websocket.send_json({"action": action, **res})
                elif action == "creative_query":
                    res = await _handle_creative_query(data)
                    await websocket.send_json({"action": action, **res})
                elif action == "creative_cross_query":
                    res = await _handle_creative_cross_query(data)
                    await websocket.send_json({"action": action, **res})
                elif action == "creative_list_projects":
                    res = await _handle_creative_list_projects()
                    await websocket.send_json({"action": action, **res})
                else:
                    await websocket.send_json({"status": "error", "message": f"Unknown action '{action}'"})
            except Exception as e:
                logger.error("Error handling module action %s: %s", action, e, exc_info=True)
                await websocket.send_json({"status": "error", "action": action, "message": str(e)})

    except WebSocketDisconnect:
        logger.info("Module WebSocket client disconnected.")
    except Exception as e:
        logger.error("Error in module WebSocket loop: %s", e)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
