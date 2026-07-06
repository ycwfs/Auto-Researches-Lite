"""Shared enums for models and schemas."""
from __future__ import annotations

import enum


class JobType(str, enum.Enum):
    discovery = "discovery"
    zotero_upload = "zotero_upload"
    add_paper = "add_paper"  # user-supplied single paper (arXiv link or PDF upload)
    paper_finder = "paper_finder"  # AI Paper Finder run, decoupled so it runs concurrently
    resummarize = "resummarize"  # force re-run of one paper's Summary / code analysis (prompt debug)


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class SourceKey(str, enum.Enum):
    arxiv = "arxiv"
    semantic_scholar = "semantic_scholar"
    ai_paper_finder = "ai_paper_finder"


class ModelKind(str, enum.Enum):
    """Which invocation channel an admin-registered model drives. Only the standard
    LLM API (Channel A) remains; the Claude Code CLI agent channel has been removed."""

    api = "api"  # Channel A: standard LLM API (Project Chat, summaries, Zotero routing)


class ProjectStage(str, enum.Enum):
    discovery = "discovery"


class ChatRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


# Steps that can have a per-project model assigned. "summary" drives paper
# summarization / relevance during discovery (the frontend labels it "Summary").
STEP_NAMES = ("summary", "chat", "zotero")

# Per-step reasoning EFFORT; "off" = use the model default. Applied to the standard
# API (Channel A, output_config.effort).
# These are the Anthropic effort levels; "off" omits the parameter.
REASONING_LEVELS = ("off", "low", "medium", "high", "xhigh", "max")
_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")  # ascending capability/cost


def clamp_effort(level: str, supported) -> str:
    """The effective effort level for a model: the requested `level` if the model lists
    it in `supported`, else the highest supported level at or below it (Claude Code's
    rule — e.g. xhigh runs as high on a model without xhigh). "" when off/unsupported,
    or when the model declares no supported effort levels."""
    if level not in _EFFORT_ORDER:  # "off" / "" / invalid → no effort
        return ""
    sup = [e for e in _EFFORT_ORDER if e in (supported or ())]
    if level in sup:
        return level
    below = [e for e in sup if _EFFORT_ORDER.index(e) <= _EFFORT_ORDER.index(level)]
    return below[-1] if below else ""
