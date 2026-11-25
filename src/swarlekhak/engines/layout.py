"""Inline placement of structured blocks (equations, diagrams, tables, chemistry, pseudocode).

Blocks are anchored *after a paragraph* of the answer text rather than at a character offset, so ordinary
edits inside a paragraph never detach them. Read-back, the Word export and the examiner view all use
interleave() to present text and blocks in the student's intended order.
"""
from __future__ import annotations

import re


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]


def anchor_for(text: str, offset: int | None) -> int:
    """Paragraph number (1-based) containing `offset`; the last paragraph when offset is None; 0 if empty."""
    paras = paragraphs(text)
    if not paras:
        return 0
    if offset is None:
        return len(paras)
    idx = 0
    for m in re.finditer(r"[^\n]+", text):
        if m.group().strip():
            idx += 1
            if m.start() <= offset <= m.end():
                return idx
            if m.start() > offset:
                return max(1, idx - 1)
    return len(paras)


def interleave(text: str, blocks: list[dict]) -> list[tuple[str, object]]:
    """[('text', paragraph) | ('block', block)] in reading order. Unanchored blocks go at the end."""
    paras = paragraphs(text)
    n = len(paras)

    def where(b: dict) -> int:
        a = b.get("anchor_paragraph")
        return n if a is None else max(0, min(int(a), n))
    out: list[tuple[str, object]] = [("block", b) for b in blocks if where(b) == 0]
    for i, p in enumerate(paras, 1):
        out.append(("text", p))
        out += [("block", b) for b in blocks if where(b) == i]
    return out


def block_spoken(b: dict) -> str:
    """Accessible spoken equivalent of a structured block."""
    from swarlekhak.engines.diagram import Diagram
    from swarlekhak.engines.structured import Pseudocode, Table
    t = b.get("type")
    if t == "equation":
        return f"Equation: {b['spoken']}."
    if t == "chemistry":
        return f"Chemical equation: {b['spoken']}."
    if t == "diagram":
        return "Diagram. " + Diagram.from_block(b).summary()
    if t == "table":
        return Table.from_block(b).summary()
    if t == "pseudocode":
        return "Pseudocode. " + Pseudocode.from_block(b).read()
    return ""


def block_visual(b: dict) -> str:
    """Compact written form used in the workspace list, the examiner view and exports."""
    from swarlekhak.engines.diagram import Diagram
    from swarlekhak.engines.structured import Pseudocode, Table
    t = b.get("type")
    if t in ("equation", "chemistry"):
        return b["linear"]
    if t == "diagram":
        return Diagram.from_block(b).adjacency_text()
    if t == "table":
        return Table.from_block(b).linear()
    if t == "pseudocode":
        return Pseudocode.from_block(b).linear()
    return ""


BLOCK_TITLES = {"equation": "Equation", "chemistry": "Chemical equation", "diagram": "Diagram", "table": "Table",
                "pseudocode": "Pseudocode"}
