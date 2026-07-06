"""Admin integration config (MinerU) + third-party API test endpoints."""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient


def test_integration_config_saves_and_key_hidden(admin_client: TestClient) -> None:
    r = admin_client.put(
        "/api/admin/integrations",
        json={"mineru_api_url": "https://mineru.example/extract", "mineru_api_key": "sk-mineru-123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mineru_api_url"] == "https://mineru.example/extract"
    assert body["mineru_key_set"] is True
    assert "sk-mineru-123" not in r.text  # the raw key is never returned

    g = admin_client.get("/api/admin/integrations")
    assert g.json()["mineru_key_set"] is True and "sk-mineru-123" not in g.text


def test_mineru_config_resolution_prefers_db(auth_client: TestClient) -> None:
    from app.core.database import SessionLocal
    from app.services import integration_service

    db = SessionLocal()
    try:
        integration_service.set_mineru(db, api_url="https://m.example/x", api_key="k123")
        key, url = integration_service.mineru_config(db)
        assert key == "k123" and url == "https://m.example/x"
    finally:
        db.close()


def test_model_test_endpoint(admin_client: TestClient, monkeypatch) -> None:
    from app.services import api_test

    # A reachable provider → ok; a key-less model → not ok (fails before the probe).
    monkeypatch.setattr(
        api_test, "_test_completion", lambda *a, **k: (True, "OK — model responded: ok")
    )
    mid = admin_client.post(
        "/api/admin/models",
        json={"label": "TestModel", "kind": "api", "provider": "claude", "model": "claude-x", "api_key": "sk-x"},
    ).json()["id"]
    r = admin_client.post(f"/api/admin/models/{mid}/test")
    assert r.status_code == 200 and r.json()["ok"] is True

    mid2 = admin_client.post(
        "/api/admin/models",
        json={"label": "NoKey", "kind": "api", "provider": "claude", "model": "y"},
    ).json()["id"]
    assert admin_client.post(f"/api/admin/models/{mid2}/test").json()["ok"] is False


def test_mineru_test_endpoint(admin_client: TestClient, monkeypatch) -> None:
    admin_client.put(
        "/api/admin/integrations",
        json={"mineru_api_url": "https://m.example/x", "mineru_api_key": "k"},
    )

    class _Resp:
        def __init__(self, sc: int, payload=None) -> None:
            self.status_code = sc
            self.text = "x"
            self._payload = payload

        def json(self):
            if self._payload is None:
                raise ValueError("no json")
            return self._payload

    def post_resp(resp):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: resp)
        return admin_client.post("/api/admin/integrations/mineru/test").json()["ok"]

    assert post_resp(_Resp(200)) is True  # clean 200
    assert post_resp(_Resp(200, {"error": "bad key"})) is False  # 200 but an error body
    assert post_resp(_Resp(500)) is False  # server error
    assert post_resp(_Resp(401)) is False  # auth
