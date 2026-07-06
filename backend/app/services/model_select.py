"""Resolve which LLM to use for a given pipeline step (Channel A: standard API).

Models are registered in the catalog (Settings → Models) and selected per step by
the user (`project.step_models[step] = {"model_id": <catalog id>}`). When no usable
model is selected, or when OFFLINE_MODE is forced, the deterministic offline mock
is used.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import REASONING_LEVELS, clamp_effort
from app.models.project import Project
from app.services import model_catalog
from app.services.llm import LLMConfig, LLMService


def _reasoning_for_step(project: Project | None, step: str) -> str:
    """The user's per-step reasoning level, validated; 'off' when unset/invalid."""
    cfg = ((project.step_models or {}).get(step) or {}) if project else {}
    level = cfg.get("reasoning", "off") if isinstance(cfg, dict) else "off"
    return level if level in REASONING_LEVELS else "off"


def effective_api_style(entry) -> str:
    """The wire protocol used to call a catalog entry: the explicit `api_style`, else
    inferred from the provider name ("openai" -> openai, everything else -> anthropic).

    This — not the free-text provider — decides the Anthropic vs OpenAI client, so an
    OpenAI-compatible third party (deepseek/glm/minimax on its native endpoint) works by
    setting api_style="openai", while an Anthropic-compatible endpoint uses "anthropic".
    """
    style = (getattr(entry, "api_style", "") or "").strip().lower()
    if style in {"anthropic", "openai"}:
        return style
    return "openai" if (entry.provider or "").strip().lower() == "openai" else "anthropic"


def _llm_from_entry(entry, reasoning: str = "off") -> LLMService:
    key = model_catalog.key_of(entry)
    if not key:
        return LLMService(LLMConfig(provider="mock"))
    # LLMConfig.provider is the client switch ("claude" -> Anthropic, "openai" -> OpenAI).
    provider = "openai" if effective_api_style(entry) == "openai" else "claude"
    return LLMService(
        LLMConfig(
            provider=provider,
            api_key=key,
            model=entry.model or None,
            base_url=entry.base_url or None,
            reasoning=reasoning if reasoning in REASONING_LEVELS else "off",
        )
    )


def _with_prompts(svc: LLMService, project: Project | None) -> LLMService:
    """Attach the per-project prompt resolver (its edited templates, else defaults)."""
    from app.services.prompts import PromptResolver

    svc.prompts = PromptResolver(project)
    return svc


def build_llm_for_step(db: Session, user_id: int, project: Project, step: str) -> LLMService:
    """Standard-API LLM for a step from the admin catalog (mock when none)."""
    if settings.offline_mode is True:  # explicit force-mock kill switch
        return _with_prompts(LLMService(LLMConfig(provider="mock")), project)
    entry = model_catalog.model_for_step(db, user_id, project, step)
    if entry is None:
        return _with_prompts(LLMService(LLMConfig(provider="mock")), project)
    # Clamp the user's per-step effort to what this model advertises (e.g. xhigh → high
    # on a model without xhigh; nothing when the model declares no effort support).
    reasoning = clamp_effort(_reasoning_for_step(project, step), entry.supported_efforts or [])
    return _with_prompts(_llm_from_entry(entry, reasoning), project)


def build_llm_for_user(db: Session, user_id: int, provider_hint: str | None = None) -> LLMService:
    """Backward-compatible resolver without a project/step: the default chat model."""
    if settings.offline_mode is True:
        return _with_prompts(LLMService(LLMConfig(provider="mock")), None)
    entry = model_catalog.model_for_step(db, user_id, None, "chat")
    if entry is None:
        return _with_prompts(LLMService(LLMConfig(provider="mock")), None)
    return _with_prompts(_llm_from_entry(entry), None)
