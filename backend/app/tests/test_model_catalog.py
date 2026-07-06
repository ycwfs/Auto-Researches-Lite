"""Admin model catalog + tier-gated user selection tests."""
from __future__ import annotations

import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import encrypt_secret


def _api_model_payload(label="API model", tiers=None):
    return {
        "label": label,
        "kind": "api",
        "provider": "claude",
        "base_url": "https://proxy/anthropic",
        "model": "claude-opus-4-8",
        "api_key": "sk-secret-123",
        "enabled": True,
        "allowed_tiers": tiers or ["free", "pro", "max"],
    }


def test_admin_model_crud_and_no_key_leak(admin_client):
    # Create
    r = admin_client.post("/api/admin/models", json=_api_model_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["key_set"] is True
    assert "api_key" not in body and "api_key_enc" not in body
    assert "sk-secret-123" not in r.text
    mid = body["id"]

    # List
    r = admin_client.get("/api/admin/models")
    assert r.status_code == 200
    assert any(m["id"] == mid for m in r.json())
    assert "sk-secret" not in r.text

    # Patch (toggle enabled, restrict tiers) — key untouched when omitted
    r = admin_client.patch(
        f"/api/admin/models/{mid}", json={"enabled": False, "allowed_tiers": ["pro"]}
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["allowed_tiers"] == ["pro"]
    assert r.json()["key_set"] is True  # still set

    # Delete
    assert admin_client.delete(f"/api/admin/models/{mid}").status_code == 204
    assert all(m["id"] != mid for m in admin_client.get("/api/admin/models").json())


def test_user_models_listed_without_secrets(admin_client, auth_client):
    # Admin adds two models; every enabled model is available (no tier gating).
    m1 = admin_client.post(
        "/api/admin/models", json=_api_model_payload("Model one")
    ).json()
    m2 = admin_client.post(
        "/api/admin/models", json=_api_model_payload("Model two")
    ).json()

    # The user-facing catalog lists every enabled model.
    r = auth_client.get("/api/models")
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()}
    assert m1["id"] in ids
    assert m2["id"] in ids
    # User view never exposes keys or base URLs.
    assert "base_url" not in r.text and "api_key" not in r.text and "/anthropic" not in r.text


def test_build_llm_resolves_api_model_from_catalog():
    from app.models.admin import ModelCatalog
    from app.models.enums import ModelKind
    from app.models.project import Project
    from app.models.user import User
    from app.services import model_select

    db = SessionLocal()
    saved = settings.offline_mode
    try:
        settings.offline_mode = None  # exercise real resolution (not forced mock)
        u = User(
            email=f"cat-{uuid.uuid4().hex[:8]}@example.com",
            full_name="Cat",
            hashed_password="!test",
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        entry = ModelCatalog(
            label="Opus api", kind=ModelKind.api, provider="claude",
            base_url="https://proxy/anthropic", model="claude-opus-4-8",
            api_key_enc=encrypt_secret("sk-x"), allowed_tiers=["free", "pro", "max"],
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        p = Project(owner_id=u.id, name="P", step_models={"chat": {"model_id": entry.id}})
        db.add(p)
        db.commit()
        db.refresh(p)

        llm = model_select.build_llm_for_step(db, u.id, p, "chat")
        assert llm.offline is False
        assert llm.config.provider == "claude"
        assert llm.config.model == "claude-opus-4-8"
        assert llm.config.base_url == "https://proxy/anthropic"
        assert llm.config.api_key == "sk-x"  # decrypted from the catalog
        # Default reasoning is off when the step doesn't set one.
        assert llm.config.reasoning == "off"
    finally:
        settings.offline_mode = saved
        db.close()


def _user_and_api_entry(db, key="sk-x"):
    from app.models.admin import ModelCatalog
    from app.models.enums import ModelKind
    from app.models.user import User

    u = User(
        email=f"cat-{uuid.uuid4().hex[:8]}@example.com",
        full_name="Cat",
        hashed_password="!test",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    entry = ModelCatalog(
        label="Opus api", kind=ModelKind.api, provider="claude",
        model="claude-opus-4-8", api_key_enc=encrypt_secret(key),
        allowed_tiers=["free", "pro", "max"],
        supported_efforts=["low", "medium", "high", "xhigh", "max"],
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return u, entry


def test_reasoning_level_flows_into_config():
    from app.models.project import Project
    from app.services import model_select

    db = SessionLocal()
    saved = settings.offline_mode
    try:
        settings.offline_mode = None
        u, entry = _user_and_api_entry(db)
        # Per-step reasoning is carried into the resolved config.
        p = Project(
            owner_id=u.id, name="P",
            step_models={"chat": {"model_id": entry.id, "reasoning": "high"}},
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        assert model_select.build_llm_for_step(db, u.id, p, "chat").config.reasoning == "high"
        # Unset -> off; invalid -> off.
        p.step_models = {
            "chat": {"model_id": entry.id},
            "zotero": {"model_id": entry.id, "reasoning": "bogus"},
        }
        db.commit()
        assert model_select.build_llm_for_step(db, u.id, p, "chat").config.reasoning == "off"
        assert model_select.build_llm_for_step(db, u.id, p, "zotero").config.reasoning == "off"
    finally:
        settings.offline_mode = saved
        db.close()


def test_zotero_step_resolves_its_own_model():
    from app.models.project import Project
    from app.services import model_select

    db = SessionLocal()
    saved = settings.offline_mode
    try:
        settings.offline_mode = None
        u, entry = _user_and_api_entry(db, key="sk-z")
        p = Project(
            owner_id=u.id, name="P",
            step_models={"zotero": {"model_id": entry.id, "reasoning": "low"}},
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        llm = model_select.build_llm_for_step(db, u.id, p, "zotero")
        assert llm.offline is False
        assert llm.config.model == "claude-opus-4-8"
        assert llm.config.reasoning == "low"
    finally:
        settings.offline_mode = saved
        db.close()


def test_summary_step_resolves_its_own_model():
    """Guards the frontend/backend per-step key contract for the summary step: the
    frontend saves the discovery-summary pick under step_models["summary"] (STEPS in
    configSections.tsx), and the summary pipeline must read the same key. A key drift
    here silently ignores the user's pick — only 'chat' was previously exercised."""
    from app.models.enums import STEP_NAMES
    from app.models.project import Project
    from app.services import model_select

    assert "summary" in STEP_NAMES  # matches frontend configSections.STEPS

    db = SessionLocal()
    saved = settings.offline_mode
    try:
        settings.offline_mode = None
        u, entry = _user_and_api_entry(db, key="sk-s")
        p = Project(
            owner_id=u.id, name="P",
            step_models={"summary": {"model_id": entry.id, "reasoning": "high"}},
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        llm = model_select.build_llm_for_step(db, u.id, p, "summary")
        assert llm.offline is False
        assert llm.config.model == "claude-opus-4-8"
        assert llm.config.api_key == "sk-s"  # the user's explicit summary-step pick
        assert llm.config.reasoning == "high"
    finally:
        settings.offline_mode = saved
        db.close()
