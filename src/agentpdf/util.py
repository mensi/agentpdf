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
