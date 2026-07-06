"""Admin conference management proxy + the year-granular enabled-only picker."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient


class _Resp:
    def __init__(self, status: int = 200, data: dict | None = None) -> None:
        self.status_code = status
        self._data = data or {}
        self.text = json.dumps(self._data)
        self.headers = {"content-type": "application/json"}

    def json(self) -> dict:
        return self._data


def test_admin_conferences_crud_proxy(admin_client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("PAPERFINDER_ENDPOINT", "http://pf:8100/search")
    seen: list[tuple] = []

    def fake(method, url, **kw):
        seen.append((method, url, kw.get("json")))
        if url.endswith("/venues"):
            return _Resp(200, {"venues": [{"venue": "CVPR", "year": "2026", "count": 5, "enabled": True}]})
        if url.endswith("/admin/ingest") and method == "POST":
            return _Resp(200, {"task_id": "t1", "state": "queued"})
        if "/admin/ingest/" in url:
            return _Resp(200, {"task_id": "t1", "state": "done", "count": 5})
        return _Resp(200, {"ok": True})

    monkeypatch.setattr("app.api.routes.admin.request_with_retry", fake)

    assert admin_client.get("/api/admin/conferences").json()["venues"][0]["venue"] == "CVPR"

    r = admin_client.post("/api/admin/conferences", json={"venue": "ICLR", "year": "2025", "source": "openreview"})
    assert r.status_code == 202, r.text
    assert r.json()["task_id"] == "t1"

    assert admin_client.get("/api/admin/conferences/ingest/t1").json()["state"] == "done"
    assert admin_client.delete("/api/admin/conferences/CVPR/2026").status_code == 200
    assert admin_client.patch(
        "/api/admin/conferences", json={"venue": "CVPR", "year": "2026", "enabled": False}
    ).status_code == 200
    # the ingest body carried venue/year/source through to the sidecar
    ingest = next(c for c in seen if c[1].endswith("/admin/ingest") and c[0] == "POST")
    assert ingest[2] == {"venue": "ICLR", "year": "2025", "source": "openreview"}


def test_picker_offers_only_enabled_conferences(auth_client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("PAPERFINDER_ENDPOINT", "http://pf:8100/search")
    monkeypatch.setattr(
        "app.api.routes.sources.request_with_retry",
        lambda *a, **k: _Resp(200, {"venues": [
            {"venue": "CVPR", "year": "2026", "count": 5, "enabled": True},
            {"venue": "CVPR", "year": "2025", "count": 3, "enabled": False},  # disabled -> hidden
        ]}),
    )
    venues = auth_client.get("/api/sources/paperfinder/venues").json()["venues"]
    assert len(venues) == 1
    assert venues[0]["year"] == "2026"
