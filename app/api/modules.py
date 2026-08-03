"""API Router for external sidecar modules communicating with PMA Core.
Provides a secure WebSocket endpoint for real-time bidirectional messaging.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/modules", tags=["modules"])


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
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as e:
                # These are the only failure modes that mean "the client sent
                # a bad frame" (bad JSON, wrong message type, bad encoding).
                # Any other exception must NOT be treated the same way, or a
                # persistent failure loops here forever instead of ever
                # reaching the close(code=1011) below.
                logger.warning("Malformed WS frame: %s", e)
                await websocket.send_json({"status": "error", "message": "Invalid JSON payload"})
                continue

            action = data.get("action")

            try:
                if action == "ping":
                    await websocket.send_json({"status": "pong"})
                else:
                    await websocket.send_json({"status": "error", "message": f"Unknown action '{action}'"})
            except Exception as e:
                logger.error("Error handling module action %s: %s", action, e, exc_info=True)
                await websocket.send_json({"status": "error", "action": action, "message": str(e)})

    except WebSocketDisconnect:
        logger.info("Module WebSocket client disconnected.")
    except Exception as e:
        logger.error("Error in module WebSocket loop: %s", e)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
