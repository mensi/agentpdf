"""Small text helpers."""

import re
import unicodedata


def normalize(text: str) -> str:
    """Lowercase alphanumerics only, for fuzzy text matching."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).lower() if ch.isalnum())


def slugify(text: str, max_length: int = 60) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug[:max_length].rstrip("-") or "untitled"


def page_range(pages: tuple[int, int] | None) -> str:
    if pages is None:
        return "no pages"
    first, last = pages
    return f"p. {first}" if first == last else f"p. {first}–{last}"


def token_estimate(chars: int) -> str:
    tokens = chars / 4
    return f"~{tokens / 1000:.1f}k tokens" if tokens >= 1000 else f"~{int(tokens)} tokens"


def parse_pages(spec: str, total_pages: int) -> list[int]:
    """Parse a 1-based page specification such as ``10,38-42``."""
    pages: set[int] = set()
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if not parts:
        raise ValueError("empty page specification")
    for part in parts:
        if "-" in part:
            pieces = [s.strip() for s in part.split("-", 1)]
            if not (pieces[0].isdigit() and pieces[1].isdigit()):
                raise ValueError(f"invalid page range: {part!r}")
            start, end = int(pieces[0]), int(pieces[1])
            if start < 1 or end < start:
                raise ValueError(f"invalid page range: {part!r}")
        else:
            if not part.isdigit() or int(part) < 1:
                raise ValueError(f"invalid page number: {part!r}")
            start = end = int(part)
        if end > total_pages:
            raise ValueError(f"page {end} out of range (1..{total_pages})")
        pages.update(range(start, end + 1))
    return sorted(pages)
