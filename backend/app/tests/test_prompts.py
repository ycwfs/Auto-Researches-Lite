"""Customizable prompts: per-project full-template overrides + validation + docs."""
from __future__ import annotations

from fastapi.testclient import TestClient

REGISTRY_KEYS = {
    "summary", "chat", "summary_5pt", "code_analysis",
}


# ---- registry / resolver (unit) -------------------------------------------- #
def test_render_default_and_validation() -> None:
    from app.services import prompts

    assert "research co-pilot" in prompts.render_default("chat", context="CTX")
    assert "CTX" in prompts.render_default("chat", context="CTX")
    # a valid edit keeps the required placeholder
    assert prompts.validate_template("chat", "Be terse.\n\n{context}") == []
    # dropping the required {context} is rejected
    problems = prompts.validate_template("chat", "Be terse, no placeholder")
    assert problems and "context" in problems[0]
    # every registry key documents the meaning of each of its placeholders
    for spec in prompts.REGISTRY.values():
        for ph in spec.required_placeholders:
            assert ph in spec.placeholder_docs and spec.placeholder_docs[ph]


def test_summary_and_code_prompts_editable_and_reach_the_llm() -> None:
    """The full-text Summary + code-analysis prompts are registry-editable, and the
    resolved template (a project's edit, else the default) is what the summarizer sends."""
    from app.services import prompts
    from app.services.llm import LLMConfig, LLMService

    # both are real, no-placeholder registry entries
    for key in ("summary_5pt", "code_analysis"):
        assert key in prompts.REGISTRY and prompts.REGISTRY[key].required_placeholders == ()

    class _Proj:
        prompt_overrides = {"summary_5pt": "MY CUSTOM SUMMARY"}

    assert prompts.effective_template(_Proj(), "summary_5pt") == "MY CUSTOM SUMMARY"
    assert prompts.effective_template(None, "summary_5pt") == prompts.REGISTRY["summary_5pt"].default_template

    svc = LLMService(LLMConfig(provider="claude", api_key="x", model="m"))
    assert not svc.offline
    sent: dict[str, str] = {}
    svc._complete = lambda prompt, system, max_tokens: (sent.update(p=prompt) or "ok")  # type: ignore[method-assign]

    svc.summarize_full_text("PAPER TEXT", prompt="MY CUSTOM SUMMARY")
    assert "MY CUSTOM SUMMARY" in sent["p"] and "PAPER TEXT" in sent["p"]
    svc.summarize_full_text("PAPER TEXT")  # no prompt -> built-in default
    assert prompts.REGISTRY["summary_5pt"].default_template[:30] in sent["p"]
    svc.summarize_codebase("REPO MATERIAL", prompt="MY CUSTOM CODE")
    assert "MY CUSTOM CODE" in sent["p"] and "REPO MATERIAL" in sent["p"]


def test_resolver_uses_project_override_else_default() -> None:
    from app.services.prompts import PromptResolver

    class _P:
        prompt_overrides = {"chat": "HOUSE STYLE {context}"}

    assert PromptResolver(_P()).build("chat", context="CTX") == "HOUSE STYLE CTX"
    # no project / no override -> built-in default
    assert "research co-pilot" in PromptResolver(None).build("chat", context="CTX")

    class _Empty:
        prompt_overrides: dict = {}

    assert "research co-pilot" in PromptResolver(_Empty()).build("chat", context="CTX")


# ---- project endpoints ----------------------------------------------------- #
def test_project_prompt_get_shape(auth_client: TestClient) -> None:
    pid = auth_client.post("/api/projects", json={"name": "PromptShape"}).json()["id"]
    items = auth_client.get(f"/api/projects/{pid}/prompts").json()
    assert {p["key"] for p in items} == REGISTRY_KEYS
    chat = next(p for p in items if p["key"] == "chat")
    assert chat["is_custom"] is False
    assert chat["template"] == chat["default_template"]
    assert "context" in chat["placeholders"]
    assert chat["placeholder_docs"].get("context")  # documented meaning present


def test_project_prompt_edit_validate_reset(auth_client: TestClient) -> None:
    pid = auth_client.post("/api/projects", json={"name": "PromptEdit"}).json()["id"]

    # valid full-template edit -> custom, template applied
    r = auth_client.put(
        f"/api/projects/{pid}/prompts",
        json={"templates": {"chat": "Terse co-pilot.\n\n{context}"}},
    )
    assert r.status_code == 200
    chat = next(p for p in r.json() if p["key"] == "chat")
    assert chat["is_custom"] is True and chat["template"].startswith("Terse co-pilot.")

    # missing the required {context} -> 422, override unchanged
    bad = auth_client.put(f"/api/projects/{pid}/prompts", json={"templates": {"chat": "no ph"}})
    assert bad.status_code == 422
    chat = next(p for p in auth_client.get(f"/api/projects/{pid}/prompts").json() if p["key"] == "chat")
    assert chat["is_custom"] is True  # the bad save did not overwrite the good one

    # editing 'summary' too, keeping its required placeholders
    r = auth_client.put(
        f"/api/projects/{pid}/prompts",
        json={"templates": {"summary": "Summarize {title} ({abstract}) for {keywords}."}},
    )
    assert r.status_code == 200
    summary = next(p for p in r.json() if p["key"] == "summary")
    assert summary["is_custom"] is True

    # resetting chat: send the default back -> is_custom False
    default_chat = next(p for p in r.json() if p["key"] == "chat")["default_template"]
    r = auth_client.put(f"/api/projects/{pid}/prompts", json={"templates": {"chat": default_chat}})
    chat = next(p for p in r.json() if p["key"] == "chat")
    assert chat["is_custom"] is False
    # summary override survived the chat reset
    assert next(p for p in r.json() if p["key"] == "summary")["is_custom"] is True


def test_project_prompt_unknown_key_ignored(auth_client: TestClient) -> None:
    pid = auth_client.post("/api/projects", json={"name": "PromptOwner"}).json()["id"]
    r = auth_client.put(
        f"/api/projects/{pid}/prompts",
        json={"templates": {"bogus": "x", "chat": "Hi {context}"}},
    )
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()}
    assert "bogus" not in keys and keys == REGISTRY_KEYS  # unknown key ignored
    assert next(p for p in r.json() if p["key"] == "chat")["is_custom"] is True
