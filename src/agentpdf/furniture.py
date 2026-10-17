"""Remove page furniture: running headers and footers, repeated logos and
printed tables of contents."""

import re
from collections import Counter

from .model import Line, OutlineEntry, Page, Region
from .util import normalize

# Lines and regions in these top/bottom fractions of the page are candidates
# for running headers and footers.
_ZONE = 0.12
# A candidate repeated on at least this fraction of pages is furniture.
_MIN_SHARE = 0.3

_TOC_PAGE_NUMBER = re.compile(r"[\s.…·_]*\d+\s*$")


def _key(text: str) -> str:
    # Word order and numbers are ignored: page numbers change, and mirrored
    # odd/even layouts reorder the same header/footer items.
    return " ".join(sorted(re.sub(r"\d+", "#", text.lower()).split()))


def _region_key(region: Region) -> str:
    # Position is ignored for the same reason (logos switch sides).
    width = round((region.bbox[2] - region.bbox[0]) / 5)
    height = round((region.bbox[3] - region.bbox[1]) / 5)
    return f"{region.kind}:{width}x{height}:{_key(region.text)}"


def _in_zone(top: float, bottom: float, page: Page) -> bool:
    return bottom <= page.height * _ZONE or top >= page.height * (1 - _ZONE)


def remove_furniture(pages: list[Page]) -> None:
    """Drop running headers, footers and repeated decorations (logos)."""
    if len(pages) < 3:
        return
    threshold = max(2, int(len(pages) * _MIN_SHARE + 0.999))

    def zone_lines(page: Page) -> list[Line]:
        return [ln for ln in page.lines if _in_zone(ln.bbox[1], ln.bbox[3], page)]

    line_counts: Counter[str] = Counter()
    region_counts: Counter[str] = Counter()
    # Running headers often include a varying part (chapter title): lines at
    # the same height whose words mostly recur on many pages also count.
    words_at: dict[int, Counter[str]] = {}
    for page in pages:
        line_counts.update({_key(ln.text) for ln in zone_lines(page)})
        region_counts.update({_region_key(r) for r in page.regions if _in_zone(r.bbox[1], r.bbox[3], page)})
        for ln in zone_lines(page):
            words_at.setdefault(round(ln.bbox[1] / 3), Counter()).update(set(_key(ln.text).split()))

    def is_furniture(ln: Line, page: Page) -> bool:
        if not _in_zone(ln.bbox[1], ln.bbox[3], page):
            return False
        if line_counts[_key(ln.text)] >= threshold:
            return True
        words = _key(ln.text).split()
        common = words_at.get(round(ln.bbox[1] / 3), Counter())
        return bool(words) and sum(common[w] >= threshold for w in words) >= len(words) * 0.5

    for page in pages:
        page.lines = [ln for ln in page.lines if not is_furniture(ln, page)]
        page.regions = [
            r
            for r in page.regions
            if not (_in_zone(r.bbox[1], r.bbox[3], page) and region_counts[_region_key(r)] >= threshold)
        ]


def drop_printed_toc(pages: list[Page], outline: list[OutlineEntry]) -> list[int]:
    """Remove the text of printed table-of-contents pages.

    With bookmarks available, a printed table of contents duplicates
    INDEX.md and matches every search. A page is a contents page when most
    of its lines are outline titles followed by a page number. Returns the
    numbers of the pages whose text was removed.
    """
    titles = {normalize(e.title) for e in outline if len(normalize(e.title)) >= 3}
    if not titles:
        return []
    dropped = []
    for page in pages:
        if len(page.lines) < 5:
            continue
        hits = sum(
            1
            for line in page.lines
            if _TOC_PAGE_NUMBER.search(line.text) and normalize(_TOC_PAGE_NUMBER.sub("", line.text)) in titles
        )
        if hits >= len(page.lines) * 0.5:
            page.lines = []
            page.regions = []
            dropped.append(page.number)
    return dropped
