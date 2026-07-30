import os
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_db


@pytest.fixture(autouse=True)
async def setup_test_db():
    db_mgr = await get_db()
    await db_mgr.init_db()
    yield


@pytest.mark.asyncio
async def test_creative_module_ws_full_flow(monkeypatch):
    monkeypatch.setenv("X_LOCAL_ACCESS_TOKEN", "test-secret-token")

    from app.search.llm_client import LLMClient

    async def fake_generate_raw(self, messages, **kwargs):
        return "The pyro solver buoyancy parameter controls thermal lift in the simulation."

    monkeypatch.setattr(LLMClient, "generate_raw", fake_generate_raw)

    client = TestClient(app)
    headers = {"x-local-access-token": "test-secret-token"}

    with client.websocket_connect("/api/modules/ws", headers=headers) as websocket:
        # 1. Test Ingest
        chunks = [
            {
                "node_path": "/obj/pyro_sim/pyrosolver1",
                "node_type": "pyrosolver",
                "solver_parms": {"buoyancy": "2.5", "divsize": "0.05"},
                "comment": "high resolution fire simulation",
            }
        ]
        ingest_msg = {
            "action": "creative_ingest",
            "project_name": "fire_explosion",
            "hip_file": "/projects/fire_explosion.hip",
            "chunks": chunks,
            "houdini_version": "20.0.368",
            "platform": "win64",
        }
        websocket.send_json(ingest_msg)
        resp_ingest = websocket.receive_json()
        assert resp_ingest["action"] == "creative_ingest"
        assert resp_ingest["status"] == "success"
        assert resp_ingest["indexed"] == 1

        # 2. Test Query
        query_msg = {
            "action": "creative_query",
            "project_name": "fire_explosion",
            "question": "buoyancy",
        }
        websocket.send_json(query_msg)
        resp_query = websocket.receive_json()
        assert resp_query["action"] == "creative_query"
        assert resp_query["status"] == "success"
        assert "buoyancy" in resp_query["answer"]

        # 3. Test Cross Query
        cross_msg = {
            "action": "creative_cross_query",
            "question": "buoyancy",
        }
        websocket.send_json(cross_msg)
        resp_cross = websocket.receive_json()
        assert resp_cross["action"] == "creative_cross_query"
        assert resp_cross["status"] == "success"
        assert len(resp_cross["sources"]) > 0

        # 4. Test List Projects
        list_msg = {"action": "creative_list_projects"}
        websocket.send_json(list_msg)
        resp_list = websocket.receive_json()
        assert resp_list["action"] == "creative_list_projects"
        assert resp_list["status"] == "success"
        assert len(resp_list["projects"]) == 1
        assert resp_list["projects"][0]["project_name"] == "fire_explosion"
