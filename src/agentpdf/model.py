"""Data model and geometry helpers shared by all stages.

Coordinates are PDF points with a top-left origin, as displayed (after page
rotation), matching pdfplumber: ``(x0, top, x1, bottom)``.
"""

from dataclasses import dataclass, field
from enum import StrEnum

BBox = tuple[float, float, float, float]


def bbox_union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def bbox_overlaps(a: BBox, b: BBox, margin: float = 0.0) -> bool:
    """Whether the boxes overlap or are within ``margin`` of each other.
    A negative margin requires a real overlap of at least that much."""
    return not (a[2] + margin < b[0] or b[2] + margin < a[0] or a[3] + margin < b[1] or b[3] + margin < a[1])


def bbox_area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def bbox_contains(outer: BBox, inner: BBox, tolerance: float = 1.0) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def overlap_ratio(a: BBox, b: BBox) -> float:
    """Intersection area relative to the smaller box."""
    inter = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    smaller = min(bbox_area(a), bbox_area(b))
    return bbox_area(inter) / smaller if smaller else 0.0


@dataclass
class Line:
    """A line of running text."""

    text: str
    bbox: BBox
    size: float
    bold: bool
    # Position in reading order on the page.
    order: float = 0
    # Ends with a hyphen that PDFium identified as a line-break hyphen.
    soft_hyphen: bool = False
    # Contains a gap much wider than a space (tab stop): tabular content.
    tabbed: bool = False


class RegionKind(StrEnum):
    TABLE = "table"
    FIGURE = "figure"


@dataclass
class Region:
    """A table or figure: rendered as an image (the source of truth) and
    transcribed as layout-preserving text."""

    kind: RegionKind
    bbox: BBox
    index: int = 0  # 1-based, per kind and page
    text: str = ""
    image: str = ""  # path relative to the document output directory
    order: float = 0  # position in reading order on the page

    @property
    def id(self) -> str:
        return f"{self.kind.value}-{self.index}"


@dataclass
class Page:
    number: int  # 1-based
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    has_text_layer: bool = True
    image: str = ""  # full-page render, if any

    @property
    def flags(self) -> list[str]:
        flags = sorted({r.kind.value for r in self.regions})
        if not self.has_text_layer:
            flags.append("no-text-layer")
        return flags


@dataclass
class OutlineEntry:
    title: str
    level: int  # 0-based nesting depth
    page: int | None  # 1-based target page, None if the entry has no target
