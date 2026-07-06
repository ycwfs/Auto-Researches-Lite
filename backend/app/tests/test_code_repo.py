"""Code-repository analysis: URL detection, silent-none, dedup, downstream rendering."""
from __future__ import annotations


def test_find_repo_url() -> None:
    from app.services import code_repo as cr

    assert cr.find_repo_url("Code at https://github.com/Owner/My-Repo.") == "https://github.com/Owner/My-Repo"
    assert cr.find_repo_url("https://github.com/foo/bar.git (MIT)") == "https://github.com/foo/bar"
    assert cr.find_repo_url("see https://gitlab.com/g/proj for code") == "https://gitlab.com/g/proj"
    assert cr.find_repo_url("github.com/sponsors/x only") is None  # non-repo path skipped
    assert cr.find_repo_url("no repository here", "no abstract repo") is None


def test_find_repo_url_prefers_own_over_cited() -> None:
    """The paper's OWN repo (availability cue) is chosen over a cited baseline that
    appears first, and clearly third-party/framework repos are never picked."""
    from app.services import code_repo as cr

    # own repo wins despite the cited baseline appearing first
    assert cr.find_repo_url(
        "We compare against the strong baseline DeepX (https://github.com/them/deepx). "
        "Our code is available at https://github.com/us/ours."
    ) == "https://github.com/us/ours"
    # a framework/tool repo is never the paper's own
    assert cr.find_repo_url(
        "We implement our method in PyTorch (https://github.com/pytorch/pytorch)."
    ) is None
    # an abstract URL is a strong own-repo signal even without a cue word
    assert cr.find_repo_url("body has no repo", "Project: https://github.com/team/proj") == (
        "https://github.com/team/proj"
    )
    # multiple ambiguous, citation-flavored URLs -> decline rather than guess wrong
    assert cr.find_repo_url(
        "Similar to [3] (https://github.com/a/one) and [7] (https://github.com/b/two)."
    ) is None


def test_find_repo_url_citation_dominates_availability() -> None:
    """A citation cue ("fork of", "based on") wins over an availability word in the same
    clause, so the cited upstream isn't returned as the paper's own (adversarial-found)."""
    from app.services import code_repo as cr

    assert cr.find_repo_url("Our code is a fork of https://github.com/a/b which we adapted.") is None
    assert cr.find_repo_url(
        "The implementation is based on https://github.com/someone/segmentation-toolkit."
    ) is None


def test_find_repo_url_scheme_less_and_subgroups() -> None:
    """Bare-domain URLs, markdown-link targets, and gitlab subgroups are recognized
    (adversarial-found recall gaps)."""
    from app.services import code_repo as cr

    assert cr.find_repo_url("Our code is available at github.com/alice/projA.") == (
        "https://github.com/alice/projA"
    )
    assert cr.find_repo_url("Our code: [GitHub](github.com/alice/projA).") == (
        "https://github.com/alice/projA"
    )
    assert cr.find_repo_url("Our implementation is at https://gitlab.com/group/subgroup/projX.") == (
        "https://gitlab.com/group/subgroup/projX"
    )
    # a deeper github view path still resolves to owner/repo
    assert cr.find_repo_url("Our code: https://github.com/o/r/tree/main/src") == "https://github.com/o/r"
    # a host that's only a suffix of a larger domain is NOT matched
    assert cr.find_repo_url("see mygithub.company.com/a/b for details") is None


def test_gather_context_skips_stub_repos(tmp_path) -> None:
    """A README-only / 'Coming Soon' / placeholder repo yields no context (skip), while a
    repo with real source code is analyzed."""
    from app.services import code_repo as cr

    # README only (the reported "Efficient-LITE / Coming Soon" case) -> skip
    (tmp_path / "README.md").write_text("# Efficient-LITE\nImplementation Coming Soon.\narXiv:2507.00416")
    assert cr._gather_context("https://github.com/x/efficient-lite", tmp_path) is None

    # README announces unreleased + only a tiny placeholder source file -> skip
    (tmp_path / "model.py").write_text("x = 1\n" * 120)  # ~720B: passes the size floor
    assert cr._gather_context("https://github.com/x/efficient-lite", tmp_path) is None

    # real code present -> context is built
    (tmp_path / "train.py").write_text("import torch\n" + "y = 2\n" * 600)  # > 3KB of code
    ctx = cr._gather_context("https://github.com/x/efficient-lite", tmp_path)
    assert ctx and "train.py" in ctx


def _doc(markdown: str = "", code_status: str = ""):
    from app.core.database import SessionLocal
    from app.models.content import PaperDocument

    db = SessionLocal()
    doc = PaperDocument(title="T", markdown=markdown, code_status=code_status)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return db, doc


def test_ensure_analyzed_no_repo_records_none_silently() -> None:
    from app.services import code_repo
    from app.services.llm import LLMService

    db, doc = _doc(markdown="A paper with no code link.")
    try:
        code_repo.ensure_analyzed(db, doc, LLMService())  # offline mock; no URL ⇒ no clone
        assert doc.code_status == "none"
        assert doc.code_url == "" and doc.code_summary == ""
    finally:
        db.delete(doc)
        db.commit()
        db.close()


def test_ensure_analyzed_ok_when_repo_analyzes(monkeypatch) -> None:
    from app.services import code_repo
    from app.services.llm import LLMService

    monkeypatch.setattr(
        code_repo, "analyze", lambda url, llm, prompt=None: (url, "1. Architecture: a CNN.\n2. Modules…")
    )
    db, doc = _doc(markdown="Code: https://github.com/x/y")
    try:
        code_repo.ensure_analyzed(db, doc, LLMService())
        assert doc.code_status == "ok"
        assert doc.code_url == "https://github.com/x/y"
        assert "Architecture" in doc.code_summary
    finally:
        db.delete(doc)
        db.commit()
        db.close()


def test_ensure_analyzed_is_dedup(monkeypatch) -> None:
    from app.services import code_repo
    from app.services.llm import LLMService

    def _boom(*a, **k):
        raise AssertionError("analyze must not run for an already-processed doc")

    monkeypatch.setattr(code_repo, "analyze", _boom)
    db, doc = _doc(markdown="https://github.com/x/y", code_status="ok")
    try:
        code_repo.ensure_analyzed(db, doc, LLMService())  # already processed ⇒ skip
        assert doc.code_status == "ok"
    finally:
        db.delete(doc)
        db.commit()
        db.close()


def test_render_summaries_md_includes_code_only_when_present() -> None:
    from app.services.fulltext import render_summaries_md

    out = render_summaries_md([
        {"title": "P1", "summary": "5-point…", "code": "1. Architecture…", "code_url": "https://github.com/a/b"},
        {"title": "P2", "summary": "5-point…", "code": "", "code_url": ""},
    ])
    assert "Code repository analysis (https://github.com/a/b)" in out  # P1 has code
    assert out.count("Code repository analysis") == 1  # P2 (none) contributes nothing
