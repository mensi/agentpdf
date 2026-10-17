from pathlib import Path

import pytest

from agentpdf.cli import main
from agentpdf.convert import Options, convert
from agentpdf.search import INDEX_FILE, build_index, search, to_fts_query

from .pdfgen import PdfBuilder


@pytest.fixture
def out_root(tmp_path: Path) -> Path:
    pdf = PdfBuilder(title="Protocol Spec")
    p1 = pdf.page()
    p1.text(72, 100, "1 Timers", size=14, bold=True)
    p1.text(72, 130, "Timer T3310 is started when the ATTACH REQUEST message is sent.")
    p1.text(72, 160, "# 22 (Congestion) is a cause value, not a heading.")
    p2 = pdf.page()
    p2.text(72, 100, "2 Messages", size=14, bold=True)
    p2.text(72, 130, "The X.509 report lists booked entries.")
    pdf.bookmark("1 Timers", 0)
    pdf.bookmark("2 Messages", 1)
    root = tmp_path / "out"
    convert(str(pdf.save(tmp_path / "spec.pdf")), str(root), Options())
    return root


def test_index_built_on_convert(out_root: Path) -> None:
    assert (out_root / INDEX_FILE).is_file()


def test_search_returns_location(out_root: Path) -> None:
    hits, relaxed = search(out_root, "attach request")
    assert not relaxed
    assert len(hits) == 1
    hit = hits[0]
    assert hit.file == "spec/sections/001-1-timers.md"
    assert hit.page == 1
    assert hit.heading == "1 Timers"
    assert "[ATTACH] [REQUEST]" in hit.snippet
    lines = (out_root / hit.file).read_text().splitlines()
    assert "T3310" in lines[hit.line - 1]


def test_search_punctuated_terms_and_stemming(out_root: Path) -> None:
    assert search(out_root, "x.509")[0][0].page == 2
    # Terms are stemmed: "booking entry" matches "booked entries".
    assert search(out_root, "booking entry")[0][0].page == 2


def test_search_relaxes_to_any_term(out_root: Path) -> None:
    hits, relaxed = search(out_root, "T3310 default")
    assert relaxed
    assert hits[0].page == 1


def test_body_text_is_not_parsed_as_heading(out_root: Path) -> None:
    hits, _ = search(out_root, "congestion")
    assert hits[0].heading == "1 Timers"
    text = (out_root / hits[0].file).read_text()
    assert "\n\\# 22 (Congestion)" in text


def test_to_fts_query() -> None:
    assert to_fts_query('attach "old P-TMSI" rau*') == '"attach" "old P TMSI" "rau"*'
    assert to_fts_query("x.509", any_term=True) == '"x 509"'
    assert to_fts_query("a OR b") == '"a" "OR" "b"'
    assert to_fts_query("--") == ""


def test_rebuild_index(out_root: Path) -> None:
    (out_root / INDEX_FILE).unlink()
    build_index(out_root)
    assert search(out_root, "T3310")[0]


def test_cli_search(out_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "T3310", "-d", str(out_root)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("spec/sections/001-1-timers.md:")
    assert main(["search", "nonexistentword", "-d", str(out_root)]) == 1
