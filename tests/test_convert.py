import json
from pathlib import Path

import pytest

from agentpdf.cli import main
from agentpdf.convert import Options, convert
from agentpdf.document import Document
from agentpdf.model import RegionKind

from .pdfgen import PageBuilder, PdfBuilder

BODY = "The quick brown fox jumps over the lazy dog near the riverbank today."


def _add_furniture(page: PageBuilder, number: int, chapter: str) -> None:
    page.text(72, 30, f"Example Manual {chapter} Page {number} of 9", size=8)
    page.text(72, 760, "Version 1.0 Copyright Example Corp", size=8)


@pytest.fixture
def spec_pdf(tmp_path: Path) -> Path:
    pdf = PdfBuilder(title="Example Specification")
    p1 = pdf.page()
    _add_furniture(p1, 1, "Intro")
    p1.text(72, 120, "1 Introduction", size=14, bold=True)
    p1.text(72, 150, BODY)
    p1.text(72, 163, "It continues on a second line of the same paragraph.")
    p1.text(72, 200, "1.1 Scope", size=12, bold=True)
    p1.text(72, 225, "The scope covers the frobnication interface.")

    p2 = pdf.page()
    _add_furniture(p2, 2, "Limits")
    p2.text(72, 120, "2 Limits", size=14, bold=True)
    p2.text(72, 150, "Recommended operating conditions are listed below.")
    p2.grid([72, 300, 380, 460, 540], [180, 200, 220, 240])
    for x, label in ((305, "MIN"), (385, "MAX"), (465, "UNIT")):
        p2.text(x, 185, label, size=9, bold=True)
    p2.text(76, 205, "Supply voltage", size=9)
    p2.text(305, 205, "4.5", size=9)
    p2.text(385, 205, "36", size=9)
    p2.text(465, 205, "V", size=9)
    p2.text(76, 225, "Dual supply", size=9)
    p2.text(305, 225, "2.25", size=9)
    p2.text(385, 225, "18", size=9)
    p2.text(465, 225, "V", size=9)
    p2.text(72, 280, "Text after the table.")

    p3 = pdf.page()
    _add_furniture(p3, 3, "Figures")
    p3.text(72, 300, "The figure above shows the response.")
    # The page title is drawn last in the content stream but placed on top.
    p3.text(72, 120, "3 Figures", size=14, bold=True)
    p3.grid([72, 300], [150, 280])
    for i in range(6):
        p3.curve(80 + i * 30, 170 + i * 10, 30, 20)

    pdf.bookmark("1 Introduction", 0)
    pdf.bookmark("1.1 Scope", 0, parent=0)
    pdf.bookmark("2 Limits", 1)
    pdf.bookmark("3 Figures", 2)
    return pdf.save(tmp_path / "Example Spec.pdf")


def _section_text(out_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((out_dir / "sections").glob("*.md")))


def _file_with(out_dir: Path, needle: str) -> str:
    matches = [
        text for p in (out_dir / "sections").glob("*.md") if needle in (text := p.read_text(encoding="utf-8"))
    ]
    assert len(matches) == 1, f"{needle!r} found in {len(matches)} files"
    return matches[0]


def test_convert_layout(spec_pdf: Path, tmp_path: Path) -> None:
    out_dir = convert(str(spec_pdf), str(tmp_path / "out"), Options())
    assert out_dir.name == "example-spec"
    # Small subsections stay in their parent's file.
    files = sorted(p.name for p in (out_dir / "sections").glob("*.md"))
    assert files == ["001-1-introduction.md", "002-2-limits.md", "003-3-figures.md"]

    intro = (out_dir / "sections" / "001-1-introduction.md").read_text(encoding="utf-8")
    assert intro.startswith("<!-- page 1 -->\n\n# 1 Introduction\n")
    assert "\n## 1.1 Scope\n" in intro
    # Lines of a paragraph are joined so phrases spanning lines are searchable.
    assert "riverbank today. It continues" in intro


def test_furniture_removed(spec_pdf: Path, tmp_path: Path) -> None:
    out_dir = convert(str(spec_pdf), str(tmp_path / "out"), Options())
    text = _section_text(out_dir)
    assert "Copyright" not in text
    assert "Example Manual" not in text


def test_table_region(spec_pdf: Path, tmp_path: Path) -> None:
    out_dir = convert(str(spec_pdf), str(tmp_path / "out"), Options())
    limits = _file_with(out_dir, "Dual supply")
    assert "**[Table on page 2](../images/p0002-table-1.png)**" in limits
    assert (out_dir / "images" / "p0002-table-1.png").is_file()

    block = limits.split("```text\n", 1)[1].split("```", 1)[0].splitlines()
    header = next(line for line in block if "MAX" in line)
    dual = next(line for line in block if "Dual supply" in line)
    # Values stay under their column headings.
    assert dual.index("18") == header.index("MAX")
    assert dual.index("2.25") == header.index("MIN")
    # Text after the table follows it, outside the code block.
    assert limits.index("Text after the table.") > limits.index("Dual supply")


def test_reading_order_and_figure(spec_pdf: Path, tmp_path: Path) -> None:
    out_dir = convert(str(spec_pdf), str(tmp_path / "out"), Options())
    figures = _file_with(out_dir, "# 3 Figures")
    assert (
        figures.index("# 3 Figures") < figures.index("Figure on page 3") < figures.index("The figure above")
    )


def test_manifest_and_index(spec_pdf: Path, tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    out_dir = convert(str(spec_pdf), str(out_root), Options())
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["title"] == "Example Specification"
    assert manifest["source"]["pages"] == 3
    assert manifest["pages"][1]["flags"] == ["table"]
    assert manifest["pages"][2]["flags"] == ["figure"]

    assert manifest["outline"][1] == {
        "title": "1.1 Scope",
        "level": 1,
        "page": 1,
        "file": "sections/001-1-introduction.md",
    }

    index = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "- [1 Introduction](sections/001-1-introduction.md) — p. 1, ~" in index

    agents = (out_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "[Example Specification](example-spec/INDEX.md)" in agents


def test_intended_line_breaks_are_kept(tmp_path: Path) -> None:
    pdf = PdfBuilder()
    page = pdf.page()
    page.text(72, 100, "Wrapped prose that fills the line up to the right margin of the column and")
    page.text(72, 113, "continues here.")
    page.text(72, 140, "Type of identity")
    page.text(72, 153, "0 0 1 IMSI")
    page.text(72, 166, "0 1 0 IMEI")
    page.text(72, 179, "0 1 1 IMEISV")
    out_dir = convert(str(pdf.save(tmp_path / "codes.pdf")), str(tmp_path / "out"), Options())
    text = _section_text(out_dir)
    assert "column and continues here." in text
    assert "Type of identity\n0 0 1 IMSI\n0 1 0 IMEI\n0 1 1 IMEISV" in text


def test_reconvert_replaces_output(spec_pdf: Path, tmp_path: Path) -> None:
    out_dir = convert(str(spec_pdf), str(tmp_path / "out"), Options())
    stale = out_dir / "sections" / "999-stale.md"
    stale.write_text("old", encoding="utf-8")
    convert(str(spec_pdf), str(tmp_path / "out"), Options())
    assert not stale.exists()


def test_refuses_to_overwrite_foreign_directory(spec_pdf: Path, tmp_path: Path) -> None:
    foreign = tmp_path / "out" / "example-spec"
    foreign.mkdir(parents=True)
    (foreign / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(FileExistsError):
        convert(str(spec_pdf), str(tmp_path / "out"), Options())
    assert (foreign / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_outline_levels_repaired_from_numbering(tmp_path: Path) -> None:
    pdf = PdfBuilder()
    page = pdf.page()
    titles = ["1. General", "1.1 Purpose", "1.2 Audience", "2. Usage", "2.1 Setup"]
    for i, title in enumerate(titles):
        page.text(72, 100 + i * 60, title, size=12, bold=True)
        page.text(72, 125 + i * 60, f"Body text for {title}.")
    # Bookmarks nest each entry under the previous one.
    parent = None
    for title in titles:
        parent = pdf.bookmark(title, 0, parent=parent)
    out_dir = convert(str(pdf.save(tmp_path / "drift.pdf")), str(tmp_path / "out"), Options())
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    levels = [h["level"] for h in manifest["outline"]]
    assert levels == [0, 1, 1, 0, 1]


def test_headings_from_typography_without_outline(tmp_path: Path) -> None:
    pdf = PdfBuilder()
    cover = pdf.page()
    cover.text(72, 200, "Big Cover Title", size=30, bold=True)
    for n in (1, 2):
        page = pdf.page()
        page.text(72, 100, f"{n}. Chapter {n}", size=14, bold=True)
        page.text(72, 130, BODY)
        page.text(72, 170, f"{n}.1 Details", size=11, bold=True)
        page.text(72, 195, BODY)
    out_dir = convert(str(pdf.save(tmp_path / "plain.pdf")), str(tmp_path / "out"), Options())
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["outline_source"] == "typography"
    headings = [(h["title"], h["level"]) for h in manifest["outline"]]
    assert headings == [("1. Chapter 1", 0), ("1.1 Details", 1), ("2. Chapter 2", 0), ("2.1 Details", 1)]


def test_layout_keeps_subscripts_on_base_line(tmp_path: Path) -> None:
    pdf = PdfBuilder()
    page = pdf.page()
    page.grid([72, 200, 300], [100, 130])
    page.text(76, 110, "V", size=9)
    page.text(82, 114, "S", size=6)
    page.text(90, 110, "supply", size=9)
    page.text(205, 110, "36", size=9)
    path = pdf.save(tmp_path / "sub.pdf")
    with Document(str(path)) as doc:
        region = doc.page(0).regions[0]
    assert region.kind == RegionKind.TABLE
    lines = region.text.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("VS supply  ") and lines[0].endswith(" 36")


def test_cli(spec_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["convert", str(spec_pdf), "-o", str(tmp_path / "out"), "--split-pdf", "--page-images"]
    assert main(args) == 0
    out_dir = tmp_path / "out" / "example-spec"
    assert (out_dir / "pdf" / "002-2-limits.pdf").is_file()
    assert (out_dir / "images" / "p0001.png").is_file()
    assert "example-spec" in capsys.readouterr().out


def test_cli_reports_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "missing.pdf"
    assert main(["convert", str(missing), "-o", str(tmp_path / "out")]) == 1
    assert "missing.pdf" in capsys.readouterr().err


def test_failed_conversion_leaves_no_partial_output(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nnot really a pdf\n")
    with pytest.raises(Exception):  # noqa: B017 - any parser error
        convert(str(broken), str(tmp_path / "out"), Options())
    out_root = tmp_path / "out"
    leftovers = [p.name for p in out_root.iterdir()] if out_root.exists() else []
    assert leftovers == []


def test_refuses_non_empty_unmanaged_out_root(spec_pdf: Path, tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    out_root.mkdir()
    unrelated = out_root / "unrelated.txt"
    unrelated.write_text("keep me", encoding="utf-8")
    with pytest.raises(FileExistsError):
        convert(str(spec_pdf), str(out_root), Options())
    assert unrelated.read_text(encoding="utf-8") == "keep me"
    assert not (out_root / ".agentpdf").exists()

    out_dir = convert(str(spec_pdf), str(out_root), Options(force=True))
    assert out_dir.is_dir()
    assert unrelated.read_text(encoding="utf-8") == "keep me"
    assert (out_root / ".agentpdf").is_file()


def test_refuses_to_overwrite_foreign_manifest_dir(spec_pdf: Path, tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    foreign = out_root / "example-spec"
    foreign.mkdir(parents=True)
    manifest = foreign / "manifest.json"
    manifest.write_text(json.dumps({"name": "my-web-app"}), encoding="utf-8")
    with pytest.raises(FileExistsError):
        convert(str(spec_pdf), str(out_root), Options(force=True))
    assert manifest.is_file()
    assert json.loads(manifest.read_text(encoding="utf-8")) == {"name": "my-web-app"}


def test_refuses_to_overwrite_foreign_agents_md(spec_pdf: Path, tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    out_root.mkdir(parents=True)
    agents_md = out_root / "AGENTS.md"
    agents_md.write_text("# My Custom Project Agents\nDo not overwrite.", encoding="utf-8")
    with pytest.raises(FileExistsError):
        convert(str(spec_pdf), str(out_root), Options(force=True))
    assert agents_md.read_text(encoding="utf-8") == "# My Custom Project Agents\nDo not overwrite."


def test_same_stem_different_pdfs_get_distinct_directories(spec_pdf: Path, tmp_path: Path) -> None:
    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()
    p1 = dir1 / "spec.pdf"
    p1.write_bytes(spec_pdf.read_bytes())

    pdf2 = PdfBuilder(title="Second Spec")
    page = pdf2.page()
    page.text(72, 120, "1 Other Document", size=14, bold=True)
    page.text(72, 150, "Different text for second spec.")
    p2 = pdf2.save(dir2 / "spec.pdf")

    out_root = tmp_path / "out"
    assert main(["convert", str(p1), str(p2), "-o", str(out_root)]) == 0
    assert (out_root / "spec" / "manifest.json").is_file()
    assert (out_root / "spec-2" / "manifest.json").is_file()

    reconverted = convert(str(p2), str(out_root), Options())
    assert reconverted == out_root / "spec-2"
    assert not (out_root / "spec-3").exists()
