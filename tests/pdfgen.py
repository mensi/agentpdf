"""Minimal PDF builder for tests: positioned text, lines and outlines."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

WIDTH, HEIGHT = 612.0, 792.0


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@dataclass
class PageBuilder:
    ops: list[str] = field(default_factory=list)

    def text(self, x: float, top: float, text: str, size: float = 10, bold: bool = False) -> Self:
        """Draw text with its top edge at ``top`` (top-left origin)."""
        font = "/F2" if bold else "/F1"
        y = HEIGHT - top - size
        self.ops.append(f"BT {font} {size} Tf {x} {y} Td ({_escape(text)}) Tj ET")
        return self

    def line(self, x0: float, top0: float, x1: float, top1: float) -> Self:
        self.ops.append(f"0.5 w {x0} {HEIGHT - top0} m {x1} {HEIGHT - top1} l S")
        return self

    def grid(self, xs: list[float], tops: list[float]) -> Self:
        """Draw a ruled grid with the given column and row boundaries."""
        for x in xs:
            self.line(x, tops[0], x, tops[-1])
        for top in tops:
            self.line(xs[0], top, xs[-1], top)
        return self

    def curve(self, x: float, top: float, w: float, h: float) -> Self:
        """Draw a wavy line, as found in plotted figures."""
        y = HEIGHT - top - h
        self.ops.append(f"1 w {x} {y} m {x + w / 3} {y + h} {x + 2 * w / 3} {y - h / 2} {x + w} {y + h} c S")
        return self


@dataclass
class PdfBuilder:
    pages: list[PageBuilder] = field(default_factory=list)
    # (title, page index, parent outline index or None)
    outline: list[tuple[str, int, int | None]] = field(default_factory=list)
    title: str | None = None

    def page(self) -> PageBuilder:
        page = PageBuilder()
        self.pages.append(page)
        return page

    def bookmark(self, title: str, page_index: int, parent: int | None = None) -> int:
        self.outline.append((title, page_index, parent))
        return len(self.outline) - 1

    def save(self, path: Path) -> Path:
        writer = PdfWriter()
        fonts = DictionaryObject()
        for name, base in (("/F1", "/Helvetica"), ("/F2", "/Helvetica-Bold")):
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject(base),
                    NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
                }
            )
            fonts[NameObject(name)] = writer._add_object(font)
        for builder in self.pages:
            page = writer.add_blank_page(WIDTH, HEIGHT)
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
            stream = DecodedStreamObject()
            stream.set_data("\n".join(builder.ops).encode("latin-1"))
            page[NameObject("/Contents")] = writer._add_object(stream)
        items = []
        for title, page_index, parent in self.outline:
            items.append(
                writer.add_outline_item(
                    title, page_index, parent=items[parent] if parent is not None else None
                )
            )
        if self.title:
            writer.add_metadata({"/Title": self.title})
        with open(path, "wb") as f:
            writer.write(f)
        return path
