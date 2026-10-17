"""Characters from PDFium, grouped into lines, words and layout text.

PDFium follows the content stream order (usually the intended reading order,
including multi-column layouts), reconstructs word spacing and decodes glyphs
reliably.
"""

import ctypes
import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from .model import BBox, Line, bbox_union

# Characters PDFium uses to mark a hyphen at a line break.
_SOFT_HYPHENS = "\ufffe\xad\u0002"


@dataclass(slots=True)
class Char:
    text: str
    index: int  # position in PDFium's text stream
    bbox: BBox | None  # None for whitespace and line breaks
    size: float = 0.0
    bold: bool = False
    upright: bool = True
    soft_hyphen: bool = False


Word = list[Char]


def _display_transform(page: pdfium.PdfPage) -> Callable[[float, float, float, float], BBox]:
    """Map PDF-space boxes (left, bottom, right, top) to displayed page
    coordinates (top-left origin, after /Rotate)."""
    mx0, my0, mx1, my1 = page.get_mediabox()
    width, height = mx1 - mx0, my1 - my0
    rotation = page.get_rotation() % 360

    def point(x: float, y: float) -> tuple[float, float]:
        x, y = x - mx0, y - my0
        if rotation == 90:
            return y, x
        if rotation == 180:
            return width - x, y
        if rotation == 270:
            return height - y, width - x
        return x, height - y

    def transform(left: float, bottom: float, right: float, top: float) -> BBox:
        ax, ay = point(left, bottom)
        bx, by = point(right, top)
        return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))

    return transform


def read_chars(page: pdfium.PdfPage) -> list[Char]:
    """Read all characters of a page in stream order."""
    textpage = page.get_textpage()
    try:
        return _read_chars(page, textpage)
    finally:
        textpage.close()


def _read_chars(page: pdfium.PdfPage, textpage: pdfium.PdfTextPage) -> list[Char]:
    to_display = _display_transform(page)
    rotation = math.radians(page.get_rotation())
    raw = textpage.raw
    font_buf = ctypes.create_string_buffer(256)
    font_flags = ctypes.c_int()
    matrix = pdfium_c.FS_MATRIX()
    chars: list[Char] = []
    last_style = None
    font = ""
    # Characters are read by index (not by slicing get_text_range(), whose
    # string offsets can drift from character indices).
    for i in range(textpage.count_chars()):
        code = pdfium_c.FPDFText_GetUnicode(raw, i)
        ch = chr(code) if 0 < code < 0x110000 and not 0xD800 <= code < 0xE000 else "�"
        if ch == "\r":
            continue
        if ch in _SOFT_HYPHENS:
            chars.append(Char("-", i, None, soft_hyphen=True))
            continue
        if ch.isspace() or pdfium_c.FPDFText_IsGenerated(raw, i) == 1:
            chars.append(Char("\n" if ch == "\n" else " ", i, None))
            continue
        # The nominal font size is often 1 with the actual size applied via
        # the text matrix; the effective size accounts for both.
        pdfium_c.FPDFText_GetMatrix(raw, i, ctypes.byref(matrix))
        size = round(pdfium_c.FPDFText_GetFontSize(raw, i) * (math.hypot(matrix.c, matrix.d) or 1.0), 2)
        weight = pdfium_c.FPDFText_GetFontWeight(raw, i)
        if (size, weight) != last_style:
            pdfium_c.FPDFText_GetFontInfo(raw, i, font_buf, len(font_buf), ctypes.byref(font_flags))
            font = font_buf.value.decode("utf-8", "replace")
            last_style = (size, weight)
        # Upright as displayed, i.e. taking the page's /Rotate into account.
        angle = pdfium_c.FPDFText_GetCharAngle(raw, i)
        displayed = (angle + rotation) % (2 * math.pi)
        chars.append(
            Char(
                text=ch,
                index=i,
                bbox=to_display(*textpage.get_charbox(i, loose=True)),
                size=size,
                bold=weight >= 600 or "bold" in font.lower(),
                upright=angle < 0 or min(displayed, 2 * math.pi - displayed) < 0.05,
            )
        )
    return chars


def lines_from_chars(chars: list[Char]) -> list[Line]:
    """Group characters into lines at PDFium's line breaks, and where the
    geometry shows a break PDFium did not report (e.g. on rotated pages)."""
    lines: list[Line] = []
    current: list[Char] = []

    def flush() -> None:
        visible = [c for c in current if c.bbox is not None]
        text = " ".join("".join(c.text for c in current).split())
        if visible and text:
            lines.append(
                Line(
                    text=text,
                    bbox=_union(visible),
                    size=max(c.size for c in visible),
                    bold=sum(c.bold for c in visible) > len(visible) / 2,
                    order=visible[0].index,
                    soft_hyphen=current[-1].soft_hyphen,
                    tabbed=any(
                        b.bbox[0] - a.bbox[2] > 1.5 * max(a.size, b.size)
                        for a, b in itertools.pairwise(visible)
                    ),
                )
            )
        current.clear()

    prev: Char | None = None
    for c in chars:
        if c.text == "\n":
            flush()
            prev = None
            continue
        if c.bbox is not None:
            if prev is not None and current and _starts_new_line(prev, c):
                flush()
            prev = c
        current.append(c)
        if c.soft_hyphen:
            flush()
    flush()
    return lines


def _starts_new_line(prev: Char, c: Char) -> bool:
    if prev.upright != c.upright:
        return True
    if not c.upright:
        return False
    no_vertical_overlap = c.bbox[1] >= prev.bbox[3] or c.bbox[3] <= prev.bbox[1]
    moved_back = c.bbox[0] < prev.bbox[0] - max(prev.size, c.size)
    return no_vertical_overlap or moved_back


def words(chars: list[Char]) -> list[Word]:
    """Split visible characters into words at whitespace."""
    return [
        list(group) for visible, group in itertools.groupby(chars, lambda c: c.bbox is not None) if visible
    ]


def word_bbox(word: Word) -> BBox:
    return _union(word)


def _union(chars: list[Char]) -> BBox:
    bbox = chars[0].bbox
    for c in chars[1:]:
        bbox = bbox_union(bbox, c.bbox)
    return bbox


def layout_text(chars: list[Char], bbox: BBox) -> str:
    """Render characters as plain text that preserves horizontal positions.

    Upright characters are placed on a character grid; rows are formed from
    vertically overlapping characters so sub- and superscripts stay on their
    base line. Rotated text (axis labels etc.) is listed separately.
    """
    upright = [c for c in chars if c.bbox is not None and c.upright]
    rotated = [c for c in chars if c.bbox is not None and not c.upright]
    out = _grid_rows(upright, bbox) if upright else []
    if rotated:
        out += ["", "[rotated text] " + " | ".join(_runs(rotated))]
    indent = min((len(s) - len(s.lstrip()) for s in out if s.strip()), default=0)
    return "\n".join(s[indent:] for s in out).strip("\n")


def _grid_rows(chars: list[Char], bbox: BBox) -> list[str]:
    sizes = sorted(c.size for c in chars)
    body = sizes[len(sizes) // 2] or 8.0
    unit = body * 0.5  # approximate width of one monospace column

    # Tallest glyphs define the rows; smaller ones (subscripts) join a row
    # they overlap substantially.
    rows: list[tuple[float, float, list[Char]]] = []
    for c in sorted(chars, key=lambda c: -c.size):
        top, bottom = c.bbox[1], c.bbox[3]
        for row_top, row_bottom, members in rows:
            overlap = min(bottom, row_bottom) - max(top, row_top)
            if overlap > 0.4 * min(bottom - top, row_bottom - row_top):
                members.append(c)
                break
        else:
            rows.append((top, bottom, [c]))
    rows.sort(key=lambda r: r[0])

    out: list[str] = []
    prev_bottom = None
    for top, bottom, members in rows:
        if prev_bottom is not None and top - prev_bottom > body * 1.2:
            out.append("")
        prev_bottom = bottom
        members.sort(key=lambda c: c.bbox[0])
        line: list[str] = []
        prev: Char | None = None
        for c in members:
            if prev is not None and c.index == prev.index + 1 and c.bbox[0] - prev.bbox[2] < c.size * 0.2:
                # Continuation of a word: glyph widths vary, so append
                # directly instead of snapping to the grid.
                line.append(c.text)
            else:
                col = max(round((c.bbox[0] - bbox[0]) / unit), len(line) + (1 if line else 0))
                line.extend(" " * (col - len(line)))
                line.append(c.text)
            prev = c
        out.append("".join(line).rstrip())
    return out


def _runs(chars: list[Char]) -> list[str]:
    """Join characters that are consecutive in the stream into labels."""
    labels: list[str] = []
    current: list[str] = []
    last_index = 0
    for c in sorted(chars, key=lambda c: c.index):
        if current:
            step = c.index - last_index
            if step > 2:
                labels.append("".join(current).strip())
                current = []
            elif step == 2:  # separated by a space
                current.append(" ")
        current.append(c.text)
        last_index = c.index
    if current:
        labels.append("".join(current).strip())
    return labels
