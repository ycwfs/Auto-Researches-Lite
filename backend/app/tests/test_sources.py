"""Admin paper-source CRUD + encrypted API-key pool + S2 key rotation."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---- admin CRUD + key encryption ------------------------------------------- #
def test_source_crud_and_key_pool(admin_client: TestClient) -> None:
    # create with a key pool
    r = admin_client.post(
        "/api/admin/sources",
        json={"key": "test_src_crud", "name": "Test", "api_keys": ["sk-zzz1", "sk-zzz2", "sk-zzz3"]},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["key_count"] == 3
    # the keys themselves (and the encrypted blob) are NEVER returned
    assert "api_keys" not in r.json() and "api_keys_enc" not in r.json()
    assert "sk-zzz" not in str(r.json())

    # update replaces the pool
    r = admin_client.patch(f"/api/admin/sources/{sid}", json={"api_keys": ["x", "y"]})
    assert r.json()["key_count"] == 2

    # editing another field leaves the pool untouched (api_keys omitted -> None)
    r = admin_client.patch(f"/api/admin/sources/{sid}", json={"description": "d"})
    assert r.json()["key_count"] == 2 and r.json()["description"] == "d"

    # empty list clears the pool
    r = admin_client.patch(f"/api/admin/sources/{sid}", json={"api_keys": []})
    assert r.json()["key_count"] == 0

    # delete
    assert admin_client.delete(f"/api/admin/sources/{sid}").status_code == 204
    assert all(s["key"] != "test_src_crud" for s in admin_client.get("/api/admin/sources").json())


def test_list_enabled_sources_for_picker(auth_client: TestClient) -> None:
    """The project picker endpoint returns enabled sources (arxiv is enabled by default)."""
    r = auth_client.get("/api/sources")
    assert r.status_code == 200, r.text
    keys = {s["key"] for s in r.json()}
    assert "arxiv" in keys
    assert all("name" in s and "key" in s for s in r.json())


# ---- decrypted pool is injected into the source config --------------------- #
def test_discovery_injects_key_pool() -> None:
    from app.api.routes.admin import _encode_keys, decode_source_keys
    from app.core.database import SessionLocal
    from app.models.admin import PaperSource
    from app.services.discovery_service import _enabled_source_keys

    enc = _encode_keys(["k1", "k2"])
    assert decode_source_keys(enc) == ["k1", "k2"]  # roundtrip

    db = SessionLocal()
    try:
        db.add(PaperSource(key="s2_test", name="S2", enabled=True, config={"a": "b"}, api_keys_enc=enc))
        db.add(PaperSource(key="pf_test", name="PF", enabled=False, config={}))
        db.commit()
        keys, configs, skipped = _enabled_source_keys(db, ["s2_test"])
        assert "s2_test" in keys
        assert configs["s2_test"]["api_keys"] == ["k1", "k2"]  # decrypted + injected
        assert configs["s2_test"]["a"] == "b"  # original config preserved

        # A disabled source is reported as skipped, not silently swapped for arXiv.
        keys, _, skipped = _enabled_source_keys(db, ["pf_test"])
        assert keys == [] and skipped == ["pf_test"]
    finally:
        db.query(PaperSource).filter(PaperSource.key.in_(["s2_test", "pf_test"])).delete()
        db.commit()
        db.close()


# ---- S2 rotates across the pool on 429 ------------------------------------- #
class _Resp:
    def __init__(self, status: int, data: dict | None = None) -> None:
        self.status_code = status
        self._data = data or {}
        self.text = ""

    def json(self) -> dict:
        return self._data


def test_s2_rotates_keys_on_429(monkeypatch) -> None:
    from app.integrations.sources import semantic_scholar as s2

    monkeypatch.setattr(s2.random, "shuffle", lambda x: None)  # deterministic order
    used: list[str] = []

    def fake(method, url, *, headers=None, **kw):
        used.append((headers or {}).get("x-api-key", ""))
        if used[-1] == "k1":
            return _Resp(429)  # first key rate-limited
        return _Resp(200, {"data": [{"title": "P", "paperId": "x"}]})

    monkeypatch.setattr(s2, "request_with_retry", fake)
    src = s2.SemanticScholarSource()
    data = src._get_with_backoff(["k1", "k2"], {"query": "x"})
    assert data["data"][0]["title"] == "P"
    assert used == ["k1", "k2"]  # rotated to k2 after k1's 429

    # every key rate-limited -> a clear error
    monkeypatch.setattr(s2, "request_with_retry", lambda *a, **k: _Resp(429))
    with pytest.raises(RuntimeError, match="429"):
        src._get_with_backoff(["k1", "k2"], {})
