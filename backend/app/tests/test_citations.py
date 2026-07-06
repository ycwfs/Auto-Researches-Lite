"""BibTeX rendering — conference (venue) papers vs arXiv preprints."""
from __future__ import annotations

from app.services.citations import to_bibtex


def test_bibtex_inproceedings_for_venue_paper() -> None:
    """An AI Paper Finder paper (venue, no arXiv id) cites as @inproceedings + booktitle."""
    bib = to_bibtex(
        [
            {
                "title": "3D Gaussian Splatting",
                "authors": ["A. Kerbl", "B. Author"],
                "year": "2026",
                "arxiv_id": "",
                "doi": "",
                "url": "https://openaccess.thecvf.com/x.pdf",
                "venue": "CVPR 2026",
                "source": "ai_paper_finder",
            }
        ]
    )
    assert "@inproceedings{" in bib
    assert "booktitle={CVPR 2026}" in bib
    assert "url={https://openaccess.thecvf.com/x.pdf}" in bib
    assert "@article" not in bib  # not a preprint


def test_bibtex_article_for_arxiv_paper() -> None:
    bib = to_bibtex(
        [{"title": "T", "authors": ["X"], "year": "2025", "arxiv_id": "2501.00001", "doi": "", "url": ""}]
    )
    assert "@article{" in bib
    assert "eprint={2501.00001}" in bib
    assert "@inproceedings" not in bib


def test_bibtex_plain_article_when_no_venue_or_arxiv() -> None:
    bib = to_bibtex([{"title": "T", "authors": ["X"], "year": "2024", "doi": "10.1/x"}])
    assert "@article{" in bib
    assert "doi={10.1/x}" in bib
    assert "booktitle" not in bib
