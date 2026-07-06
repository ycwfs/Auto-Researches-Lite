"""Regression tests for the 'false success' audit — blank/missing required input must NOT
save-and-report-connected/configured. Covers fix A (credentials) and B (model catalog)."""
from __future__ import annotations

from fastapi.testclient import TestClient


# --- Fix A: credentials ------------------------------------------------------
def _put_cred(client: TestClient, provider: str, data: dict):
    return client.put("/api/credentials", json={"provider": provider, "data": data})


def test_blank_credential_rejected(auth_client: TestClient) -> None:
    # Empty and partial saves are refused (was: saved + shown "connected").
    assert _put_cred(auth_client, "zotero", {}).status_code == 422
    assert _put_cred(auth_client, "zotero", {"api_key": "k"}).status_code == 422  # no library_id
    # And a blank save never flips the connected flag.
    creds = {c["provider"]: c for c in auth_client.get("/api/credentials").json()}
    assert creds["zotero"]["configured"] is False


def test_full_credential_configured(auth_client: TestClient) -> None:
    r = _put_cred(auth_client, "zotero", {"api_key": "abc", "library_id": "123", "library_type": "user"})
    assert r.status_code == 200 and r.json()["configured"] is True
    creds = {c["provider"]: c for c in auth_client.get("/api/credentials").json()}
    assert creds["zotero"]["configured"] is True


def test_leave_blank_to_keep_merges_not_wipes(auth_client: TestClient) -> None:
    # Establish a full zotero credential, then re-save changing ONLY the api_key.
    assert _put_cred(auth_client, "zotero",
                     {"api_key": "old", "library_id": "999", "library_type": "user"}).status_code == 200
    r = _put_cred(auth_client, "zotero", {"api_key": "new"})  # library_id omitted
    # Merge keeps library_id → still configured (was: whole-blob replace wiped it).
    assert r.status_code == 200 and r.json()["configured"] is True
    assert "library_id" in r.json()["masked"]


def test_unknown_provider_rejected(auth_client: TestClient) -> None:
    assert _put_cred(auth_client, "openai", {"api_key": "x"}).status_code == 400


# --- Fix B/C: model catalog + templates -------------------------------------
def test_admin_model_blank_fields_rejected(admin_client: TestClient) -> None:
    base = {"kind": "api", "provider": "claude", "model": "m", "api_key": "sk-x", "allowed_tiers": ["free"]}
    assert admin_client.post("/api/admin/models", json={**base, "label": ""}).status_code == 422
    assert admin_client.post("/api/admin/models", json={**base, "label": "L", "model": ""}).status_code == 422


def test_admin_model_patch_cannot_blank_required(admin_client: TestClient) -> None:
    mid = admin_client.post("/api/admin/models", json={
        "label": "PatchTest", "kind": "api", "provider": "claude", "model": "m",
        "api_key": "sk-x", "allowed_tiers": ["free"]}).json()["id"]
    try:
        assert admin_client.patch(f"/api/admin/models/{mid}", json={"model": "  "}).status_code == 422
        assert admin_client.patch(f"/api/admin/models/{mid}", json={"provider": ""}).status_code == 422
        assert admin_client.patch(f"/api/admin/models/{mid}", json={"label": "Renamed"}).status_code == 200
    finally:
        admin_client.delete(f"/api/admin/models/{mid}")


def test_model_option_exposes_key_set(admin_client: TestClient, auth_client: TestClient) -> None:
    keyed = admin_client.post("/api/admin/models", json={
        "label": "Keyed", "kind": "api", "provider": "claude", "model": "m1",
        "api_key": "sk-real", "allowed_tiers": ["free", "pro", "max"]}).json()["id"]
    keyless = admin_client.post("/api/admin/models", json={
        "label": "Keyless", "kind": "api", "provider": "claude", "model": "m2",
        "api_key": "", "allowed_tiers": ["free", "pro", "max"]}).json()["id"]
    try:
        opts = {m["label"]: m for m in auth_client.get("/api/models").json()}
        assert opts["Keyed"]["key_set"] is True
        assert opts["Keyless"]["key_set"] is False  # picker can now warn "no key (mock)"
    finally:
        admin_client.delete(f"/api/admin/models/{keyed}")
        admin_client.delete(f"/api/admin/models/{keyless}")


