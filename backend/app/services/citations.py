"""BibTeX formatting for a set of papers — Paper models or trace/Zotero dicts.

Used by the idea-provenance citations export (GET /ideas/{id}/citations.bib).
"""
from __future__ import annotations

import re
from typing import Any


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _clean(value: Any) -> str:
    return re.sub(r"[{}]", "", str(value or "")).strip()


def _year(meta: Any) -> str:
    return _clean(_field(meta, "year") or _field(meta, "published"))[:4]


def _cite_key(meta: Any, i: int, key_prefix: str | None) -> str:
    if key_prefix is not None:
        return f"{key_prefix}{i}"
    arxiv = re.sub(r"[^0-9A-Za-z]", "", _clean(_field(meta, "arxiv_id")))
    if arxiv:
        return f"arxiv{arxiv}"
    authors = _field(meta, "authors") or []
    surname = re.sub(r"[^A-Za-z]", "", (str(authors[0]).split()[-1] if authors else "ref")) or "ref"
    return f"{surname.lower()}{_year(meta) or 'n'}_{i}"


def to_bibtex(papers: list, key_prefix: str | None = None) -> str:
    """Render papers as a BibTeX bibliography.

    `key_prefix` (e.g. "ref") forces sequential keys ref0, ref1, … so generated
    LaTeX `\\cite{ref0,…}` resolves; otherwise readable author-year keys are used.
    """
    entries: list[str] = []
    for i, p in enumerate(papers):
        authors = _field(p, "authors") or []
        author = " and ".join(str(a) for a in list(authors)[:8]) or "Unknown"
        title = _clean(_field(p, "title"))
        arxiv = _clean(_field(p, "arxiv_id"))
        doi = _clean(_field(p, "doi"))
        url = _clean(_field(p, "url") or _field(p, "pdf_url"))
        venue = _clean(_field(p, "venue") or _field(p, "booktitle"))
        fields = [
            f"  title={{{title}}}",
            f"  author={{{author}}}",
            f"  year={{{_year(p) or '2025'}}}",
        ]
        # A conference paper with a venue but no arXiv id (e.g. an AI Paper Finder result)
        # becomes a proper @inproceedings with its venue as booktitle; arXiv stays @article.
        entry_type = "inproceedings" if (venue and not arxiv) else "article"
        if arxiv:
            fields.append(f"  journal={{arXiv preprint arXiv:{arxiv}}}")
            fields.append(f"  eprint={{{arxiv}}}")
        elif venue:
            fields.append(f"  booktitle={{{venue}}}")
        if doi:
            fields.append(f"  doi={{{doi}}}")
        if url:
            fields.append(f"  url={{{url}}}")
        entries.append("@" + entry_type + "{" + _cite_key(p, i, key_prefix) + ",\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries) or "% no references\n"
