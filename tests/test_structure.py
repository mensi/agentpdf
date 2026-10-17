from agentpdf.headings import repair_outline_levels
from agentpdf.model import OutlineEntry
from agentpdf.paragraphs import Block, resolve_hyphens
from agentpdf.sections import build_sections


def test_resolve_hyphens_uses_document_vocabulary() -> None:
    blocks = [
        Block(1, 0, "Send an e-mail to the plant. Manufacturing is local."),
        Block(1, 1, "Send the e\x00mail and check the manu\x00facturing date."),
        Block(1, 2, "Unknown words: frob\x00nication and ACME\x00Widget."),
    ]
    resolve_hyphens(blocks)
    assert blocks[1].text == "Send the e-mail and check the manufacturing date."
    # Without evidence: drop before lowercase, keep before uppercase.
    assert blocks[2].text == "Unknown words: frobnication and ACME-Widget."


def test_repair_outline_levels_keeps_consistent_outline() -> None:
    outline = [
        OutlineEntry("1 Intro", 0, 1),
        OutlineEntry("1.1 Scope", 1, 1),
        OutlineEntry("Annex", 0, 2),
        OutlineEntry("2 Usage", 0, 3),
    ]
    assert repair_outline_levels(outline) == outline


def _doc(spec: list[tuple[int | None, int]]) -> list[Block]:
    """Blocks from (heading level or None, text length) pairs."""
    blocks = []
    for i, (level, length) in enumerate(spec):
        text = f"H{i}" if level is not None else "x" * length
        blocks.append(Block(1 + i // 4, i, text, heading_level=level))
    return blocks


def test_build_sections_keeps_small_subtrees_together() -> None:
    blocks = _doc([(None, 50), (0, 0), (None, 100), (1, 0), (None, 100), (0, 0), (None, 100)])
    sections = build_sections(blocks, max_chars=1000)
    assert [(s.title, s.level, s.filename) for s in sections] == [
        ("Front matter", 0, "001-front-matter.md"),
        ("H1", 0, "002-h1.md"),
        ("H5", 0, "003-h5.md"),
    ]


def test_build_sections_splits_large_subtrees_at_subheadings() -> None:
    # H0 has no text of its own; H3 has intro text before its subheading.
    blocks = _doc([(0, 0), (1, 0), (None, 600), (1, 0), (None, 500), (2, 0), (None, 600)])
    sections = build_sections(blocks, max_chars=1000)
    assert [(s.title, s.level, s.has_file) for s in sections] == [
        ("H0", 0, False),
        ("H1", 1, True),
        ("H3", 1, True),
        ("H5", 2, True),
    ]
    assert [b.text for b in sections[2].blocks] == ["H3", "x" * 500]


def test_build_sections_cuts_oversized_text_into_parts() -> None:
    blocks = _doc([(0, 0), (None, 600), (None, 600), (None, 600)])
    sections = build_sections(blocks, max_chars=1000)
    assert [(s.display_title, s.filename) for s in sections] == [
        ("H0 (part 1 of 3)", "001-h0-part1.md"),
        ("H0 (part 2 of 3)", "002-h0-part2.md"),
        ("H0 (part 3 of 3)", "003-h0-part3.md"),
    ]


def test_resolve_hyphens_keeps_hyphen_between_known_words() -> None:
    blocks = [
        Block(1, 0, "The client and the server exchange messages."),
        Block(1, 1, "A client\x00server connection is established."),
    ]
    resolve_hyphens(blocks)
    assert blocks[1].text == "A client-server connection is established."
