"""Zotero schemas."""
from __future__ import annotations

from pydantic import BaseModel


class ZoteroStatus(BaseModel):
    configured: bool


class ZoteroCollection(BaseModel):
    key: str
    name: str
    num_items: int


class ZoteroItem(BaseModel):
    key: str
    item_type: str
    title: str
    abstract: str
    url: str
    date: str
    creators: list[str]


class ZoteroUploadRequest(BaseModel):
    project_id: int
    include_papers: bool = True
    include_ideas: bool = True
    # When provided, upload only these papers/ideas (overrides include_*). An empty
    # list uploads none; None (default) keeps the include_* behavior.
    paper_ids: list[int] | None = None
    idea_ids: list[int] | None = None


class ZoteroUploadResult(BaseModel):
    papers_uploaded: int
    ideas_uploaded: int
    # Child items attached to the uploaded papers: Summary + code-analysis notes, and
    # PDF link attachments. Default 0 for back-compat with any other producer.
    notes_uploaded: int = 0
    attachments_uploaded: int = 0
    errors: list[str]
