import json
from pathlib import Path

import pytest

from agentpdf.cli import main
from agentpdf.convert import Options, convert, render_pages
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


def test_connected_schematic_stays_single_figure(tmp_path: Path) -> None:
    pdf = PdfBuilder()
    page = pdf.page()
    page.text(72, 120, "8 Schematics", size=14, bold=True)
    # Closed outer schematic frame (>50% page width) with internal IC grid,
    # empty component boxes, parallel wire buses and vertical signal crossings.
    page.rect(72, 160, 468, 220)
    page.grid([180, 280], [200, 230, 260, 290])
    page.text(195, 210, "U1", size=8)
    page.text(195, 240, "VDD", size=8)
    page.text(195, 270, "GND", size=8)
    page.rect(350, 215, 24, 10)  # empty resistor symbol
    for y in (215, 245, 275):
        page.line(100, y, 480, y)
    for x in (130, 320, 420):
        page.line(x, 190, x, 340)
    page.curve(430, 250, 40, 25)
    page.text(72, 400, "Figure 8-1: Reference Schematic")

    path = pdf.save(tmp_path / "schematic.pdf")
    with Document(str(path)) as doc:
        p = doc.page(0)
    assert len(p.regions) == 1
    assert p.regions[0].kind == RegionKind.FIGURE
    assert p.regions[0].bbox[0] <= 73 and p.regions[0].bbox[2] >= 539
    assert p.regions[0].bbox[1] <= 161 and p.regions[0].bbox[3] >= 379


def test_horizontal_rule_table_vs_underlined_list(tmp_path: Path) -> None:
    pdf = PdfBuilder()
    # Page 1: genuine 3-rule booktabs table with multi-column values.
    p1 = pdf.page()
    for y in (180, 200, 245):
        p1.line(72, y, 520, y)
    p1.text(76, 185, "Parameter", size=9, bold=True)
    p1.text(300, 185, "Typical", size=9, bold=True)
    p1.text(430, 185, "Unit", size=9, bold=True)
    p1.text(76, 206, "Open-loop gain", size=9)
    p1.text(300, 206, "120", size=9)
    p1.text(430, 206, "dB", size=9)
    p1.text(76, 224, "Bandwidth", size=9)
    p1.text(300, 224, "50", size=9)
    p1.text(430, 224, "MHz", size=9)

    # Page 2: single-column underlined hyperlink list of varying widths.
    p2 = pdf.page()
    links = [
        (220, "Hardware Design Guidelines"),
        (275, "Technical Reference Manual"),
        (195, "Product Errata Sheet"),
        (250, "Compliance Certificates"),
    ]
    for i, (w, label) in enumerate(links):
        top = 180 + i * 28
        p2.text(80, top, label, size=10)
        p2.line(80, top + 12, 80 + w, top + 12)

    path = pdf.save(tmp_path / "rules.pdf")
    with Document(str(path)) as doc:
        assert [r.kind for r in doc.page(0).regions] == [RegionKind.TABLE]
        assert doc.page(1).regions == []


def test_figure_absorbs_callouts_and_rotated_dims_not_prose(tmp_path: Path) -> None:
    pdf = PdfBuilder()
    page = pdf.page()
    page.text(72, 140, "3 Pin Layout", size=14, bold=True)
    page.text(180, 178, "continues here.", size=9)
    # Package outline + 12 pin pads so it forms a vector figure.
    page.rect(190, 200, 140, 120)
    for i in range(6):
        page.rect(190, 210 + i * 16, 10, 8)
        page.rect(320, 210 + i * 16, 10, 8)
    page.text(245, 255, "TOP", size=8)
    # Perimeter pin labels (8-10 pt outside the graphic box) + 90-deg CAD dimension.
    page.text(162, 210, "GND", size=8)
    page.text(164, 226, "3V3", size=8)
    page.text(339, 210, "IO14", size=8)
    page.text(339, 226, "OUT A", size=8)
    page.rotated_text(339, 290, "12.5", size=8)
    # Caption and prose right below must remain body text, not figure labels.
    page.text(190, 328, "Figure 3-1: Pinout", size=9)
    page.text(72, 355, "Place decoupling capacitors close to the supply pins.", size=10)
    pdf.bookmark("3 Pin Layout", 0)

    out_dir = convert(str(pdf.save(tmp_path / "pinout.pdf")), str(tmp_path / "out"), Options())
    text = _section_text(out_dir)
    body_outside_fence = text.split("```text\n")[0] + text.split("```\n", 1)[1]
    assert "# 3 Pin Layout" in body_outside_fence
    assert "continues here." in body_outside_fence
    assert "Figure 3-1: Pinout" in body_outside_fence
    assert "Place decoupling capacitors" in body_outside_fence
    assert "[rotated text] 12.5" in text
    normalized = " ".join(text.split())
    for pin in ("GND", "3V3", "IO14", "OUT A", "12.5"):
        assert pin not in body_outside_fence
        assert pin in normalized


def test_render_pages_cli_and_api(spec_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_root = tmp_path / "out"
    out_dir = convert(str(spec_pdf), str(out_root), Options())
    assert not (out_dir / "images" / "p0001.png").exists()
    assert not (out_dir / "images" / "p0003.png").exists()

    assert main(["render", "-d", str(out_root), "example-spec", "1,3"]) == 0
    out = capsys.readouterr().out
    assert "p0001.png" in out and "p0003.png" in out
    assert (out_dir / "images" / "p0001.png").is_file()
    assert not (out_dir / "images" / "p0002.png").exists()
    assert (out_dir / "images" / "p0003.png").is_file()

    pages_md = (out_dir / "PAGES.md").read_text(encoding="utf-8")
    assert "[full page](images/p0001.png)" in pages_md
    assert "[full page](images/p0003.png), [figure 1](images/p0003-figure-1.png)" in pages_md

    # Moved source PDF works with explicit --pdf; out-of-range page fails cleanly.
    moved = tmp_path / "moved.pdf"
    spec_pdf.rename(moved)
    written = render_pages("example-spec", "2-3", out_root, pdf_path=moved)
    assert [p.name for p in written] == ["p0002.png", "p0003.png"]
    assert (out_dir / "images" / "p0002.png").is_file()

    assert main(["render", "-d", str(out_root), "--pdf", str(moved), "example-spec", "99"]) == 1
    assert "out of range" in capsys.readouterr().err


def test_html_wrapper_and_non_pdf_rejected_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    html_pdf = tmp_path / "datasheet.pdf"
    html_pdf.write_text("<!DOCTYPE html>\n<html><body>Sign in to download</body></html>\n", encoding="utf-8")
    out_root = tmp_path / "out"

    assert main(["convert", str(html_pdf), "-o", str(out_root)]) == 1
    err = capsys.readouterr().err
    assert "HTML page, not a PDF" in err
    assert not out_root.exists()

    empty_pdf = tmp_path / "empty.pdf"
    empty_pdf.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        convert(empty_pdf, out_root, Options())

    junk_pdf = tmp_path / "junk.pdf"
    junk_pdf.write_bytes(b'{"error": "not found"}')
    with pytest.raises(ValueError, match="not a PDF"):
        convert(junk_pdf, out_root, Options())


def test_inherited_mediabox_aligns_text_and_graphics(tmp_path: Path) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, FloatObject, NameObject

    # Build a page with a ruled table, then move /MediaBox (A4 height 842) onto
    # the parent /Pages node, as LaTeX/pdftex does.
    base = PdfBuilder()
    p = base.page()
    p.grid([72, 220, 360], [180, 200, 220])
    p.text(76, 185, "Pin", size=9)
    p.text(225, 185, "Signal", size=9)
    p.text(76, 205, "1", size=9)
    p.text(225, 205, "GND", size=9)
    raw_path = base.save(tmp_path / "base.pdf")

    reader = PdfReader(raw_path)
    writer = PdfWriter()
    page = writer.add_page(reader.pages[0])
    del page[NameObject("/MediaBox")]
    pages_root = writer._root_object[NameObject("/Pages")].get_object()
    pages_root[NameObject("/MediaBox")] = ArrayObject(
        [FloatObject(0), FloatObject(0), FloatObject(595.28), FloatObject(841.89)]
    )
    a4_path = tmp_path / "inherited_a4.pdf"
    with open(a4_path, "wb") as f:
        writer.write(f)

    with Document(str(a4_path)) as doc:
        extracted = doc.page(0)
    assert len(extracted.regions) == 1
    assert "Pin" in extracted.regions[0].text
    assert "GND" in extracted.regions[0].text
    assert extracted.lines == []
