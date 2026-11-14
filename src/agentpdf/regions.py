"""Locate tables and figures on a page.

Detection uses pdfplumber's access to ruling lines, vector graphics and
images. Regions are not interpreted further: they are rendered as images and
transcribed as layout text.
"""

import itertools

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
# Compact callouts (pin names, CAD dimensions) sit slightly further outside a figure.
_FIGURE_LABEL_MARGIN = 14.0

_CAPTION_PREFIXES = (
    "figure",
    "fig",
    "table",
    "tab",
    "abbildung",
    "abb",
    "tabelle",
    "note",
    "notes",
)

_WHITE_FILLS = (
    1,
    1.0,
    (1, 1, 1),
    (1.0, 1.0, 1.0),
    [1, 1, 1],
    [1.0, 1.0, 1.0],
    (0, 0, 0, 0),
    (0.0, 0.0, 0.0, 0.0),
    [0, 0, 0, 0],
    [0.0, 0.0, 0.0, 0.0],
)

PlacedRegion = tuple[RegionKind, BBox]


def locate_regions(page: pdfplumber.page.Page, page_words: list[Word]) -> list[PlacedRegion]:
    """Find the tables and figures on a page, including their labels.

    Returns regions sorted top to bottom.
    """
    boxes = [(w, word_bbox(w)) for w in page_words]
    grown = [(kind, _absorb_labels(kind, bbox, boxes)) for kind, bbox in _detect(page, boxes)]
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


def _is_invisible_fill(obj: dict) -> bool:
    """Unstroked white or unfilled paths are clipping/background boxes, not visible graphics."""
    if obj.get("stroke", True):
        return False
    if not obj.get("fill", False):
        return True
    return obj.get("non_stroking_color") in _WHITE_FILLS


def _clip_page(b: BBox, width: float, height: float) -> BBox | None:
    if b[2] < 0 or b[3] < 0 or b[0] > width or b[1] > height:
        return None
    x0, top, x1, bottom = max(0.0, b[0]), max(0.0, b[1]), min(width, b[2]), min(height, b[3])
    return (x0, top, x1, bottom) if x1 >= x0 and bottom >= top else None


def _detect(page: pdfplumber.page.Page, word_boxes: list[tuple[Word, BBox]]) -> list[PlacedRegion]:
    w, h = float(page.width), float(page.height)
    curves = [cb for o in page.curves if not _is_invisible_fill(o) and (cb := _clip_page(_obj_bbox(o), w, h))]
    images = [cb for o in page.images if (cb := _clip_page(_obj_bbox(o), w, h))]
    rects = [cb for o in page.rects if not _is_invisible_fill(o) and (cb := _clip_page(_obj_bbox(o), w, h))]
    lines = [cb for o in page.lines if (cb := _clip_page(_obj_bbox(o), w, h))]

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
        bbox = _clip_page(tuple(table.bbox), w, h)
        if bbox is None or any(bbox_contains(r, bbox) for _, r in regions):
            continue
        if not is_figure(bbox):
            has_words = any(bbox_overlaps(bbox, wb, -1.0) for _, wb in word_boxes)
            if has_words and min(bbox[2] - bbox[0], bbox[3] - bbox[1]) >= 10.0:
                regions.append((RegionKind.TABLE, bbox))
            continue
        cells = [
            cb
            for c in table.cells
            if c is not None and (cb := _clip_page(tuple(c), w, h)) and bbox_area(cb) >= _MIN_FIGURE_AREA
        ]
        fig_cells = [
            c
            for c in cells
            if is_figure(c) or sum(1 for g in lines + rects if bbox_overlaps(g, c, -1.0)) >= 16
        ]
        for cell in fig_cells or [bbox]:
            if bbox_area(cell) >= _MIN_FIGURE_AREA:
                regions.append((RegionKind.FIGURE, cell))

    for bbox in _rule_tables(lines + rects, curves, word_boxes):
        if not any(bbox_overlaps(bbox, r) for _, r in regions):
            regions.append((RegionKind.TABLE, bbox))

    def contained_in_region(b: BBox) -> bool:
        return any(bbox_contains(r, b, 2.0) for _, r in regions)

    v_borders = [b for b in lines + rects if (b[2] - b[0]) < 2 and 10 < (b[3] - b[1]) < h * 0.85]

    def is_framed_edge(b: BBox) -> bool:
        has_left = any(abs(v[0] - b[0]) < 3.0 and v[1] - 3.0 <= b[1] <= v[3] + 3.0 for v in v_borders)
        has_right = any(abs(v[2] - b[2]) < 3.0 and v[1] - 3.0 <= b[1] <= v[3] + 3.0 for v in v_borders)
        return has_left and has_right

    # Figures: clusters of vector graphics and images not contained in a table.
    # Connected diagrams (schematics, state machines, CAD drawings) often
    # contain small sub-boxes that find_tables() picked up; when the surrounding
    # graphic cluster encloses or bridges into those boxes, absorb them into the
    # figure instead of shattering the diagram.
    graphics: list[BBox] = list(images)
    for b in curves + rects + lines:
        is_separator = min(b[2] - b[0], b[3] - b[1]) < 2 and (b[2] - b[0]) > w * 0.5 and not is_framed_edge(b)
        if not is_separator and not contained_in_region(b):
            graphics.append(b)

    for cluster in _cluster(graphics, _CLUSTER_GAP):
        if bbox_area(cluster) < _MIN_FIGURE_AREA or contained_in_region(cluster):
            continue
        # A few rectangles make a text box or shading, not a figure.
        cluster_graphics = [g for g in graphics if bbox_overlaps(g, cluster)]
        has_rich = any(bbox_overlaps(c, cluster) for c in curves + images)
        if not (has_rich or len(cluster_graphics) >= 12):
            continue
        kept: list[PlacedRegion] = []
        for r_kind, r_box in regions:
            bridged = (
                bbox_contains(cluster, r_box, 2.0)
                or overlap_ratio(cluster, r_box) > 0.3
                or any(bbox_overlaps(g, r_box, -2.0) for g in cluster_graphics)
            )
            if bridged:
                cluster = bbox_union(cluster, r_box)
            else:
                kept.append((r_kind, r_box))
        regions = kept
        regions.append((RegionKind.FIGURE, cluster))
    return regions


def _rule_tables(
    lines_and_rects: list[BBox], curves: list[BBox], word_boxes: list[tuple[Word, BBox]]
) -> list[BBox]:
    """Find tables drawn with horizontal rules only.

    Collinear horizontal segments are joined into rules; at least three
    stacked rules of matching extent with multi-column text between them
    (and without schematic wire crossings or curves) form a table.
    """
    v_segments = [b for b in lines_and_rects if b[2] - b[0] < 2 and b[3] - b[1] > 6]
    segments = sorted(
        (b for b in lines_and_rects if b[3] - b[1] < 2 and b[2] - b[0] > 2),
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
            v_cross = sum(
                1
                for v in v_segments
                if v[0] > box[0] + 2.0
                and v[2] < box[2] - 2.0
                and any(v[1] < r[1] - 1.0 and v[3] > r[1] + 1.0 for r in group)
            )
            has_curves = sum(1 for c in curves if bbox_overlaps(box, c, -1.0)) >= 2
            if v_cross < 2 and not has_curves and _has_columns(box, word_boxes):
                tables.append(box)
        group.clear()

    for rule in rules:
        if group:
            prev = group[-1]
            overlap = min(prev[2], rule[2]) - max(prev[0], rule[0])
            wider = max(prev[2] - prev[0], rule[2] - rule[0])
            if overlap < 0.85 * wider or rule[1] - prev[1] > 120:
                flush()
        group.append(rule)
    flush()
    return tables


def _has_columns(box: BBox, word_boxes: list[tuple[Word, BBox]]) -> bool:
    """Whether ``box`` contains at least one row with multi-column horizontal spacing."""
    in_words = [(w, wb) for w, wb in word_boxes if all(c.upright for c in w) and bbox_overlaps(box, wb, -1.0)]
    if len(in_words) < 2:
        return False
    in_words.sort(key=lambda item: (item[1][1], item[1][0]))
    lines: list[list[tuple[Word, BBox]]] = []
    for w, wb in in_words:
        for line in lines:
            ref = line[0][1]
            if min(ref[3], wb[3]) - max(ref[1], wb[1]) > 0.5 * min(ref[3] - ref[1], wb[3] - wb[1]):
                line.append((w, wb))
                break
        else:
            lines.append([(w, wb)])
    for line in lines:
        line.sort(key=lambda item: item[1][0])
        for (w1, b1), (w2, b2) in itertools.pairwise(line):
            if b2[0] - b1[2] > 1.5 * max(w1[0].size, w2[0].size):
                return True
    return False


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


def _word_runs(outside: list[tuple[Word, BBox]]) -> list[list[tuple[Word, BBox]]]:
    """Group nearby outside words into horizontal (upright) or vertical (rotated) runs."""
    runs: list[list[tuple[Word, BBox]]] = []
    visited: set[int] = set()
    for idx, (w, wb) in enumerate(outside):
        if idx in visited:
            continue
        up = all(c.upright for c in w)
        run = [(w, wb)]
        visited.add(idx)
        stack = [(w, wb)]
        while stack:
            aw, ab = stack.pop()
            for j, (ow, ob) in enumerate(outside):
                if j in visited or (all(c.upright for c in ow) != up):
                    continue
                if up:
                    same = min(ab[3], ob[3]) - max(ab[1], ob[1]) > 0.5 * min(ab[3] - ab[1], ob[3] - ob[1])
                    gap = max(ob[0] - ab[2], ab[0] - ob[2])
                else:
                    same = min(ab[2], ob[2]) - max(ab[0], ob[0]) > 0.5 * min(ab[2] - ab[0], ob[2] - ob[0])
                    gap = max(ob[1] - ab[3], ab[1] - ob[3])
                if same and gap < max(aw[0].size, ow[0].size) * 0.8:
                    visited.add(j)
                    run.append((ow, ob))
                    stack.append((ow, ob))
        run.sort(key=lambda item: item[1][0] if up else -item[1][3])
        runs.append(run)
    return runs


def _absorb_labels(kind: RegionKind, bbox: BBox, words: list[tuple[Word, BBox]]) -> BBox:
    """Grow a region over the words that belong to it.

    These are words it overlaps and labels right next to it (axis labels,
    bit numbers above a table, row labels beside it), continued along their
    line ("octet 1"). Figures also absorb compact perimeter callouts such as
    pin names and rotated CAD dimensions. Text set larger than the region's
    own text, i.e. headings, is never absorbed, and absorption never chains
    vertically across paragraphs.
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

    if kind == RegionKind.FIGURE:
        callout_limit = max(inside[len(inside) // 2] * 1.15, 9.5) if inside else 10.5
        candidates = [
            (word, wb)
            for word, wb in words
            if not bbox_overlaps(bbox, wb, -1.0) and bbox_overlaps(bbox, wb, 60.0)
        ]
        for run in _word_runs(candidates):
            if not any(bbox_overlaps(bbox, wb, _FIGURE_LABEL_MARGIN) for _, wb in run):
                continue
            if any(max(c.size for c in w) > callout_limit for w, _ in run):
                continue
            texts = ["".join(c.text for c in w) for w, _ in run]
            first = texts[0].lower().rstrip(":.")
            last_stem = texts[-1][:-1] if texts[-1].endswith(".") else ""
            is_prose_tail = len(texts) >= 2 and last_stem.isalpha() and last_stem.islower()
            if first in _CAPTION_PREFIXES or is_prose_tail or len(run) > 3 or sum(len(t) for t in texts) > 16:
                continue
            for _, wb in run:
                if wb not in taken:
                    taken.add(wb)
                    absorbed.append(wb)

    for wb in absorbed:
        bbox = bbox_union(bbox, wb)
    return bbox
