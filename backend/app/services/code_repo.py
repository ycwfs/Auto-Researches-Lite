"""Code-repository analysis for explored papers.

While a paper is processed (paper_db.convert_and_store), we also: find a code-repo
URL in its text, verify it (shallow clone — fails for broken/private/empty), and
produce a structured analysis (architecture / modules / entry points / data pipeline
/ dependencies) analogous to the 5-point summary, stored on the PaperDocument.

READ-ONLY: the clone is only read (README + file tree + a few small text files) and
then discarded — no repo file is ever executed. Missing / broken / empty repos are
handled silently (code_status = "none"); never surfaced as an error to the user.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.content import PaperDocument

logger = logging.getLogger("far.code")

CLONE_TIMEOUT = 45  # seconds
_README_CHARS = 8000
_KEYFILE_CHARS = 2000
_MAX_TREE = 400  # file-tree entries listed
_MAX_KEYFILES = 12

# Repo hosts we recognize (kept narrow — code hosts, not data/model hubs). The scheme is
# optional (bare "github.com/o/r" and markdown "[x](github.com/o/r)" are common in papers);
# the leading lookbehind stops a host matching as a suffix of a larger domain/email.
_REPO_RE = re.compile(
    r"(?<![\w@.])(?:https?://)?(?:www\.)?(github\.com|gitlab\.com)/([A-Za-z0-9_./-]+)",
    re.IGNORECASE,
)
# First path segments on github that are NOT a user/repo.
_NON_REPO = {"sponsors", "topics", "about", "features", "marketplace", "settings", "orgs", "explore"}
# Path segments that begin a repo VIEW (not part of owner/repo): github /tree, gitlab /-/, etc.
_VIEW_SEGS = {
    "-", "tree", "blob", "issues", "pull", "pulls", "commit", "commits", "releases",
    "wiki", "wikis", "actions", "blame", "raw", "merge_requests", "branches", "find",
}
# Files worth feeding the model (entry points + dependency/config manifests).
_KEY_NAMES = {
    "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml", "environment.yml",
    "environment.yaml", "makefile", "dockerfile", "config.yaml", "config.yml",
}
_KEY_STEMS = ("train", "main", "run", "model", "models", "inference", "infer", "eval", "demo")
# Source-code extensions used to tell a real code release from a README/docs-only stub.
_CODE_EXT = {
    ".py", ".ipynb", ".cpp", ".cc", ".cxx", ".c", ".cu", ".cuh", ".h", ".hpp", ".java",
    ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".m", ".mm", ".jl", ".scala",
    ".swift", ".sh", ".lua", ".r", ".php",
}
_NON_CODE_PY = {"setup.py", "conftest.py"}  # packaging/test scaffolding, not the release
_COMING_SOON = re.compile(
    r"coming soon|to be released|stay tuned|under construction|work in progress|"
    r"(?:code|model|will be).{0,20}released? soon|release\w*.{0,12}soon",
    re.I,
)

# --- Telling the paper's OWN released repo apart from a cited third-party / baseline --- #
# Availability cues near a URL that mark it as this paper's code.
_AVAIL_WORD = re.compile(
    r"\b(our code|our implementation|code(?:base)?|implementation|source code|"
    r"available|released?|reproduc\w*|official|project page|open[- ]?sourced?|"
    r"we (?:release|provide|open))\b",
    re.I,
)
# Citation cues that mark a URL as a third-party/baseline repo (not this paper's).
_CITE_CUE = re.compile(
    r"(based on|buil[dt] (?:on|upon)|baseline|compared? (?:to|with|against)|"
    r"adapted from|borrowed from|fork(?:ed)? (?:of|from)|provided by|courtesy of|"
    r"on top of|similar to|\[\d+\]\s*\(?$)",
    re.I,
)
# Owners that are frameworks/tools — essentially never a research paper's own repo.
_DENY_OWNERS = {
    "pytorch", "tensorflow", "keras", "huggingface", "scikit-learn", "scikit_learn",
    "numpy", "scipy", "pandas", "matplotlib", "opencv", "open-mmlab", "openmmlab",
    "ultralytics", "cocodataset", "pytorchlightning", "lightning-ai", "wandb", "rwightman",
}


def normalize_repo_url(url: str) -> str | None:
    """Canonicalize a USER-SUPPLIED repository URL to https://github.com/owner/repo
    (or the gitlab equivalent), or None when it isn't a recognizable repo URL.
    Request-time validation for the manual per-paper Code Analysis action."""
    m = _REPO_RE.search(url or "")
    if not m:
        return None
    return _norm_repo(m.group(1), m.group(2))


def _norm_repo(host: str, path: str) -> str | None:
    """Normalize a matched host + path to the canonical repo URL, or None if it isn't one.
    github → owner/repo (deeper path / /tree/... dropped); gitlab → owner/[subgroups…]/project
    up to the first view segment."""
    host = host.lower()
    segs = [s for s in path.split("/") if s and s not in {".", ".."}]
    if len(segs) < 2 or segs[0].lower() in _NON_REPO:
        return None
    if host == "github.com":
        keep = segs[:2]  # owner/repo — ignore /tree/main, /blob/..., deeper paths
    else:  # gitlab supports subgroups: owner/sub/.../project, up to a view segment
        keep = []
        for s in segs:
            if s.lower() in _VIEW_SEGS:
                break
            keep.append(s)
        if len(keep) < 2:
            return None
    keep[-1] = re.sub(r"\.git$", "", keep[-1]).rstrip(".,)]}>\"'")
    if not keep[-1]:
        return None
    return f"https://{host}/{'/'.join(keep)}"


def _candidate_score(owner: str, before: str, after: str, in_abstract: bool) -> int:
    """How likely a URL is THIS paper's own repo, from its surrounding text."""
    # A citation cue ("based on", "fork of", "baseline", "[12]") marks the URL as a
    # third-party/baseline repo and DOMINATES any availability word in the same clause —
    # otherwise "Our code is a fork of X" / "implementation is based on X" would cancel to
    # a neutral 0 that the lone-URL fallback then wrongly accepts.
    if _CITE_CUE.search(before):
        return -3
    score = 0
    if _AVAIL_WORD.search(before) or _AVAIL_WORD.search(after):
        score += 2
    if in_abstract:  # the abstract almost never cites a third-party repo
        score += 3
    if owner.lower() in _DENY_OWNERS:
        score -= 3
    return score


def find_repo_url(markdown: str, abstract: str = "") -> str | None:
    """The repo URL most likely to be THIS paper's own released code.

    Scores every github/gitlab candidate by nearby availability cues ("our code",
    "available at", "project page") vs citation cues ("based on", "baseline", "[12]")
    and a framework-owner denylist — so a cited third-party / baseline repo isn't
    mistaken for the paper's own. Returns the best positive-scoring candidate; for a
    lone, context-free URL it's still returned, but multiple ambiguous or
    citation-flavored URLs yield None rather than a wrong guess."""
    best: dict[str, tuple[int, int]] = {}  # url -> (best score, first-seen order)
    order = 0
    for text, in_abstract in ((markdown or "", False), (abstract or "", True)):
        for m in _REPO_RE.finditer(text):
            url = _norm_repo(m.group(1), m.group(2))
            if not url:
                continue
            # Only the URL's OWN sentence/clause counts — truncate at the nearest
            # sentence boundary so a neighbouring sentence's cue (e.g. a "baseline"
            # mention before, or an "our code" after) doesn't leak onto this candidate.
            before = re.split(r"[.!?]\s|\n", text[max(0, m.start() - 160):m.start()])[-1]
            after = re.split(r"[.!?]\s|\n", text[m.end():m.end() + 60])[0]
            owner = m.group(2).split("/")[0]
            score = _candidate_score(owner, before, after, in_abstract)
            if url not in best:
                best[url] = (score, order)
            elif score > best[url][0]:
                best[url] = (score, best[url][1])
            order += 1
    if not best:
        return None
    ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
    top_url, (top_score, _) = ranked[0]
    if top_score > 0:
        return top_url
    # No positive signal anywhere: accept a single, neutral (non-cited) URL; otherwise
    # decline rather than risk picking a cited repo.
    if len(ranked) == 1 and top_score == 0:
        return top_url
    return None


def _safe_clone(url: str, dest: Path) -> bool:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true",
           "GCM_INTERACTIVE": "never"}
    cmd = [
        "git", "-c", "core.symlinks=false",
        "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never",
        "clone", "--depth", "1", "--no-tags", "--single-branch", url, str(dest),
    ]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=CLONE_TIMEOUT, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            # Server-side diagnostics only (the user still sees nothing): distinguish a
            # broken/private repo from a too-slow clone from a missing git binary.
            logger.debug("clone failed for %s (rc=%s): %s", url, proc.returncode, (proc.stderr or "")[:200])
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        logger.info("clone timed out after %ss: %s", CLONE_TIMEOUT, url)
        return False
    except OSError as exc:
        logger.warning("git unavailable while cloning %s: %s", url, exc)
        return False


def _read(path: Path, limit: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _gather_context(url: str, root: Path) -> str | None:
    """Build the read-only repository material for the LLM, or None if there's no real
    code to analyze (empty repo, README/docs only, placeholder files, or a repo that
    announces the code is unreleased — "Coming Soon"). Skipping those keeps us from
    storing a "this is only a README / cannot be determined" non-summary."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git" and not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            p = Path(dirpath) / fn
            if not os.path.islink(p) and p.is_file():
                files.append(p)
    if not files:
        return None  # empty repository

    readme = ""
    for p in files:
        if p.name.lower().startswith("readme"):
            readme = _read(p, _README_CHARS)
            if readme:
                break

    # Stub guard: a repo with no real source (only README/docs/placeholders), or a tiny
    # repo that says the code is coming, has nothing to analyze — skip it.
    code_files = [p for p in files if p.suffix.lower() in _CODE_EXT and p.name.lower() not in _NON_CODE_PY]
    code_bytes = 0
    for p in code_files:
        try:
            code_bytes += p.stat().st_size
        except OSError:
            pass
    if not code_files or code_bytes < 500:
        return None  # README/docs only or placeholder files — not a real code release
    if code_bytes < 3000 and _COMING_SOON.search(readme):
        return None  # explicitly announced as unreleased ("Coming Soon")

    tree = "\n".join(sorted(str(p.relative_to(root)) for p in files)[:_MAX_TREE])

    key_blocks: list[str] = []
    for p in files:
        if len(key_blocks) >= _MAX_KEYFILES:
            break
        name = p.name.lower()
        stem = p.stem.lower()
        if name in _KEY_NAMES or (p.suffix.lower() == ".py" and any(stem.startswith(s) for s in _KEY_STEMS)):
            body = _read(p, _KEYFILE_CHARS)
            if body.strip():
                key_blocks.append(f"### {p.relative_to(root)}\n{body}")

    return (
        f"# Repository: {url}\n\n"
        f"## File tree ({len(files)} files)\n{tree}\n\n"
        f"## README\n{readme or '(none)'}\n\n"
        f"## Key files\n{chr(10).join(key_blocks) or '(none found)'}"
    )


def analyze(url: str, llm, prompt: str | None = None) -> tuple[str, str] | None:
    """Verify + analyze a repo. Returns (url, structured_summary) or None when the repo
    is broken/private/empty. Read-only: nothing in the clone is executed. `prompt` is the
    project's editable code-analysis template (else the built-in default)."""
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "repo"
        if not _safe_clone(url, dest):
            return None  # broken / private / unreachable
        context = _gather_context(url, dest)
        if not context:
            return None  # empty repository
        summary = llm.summarize_codebase(context, prompt=prompt)
    return (url, summary.strip()) if summary and summary.strip() else None


def ensure_analyzed(
    db: Session, doc: PaperDocument, llm, code_prompt: str | None = None, force: bool = False
) -> PaperDocument:
    """Find + analyze the paper's code repo once (dedup on code_status). Silent on any
    failure: records code_status = "none". Never raises to the caller's pipeline. `force`
    re-runs the analysis even if already processed (per-paper re-analyze debug action)."""
    if (doc.code_status or "").strip() and not force:
        return doc  # already processed (dedup across projects/runs)
    # Best-effort, no lock: two concurrent runs could both analyze the same fresh doc
    # (a benign redundant clone — both write the same fields, consistent final state).
    # This matches ensure_summarized's accepted convention; a "wip" claim would add a
    # stuck-state failure mode not worth the rare duplicate.
    try:
        url = find_repo_url(doc.markdown or "", doc.abstract or "")
        result = analyze(url, llm, prompt=code_prompt) if url else None
        if result:
            doc.code_url, doc.code_summary = result
            doc.code_status = "ok"
            doc.code_model = getattr(getattr(llm, "config", None), "provider", "") or (
                "mock" if getattr(llm, "offline", False) else ""
            )
        else:
            doc.code_status = "none"  # missing / broken / empty — handled silently
    except Exception:  # noqa: BLE001 — best-effort; a bad repo must not fail discovery
        logger.debug("code analysis failed for doc %s", doc.id, exc_info=True)
        doc.code_status = "none"
    db.commit()
    return doc
