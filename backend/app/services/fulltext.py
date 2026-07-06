"""Extract the full text of relevant papers into a single sources file the CLI
agent reads. Keeps agent prompts small (no inlined paper bodies) and grounds
idea generation in the real content of the source papers.

`items` is an ordered list of (title, src) where `src` is a Paper model or a dict
accepted by `mineru.extract` (keys: pdf_url / title / abstract / id). Extraction is
cached per project under discovery/fulltext, so repeat runs are cheap.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.integrations.mineru import extract

SOURCES_FILENAME = "sources.md"
# Defaults: no cap — every chosen paper's FULL text is written to sources.md for the
# agent. `top_n`/`per_cap` remain as optional knobs (a caller can still bound it);
# pass an int to re-enable a cap.
_TOP_N: int | None = None
_PER_CAP: int | None = None


def extract_sources(
    items: list[tuple[str, Any]], cache_dir: Path, *,
    top_n: int | None = _TOP_N, per_cap: int | None = _PER_CAP,
) -> list[dict[str, Any]]:
    """Extract the chosen papers (deduped by title); returns per-paper records with
    method/chars/snippet (for provenance) and the full `text`. With `top_n`/`per_cap`
    None (the default) nothing is capped — the complete full text of every paper."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for title, src in items:
        key = (title or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        res = extract(src, cache_dir)
        out.append({
            "title": title,
            "method": res.method,
            "chars": res.chars,
            "snippet": res.text[:600],  # provenance preview only (not model-bound)
            "cache_file": res.cache_file,
            "text": res.text if per_cap is None else res.text[:per_cap],
        })
        if top_n is not None and len(out) >= top_n:
            break
    return out


def render_markdown(sources: list[dict[str, Any]]) -> str:
    """Render extracted sources as a single markdown document for the agent."""
    if not sources:
        return "# Source papers\n\n(no full text could be extracted)\n"
    body = "\n\n---\n\n".join(f"## {s['title']}\n\n{s['text']}" for s in sources)
    return "# Source papers — full text extracted via MinerU/pypdf\n\n" + body


def write_sources_md(sources: list[dict[str, Any]], out_path: Path) -> Path:
    """Write the sources markdown to `out_path` (the agent's workdir)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(sources), encoding="utf-8")
    return out_path


def render_summaries_md(summaries: list[dict[str, Any]]) -> str:
    """Render explored-paper 5-point summaries (NOT full text) as one markdown doc for
    the idea agent. `summaries` is a list of {title, summary, code?, code_url?}; the
    code-repository analysis (when present) rides along under each paper."""
    if not summaries:
        return "# Explored papers — summaries\n\n(no summaries available)\n"

    def _one(s: dict[str, Any]) -> str:
        block = f"## {s.get('title', '(untitled)')}\n\n{s.get('summary', '')}"
        code = s.get("code") or ""
        if code:  # papers without an analyzed repo add nothing extra
            block += f"\n\n### Code repository analysis ({s.get('code_url', '')})\n\n{code}"
        return block

    body = "\n\n---\n\n".join(_one(s) for s in summaries)
    return "# Explored papers — structured 5-point summaries (+ code analysis)\n\n" + body
