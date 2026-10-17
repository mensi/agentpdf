"""Find headings: from the PDF's bookmarks, or from typography without them."""

import re
from collections import Counter

from .model import OutlineEntry
from .paragraphs import Block
from .util import normalize

_BULLET = re.compile(r"^[•▪◦●○■□➢►\-–—*]\s")
_NUMBERING = re.compile(r"^((?:\d+|[A-Z])(?:\.\d+)+|\d+)\.?(?:\s|$)")


def repair_outline_levels(outline: list[OutlineEntry]) -> list[OutlineEntry]:
    """Derive nesting from section numbers when the bookmarks contradict them.

    Some PDFs have bookmark trees whose nesting drifts (e.g. "1.2" nested
    under "1.1"). When most entries are numbered and the nesting disagrees
    with the numbering, levels are taken from the numbering; unnumbered
    entries are placed at the top level.
    """
    depths = [_numbering_depth(e.title) for e in outline]
    numbered = [(e, d) for e, d in zip(outline, depths, strict=True) if d is not None]
    if len(numbered) < max(3, len(outline) * 0.6):
        return outline
    min_level = min(e.level for e, _ in numbered)
    mismatches = sum(1 for e, d in numbered if e.level - min_level != d - 1)
    if mismatches <= len(numbered) * 0.1:
        return outline
    return [OutlineEntry(e.title, (d - 1) if d else 0, e.page) for e, d in zip(outline, depths, strict=True)]


def _numbering_depth(title: str) -> int | None:
    match = _NUMBERING.match(title)
    return match.group(1).count(".") + 1 if match else None


def mark_outline_headings(blocks: list[Block], outline: list[OutlineEntry]) -> list[Block]:
    """Mark the block that starts each outline entry as a heading.

    Headings are searched on the entry's target page and, as bookmark
    targets are sometimes off by one, on the neighbouring pages. Entries
    whose heading is not found get a heading inserted at the top of their
    page. Returns the new block list.
    """
    by_page: dict[int, list[Block]] = {}
    for b in blocks:
        by_page.setdefault(b.page, []).append(b)

    missing: dict[int, list[Block]] = {}
    split_tails: dict[int, Block] = {}  # id(heading block) -> text split off it
    absorbed: set[Block] = set()
    for entry in outline:
        if entry.page is None:
            continue
        if not any(
            _mark_heading(entry, by_page.get(page, []), absorbed, split_tails)
            for page in (entry.page, entry.page - 1, entry.page + 1)
        ):
            missing.setdefault(entry.page, []).append(
                Block(entry.page, -1.0, entry.title, heading_level=entry.level)
            )

    result: list[Block] = []
    for b in blocks:
        if b.page in missing:
            result.extend(missing.pop(b.page))
        if b in absorbed:
            continue
        result.append(b)
        if id(b) in split_tails:
            result.append(split_tails[id(b)])
    for page_blocks in missing.values():  # pages without any text
        result.extend(page_blocks)
    result.sort(key=lambda b: b.page)  # stable: keeps the order within a page
    return result


def _mark_heading(
    entry: OutlineEntry, blocks: list[Block], absorbed: set[Block], split_tails: dict[int, Block]
) -> bool:
    """Find and mark the heading for ``entry`` among a page's blocks."""
    title = normalize(entry.title)
    for i, block in enumerate(blocks):
        if block.region is not None or block.heading_level is not None or block in absorbed:
            continue
        text = normalize(block.text)
        if not (len(text) >= 3 and len(title) >= 3 and (text.startswith(title) or title.startswith(text))):
            continue
        block.heading_level = entry.level
        if text == title:
            block.text = entry.title  # the bookmark's spelling is authoritative
        elif len(text) > len(title) + 3:
            # The heading was merged with the paragraph after it.
            rest = _text_after(block.text, len(title))
            if rest:
                block.text = block.text[: len(block.text) - len(rest)].rstrip()
                split_tails[id(block)] = Block(
                    block.page, block.order + 0.1, rest.lstrip(), size=block.size, bold=block.bold
                )
        elif len(title) > len(text):
            # The heading wraps onto the following paragraphs.
            for following in blocks[i + 1 :]:
                combined = text + normalize(following.text)
                if following.region is not None or len(text) >= len(title) or not title.startswith(combined):
                    break
                text = combined
                absorbed.add(following)
            block.text = entry.title
        return True
    return False


def _text_after(text: str, alnum_count: int) -> str:
    """The part of ``text`` after its first ``alnum_count`` alphanumerics."""
    seen = 0
    for i, ch in enumerate(text):
        seen += len(normalize(ch))
        if seen >= alnum_count:
            return text[i + 1 :]
    return ""


def detect_headings(blocks: list[Block]) -> list[OutlineEntry]:
    """Infer headings from typography when the PDF has no bookmarks.

    Short paragraphs set larger or bolder than the body text are headings;
    their styles are ranked by size to assign levels. Styles used on a single
    page only (title pages) are ignored.
    """
    body = _body_size(blocks)
    candidates = [
        b
        for b in blocks
        if b.region is None
        and len(b.text) <= 120
        and not b.text.rstrip().endswith((".", ",", ":", ";"))
        and (b.size >= body * 1.15 or (b.bold and b.size >= body * 0.95))
        and not _BULLET.match(b.text)
        and any(ch.isalpha() for ch in b.text)
    ]

    def style(b: Block) -> tuple[int, bool]:
        return (round(b.size), b.bold)

    pages_by_style: dict[tuple[int, bool], set[int]] = {}
    for b in candidates:
        pages_by_style.setdefault(style(b), set()).add(b.page)
    candidates = [b for b in candidates if len(pages_by_style[style(b)]) >= 2]

    styles = sorted({style(b) for b in candidates}, key=lambda s: (-s[0], not s[1]))
    level_of = {s: min(i, 5) for i, s in enumerate(styles)}
    for b in candidates:
        b.heading_level = level_of[style(b)]
    return [OutlineEntry(b.text, b.heading_level, b.page) for b in candidates]


def _body_size(blocks: list[Block]) -> float:
    sizes: Counter[float] = Counter()
    for b in blocks:
        if b.region is None:
            sizes[round(b.size, 1)] += len(b.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0
