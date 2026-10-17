"""Split a document into section files of bounded size along its headings."""

from dataclasses import dataclass, field
from typing import Self

from .paragraphs import Block
from .util import slugify


@dataclass
class Section:
    title: str
    level: int  # nesting depth in the table of contents
    blocks: list[Block] = field(default_factory=list)
    base_level: int = 0  # heading level of the section's own heading
    part: tuple[int, int] | None = None  # (n, total) for oversized sections
    has_file: bool = True  # False for headings without content of their own
    filename: str = ""

    @property
    def display_title(self) -> str:
        return f"{self.title} (part {self.part[0]} of {self.part[1]})" if self.part else self.title

    @property
    def pages(self) -> tuple[int, int] | None:
        pages = [b.page for b in self.blocks]
        return (min(pages), max(pages)) if pages else None

    @property
    def chars(self) -> int:
        return _chars(self.blocks)


def _chars(blocks: list[Block]) -> int:
    return sum(b.chars for b in blocks)


@dataclass(eq=False)
class _Node:
    heading: Block | None
    level: int
    own: list[Block] = field(default_factory=list)  # heading and text before the first child
    children: list[Self] = field(default_factory=list)

    def blocks(self) -> list[Block]:
        result = list(self.own)
        for child in self.children:
            result.extend(child.blocks())
        return result


def _heading_tree(blocks: list[Block]) -> _Node:
    root = _Node(None, -1)
    stack = [root]
    for block in blocks:
        if block.heading_level is None:
            stack[-1].own.append(block)
            continue
        while len(stack) > 1 and stack[-1].level >= block.heading_level:
            stack.pop()
        node = _Node(block, block.heading_level, [block])
        stack[-1].children.append(node)
        stack.append(node)
    return root


def build_sections(blocks: list[Block], max_chars: int) -> list[Section]:
    """Split the block stream into sections of at most ``max_chars``.

    A heading whose content (including subsections) fits the budget becomes
    one section. Larger ones are split at their subheadings, recursively;
    the text before the first subheading becomes a section of its own.
    Content that is still too large is cut into parts at paragraph
    boundaries. Sections are returned in document order with file names.
    """
    sections: list[Section] = []

    def add(title: str, depth: int, base: int, content: list[Block]) -> None:
        if _chars(content) <= max_chars:
            sections.append(Section(title, depth, content, base))
            return
        parts: list[list[Block]] = [[]]
        part_chars = 0
        for block in content:
            if parts[-1] and part_chars + block.chars > max_chars:
                parts.append([])
                part_chars = 0
            parts[-1].append(block)
            part_chars += block.chars
        for n, part in enumerate(parts, 1):
            sections.append(Section(title, depth, part, base, part=(n, len(parts))))

    def emit(node: _Node, depth: int) -> None:
        assert node.heading is not None
        title, base = node.heading.text, node.level
        all_blocks = node.blocks()
        if _chars(all_blocks) <= max_chars or not node.children:
            add(title, depth, base, all_blocks)
            return
        if len(node.own) > 1:
            add(title, depth, base, node.own)
        else:
            sections.append(Section(title, depth, list(node.own), base, has_file=False))
        for child in node.children:
            emit(child, depth + 1)

    root = _heading_tree(blocks)
    if root.own:
        add("Front matter", 0, 0, root.own)
    for child in root.children:
        emit(child, 0)

    number = 0
    for section in sections:
        if section.has_file:
            number += 1
            suffix = f"-part{section.part[0]}" if section.part else ""
            section.filename = f"{number:03d}-{slugify(section.title)}{suffix}.md"
    return sections
