"""Model connectivity-test truthfulness + persisted health filtering.

Covers the two admin-panel bugs around third-party models:
1. A completed API round-trip with no plain text (safety refusal, or a reasoning
   model spending the whole probe budget on thinking) must count as endpoint OK —
   the provider billed the call, so "test failed" was a false negative.
2. The last test outcome is persisted on the catalog entry: models whose last test
   FAILED are hidden from non-admin users' per-step pickers (admins still see them,
   flagged), and editing a connection-relevant field resets the outcome to
   "never tested".
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services import api_test


# ---- stubbed anthropic SDK for _test_completion unit tests --------------------
class _Block:
    def __init__(self, type_: str, text: str = "") -> None:
        self.type = type_
        self.text = text


class _Usage:
    def __init__(self, output_tokens: int) -> None:
        self.output_tokens = output_tokens


class _Resp:
    def __init__(self, blocks: list, stop_reason: str, output_tokens: int) -> None:
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = _Usage(output_tokens)


def _stub_anthropic(monkeypatch, resp: _Resp) -> None:
    class _Messages:
        def create(self, **_kw):
            return resp

    class _Client:
        def __init__(self, **_kw) -> None:
            self.messages = _Messages()

    monkeypatch.setattr("anthropic.Anthropic", _Client)


def test_probe_text_response_ok(monkeypatch) -> None:
    _stub_anthropic(monkeypatch, _Resp([_Block("text", "4")], "end_turn", 3))
    ok, detail = api_test._test_completion("anthropic", "k", "", "m")
    assert ok is True and "model responded" in detail


def test_probe_refusal_counts_as_endpoint_ok(monkeypatch) -> None:
    # Observed with claude-fable-5 behind a proxy: HTTP 200, billed, zero content
    # blocks, stop_reason=refusal. The key/endpoint work — must not report failure.
    _stub_anthropic(monkeypatch, _Resp([], "refusal", 4))
    ok, detail = api_test._test_completion("anthropic", "k", "", "m")
    assert ok is True and "refused" in detail


def test_probe_reasoning_only_counts_as_endpoint_ok(monkeypatch) -> None:
    _stub_anthropic(monkeypatch, _Resp([_Block("thinking")], "max_tokens", 512))
    ok, detail = api_test._test_completion("anthropic", "k", "", "m")
    assert ok is True and "reasoning" in detail


def test_probe_truly_empty_fails(monkeypatch) -> None:
    _stub_anthropic(monkeypatch, _Resp([], "end_turn", 0))
    ok, detail = api_test._test_completion("anthropic", "k", "", "m")
    assert ok is False and "Empty response" in detail


# ---- persisted outcome + picker filtering -------------------------------------
def _make_model(admin_client: TestClient) -> int:
    r = admin_client.post(
        "/api/admin/models",
        json={
            "label": "HealthProbe",
            "kind": "api",
            "provider": "claude",
            "model": "claude-h",
            "api_key": "sk-h",
            "allowed_tiers": ["free", "pro", "max"],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _admin_entry(admin_client: TestClient, mid: int) -> dict:
    return next(m for m in admin_client.get("/api/admin/models").json() if m["id"] == mid)


def test_failed_models_stay_listed_but_flagged_until_retested(
    admin_client: TestClient, auth_client: TestClient, monkeypatch
) -> None:
    """Single-user edition: a model whose connectivity test failed stays in the picker
    (the local user is the admin who must debug it) but is flagged ``test_failed`` so
    the UI can warn. A passing re-test clears the flag."""
    mid = _make_model(admin_client)
    try:
        entry = _admin_entry(admin_client, mid)
        assert entry["last_test_ok"] is None and entry["last_test_at"] is None
        assert any(m["id"] == mid for m in auth_client.get("/api/models").json())

        # Failing test → persisted ✗ → still listed, flagged for the user to fix.
        monkeypatch.setattr(
            api_test, "_test_completion", lambda *a, **k: (False, "Empty response from the model.")
        )
        assert admin_client.post(f"/api/admin/models/{mid}/test").json()["ok"] is False
        entry = _admin_entry(admin_client, mid)
        assert entry["last_test_ok"] is False and entry["last_test_at"]
        view = next(m for m in auth_client.get("/api/models").json() if m["id"] == mid)
        assert view["test_failed"] is True

        # Passing re-test → flag cleared.
        monkeypatch.setattr(
            api_test, "_test_completion", lambda *a, **k: (True, "OK — model responded: 4")
        )
        assert admin_client.post(f"/api/admin/models/{mid}/test").json()["ok"] is True
        assert _admin_entry(admin_client, mid)["last_test_ok"] is True
        user_view = next(
            m for m in auth_client.get("/api/models").json() if m["id"] == mid
        )
        assert user_view["test_failed"] is False
    finally:
        admin_client.delete(f"/api/admin/models/{mid}")


def test_connection_edit_resets_health(admin_client: TestClient, monkeypatch) -> None:
    mid = _make_model(admin_client)
    try:
        monkeypatch.setattr(
            api_test, "_test_completion", lambda *a, **k: (True, "OK — model responded: 4")
        )
        admin_client.post(f"/api/admin/models/{mid}/test")
        assert _admin_entry(admin_client, mid)["last_test_ok"] is True

        # A non-connection edit (toggling enabled) keeps the outcome…
        admin_client.patch(f"/api/admin/models/{mid}", json={"enabled": True})
        assert _admin_entry(admin_client, mid)["last_test_ok"] is True
        # …and re-sending the SAME connection values keeps it too.
        admin_client.patch(f"/api/admin/models/{mid}", json={"model": "claude-h"})
        assert _admin_entry(admin_client, mid)["last_test_ok"] is True

        # Changing the base_url makes the stored outcome stale → reset to None.
        admin_client.patch(f"/api/admin/models/{mid}", json={"base_url": "https://new.example"})
        entry = _admin_entry(admin_client, mid)
        assert entry["last_test_ok"] is None and entry["last_test_at"] is None
    finally:
        admin_client.delete(f"/api/admin/models/{mid}")


# ---- per-effort-level connectivity test (admin "Test" button) -----------------
def _api_entry(db, efforts):
    from app.core.security import encrypt_secret
    from app.models.admin import ModelCatalog
    from app.models.enums import ModelKind

    entry = ModelCatalog(
        label="M", kind=ModelKind.api, provider="anthropic", model="claude-fable-5",
        api_key_enc=encrypt_secret("k"), allowed_tiers=["free", "pro", "max"],
        supported_efforts=efforts,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def test_model_test_probes_each_supported_effort(monkeypatch) -> None:
    """The Test button probes the endpoint once per declared effort level, passing the
    effort through, and passes overall only when every level succeeds."""
    from app.core.database import SessionLocal

    seen: list[str] = []

    class _Messages:
        def create(self, **kw):
            seen.append((kw.get("output_config") or {}).get("effort", ""))
            return _Resp([_Block("text", "4")], "end_turn", 3)

    class _Client:
        def __init__(self, **_kw) -> None:
            self.messages = _Messages()

    monkeypatch.setattr("anthropic.Anthropic", _Client)

    db = SessionLocal()
    try:
        entry = _api_entry(db, ["low", "high", "xhigh"])
        ok, detail = api_test.test_model(db, entry)
        assert ok is True
        assert seen == ["low", "high", "xhigh"]  # each level probed with its effort
        for lvl in ("low", "high", "xhigh"):
            assert f"[{lvl}] ✓" in detail
    finally:
        db.close()


def test_model_test_fails_when_one_level_fails(monkeypatch) -> None:
    from app.core.database import SessionLocal

    class _Messages:
        def create(self, **kw):
            if (kw.get("output_config") or {}).get("effort") == "xhigh":
                raise ValueError("effort xhigh not supported by this endpoint")
            return _Resp([_Block("text", "4")], "end_turn", 3)

    class _Client:
        def __init__(self, **_kw) -> None:
            self.messages = _Messages()

    monkeypatch.setattr("anthropic.Anthropic", _Client)

    db = SessionLocal()
    try:
        entry = _api_entry(db, ["low", "high", "xhigh"])
        ok, detail = api_test.test_model(db, entry)
        assert ok is False  # one level failed -> overall fail
        assert "[low] ✓" in detail and "[high] ✓" in detail
        assert "[xhigh] ✗" in detail and "not supported" in detail
    finally:
        db.close()


def test_model_test_no_efforts_single_probe(monkeypatch) -> None:
    """A model that declares no effort levels is probed once, with no effort param."""
    from app.core.database import SessionLocal

    calls: list[bool] = []

    class _Messages:
        def create(self, **kw):
            calls.append("output_config" in kw)
            return _Resp([_Block("text", "4")], "end_turn", 3)

    class _Client:
        def __init__(self, **_kw) -> None:
            self.messages = _Messages()

    monkeypatch.setattr("anthropic.Anthropic", _Client)

    db = SessionLocal()
    try:
        entry = _api_entry(db, [])
        ok, detail = api_test.test_model(db, entry)
        assert ok is True and calls == [False]  # exactly one probe, no effort param
        assert "[no effort]" in detail
    finally:
        db.close()
