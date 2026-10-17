"""Group lines into paragraphs and interleave them with tables and figures."""

import re
from collections import Counter
from dataclasses import dataclass

from .model import Line, Page, Region

_LIST_MARKER = re.compile(r"^([•▪◦●○■□➢►\-–—*]|\(?\d{1,2}[.)]|\(?[a-z][.)])\s")

# Placeholder for a hyphen at a line break, resolved by resolve_hyphens().
_BREAK_HYPHEN = "\x00"
_WORD = r"[^\W_][\w\-]*"


@dataclass(eq=False)
class Block:
    """A unit of output in reading order: paragraph, heading or region."""

    page: int
    order: float
    text: str = ""
    heading_level: int | None = None  # 0-based, for headings
    region: Region | None = None
    size: float = 0.0
    bold: bool = False

    @property
    def chars(self) -> int:
        return len(self.text) + (len(self.region.text) if self.region else 0)


def page_blocks(page: Page) -> list[Block]:
    """Group a page's lines into paragraphs, interleaved with its regions.

    Wrapped lines are joined so phrases are searchable across line breaks;
    intended line breaks (lists, coding tables) are kept.
    """
    blocks: list[Block] = []
    regions = sorted(page.regions, key=lambda r: r.order)
    prev: Line | None = None
    prev_right = 0.0
    for line, right in zip(page.lines, _column_rights(page.lines), strict=True):
        # Regions positioned before this line interrupt the paragraph.
        while regions and regions[0].order < line.order:
            region = regions.pop(0)
            blocks.append(Block(page.number, region.order, region=region))
            prev = None
        if prev is not None and _continues(prev, line):
            blocks[-1].text = _join(blocks[-1].text, line, prev, _intended_break(prev, line, prev_right))
        else:
            blocks.append(Block(page.number, line.order, line.text, size=line.size, bold=line.bold))
        prev, prev_right = line, right
    blocks.extend(Block(page.number, r.order, region=r) for r in regions)
    return blocks


def _continues(prev: Line, line: Line) -> bool:
    """Whether ``line`` continues the paragraph that ``prev`` belongs to."""
    if abs(prev.size - line.size) > 0.6 or prev.bold != line.bold:
        return False
    gap = line.bbox[1] - prev.bbox[3]
    if gap < -prev.size * 0.3 or gap > prev.size * 0.8:
        return False  # column change, overlapping text or paragraph spacing
    return not _LIST_MARKER.match(line.text)


def _column_rights(lines: list[Line], window: int = 6) -> list[float]:
    """Estimate the right edge of the text column at each line: the furthest
    that nearby lines starting at (roughly) the same left edge extend."""
    rights = []
    for i, line in enumerate(lines):
        nearby = lines[max(0, i - window) : i + window + 1]
        rights.append(
            max(
                (o.bbox[2] for o in nearby if abs(o.bbox[0] - line.bbox[0]) < 2 * line.size),
                default=line.bbox[2],
            )
        )
    return rights


def _intended_break(prev: Line, line: Line, right: float) -> bool:
    """Whether the break after ``prev`` is intended rather than wrapping.

    Lines with tab-stop gaps are tabular. Otherwise, text wraps only when the
    next word does not fit: if the first word of ``line`` would have fit
    after ``prev``, the author broke the line.
    """
    if prev.tabbed or line.tabbed:
        return True
    first_word = line.text.split(" ", 1)[0]
    char_width = (line.bbox[2] - line.bbox[0]) / max(len(line.text), 1)
    return right - prev.bbox[2] > (len(first_word) + 1) * char_width


def _join(text: str, line: Line, prev: Line, hard_break: bool) -> str:
    if prev.soft_hyphen and text.endswith("-"):
        return text[:-1] + _BREAK_HYPHEN + line.text
    return text + ("\n" if hard_break else " ") + line.text


def resolve_hyphens(blocks: list[Block]) -> None:
    """Decide for each line-break hyphen whether it belongs to the word.

    "manu-|facturing" becomes "manufacturing", but "e-|mail" stays "e-mail":
    whichever form occurs elsewhere in the document wins. Without evidence,
    the hyphen is dropped before a lowercase continuation.
    """
    pattern = re.compile(rf"({_WORD})?{_BREAK_HYPHEN}({_WORD})?")
    vocabulary: Counter[str] = Counter()
    for b in blocks:
        # The broken words themselves are not evidence.
        vocabulary.update(w.lower() for w in re.findall(_WORD, pattern.sub(" ", b.text)))
        if b.region is not None:
            vocabulary.update(w.lower() for w in re.findall(_WORD, b.region.text))

    def decide(match: re.Match[str]) -> str:
        left, right = match.group(1) or "", match.group(2) or ""
        joined, hyphenated = left + right, f"{left}-{right}"
        if vocabulary[hyphenated.lower()] > vocabulary[joined.lower()]:
            return hyphenated
        if vocabulary[joined.lower()] > 0:
            return joined
        # Both halves are words of their own ("client-server").
        if vocabulary[left.lower()] > 0 and vocabulary[right.lower()] > 0:
            return hyphenated
        return joined if right[:1].islower() else hyphenated

    for b in blocks:
        if _BREAK_HYPHEN in b.text:
            b.text = pattern.sub(decide, b.text)
