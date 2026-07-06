"""LLM provider abstraction with a deterministic offline mock.

The service exposes task-level methods (summarize_paper, summarize_full_text,
summarize_codebase, pick_collection_name, chat). Real providers are used when a key
is configured; otherwise a deterministic MockLLM keeps every step demonstrable
offline. Real-provider calls are defensive: on any error they fall back to the mock
so a job never hard-fails because of an LLM hiccup.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.core.config import settings

logger = logging.getLogger("far.llm")


@dataclass
class LLMConfig:
    provider: str  # 'claude' | 'openai' | 'mock'
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None  # optional custom endpoint (admin model catalog)
    reasoning: str = "off"  # REASONING_LEVELS: off|low|medium|high|xhigh|max


# Anthropic effort levels, sent via `output_config.effort` (modern Claude models control
# thinking depth with effort, not a manual token budget). "off" omits it (model default).
_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
# OpenAI's reasoning_effort tops out at "high"; xhigh/max map down to it.
_OPENAI_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}

# The full-text "Summary" + code-analysis prompts are the editable registry defaults
# (a project may override them per prompts.effective_template). The summarizers below
# accept the resolved template; these are the fallback when no prompt is passed.
from app.services.prompts import REGISTRY as _PROMPT_REGISTRY  # noqa: E402

_FIVE_POINT_PROMPT = _PROMPT_REGISTRY["summary_5pt"].default_template
_CODE_ANALYSIS_PROMPT = _PROMPT_REGISTRY["code_analysis"].default_template


# --------------------------------------------------------------------------- #
# Low-level completion backends
# --------------------------------------------------------------------------- #
def _complete_anthropic(cfg: LLMConfig, prompt: str, system: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(
        api_key=cfg.api_key, **({"base_url": cfg.base_url} if cfg.base_url else {})
    )
    kwargs: dict[str, Any] = dict(
        model=cfg.model or "claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system or "You are a precise research assistant. Output only what is asked.",
        messages=[{"role": "user", "content": prompt}],
    )
    if cfg.reasoning in _EFFORT_LEVELS:
        # Modern Claude models set thinking depth via effort (a manual thinking budget
        # 400s on Sonnet 5 / Opus 4.8 / Fable 5). At xhigh/max give room to think, since
        # max_tokens caps thinking + text together.
        kwargs["output_config"] = {"effort": cfg.reasoning}
        if cfg.reasoning in ("xhigh", "max"):
            kwargs["max_tokens"] = max(max_tokens, 32000)
    try:
        resp = client.messages.create(**kwargs)
    except (TypeError, anthropic.BadRequestError):
        # SDK/endpoint/model rejected `output_config` — retry once without it.
        if "output_config" not in kwargs:
            raise
        kwargs.pop("output_config")
        kwargs["max_tokens"] = max_tokens
        resp = client.messages.create(**kwargs)
    return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")


def _complete_openai(cfg: LLMConfig, prompt: str, system: str, max_tokens: int) -> str:
    from openai import OpenAI

    client = OpenAI(
        api_key=cfg.api_key, **({"base_url": cfg.base_url} if cfg.base_url else {})
    )
    kwargs: dict[str, Any] = dict(
        model=cfg.model or "gpt-4o-mini",
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system or "You are a precise research assistant."},
            {"role": "user", "content": prompt},
        ],
    )
    effort = _OPENAI_EFFORT.get(cfg.reasoning)
    if effort:
        kwargs["reasoning_effort"] = effort
    try:
        resp = client.chat.completions.create(**kwargs)
    except (TypeError, _openai_bad_request()):
        # Non-reasoning model rejected `reasoning_effort` — retry once without it.
        if "reasoning_effort" not in kwargs:
            raise
        kwargs.pop("reasoning_effort")
        resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def _openai_bad_request() -> type[Exception]:
    """The openai SDK's 400 error type, resolved lazily to avoid a hard import."""
    try:
        from openai import BadRequestError

        return BadRequestError
    except Exception:  # noqa: BLE001 — fall back to a catch-all if the SDK shape changes
        return Exception


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class LLMService:
    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig(provider="mock")
        self.offline = self.config.provider == "mock" or not self.config.api_key
        # Optional admin-template + project-guidance resolver (attached by
        # build_llm_for_step). None ⇒ use the registry's built-in default templates.
        self.prompts = None

    def _p(self, key: str, **vals: object) -> str:
        """Resolve a customizable prompt (admin template + project guidance), or the
        built-in default when no resolver is attached."""
        from app.services import prompts as prompt_registry

        if self.prompts is not None:
            return self.prompts.build(key, **vals)
        return prompt_registry.render_default(key, **vals)

    # ---- public task methods -------------------------------------------- #
    def summarize_paper(
        self, paper: dict[str, Any], keywords: list[str], context: str = ""
    ) -> dict[str, Any]:
        if self.offline:
            return _mock_summary(paper, keywords)
        steer = (
            "\n\nProject context — steer the summary and the relevance score toward "
            f"this project's focus and direction:\n{context.strip()}"
            if context.strip()
            else ""
        )
        prompt = self._p(
            "summary",
            keywords=", ".join(keywords) or "general ML",
            title=paper.get("title", ""),
            abstract=paper.get("abstract", ""),
            steer=steer,
        )
        raw = self._complete(prompt, max_tokens=1200)  # avoid a truncated-JSON → mock fallback
        data = _parse_json(raw)
        if not data:
            return _mock_summary(paper, keywords)
        return {
            "summary_en": str(data.get("summary_en", "")).strip(),
            "summary_zh": str(data.get("summary_zh", "")).strip(),
            "relevance": _clamp_float(data.get("relevance", 0.5)),
        }

    def summarize_excerpt(self, title: str, text: str) -> str:
        """1-2 sentence summary of a paper's extracted full text (provenance)."""
        if self.offline:
            return _mock_excerpt(title, text)
        system = (
            "Summarize the paper excerpt in 1-2 sentences, naming the method and the "
            "key finding. Plain text, no preamble."
        )
        out = self._complete(f"Title: {title}\n\nExcerpt:\n{text[:4000]}", system=system, max_tokens=160)
        return out.strip() or _mock_excerpt(title, text)

    def summarize_full_text(self, markdown: str, prompt: str | None = None) -> str:
        """The paper-database full-text "Summary". `prompt` is the project's editable
        template (prompts key "summary_5pt"); falls back to the built-in default."""
        if self.offline:
            return _mock_full_summary(markdown)
        system = (
            "You are a careful research assistant. Read the paper text and produce a "
            "structured summary as numbered markdown sections, one per requested "
            "point. Be specific and faithful to the paper; do not invent details."
        )
        out = self._complete(
            f"{prompt or _FIVE_POINT_PROMPT}\n\nPaper full text:\n{markdown[:60000]}",
            system=system,
            max_tokens=4000,  # 5 sections over a full paper — enough to not cut mid-section
        )
        return out.strip() or _mock_full_summary(markdown)

    def summarize_codebase(self, repo_context: str, prompt: str | None = None) -> str:
        """Structured analysis of a paper's code repository. `prompt` is the project's
        editable template (prompts key "code_analysis"); falls back to the default."""
        if self.offline:
            return _mock_code_summary(repo_context)
        system = (
            "You are a careful software engineer. Read the repository material and produce "
            "a structured analysis as numbered markdown sections, one per requested "
            "point. Be specific and faithful to the code; do not invent details."
        )
        out = self._complete(
            f"{prompt or _CODE_ANALYSIS_PROMPT}\n\nRepository material:\n{repo_context[:60000]}",
            system=system,
            max_tokens=2500,
        )
        return out.strip() or _mock_code_summary(repo_context)

    def pick_collection_name(
        self, project_name: str, paper_titles: list[str], existing: list[str]
    ) -> str:
        """Pick a Zotero collection name (existing or a concise new one).

        One-shot classification on Channel A — deliberately NOT the CLI agent,
        so no agent persona/preamble can leak into the name. Returns "" offline
        or on failure so the caller falls back to its default.
        """
        if self.offline:
            return ""
        papers = "\n".join(f"- {t}" for t in paper_titles[:30]) or "(none)"
        cols = "\n".join(f"- {c}" for c in existing) or "(none yet)"
        system = (
            "You file research papers into a Zotero collection. Reply with ONLY "
            "the collection name: 2-5 words, Title Case, no quotes, no "
            "punctuation, no explanation, no preamble."
        )
        prompt = (
            f"Project: {project_name}\n\n"
            f"## Papers\n{papers}\n\n"
            f"## Existing collections\n{cols}\n\n"
            "Pick an existing collection if one clearly fits; otherwise propose a "
            "concise new name."
        )
        return self._complete(prompt, system=system, max_tokens=48).strip()

    # ---- internals ------------------------------------------------------ #
    def chat(self, context: str, history: list[tuple[str, str]], message: str) -> str:
        """Project dialogue reply, grounded in the project context."""
        if self.offline:
            return _mock_chat(context, message)
        system = self._p("chat", context=context)
        convo = "\n".join(f"{role}: {content}" for role, content in history[-8:])
        prompt = f"{convo}\nuser: {message}\nassistant:"
        return self._complete(prompt, system=system, max_tokens=8192) or _mock_chat(context, message)

    def _complete(self, prompt: str, system: str = "", max_tokens: int = 800) -> str:
        try:
            if self.config.provider == "claude":
                return _complete_anthropic(self.config, prompt, system, max_tokens)
            if self.config.provider == "openai":
                return _complete_openai(self.config, prompt, system, max_tokens)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM call failed (%s); falling back to mock", exc)
        return ""


def build_llm_config(provider: str | None, api_key: str | None) -> LLMConfig:
    """Pick a usable LLM config given a requested provider and available key."""
    if settings.is_offline or not api_key:
        return LLMConfig(provider="mock")
    prov = (provider or settings.default_llm_provider or "claude").lower()
    if prov not in {"claude", "openai"}:
        prov = "claude"
    return LLMConfig(provider=prov, api_key=api_key)


# --------------------------------------------------------------------------- #
# Deterministic offline mock implementations
# --------------------------------------------------------------------------- #
def _first_sentences(text: str, n: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n]).strip()


def _keyword_overlap(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 0.5
    text_l = text.lower()
    hits = sum(1 for k in keywords if k.lower() in text_l)
    return round(min(1.0, 0.3 + 0.7 * hits / max(1, len(keywords))), 3)


def _mock_summary(paper: dict[str, Any], keywords: list[str]) -> dict[str, Any]:
    abstract = paper.get("abstract", "")
    title = paper.get("title", "")
    gist = _first_sentences(abstract, 2) or title
    return {
        "summary_en": (
            f"{gist} This work targets problems relevant to {', '.join(keywords) or 'machine learning'}, "
            "and reports an approach with supporting empirical evidence. "
            "[offline summary — configure an LLM key for full analysis]"
        ),
        "summary_zh": f"本文研究：{title}。摘要要点：{gist}（离线模式摘要，配置 LLM 密钥可获得完整分析）",
        "relevance": _keyword_overlap(f"{title} {abstract}", keywords),
    }


def _mock_chat(context: str, message: str) -> str:
    snippet = context.strip().replace("\n", " ")
    return (
        f"[offline assistant — configure an LLM key for full dialogue]\n\n"
        f"On '{message.strip()}': this project is in the **discovery** stage. "
        f"Based on its context — {snippet[:280]} — the next useful step is to "
        "collect more papers and run discovery."
    )


def _mock_excerpt(title: str, text: str) -> str:
    """Deterministic offline summary of an extracted paper (provenance proof)."""
    snippet = " ".join((text or "").split())[:220]
    return f"[offline summary] {title}: {snippet}"


def _mock_full_summary(markdown: str) -> str:
    """Deterministic offline 5-point summary (offline/test mode)."""
    snippet = " ".join((markdown or "").split())[:300]
    return (
        f"1. Task definition / background / motivation: [offline summary] {snippet}\n"
        "2. Research methods (key processes / core formulas): (offline mock — no LLM configured).\n"
        "3. Datasets (annotation format, input/output mapping): (offline mock).\n"
        "4. Evaluation metrics (what is measured, how computed): (offline mock).\n"
        "5. Experimental results and conclusions: (offline mock)."
    )


def _mock_code_summary(repo_context: str) -> str:
    """Deterministic offline code-repository analysis (offline/test mode)."""
    snippet = " ".join((repo_context or "").split())[:300]
    return (
        f"1. Overall architecture: [offline analysis] {snippet}\n"
        "2. Key modules: (offline mock — no LLM configured).\n"
        "3. Training / inference entry points: (offline mock).\n"
        "4. Data pipeline: (offline mock).\n"
        "5. Dependencies: (offline mock)."
    )




def _parse_json(text: str) -> Any:
    if not text:
        return None
    text = text.strip()
    # strip ```json fences if present
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # try to locate the first JSON object/array
        match = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _salvage_json_array(text: str) -> list[Any] | None:
    """Recover the complete-object prefix of a JSON array truncated mid-element (e.g. the
    model hit its output-token cap). Closes the array at the last complete `}`; returns
    None if nothing parses."""
    if not text:
        return None
    start = text.find("[")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1] + "]")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) and parsed else None


def _clamp_float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5
