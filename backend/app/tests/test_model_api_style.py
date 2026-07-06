"""Model routing by explicit api_style (Anthropic vs OpenAI client)."""
from __future__ import annotations

from types import SimpleNamespace

from app.core.security import encrypt_secret
from app.models.admin import ModelCatalog
from app.services.model_select import _llm_from_entry, effective_api_style


def _stub(provider: str, api_style: str = "") -> SimpleNamespace:
    return SimpleNamespace(provider=provider, api_style=api_style)


def test_effective_api_style_explicit_overrides_provider() -> None:
    # explicit api_style wins over the provider name
    assert effective_api_style(_stub("deepseek", "openai")) == "openai"
    assert effective_api_style(_stub("openai", "anthropic")) == "anthropic"
    # unset -> inferred from provider ("openai" -> openai, everything else -> anthropic)
    assert effective_api_style(_stub("openai", "")) == "openai"
    assert effective_api_style(_stub("deepseek", "")) == "anthropic"
    assert effective_api_style(_stub("claude", "")) == "anthropic"
    # a row predating the column (api_style attribute missing) infers, too
    assert effective_api_style(SimpleNamespace(provider="openai")) == "openai"


def test_llm_from_entry_maps_style_to_client() -> None:
    """An OpenAI-compatible third party routes to the OpenAI client via api_style,
    independent of its provider label."""
    key = encrypt_secret("sk-test")
    deepseek_openai = ModelCatalog(
        label="DS", provider="deepseek", api_style="openai", model="deepseek-chat", api_key_enc=key
    )
    deepseek_default = ModelCatalog(
        label="DS2", provider="deepseek", api_style="", model="deepseek-chat", api_key_enc=key
    )
    assert _llm_from_entry(deepseek_openai).config.provider == "openai"
    assert _llm_from_entry(deepseek_default).config.provider == "claude"  # inferred anthropic
