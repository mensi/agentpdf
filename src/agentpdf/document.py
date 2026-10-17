"""Page extraction: running text in reading order, plus tables and figures."""

import heapq
from collections.abc import Iterator
from typing import Self

import pdfplumber
import pypdfium2 as pdfium

from .chars import Char, layout_text, lines_from_chars, read_chars, word_bbox, words
from .model import BBox, Line, OutlineEntry, Page, Region, RegionKind
from .regions import PlacedRegion, locate_regions


class Document:
    """An open PDF, read with PDFium (text, rendering) and pdfplumber
    (graphics for region detection)."""

    def __init__(self, path: str):
        self.path = path
        self.pdfium = pdfium.PdfDocument(path)
        try:
            self.plumber = pdfplumber.open(path)
        except BaseException:
            self.pdfium.close()
            raise

    def close(self) -> None:
        self.plumber.close()
        self.pdfium.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __len__(self) -> int:
        return len(self.pdfium)

    @property
    def metadata(self) -> dict[str, str]:
        return dict(self.pdfium.get_metadata_dict(skip_empty=True))

    def outline(self) -> list[OutlineEntry]:
        entries = []
        for bookmark in self.pdfium.get_toc():
            dest = bookmark.get_dest()
            index = dest.get_index() if dest is not None else None
            entries.append(
                OutlineEntry(
                    title=" ".join(bookmark.get_title().split()),
                    level=bookmark.level,
                    page=index + 1 if index is not None else None,
                )
            )
        return entries

    def pages(self) -> Iterator[Page]:
        for index in range(len(self)):
            yield self.page(index)

    def page(self, index: int) -> Page:
        pdf_page = self.pdfium[index]
        plumber_page = self.plumber.pages[index]
        try:
            chars = read_chars(pdf_page)
            regions = locate_regions(plumber_page, words(chars))
            page = Page(
                number=index + 1,
                width=float(plumber_page.width),
                height=float(plumber_page.height),
                has_text_layer=any(c.bbox is not None for c in chars),
            )
            page.lines, page.regions = _split_text(chars, regions)
            _apply_reading_order(page)
            return page
        finally:
            # Release parsed page objects; large documents would otherwise
            # keep every page in memory.
            plumber_page.close()
            pdf_page.close()

    def render(self, index: int, path: str, dpi: int, bbox: BBox | None = None) -> None:
        """Render a page, or a region of it, to PNG."""
        pdf_page = self.pdfium[index]
        try:
            crop = (0.0, 0.0, 0.0, 0.0)
            if bbox is not None:
                width, height = pdf_page.get_size()
                pad = 4.0
                x0, top, x1, bottom = bbox
                crop = (
                    max(0.0, x0 - pad),
                    max(0.0, height - bottom - pad),
                    max(0.0, width - x1 - pad),
                    max(0.0, top - pad),
                )
            image = pdf_page.render(scale=dpi / 72, crop=crop).to_pil()
        finally:
            pdf_page.close()
        image.save(path, optimize=True)


def _split_text(chars: list[Char], placed: list[PlacedRegion]) -> tuple[list[Line], list[Region]]:
    """Divide the page text between running text and regions.

    Whole words go to the region containing the word's center, so a region
    edge never splits a word.
    """
    owner_of: dict[int, int] = {}
    for word in words(chars):
        x0, top, x1, bottom = word_bbox(word)
        cx, cy = (x0 + x1) / 2, (top + bottom) / 2
        for i, (_, b) in enumerate(placed):
            if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
                owner_of.update((c.index, i) for c in word)
                break

    owned: list[list[Char]] = [[] for _ in placed]
    running: list[Char] = []
    for c in chars:
        owner = owner_of.get(c.index) if c.bbox is not None else None
        if owner is None:
            running.append(c)
        else:
            owned[owner].append(c)
            # Keep a line break where region text interrupts the running text.
            if running and running[-1].text != "\n":
                running.append(Char("\n", c.index, None))

    lines = lines_from_chars(running)
    regions: list[Region] = []
    counters: dict[RegionKind, int] = {}
    for (kind, bbox), region_chars in zip(placed, owned, strict=True):
        counters[kind] = counters.get(kind, 0) + 1
        region = Region(kind, bbox, counters[kind], layout_text(region_chars, bbox))
        if region_chars:
            region.order = min(c.index for c in region_chars)
        else:
            # No text inside: place before the first line below the region.
            below = [ln.order for ln in lines if ln.bbox[1] >= bbox[1]]
            region.order = min(below) - 0.5 if below else float("inf")
        regions.append(region)
    return lines, regions


def _apply_reading_order(page: Page) -> None:
    """Order lines and regions by stream position, corrected by geometry.

    The content stream order is a good reading order for most PDFs (and
    handles multi-column layouts), but some producers emit text out of order,
    e.g. a page title drawn last. Of two items that overlap horizontally,
    the upper one must come first; otherwise stream order is kept.
    """
    items: list[Line | Region] = [*page.lines, *page.regions]
    n = len(items)
    successors: list[list[int]] = [[] for _ in range(n)]
    indegree = [0] * n
    for i, a in enumerate(items):
        for j, b in enumerate(items):
            x_overlap = min(a.bbox[2], b.bbox[2]) - max(a.bbox[0], b.bbox[0])
            if i != j and x_overlap > 0 and a.bbox[3] <= b.bbox[1] + 1.0:  # a above b
                successors[i].append(j)
                indegree[j] += 1
    heap = [(items[i].order, i) for i in range(n) if indegree[i] == 0]
    heapq.heapify(heap)
    rank = 0
    while heap:
        _, i = heapq.heappop(heap)
        for j in successors[i]:
            indegree[j] -= 1
            if indegree[j] == 0:
                heapq.heappush(heap, (items[j].order, j))
        items[i].order = rank
        rank += 1
    page.lines.sort(key=lambda ln: ln.order)
    page.regions.sort(key=lambda r: r.order)
