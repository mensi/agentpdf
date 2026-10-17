"""Locate tables and figures on a page.

Detection uses pdfplumber's access to ruling lines, vector graphics and
images. Regions are not interpreted further: they are rendered as images and
transcribed as layout text.
"""

import pdfplumber

from .chars import Word, word_bbox
from .model import BBox, RegionKind, bbox_area, bbox_contains, bbox_overlaps, bbox_union, overlap_ratio

# Graphics clusters smaller than this (square points) are decoration
# (bullets, icons, underlines) rather than figures.
_MIN_FIGURE_AREA = 1500.0
# Graphics within this distance (points) of each other form one cluster.
_CLUSTER_GAP = 8.0
# Words within this distance of a region are labels belonging to it.
_LABEL_MARGIN = 3.0

PlacedRegion = tuple[RegionKind, BBox]


def locate_regions(page: pdfplumber.page.Page, page_words: list[Word]) -> list[PlacedRegion]:
    """Find the tables and figures on a page, including their labels.

    Returns regions sorted top to bottom.
    """
    boxes = [(w, word_bbox(w)) for w in page_words]
    grown = [(kind, _absorb_labels(bbox, boxes)) for kind, bbox in _detect(page)]
    # Absorbing labels can make regions overlap; merge those that largely do.
    merged: list[PlacedRegion] = []
    for kind, bbox in grown:
        for i, (other_kind, other) in enumerate(merged):
            if overlap_ratio(bbox, other) > 0.3:
                k = RegionKind.FIGURE if RegionKind.FIGURE in (kind, other_kind) else kind
                merged[i] = (k, bbox_union(bbox, other))
                break
        else:
            merged.append((kind, bbox))
    return sorted(merged, key=lambda r: (r[1][1], r[1][0]))


def _obj_bbox(obj: dict) -> BBox:
    return (obj["x0"], obj["top"], obj["x1"], obj["bottom"])


def _detect(page: pdfplumber.page.Page) -> list[PlacedRegion]:
    curves = [_obj_bbox(o) for o in page.curves]
    images = [_obj_bbox(o) for o in page.images]

    def is_figure(b: BBox) -> bool:
        # Plotted data and pictures are figures even when a grid makes them
        # look like a ruled table.
        rich = [g for g in curves + images if bbox_overlaps(g, b, -1.0)]
        return sum(bbox_area(g) for g in rich) > 0.1 * bbox_area(b) or len(rich) > 4

    regions: list[PlacedRegion] = []

    # Ruled tables, largest first so that nested grids (chart gridlines,
    # tables used to lay out figures) are handled by their container. A
    # ruled grid with graphical content is a layout of figures: each
    # graphical cell becomes a figure of its own.
    for table in sorted(page.find_tables(), key=lambda t: -bbox_area(tuple(t.bbox))):
        bbox = tuple(table.bbox)
        if any(bbox_contains(r, bbox) for _, r in regions):
            continue
        if not is_figure(bbox):
            regions.append((RegionKind.TABLE, bbox))
            continue
        cells = [tuple(c) for c in table.cells if c is not None and bbox_area(tuple(c)) >= _MIN_FIGURE_AREA]
        for cell in [c for c in cells if is_figure(c)] or [bbox]:
            regions.append((RegionKind.FIGURE, cell))

    for bbox in _rule_tables(page):
        if not any(bbox_overlaps(bbox, r) for _, r in regions):
            regions.append((RegionKind.TABLE, bbox))

    def inside_region(b: BBox) -> bool:
        return any(bbox_overlaps(b, r, -1.0) for _, r in regions)

    # Figures: clusters of vector graphics and images outside the tables.
    graphics: list[BBox] = list(images)
    for obj in page.curves + page.rects + page.lines:
        b = _obj_bbox(obj)
        is_separator = min(b[2] - b[0], b[3] - b[1]) < 2 and (b[2] - b[0]) > page.width * 0.5
        if not is_separator and not inside_region(b):
            graphics.append(b)
    for cluster in _cluster(graphics, _CLUSTER_GAP):
        if bbox_area(cluster) < _MIN_FIGURE_AREA or inside_region(cluster):
            continue
        # A few rectangles make a text box or shading, not a figure.
        members = sum(1 for g in graphics if bbox_overlaps(g, cluster))
        has_rich = any(bbox_overlaps(c, cluster) for c in curves + images)
        if has_rich or members >= 12:
            regions.append((RegionKind.FIGURE, cluster))
    return regions


def _rule_tables(page: pdfplumber.page.Page) -> list[BBox]:
    """Find tables drawn with horizontal rules only.

    Collinear horizontal segments are joined into rules; at least three
    stacked rules of similar extent with text between them form a table.
    """
    segments = sorted(
        (
            _obj_bbox(o)
            for o in page.lines + page.rects
            if o["bottom"] - o["top"] < 2 and o["x1"] - o["x0"] > 2
        ),
        key=lambda b: (round(b[1]), b[0]),
    )
    rules: list[list[float]] = []  # [x0, y, x1]
    for x0, top, x1, _ in segments:
        if rules and abs(rules[-1][1] - top) < 1.0 and x0 <= rules[-1][2] + 2.0:
            rules[-1][2] = max(rules[-1][2], x1)
        else:
            rules.append([x0, top, x1])
    rules = sorted((r for r in rules if r[2] - r[0] > 100), key=lambda r: r[1])

    tables: list[BBox] = []
    group: list[list[float]] = []

    def flush() -> None:
        if len(group) >= 3:
            box = (min(r[0] for r in group), group[0][1], max(r[2] for r in group), group[-1][1])
            if any(bbox_overlaps(box, _obj_bbox(c), -1.0) for c in page.chars):
                tables.append(box)
        group.clear()

    for rule in rules:
        if group:
            prev = group[-1]
            overlap = min(prev[2], rule[2]) - max(prev[0], rule[0])
            narrower = min(prev[2] - prev[0], rule[2] - rule[0])
            if overlap < 0.8 * narrower or rule[1] - prev[1] > 120:
                flush()
        group.append(rule)
    flush()
    return tables


def _cluster(boxes: list[BBox], gap: float) -> list[BBox]:
    """Merge boxes that overlap or lie within ``gap`` of each other."""
    clusters = list(boxes)
    merged = True
    while merged:
        merged = False
        result: list[BBox] = []
        for box in clusters:
            for i, other in enumerate(result):
                if bbox_overlaps(box, other, gap):
                    result[i] = bbox_union(box, other)
                    merged = True
                    break
            else:
                result.append(box)
        clusters = result
    return clusters


def _absorb_labels(bbox: BBox, words: list[tuple[Word, BBox]]) -> BBox:
    """Grow a region over the words that belong to it.

    These are words it overlaps and labels right next to it (axis labels,
    bit numbers above a table, row labels beside it), continued along their
    line ("octet 1"). Text set larger than the region's own text, i.e.
    headings, is never absorbed, and absorption never chains vertically.
    """
    inside = sorted(w[0].size for w, wb in words if bbox_overlaps(bbox, wb, -1.0))
    limit = inside[len(inside) // 2] * 1.15 if inside else 12.0
    absorbed = [
        wb
        for word, wb in words
        if bbox_overlaps(bbox, wb, _LABEL_MARGIN)
        and (bbox_overlaps(bbox, wb, -1.0) or max(c.size for c in word) <= limit)
    ]
    outside = [
        (word, wb)
        for word, wb in words
        if all(c.upright for c in word)
        and not bbox_overlaps(bbox, wb, -1.0)
        and wb[3] >= bbox[1] - _LABEL_MARGIN
        and wb[1] <= bbox[3] + _LABEL_MARGIN
        and max(c.size for c in word) <= limit
    ]
    taken = set(absorbed)
    pending = [wb for _, wb in outside if wb in taken]
    while pending:
        anchor = pending.pop()
        for word, wb in outside:
            if wb in taken:
                continue
            same_line = min(anchor[3], wb[3]) - max(anchor[1], wb[1]) > 0.5 * (wb[3] - wb[1])
            gap = max(wb[0] - anchor[2], anchor[0] - wb[2])
            if same_line and gap < word[0].size * 0.6:
                taken.add(wb)
                absorbed.append(wb)
                pending.append(wb)
    for wb in absorbed:
        bbox = bbox_union(bbox, wb)
    return bbox
