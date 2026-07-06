"""Zotero integration via pyzotero.

Reads a user's stored Zotero credentials (api_key, library_id, library_type)
and exposes collection/item listing plus uploading discovered papers.
All network/auth failures raise a clear HTTPException so the UI can prompt the
user to (re)connect.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.content import Paper
from app.models.user import User
from app.services.credentials import get_credential

logger = logging.getLogger("far.zotero")


class ZoteroNotConfigured(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Zotero is not connected. Add your API key, library ID and type in Settings.",
        )


def _normalize_library_type(value: str | None) -> str:
    """Zotero paths are case-sensitive (`users`/`groups`); normalize input.

    pyzotero builds the path as `{library_type}s/{id}`, so "USER" would become
    "USERs" and 404. Accept common variants and fall back to "user".
    """
    v = (value or "user").strip().lower()
    if v in ("user", "users"):
        return "user"
    if v in ("group", "groups"):
        return "group"
    return "user"


def _client(db: Session, user: User):
    creds = get_credential(db, user, "zotero")
    if not creds or not creds.get("api_key") or not creds.get("library_id"):
        raise ZoteroNotConfigured()
    from pyzotero import zotero

    return zotero.Zotero(
        str(creds["library_id"]).strip(),
        _normalize_library_type(creds.get("library_type")),
        str(creds["api_key"]).strip(),
    )


def _wrap(call, action: str):
    try:
        return call()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the client
        logger.warning("Zotero %s failed: %s", action, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Zotero {action} failed: {exc}",
        ) from exc


def is_configured(db: Session, user: User) -> bool:
    creds = get_credential(db, user, "zotero")
    return bool(creds and creds.get("api_key") and creds.get("library_id"))


def validate(db: Session, user: User) -> dict[str, Any]:
    client = _client(db, user)
    key_info = _wrap(client.key_info, "key validation")
    return {"valid": True, "access": key_info}


def list_collections(db: Session, user: User) -> list[dict[str, Any]]:
    client = _client(db, user)
    raw = _wrap(lambda: client.collections(limit=100), "collections fetch")
    return [
        {
            "key": c["key"],
            "name": c["data"].get("name", ""),
            "num_items": c["meta"].get("numItems", 0),
        }
        for c in raw
    ]


def list_items(
    db: Session, user: User, collection_key: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    client = _client(db, user)
    if collection_key:
        raw = _wrap(
            lambda: client.collection_items_top(collection_key, limit=limit), "items fetch"
        )
    else:
        raw = _wrap(lambda: client.top(limit=limit), "items fetch")
    items = []
    for it in raw:
        d = it.get("data", {})
        items.append(
            {
                "key": it["key"],
                "item_type": d.get("itemType", ""),
                "title": d.get("title", "") or d.get("note", "")[:80],
                "abstract": d.get("abstractNote", ""),
                "url": d.get("url", ""),
                "date": d.get("date", ""),
                "creators": [
                    f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
                    for c in d.get("creators", [])
                ],
            }
        )
    return items


def list_all_items(db: Session, user: User, max_items: int = 400) -> list[dict[str, Any]]:
    """Fetch ALL top-level library items (paginated), for idea grounding.

    Returns lightweight dicts (title/abstract/authors/year/url/doi), skipping
    attachments and standalone notes. Capped at `max_items`.
    """
    client = _client(db, user)
    raw = _wrap(lambda: client.everything(client.top(limit=100)), "library fetch")
    items: list[dict[str, Any]] = []
    for it in raw:
        d = it.get("data", {})
        if d.get("itemType") in ("attachment", "note"):
            continue
        title = d.get("title", "") or ""
        if not title:
            continue
        creators = []
        for c in d.get("creators", []) or []:
            name = (f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
                    or c.get("name", ""))
            if name:
                creators.append(name)
        items.append(
            {
                "key": it.get("key", ""),
                "title": title,
                "abstract": d.get("abstractNote", "") or "",
                "authors": creators,
                "year": (d.get("date", "") or "")[:4],
                "url": d.get("url", "") or "",
                "doi": d.get("DOI", "") or "",
                "item_type": d.get("itemType", ""),
            }
        )
        if len(items) >= max_items:
            break
    return items


def _ensure_collection(client, name: str) -> str:
    """Return the key of a collection named `name`, creating it if needed."""
    for c in client.collections(limit=100):
        if c["data"].get("name") == name:
            return c["key"]
    resp = client.create_collections([{"name": name}])
    return resp["successful"]["0"]["key"]


_MAX_COLLECTION_NAME_LEN = 80  # caps preamble/markdown leakage, not legitimate long names

# Preambles that are never part of a real collection name, wherever they appear.
_PREAMBLE_ANY_RE = re.compile(
    r"\b(?:goal acknowledg\w*|i['’]ll|i will|i['’]d|"
    r"i (?:recommend|suggest|propose|think)|"
    r"the best (?:collection|fit|match|option))",
    re.IGNORECASE,
)
# Soft openers that are a preamble ONLY when they lead the line and are followed
# by punctuation — so "Sure Independence Screening" (a real name) still passes,
# while "Sure, ..." / "Of course. ..." are rejected.
_PREAMBLE_LEAD_RE = re.compile(
    r"^(?:sure|certainly|okay|ok|understood|of course|got it|absolutely|great|"
    r"thanks|thank you|happy to help|here['’]?s|here is|based on)\b\s*[,.!:;-]",
    re.IGNORECASE,
)
# Label prefixes to peel off so the real name after them survives.
_LABEL_RE = re.compile(
    r"^(?:collection(?:\s*name)?|name|answer|final answer|recommended collection|"
    r"suggestion|recommendation)\s*[:\-]\s*",
    re.IGNORECASE,
)


def _sanitize_collection_line(line: str) -> str:
    """Clean one candidate line; "" if it isn't a usable collection name."""
    line = re.sub(r"^\*{1,3}|\*{1,3}$", "", line.strip()).strip()  # surrounding **bold**/*italics*
    line = re.sub(r"^#+\s*", "", line)                             # markdown heading
    line = _LABEL_RE.sub("", line).strip(" \"'`*").strip()         # 'Collection: X' -> 'X'
    if not line or line.endswith(":") or len(line) > _MAX_COLLECTION_NAME_LEN:
        return ""
    if _PREAMBLE_LEAD_RE.match(line) or _PREAMBLE_ANY_RE.search(line):
        return ""
    return line


def _clean_collection_name(raw: str) -> str:
    """Sanitize an LLM-proposed collection name; "" means 'unusable, fall back'.

    Scans each line and returns the first that survives sanitizing, so a stray
    preamble line (the cause of the bogus "Goal acknowledged: ..." collection)
    doesn't poison the result or discard a valid name on a later line.
    """
    for line in (raw or "").splitlines():
        cand = _sanitize_collection_line(line)
        if cand:
            return cand
    return ""


def pick_collection(db: Session, user: User, project, papers: list[Paper],
                    default: str = "Daily Papers") -> str:
    """Choose the target Zotero collection name for newly discovered papers.

    Uses Channel A (the plain LLM API) to route papers to the best existing or
    new collection — a one-shot classification, deliberately NOT the CLI agent,
    so no agent persona/preamble can leak into the name. Falls back to `default`
    when the model returns nothing usable or collections can't be read, and never
    fails the sync over naming.
    """
    if not papers:
        return default
    try:
        from app.services.model_select import build_llm_for_step

        existing = [c["name"] for c in list_collections(db, user)]
        llm = build_llm_for_step(db, user.id, project, "zotero")
        name = _clean_collection_name(
            llm.pick_collection_name(
                project_name=project.name,
                paper_titles=[p.title for p in papers if p.title],
                existing=existing,
            )
        )
        if name:
            # Snap to an existing collection on a case-insensitive match so a
            # casing/truncation difference doesn't create a near-duplicate.
            for existing_name in existing:
                if existing_name.lower() == name.lower():
                    return existing_name
            return name
        return default
    except HTTPException as exc:
        logger.info("Zotero collection routing fell back to '%s': %s", default, exc)
        return default
    except Exception as exc:  # noqa: BLE001 — naming must never break the sync
        logger.info("Zotero collection routing errored, using '%s': %s", default, exc)
        return default


def _esc(s: str) -> str:
    return (
        (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _text_to_html(s: str) -> str:
    """A safe HTML rendering of the (markdown-ish) summary text for a Zotero note:
    escape, keep line breaks, bold **…**."""
    import re as _re

    html = _esc(s.strip())
    html = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html)
    return html.replace("\n", "<br>\n")


def _created_keys(resp: dict) -> dict[int, str]:
    """Map created-item index → Zotero key from a create_items response."""
    out: dict[int, str] = {}
    for k, v in (resp.get("success") or {}).items():
        try:
            out[int(k)] = str(v)
        except (TypeError, ValueError):
            pass
    if not out:  # some pyzotero versions only populate `successful` (full objects)
        for k, v in (resp.get("successful") or {}).items():
            key = (v or {}).get("key") or ((v or {}).get("data") or {}).get("key")
            if key:
                try:
                    out[int(k)] = str(key)
                except (TypeError, ValueError):
                    pass
    return out


_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}")


def accessible_pdf_url(db: Session, paper: Paper) -> str:
    """The best PDF link to send to Zotero. A paper's own pdf_url may be a server-side
    dead end (OpenReview behind Cloudflare); when the paper is on arXiv, prefer the
    accessible arXiv link — from the resolved link cached at extraction time, an arXiv
    id, or a one-off title lookup (cached on the document to avoid re-querying)."""
    from app.integrations import mineru
    from app.models.content import PaperDocument

    url = (paper.pdf_url or "").strip()
    if "arxiv.org" in url:
        return url
    doc = db.get(PaperDocument, paper.document_id) if paper.document_id else None
    if doc is not None and (doc.resolved_pdf_url or "").strip():
        return doc.resolved_pdf_url
    aid = ((paper.arxiv_id or "") or (doc.arxiv_id if doc else "") or "").strip()
    if aid and _ARXIV_ID_RE.match(aid):
        return f"https://arxiv.org/pdf/{aid}"
    resolved = mineru.find_arxiv_pdf_by_title(paper.title or "")
    if resolved and doc is not None:
        doc.resolved_pdf_url = resolved  # cache so the next sync skips the lookup
        db.commit()
    return resolved or url


def _paper_children(db: Session, client, project, paper: Paper, pdf_url: str) -> list[dict]:
    """Child items for a synced paper: a Summary note, a Code-analysis note (when the
    repo was analyzed), and a linked-URL attachment to the PDF (`pdf_url` — the
    accessible link) — so the paper, its 5-point summary, and its code analysis travel
    together into Zotero."""
    from app.models.content import PaperDocument
    from app.services import paper_db

    doc = db.get(PaperDocument, paper.document_id) if paper.document_id else None
    if project is not None and doc is not None:
        view = paper_db.project_view(db, project.id, doc)
        summary = view.get("summary") or ""
        code_status, code_summary = view.get("code_status") or "", view.get("code_summary") or ""
        code_url = view.get("code_url") or ""
    else:
        summary = (doc.summary if doc else "") or ""
        code_status = (doc.code_status if doc else "") or ""
        code_summary = (doc.code_summary if doc else "") or ""
        code_url = (doc.code_url if doc else "") or ""

    children: list[dict] = []
    if summary.strip():
        note = client.item_template("note")
        note["note"] = f"<h2>Summary</h2>\n{_text_to_html(summary)}"
        children.append(note)
    if code_status == "ok" and code_summary.strip():
        note = client.item_template("note")
        link = f'<p><a href="{_esc(code_url)}">{_esc(code_url)}</a></p>' if code_url else ""
        note["note"] = f"<h2>Code repository analysis</h2>\n{link}{_text_to_html(code_summary)}"
        children.append(note)
    if pdf_url:
        att = client.item_template("attachment", "linked_url")
        att["title"] = "Full-text PDF"
        att["url"] = pdf_url
        children.append(att)
    return children


def upload_project(
    db: Session,
    user: User,
    papers: list[Paper],
    project=None,
    papers_collection: str = "Daily Papers",
    progress=None,
) -> dict[str, Any]:
    """Upload papers (as preprints, each with its Summary + code-analysis notes and a
    PDF link attached) to a Zotero collection. ALL selected papers are uploaded —
    batched into Zotero's 50-per-request `create_items` limit (so 70 papers no longer
    silently truncate to 50). `progress(pct, msg)` is called as it goes (used by the
    async job for a live log)."""
    client = _client(db, user)
    result: dict[str, Any] = {
        "papers_uploaded": 0, "ideas_uploaded": 0, "notes_uploaded": 0,
        "attachments_uploaded": 0, "errors": [],
    }

    def _report(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    if papers:
        col_key = _wrap(lambda: _ensure_collection(client, papers_collection), "collection create")
        total = len(papers)
        # Resolve each paper's best PDF link once (prefers an accessible arXiv URL over
        # an unfetchable OpenReview one), reused for the item url + the PDF attachment.
        acc_urls = {p.id: accessible_pdf_url(db, p) for p in papers}
        # Create paper items in chunks of 50 (Zotero's per-request cap), tracking each
        # created item's key so its child notes/attachments target the right parent.
        keys_by_index: dict[int, str] = {}
        for start in range(0, total, 50):
            chunk = papers[start:start + 50]
            templates = []
            for p in chunk:
                tmpl = client.item_template("preprint")
                tmpl["title"] = p.title
                tmpl["abstractNote"] = p.abstract
                tmpl["url"] = acc_urls.get(p.id) or p.pdf_url
                tmpl["repository"] = "arXiv"
                tmpl["archiveID"] = f"arXiv:{p.arxiv_id}"
                tmpl["collections"] = [col_key]
                tmpl["creators"] = [
                    {"creatorType": "author", "name": a} for a in (p.authors or [])[:30]
                ]
                templates.append(tmpl)
            resp = _wrap(lambda tp=templates: client.create_items(tp), "paper upload")
            result["papers_uploaded"] += len(resp.get("successful", {})) or len(_created_keys(resp))
            for i, key in _created_keys(resp).items():
                keys_by_index[start + i] = key
            _report(10 + int(40 * min(start + len(chunk), total) / total),
                    f"Uploaded {result['papers_uploaded']}/{total} papers…")
        # Attach each paper's Summary note + code note + PDF link as child items.
        for i, paper in enumerate(papers):
            key = keys_by_index.get(i)
            if not key:
                continue
            children = _paper_children(db, client, project, paper, acc_urls.get(paper.id) or paper.pdf_url)
            if not children:
                continue
            try:  # best-effort — one paper's attachments must not abort the whole sync
                client.create_items(children, parentid=key)
                result["notes_uploaded"] += sum(1 for c in children if c.get("itemType") == "note")
                result["attachments_uploaded"] += sum(
                    1 for c in children if c.get("itemType") == "attachment")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Zotero child items failed for %s: %s", key, exc)
                result["errors"].append(f"Attachments for '{paper.title[:40]}': {exc}"[:200])
            if (i + 1) % 10 == 0 or i + 1 == total:
                _report(50 + int(45 * (i + 1) / total),
                        f"Attached notes + PDF for {i + 1}/{total} papers…")

    return result


def run_upload_job(db: Session, job_id: int) -> None:
    """Background Zotero sync (JobType.zotero_upload): resolve the job's papers and
    upload them with a live progress log, so it survives navigating away and all
    selected papers are uploaded (batched past Zotero's 50-per-request limit)."""
    from app.models.job import Job
    from app.models.project import Project
    from app.models.enums import JobStatus

    job = db.get(Job, job_id)
    if job is None:
        return
    project = db.get(Project, job.project_id)
    user = db.get(User, job.user_id)
    if project is None or user is None:
        job.status, job.error = JobStatus.failed, "Project or user not found"
        db.commit()
        return

    def _set(status: JobStatus, pct: int, msg: str) -> None:
        job.status, job.progress = status, pct
        job.log = (job.log or "") + msg + "\n"
        db.commit()

    payload = job.payload or {}
    paper_ids = payload.get("paper_ids")
    papers = (
        db.query(Paper).filter(Paper.project_id == project.id, Paper.id.in_(paper_ids)).all()
        if paper_ids is not None
        else (db.query(Paper).filter(Paper.project_id == project.id).all()
              if payload.get("include_papers") else [])
    )
    try:
        _set(JobStatus.running, 5, f"Syncing {len(papers)} papers to Zotero…")
        papers_collection = pick_collection(db, user, project, papers)
        res = upload_project(
            db, user, papers, project=project, papers_collection=papers_collection,
            progress=lambda pct, msg: _set(JobStatus.running, pct, msg),
        )
        summary = (
            f"Done — uploaded {res['papers_uploaded']} papers "
            f"({res['notes_uploaded']} summary/code notes + {res['attachments_uploaded']} PDF links)."
        )
        for e in res.get("errors", [])[:5]:
            job.log = (job.log or "") + f"warning: {e}\n"
        _set(JobStatus.succeeded, 100, summary)
    except HTTPException as exc:
        _set(JobStatus.failed, job.progress, "")
        job.error = str(exc.detail)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        _set(JobStatus.failed, job.progress, "")
        job.error = f"{type(exc).__name__}: {exc}"[:300]
        db.commit()


def upload_papers_to_collection(
    db: Session, user: User, papers_meta: list[dict[str, Any]], collection_name: str
) -> dict[str, Any]:
    """Upload an arbitrary paper set (dicts) as preprints into a named collection.

    Used to sync the exact papers an idea cited into a dedicated Zotero collection.
    """
    client = _client(db, user)
    if not papers_meta:
        return {"collection": collection_name, "uploaded": 0}
    col_key = _wrap(lambda: _ensure_collection(client, collection_name), "collection create")
    templates = []
    for p in papers_meta[:60]:
        tmpl = client.item_template("preprint")
        tmpl["title"] = p.get("title", "")
        tmpl["url"] = p.get("url", "")
        tmpl["date"] = str(p.get("year", "") or "")
        if p.get("arxiv_id"):
            tmpl["repository"] = "arXiv"
            tmpl["archiveID"] = f"arXiv:{p['arxiv_id']}"
        if p.get("doi"):
            tmpl["DOI"] = p["doi"]
        tmpl["collections"] = [col_key]
        tmpl["creators"] = [
            {"creatorType": "author", "name": str(a)} for a in (p.get("authors") or [])[:30]
        ]
        templates.append(tmpl)
    resp = _wrap(lambda: client.create_items(templates), "citation upload")
    return {"collection": collection_name, "uploaded": len(resp.get("successful", {}))}
