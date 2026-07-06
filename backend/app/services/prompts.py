"""Customizable prompt registry + resolver.

Single source of truth for the predefined prompts a project may edit. Each project
edits the FULL template for a key (``Project.prompt_overrides`` — a ``{key: template}``
map); an absent/empty override falls back to the built-in default below. Edits are
validated so required ``{placeholders}`` survive, and every placeholder's meaning is
documented for the editor.

Resolution: ``effective = render(project override or built-in default, values)``. The
resolver feeds Channel A (``llm.py``). A malformed override still degrades safely — the
JSON parser / mock fallback catches it.
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_TEMPLATE = 8000

# --------------------------------------------------------------------------- #
# Built-in default templates (the current production prompts).
# --------------------------------------------------------------------------- #
_SUMMARY = (
    "Summarize this paper for a research digest. Return STRICT JSON with keys "
    '"summary_en" (3-4 sentences), "summary_zh" (3-4 sentences in Chinese), '
    '"relevance" (0-1 float for relevance to keywords: {keywords}).\n\n'
    "Title: {title}\nAbstract: {abstract}{steer}"
)
_CHAT = (
    "You are a research co-pilot for this project. Use the project context "
    "to answer concisely and help advance the literature review.\n\n"
    "{context}"
)
# The structured full-text "Summary" shown on a paper card. The paper's full text is
# appended by the summarizer, so this template takes no placeholders — it is the
# instruction prepended to the text.
_SUMMARY_5PT = (
    "Please first read the full text thoroughly, and then summarize according to the "
    "following points: 1. Task definition (input and output), research background, "
    "research motivation. 2. Research methods: methods to solve the problem, including "
    "key processes and core formulas. 3. Data sets used (format of annotation and how "
    "it matches the model input and output). 4. Evaluation indicators of the model "
    "(what performance is measured and how to calculate). 5. Experimental results and "
    "conclusions."
)
# The code-repository analog, shown as a paper card's "Code repository analysis". The
# repository material is appended by the summarizer, so this template takes no placeholders.
_CODE_ANALYSIS = (
    "You are given a code repository linked from a research paper (README, file tree, "
    "and key files). Produce a structured summary as five numbered markdown sections, "
    "faithful to the repository — do not invent details: 1. Overall architecture (what "
    "the codebase implements and how it is organized). 2. Key modules (the main "
    "packages/files and their responsibilities). 3. Training / inference entry points "
    "(the scripts/commands to train and to run inference). 4. Data pipeline (how data "
    "is loaded, preprocessed, and fed to the model). 5. Dependencies (frameworks, key "
    "libraries, and environment/setup)."
)
@dataclass(frozen=True)
class PromptSpec:
    key: str
    label: str
    stage: str
    channel: str  # "A" (LLM API user/system prompt)
    default_template: str
    required_placeholders: tuple[str, ...]  # must remain in an edited template
    placeholder_docs: dict[str, str]  # every placeholder (required + optional) → meaning
    contract_note: str


REGISTRY: dict[str, PromptSpec] = {
    "summary": PromptSpec(
        "summary", "Paper summary", "Discovery", "A", _SUMMARY,
        ("keywords", "title", "abstract"),
        {
            "keywords": "The project's focus keywords (comma-separated).",
            "title": "The paper's title.",
            "abstract": "The paper's abstract.",
            "steer": "Auto-injected project context that steers the summary + relevance (optional; keep it to stay on-topic).",
        },
        'Must return STRICT JSON with keys "summary_en", "summary_zh" (Chinese), "relevance" (0-1).',
    ),
    "chat": PromptSpec(
        "chat", "Assistant persona", "Chat", "A", _CHAT,
        ("context",),
        {"context": "The project context the assistant answers from."},
        "Free-form reply; keep {context} so the assistant sees the project.",
    ),
    "summary_5pt": PromptSpec(
        "summary_5pt", "Paper summary (full text)", "Discovery", "A", _SUMMARY_5PT,
        (),
        {},
        "The paper's full text is appended automatically — write only the instruction. "
        "Output is shown as the card's 'Summary'. Applies to papers THIS project "
        "summarizes first (a paper already summarized by another project keeps that "
        "summary).",
    ),
    "code_analysis": PromptSpec(
        "code_analysis", "Code repository analysis", "Discovery", "A", _CODE_ANALYSIS,
        (),
        {},
        "The repository material (README, file tree, key files) is appended "
        "automatically — write only the instruction. Output is the card's 'Code "
        "repository analysis'. Applies to papers THIS project analyzes first.",
    ),
}


# --------------------------------------------------------------------------- #
# Rendering + validation (pure)
# --------------------------------------------------------------------------- #
def _render(template: str, placeholders, vals: dict) -> str:
    """Replace each ``{placeholder}`` with its value. Deliberately NOT str.format
    so stray braces in a custom template (e.g. LaTeX) never raise."""
    out = template
    for ph in placeholders:
        out = out.replace("{" + ph + "}", str(vals.get(ph, "")))
    return out


def render_default(key: str, **vals) -> str:
    """Render the built-in template (ignores any project override)."""
    spec = REGISTRY[key]
    return _render(spec.default_template, spec.placeholder_docs.keys(), vals)


def validate_template(key: str, text: str) -> list[str]:
    """Return a list of human-readable problems; empty ⇒ the template is acceptable."""
    spec = REGISTRY.get(key)
    if spec is None:
        return [f"unknown prompt key '{key}'"]
    problems: list[str] = []
    if not text.strip():
        problems.append("template is empty")
    if len(text) > MAX_TEMPLATE:
        problems.append(f"template too long ({len(text)} > {MAX_TEMPLATE} chars)")
    missing = [ph for ph in spec.required_placeholders if "{" + ph + "}" not in text]
    if missing:
        problems.append("missing required placeholders: " + ", ".join("{" + m + "}" for m in missing))
    return problems


def effective_template(project, key: str) -> str:
    """The project's edited template for `key`, else the built-in default."""
    spec = REGISTRY[key]
    if project is not None:
        override = (getattr(project, "prompt_overrides", None) or {}).get(key)
        if override and str(override).strip():
            return str(override)
    return spec.default_template


# --------------------------------------------------------------------------- #
# Resolver (project aware)
# --------------------------------------------------------------------------- #
class PromptResolver:
    """Resolves the effective prompt for a project (its override, else the default)."""

    def __init__(self, project=None):
        self.project = project

    def build(self, key: str, **vals) -> str:
        spec = REGISTRY[key]
        return _render(effective_template(self.project, key), spec.placeholder_docs.keys(), vals)
