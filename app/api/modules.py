"""API Router for external sidecar modules communicating with PMA Core.
Provides a secure WebSocket endpoint for real-time bidirectional messaging.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import time
from collections import deque

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.deps import get_db, get_emb, get_lancedb, get_planner
from app.search.retrieval import retrieve_only

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/modules", tags=["modules"])

# Per-connection request budget for the module socket.
RATE_LIMIT_COUNT = 50
RATE_LIMIT_WINDOW = 60.0


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

    request_times: deque[float] = deque()

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as e:
                logger.warning("Malformed WS frame: %s", e)
                await websocket.send_json({"status": "error", "message": "Invalid JSON payload"})
                continue

            now = time.time()
            while request_times and now - request_times[0] > RATE_LIMIT_WINDOW:
                request_times.popleft()

            if len(request_times) >= RATE_LIMIT_COUNT:
                await websocket.send_json(
                    {"status": "error", "message": "Rate limit exceeded. Too many requests."}
                )
                continue

            request_times.append(now)

            action = data.get("action")

            try:
                if action == "session.hello":
                    await websocket.send_json(
                        {
                            "status": "ok",
                            "version": "0.1",
                            "capabilities": ["retrieve", "chunk.get", "corpus.stats"],
                        }
                    )
                elif action == "retrieve":
                    query = data.get("query", "")
                    k = data.get("k", 10)
                    file_type = data.get("file_type")
                    folder_tag = data.get("folder_tag")

                    db = await get_db()
                    embedding_service = get_emb()
                    lancedb_client = get_lancedb()
                    planner = get_planner()

                    res = await retrieve_only(
                        query=query,
                        db=db,
                        embedding_service=embedding_service,
                        lancedb_client=lancedb_client,
                        planner=planner,
                        k=k,
                        file_type=file_type,
                        folder_tag=folder_tag,
                    )
                    await websocket.send_json({"status": "ok", "data": res})
                elif action == "chunk.get":
                    chunk_ids = data.get("chunk_ids", [])
                    db = await get_db()
                    chunks = await db.get_chunks_by_ids(chunk_ids)
                    await websocket.send_json({"status": "ok", "data": chunks})
                elif action == "corpus.stats":
                    db = await get_db()
                    stats = await db.get_file_stats_summary()
                    await websocket.send_json({"status": "ok", "data": stats})
                elif action == "ping":
                    await websocket.send_json({"status": "pong"})
                else:
                    await websocket.send_json(
                        {"status": "error", "message": f"Unknown action '{action}'"}
                    )
            except Exception as e:
                logger.error("Error handling module action %s: %s", action, e, exc_info=True)
                await websocket.send_json({"status": "error", "action": action, "message": str(e)})

    except WebSocketDisconnect:
        logger.info("Module WebSocket client disconnected.")
    except Exception as e:
        logger.error("Error in module WebSocket loop: %s", e)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
