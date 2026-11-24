"""Audio Cursor: structured selection over the answer text (sentences, paragraphs, tokens).

All operations are pure: they return an EditProposal or text to speak; the
orchestrator decides whether to confirm, commit and record a revision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*", re.UNICODE)
_SENT = re.compile(r"[^.!?\n]+(?:[.!?]+|$)", re.UNICODE)


@dataclass(frozen=True)
class Span:
    start: int
    end: int


@dataclass
class EditProposal:
    new_text: str
    span: Span            # span of the inserted / replaced text in new_text
    description: str      # spoken preview
    ambiguous: bool = False


class CursorError(ValueError):
    """Raised with a short spoken clarification."""


class AudioCursor:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.selection: Span | None = None
        self.sentence_index = -1

    # ----- structure
    def set_text(self, text: str) -> None:
        self.text = text
        if self.selection and self.selection.end > len(text):
            self.selection = None

    def sentences(self) -> list[Span]:
        out = []
        for m in _SENT.finditer(self.text):
            s, e = m.start(), m.end()
            seg = self.text[s:e]
            lead = len(seg) - len(seg.lstrip())
            if seg.strip():
                out.append(Span(s + lead, e))
        return out

    def paragraphs(self) -> list[Span]:
        out, pos = [], 0
        for para in re.split(r"(\n\s*\n|\n)", self.text):
            if para.strip() and not re.fullmatch(r"\n\s*\n|\n", para):
                lead = len(para) - len(para.lstrip())
                out.append(Span(pos + lead, pos + len(para.rstrip())))
            pos += len(para)
        return out

    def words(self) -> list[Span]:
        return [Span(m.start(), m.end()) for m in _WORD.finditer(self.text)]

    def slice(self, sp: Span) -> str:
        return self.text[sp.start:sp.end]

    # ----- navigation
    def read_sentence(self, which: str) -> str:
        sents = self.sentences()
        if not sents:
            raise CursorError("The answer is empty.")
        if which == "next":
            idx = self.sentence_index + 1
        elif which == "previous":
            idx = self.sentence_index - 1
        else:
            idx = max(0, self.sentence_index)
        if idx >= len(sents):
            raise CursorError("That was the last sentence.")
        if idx < 0:
            raise CursorError("That was the first sentence.")
        self.sentence_index = idx
        self.selection = sents[idx]
        return f"Sentence {idx + 1} of {len(sents)}. {self.slice(sents[idx])}"

    def read_paragraph(self, n: int) -> str:
        paras = self.paragraphs()
        if not paras:
            raise CursorError("The answer is empty.")
        if n == -1:
            n = len(paras)
        if not 1 <= n <= len(paras):
            raise CursorError(f"There {'is' if len(paras) == 1 else 'are'} {len(paras)} paragraph{'s' if len(paras) != 1 else ''}.")
        self.selection = paras[n - 1]
        return f"Paragraph {n}. {self.slice(paras[n - 1])}"

    def current_word(self) -> Span:
        if self.selection is None:
            raise CursorError("No word is selected. Say find, followed by a word.")
        ws = [w for w in self.words() if w.start >= self.selection.start and w.end <= self.selection.end]
        if len(ws) == 1:
            return ws[0]
        if ws and self.selection.end - self.selection.start < 40 and len(ws) <= 3:
            return ws[0]
        raise CursorError("The selection is more than one word. Say find, followed by a word.")

    def spell_current_word(self) -> str:
        w = self.slice(self.current_word())
        return f"{w}. " + ", ".join(ch if ch.isalnum() else repr(ch) for ch in w)

    def find(self, target: str, occurrence: int | None = None) -> Span:
        occ = self.occurrences(target)
        if not occ:
            raise CursorError(f"I could not find {target}.")
        if occurrence is None:
            if len(occ) > 1:
                raise CursorError(f"{target} appears {len(occ)} times. Say, for example, find the second {target}.")
            sp = occ[0]
        else:
            idx = len(occ) - 1 if occurrence == -1 else occurrence - 1
            if not 0 <= idx < len(occ):
                raise CursorError(f"{target} appears only {len(occ)} time{'s' if len(occ) != 1 else ''}.")
            sp = occ[idx]
        self.selection = sp
        return sp

    def occurrences(self, target: str) -> list[Span]:
        target = target.strip()
        if not target:
            return []
        rx = re.compile(r"(?<![\w])" + r"\s+".join(re.escape(p) for p in target.split()) + r"(?![\w])", re.IGNORECASE)
        return [Span(m.start(), m.end()) for m in rx.finditer(self.text)]

    def context(self, sp: Span, width: int = 40) -> str:
        a = max(0, sp.start - width)
        b = min(len(self.text), sp.end + width)
        return self.text[a:b].strip()

    # ----- edits (pure)
    def resolve_target(self, target: str, occurrence: int | None) -> Span:
        t = target.strip().lower()
        if t in ("that word", "this word", "the word", "it", "that", "current word", "the selection", "selection"):
            return self.current_word() if "word" in t else self._require_selection()
        return self.find(target, occurrence)

    def _require_selection(self) -> Span:
        if self.selection is None:
            raise CursorError("Nothing is selected.")
        return self.selection

    def propose_replace(self, target: str, new: str, occurrence: int | None = None) -> EditProposal:
        sp = self.resolve_target(target, occurrence)
        old = self.slice(sp)
        new_text = self.text[:sp.start] + new + self.text[sp.end:]
        new_span = Span(sp.start, sp.start + len(new))
        return EditProposal(new_text, new_span, f"Replace {old} with {new}, in: {self.context(sp)}")

    def propose_insert(self, new: str, where: str, target: str, occurrence: int | None = None) -> EditProposal:
        sp = self.resolve_target(target, occurrence)
        if where == "before":
            pos = sp.start
            ins = new + " "
            new_span = Span(pos, pos + len(new))
        else:
            pos = sp.end
            ins = " " + new
            new_span = Span(pos + 1, pos + 1 + len(new))
        new_text = self.text[:pos] + ins + self.text[pos:]
        return EditProposal(new_text, new_span, f"Insert {new} {where} {self.slice(sp)}")

    def propose_delete(self, target: str, occurrence: int | None = None) -> EditProposal:
        sp = self.resolve_target(target, occurrence)
        a, b = sp.start, sp.end
        if b < len(self.text) and self.text[b] == " ":
            b += 1
        elif a > 0 and self.text[a - 1] == " ":
            a -= 1
        new_text = self.text[:a] + self.text[b:]
        return EditProposal(new_text, Span(a, a), f"Delete {self.slice(sp)}, in: {self.context(sp)}")
