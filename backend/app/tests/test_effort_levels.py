"""Each reasoning-effort level flows through Channel A once a model declares support.

Covers, for every level in low/medium/high/xhigh/max:
  * Channel A (api): lands on the resolved LLM config, and the Anthropic call carries
    output_config={"effort": level} (OpenAI-style clamps xhigh/max down to "high").
"""
from __future__ import annotations

import types
import uuid

import pytest

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models.admin import ModelCatalog
from app.models.enums import ModelKind

LEVELS = ["low", "medium", "high", "xhigh", "max"]


def _user_project_entry(db, kind: ModelKind, *, provider="anthropic", api_style=""):
    from app.models.project import Project
    from app.models.user import User

    u = User(
        email=f"eff-{uuid.uuid4().hex[:8]}@example.com",
        full_name="Effort Tester",
        hashed_password="!test",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    entry = ModelCatalog(
        label="M", kind=kind, provider=provider, api_style=api_style,
        base_url="https://api.anthropic.com", model="claude-fable-5",
        api_key_enc=encrypt_secret("k"), allowed_tiers=["free", "pro", "max"],
        supported_efforts=list(LEVELS),  # declares support for every level
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    p = Project(owner_id=u.id, name="P", categories=["cs.AI"], keywords=["x"])
    db.add(p)
    db.commit()
    db.refresh(p)
    return u, p, entry


@pytest.mark.parametrize("level", LEVELS)
def test_api_effort_flows_per_level(level):
    """Channel A: the level lands on the resolved standard-API LLM config."""
    from app.core.config import settings
    from app.services import model_select

    db = SessionLocal()
    saved = settings.offline_mode
    try:
        settings.offline_mode = None  # don't force the mock
        u, p, entry = _user_project_entry(db, ModelKind.api)
        p.step_models = {"chat": {"model_id": entry.id, "reasoning": level}}
        db.commit()
        assert model_select.build_llm_for_step(db, u.id, p, "chat").config.reasoning == level
    finally:
        settings.offline_mode = saved
        db.close()


@pytest.mark.parametrize("level", LEVELS)
def test_channel_a_anthropic_sends_output_config_effort(level, monkeypatch):
    """Channel A: the Anthropic call carries output_config={'effort': level}; xhigh/max
    also floor max_tokens so thinking has room."""
    import anthropic

    from app.services import llm as llm_mod

    captured: dict = {}

    class _Msgs:
        def create(self, **kw):
            captured.update(kw)
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="ok")])

    class _Client:
        def __init__(self, **kw):
            self.messages = _Msgs()

    monkeypatch.setattr(anthropic, "Anthropic", _Client)
    cfg = llm_mod.LLMConfig(provider="claude", api_key="k", model="claude-fable-5", reasoning=level)
    out = llm_mod._complete_anthropic(cfg, "hi", "", 1000)
    assert out == "ok"
    assert captured["output_config"] == {"effort": level}
    assert captured["max_tokens"] >= 32000 if level in ("xhigh", "max") else captured["max_tokens"] == 1000


@pytest.mark.parametrize("level,expected", [
    ("low", "low"), ("medium", "medium"), ("high", "high"),
    ("xhigh", "high"), ("max", "high"),  # OpenAI reasoning_effort tops out at high
])
def test_channel_a_openai_reasoning_effort_clamped(level, expected, monkeypatch):
    """Channel A (OpenAI-style): each level maps to reasoning_effort, xhigh/max → high."""
    import openai

    from app.services import llm as llm_mod

    captured: dict = {}

    class _Comp:
        def create(self, **kw):
            captured.update(kw)
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
            )

    class _Chat:
        def __init__(self):
            self.completions = _Comp()

    class _Client:
        def __init__(self, **kw):
            self.chat = _Chat()

    monkeypatch.setattr(openai, "OpenAI", _Client)
    cfg = llm_mod.LLMConfig(provider="openai", api_key="k", model="gpt-5", reasoning=level)
    out = llm_mod._complete_openai(cfg, "hi", "", 1000)
    assert out == "ok"
    assert captured["reasoning_effort"] == expected
