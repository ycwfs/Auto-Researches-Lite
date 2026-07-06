"""Collect papers from OpenReview for a venue/year, emitting normalized JSONL on
stdout for build_corpus.py. Covers OpenReview-hosted venues (ICLR/ICML/NeurIPS).

  python collect_openreview.py --venue ICLR --year 2025 --state Accepted > papers.jsonl

Credentials (only needed for 'Submission' / non-public notes) come from
--email/--password or OPENREVIEW_EMAIL / OPENREVIEW_PASSWORD. Each emitted line:
  {"id","title","abstract","pdf","bibtex","keywords","venue","year"}

Note: OpenReview's API/invitation shapes vary by year; this is a pragmatic
starting point — adjust the invitation pattern per venue if a year returns 0.
CVF venues (CVPR/ICCV) use a different (scraping) path — see the upstream repo's
additional_venues/CVPR_ICCV.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests

API2 = os.environ.get("OPENREVIEW_API", "https://api2.openreview.net")


def _login(email: str, password: str) -> str | None:
    if not (email and password):
        return None
    r = requests.post(f"{API2}/login", json={"id": email, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json().get("token")


def _fetch_notes(venue: str, year: str, state: str, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Modern OpenReview (API2) marks acceptance via `venueid`, NOT an "Accepted"
    # invitation: accepted papers carry venueid == "{venue}.cc/{year}/Conference",
    # while rejected/withdrawn ones get a suffix (e.g. .../Rejected_Submission). Older
    # years exposed a "/-/Accepted" invitation. So query accepted papers by venueid and
    # everything else (Submission, Blind_Submission, …) by the invitation.
    if state.strip().lower() == "accepted":
        base = {"content.venueid": f"{venue}.cc/{year}/Conference"}
    else:
        base = {"invitation": f"{venue}.cc/{year}/Conference/-/{state}"}
    offset, limit = 0, 1000
    while True:
        params = {**base, "limit": limit, "offset": offset}
        r = requests.get(f"{API2}/notes", headers=headers, params=params, timeout=90)
        r.raise_for_status()
        notes = r.json().get("notes", [])
        if not notes:
            break
        yield from notes
        offset += len(notes)
        if len(notes) < limit:
            break


def _value(content: dict, key: str):
    v = content.get(key)
    return v.get("value") if isinstance(v, dict) else v


def _normalize(note: dict, venue: str, year: str) -> dict:
    content = note.get("content", {}) or {}
    pdf = _value(content, "pdf") or ""
    if isinstance(pdf, str) and pdf.startswith("/"):
        pdf = f"https://openreview.net{pdf}"
    return {
        "id": note.get("id", ""),
        "title": _value(content, "title") or "",
        "abstract": _value(content, "abstract") or "",
        "pdf": pdf,
        "bibtex": _value(content, "_bibtex") or "",
        "keywords": _value(content, "keywords") or [],
        "venue": venue,
        "year": str(year),
    }


def collect(venue: str, year, state: str = "Accepted", *, email: str = "", password: str = "") -> list[dict]:
    """Collect normalized paper records for an OpenReview venue/year (importable: used by
    the sidecar's in-process ingest as well as the CLI). Only records with an abstract."""
    token = _login(email or os.environ.get("OPENREVIEW_EMAIL", ""),
                   password or os.environ.get("OPENREVIEW_PASSWORD", ""))
    out: list[dict] = []
    for note in _fetch_notes(venue, str(year), state, token):
        rec = _normalize(note, venue, str(year))
        if rec["abstract"]:
            out.append(rec)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect OpenReview papers as normalized JSONL.")
    ap.add_argument("--venue", required=True, help="ICLR | ICML | NeurIPS")
    ap.add_argument("--year", required=True)
    ap.add_argument("--state", default="Accepted", help="Submission | Blind_Submission | Accepted")
    ap.add_argument("--email", default=os.environ.get("OPENREVIEW_EMAIL", ""))
    ap.add_argument("--password", default=os.environ.get("OPENREVIEW_PASSWORD", ""))
    args = ap.parse_args()

    records = collect(args.venue, args.year, args.state, email=args.email, password=args.password)
    for rec in records:
        print(json.dumps(rec, ensure_ascii=False))
    print(f"# collected {len(records)} papers for {args.venue} {args.year} ({args.state})", file=sys.stderr)


if __name__ == "__main__":
    main()
