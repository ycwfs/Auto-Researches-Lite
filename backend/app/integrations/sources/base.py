"""Source interface and shared types."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Common paper shape (matches ArxivFetcher output):
# id, title, authors[list], abstract, categories[list], pdf_url, published, source, venue
PaperDict = dict[str, Any]


def norm_title(title: str) -> str:
    """A normalized title key for cross-source dedup: lowercase, alphanumerics + single
    spaces only (digits kept, so "GPT-3" and "GPT-4" stay distinct). Lets the same paper
    from different sources (an arXiv id vs the AI-Paper-Finder content hash) collapse to
    one entry. Not truncated — two long titles sharing a 200-char prefix must not merge."""
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z ]", "", (title or "").lower())).strip()


@dataclass
class SourceQuery:
    categories: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    max_results: int = 20
    days_back: int = 5
    config: dict[str, Any] = field(default_factory=dict)  # admin source config


class Source(ABC):
    key: str = ""
    name: str = ""

    @abstractmethod
    def fetch(self, query: SourceQuery) -> list[PaperDict]:
        """Return a list of PaperDicts for the query."""
        raise NotImplementedError
