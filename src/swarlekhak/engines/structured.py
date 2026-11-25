"""Constrained voice grammars for chemistry, tables and pseudocode.

Like the math compiler: explicit student wording is the only source of content. Chemical equations are
never balanced or checked; pseudocode is never executed; tables hold exactly the values dictated.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from swarlekhak.engines.numbers import NUMBER_WORDS, parse_int, parse_number_words

STRUCTURED_VERSION = "structured-v1"


class StructuredError(ValueError):
    """Raised with a short spoken clarification."""


# ======================================================================= chemistry
ELEMENTS = {
    "hydrogen": "H", "helium": "He", "lithium": "Li", "carbon": "C", "nitrogen": "N", "oxygen": "O",
    "fluorine": "F", "sodium": "Na", "magnesium": "Mg", "aluminium": "Al", "aluminum": "Al", "silicon": "Si",
    "phosphorus": "P", "sulfur": "S", "sulphur": "S", "chlorine": "Cl", "potassium": "K", "calcium": "Ca",
    "chromium": "Cr", "manganese": "Mn", "iron": "Fe", "nickel": "Ni", "copper": "Cu", "zinc": "Zn",
    "bromine": "Br", "silver": "Ag", "tin": "Sn", "iodine": "I", "barium": "Ba", "gold": "Au", "mercury": "Hg",
    "lead": "Pb", "argon": "Ar", "neon": "Ne", "boron": "B", "beryllium": "Be", "cobalt": "Co", "uranium": "U",
}
SYMBOLS = set(ELEMENTS.values())
LETTER_SOUNDS = {"aitch": "h", "age": "h", "oh": "o", "see": "c", "en": "n", "ess": "s", "pee": "p", "kay": "k",
                 "eye": "i", "eff": "f", "bee": "b", "you": "u"}
ARROWS = {"gives": "→", "yields": "→", "produces": "→", "forms": "→", "arrow": "→", "reacts to give": "→",
          "reversibly gives": "⇌", "equilibrium": "⇌", "reversible arrow": "⇌"}
STATES = {"solid": "(s)", "liquid": "(l)", "gas": "(g)", "aqueous": "(aq)"}
SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
SUP = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")


def _element(word: str, nxt: str | None) -> tuple[str, int] | None:
    """Element from a spoken name, a typed symbol ('Na', 'Cl') or spelled letters; returns (symbol, words used).
    Two spelled letters join into one symbol only when the second letter is not itself an element, so
    "c l" -> Cl and "n a" -> Na, but "c o" stays C, O (carbon monoxide), never cobalt."""
    w = word.lower()
    if w in ELEMENTS:
        return ELEMENTS[w], 1
    first = LETTER_SOUNDS.get(w, w)
    if len(first) == 1 and first.isalpha() and nxt:
        second = LETTER_SOUNDS.get(nxt.lower(), nxt.lower())
        if len(second) == 1 and second.isalpha():
            two = first.upper() + second
            if two in SYMBOLS and second.upper() not in SYMBOLS:
                return two, 2
    if word in SYMBOLS:
        return word, 1
    if len(first) == 1 and first.upper() in SYMBOLS:
        return first.upper(), 1
    return None


_FORMULA = re.compile(r"^(\d*)((?:[A-Z][a-z]?\d*)+)$")


def _typed_formula(tok: str) -> tuple[int, list[tuple[str, int]]] | None:
    """Typed compact formula: '2H2O' -> (2, [('H', 2), ('O', 1)]). Unknown symbols -> None."""
    m = _FORMULA.match(tok)
    if not m:
        return None
    atoms = []
    for sym, n in re.findall(r"([A-Z][a-z]?)(\d*)", m.group(2)):
        if sym not in SYMBOLS:
            return None
        atoms.append((sym, int(n) if n else 1))
    return (int(m.group(1)) if m.group(1) else 1), atoms


@dataclass
class ChemEquation:
    source: str
    linear: str
    spoken: str
    species: list[dict]

    def to_block(self, block_id: str) -> dict:
        return {"id": block_id, "type": "chemistry", "source": self.source, "linear": self.linear,
                "spoken": self.spoken, "species": self.species, "compiler": STRUCTURED_VERSION}


def compile_chemistry(text: str) -> ChemEquation:
    raw = re.sub(r"[,;]", " ", text.replace("→", " gives ").replace("->", " gives ").replace("+", " plus "))
    words = [w for w in raw.split() if w]
    low = [w.lower() for w in words]
    i = 0
    parts_linear: list[str] = []
    parts_spoken: list[str] = []
    species: list[dict] = []
    cur: dict | None = None

    def close():
        nonlocal cur
        if cur:
            if not cur["atoms"]:
                raise StructuredError("Each substance needs at least one element.")
            lin = (str(cur["coef"]) if cur["coef"] > 1 else "") + "".join(
                s + (str(n).translate(SUB) if n > 1 else "") for s, n in cur["atoms"])
            if cur["charge"]:
                lin += cur["charge"].translate(SUP)
            lin += cur["state"]
            spk = (f"{cur['coef']} " if cur["coef"] > 1 else "") + " ".join(
                s + (f" {n}" if n > 1 else "") for s, n in cur["atoms"])
            if cur["charge"]:
                spk += f", charge {cur['charge']}"
            parts_linear.append(lin)
            parts_spoken.append(spk)
            species.append(cur)
        cur = None

    def new():
        return {"coef": 1, "atoms": [], "charge": "", "state": ""}
    while i < len(words):
        w = low[i]
        two = " ".join(low[i:i + 2])
        three = " ".join(low[i:i + 3])
        if three in ARROWS or two in ARROWS or w in ARROWS:
            k = 3 if three in ARROWS else (2 if two in ARROWS else 1)
            close()
            arrow = ARROWS[" ".join(low[i:i + k])]
            parts_linear.append(arrow)
            parts_spoken.append("reversibly gives" if arrow == "⇌" else "gives")
            i += k
            continue
        if w == "plus":
            close()
            parts_linear.append("+")
            parts_spoken.append("plus")
            i += 1
            continue
        if w == "charge":
            if cur is None:
                raise StructuredError("Say charge after the substance it belongs to.")
            j = i + 1
            mag = ""
            if j < len(words) and (low[j] in NUMBER_WORDS or low[j].isdigit()):
                val, used = parse_number_words(low, j) if low[j] in NUMBER_WORDS else (low[j], 1)
                mag = val if val != "1" else ""
                j += used
            if j >= len(words) or low[j] not in ("plus", "minus", "positive", "negative"):
                raise StructuredError("Say charge plus or charge minus, for example charge two minus.")
            cur["charge"] = mag + ("+" if low[j] in ("plus", "positive") else "-")
            i = j + 1
            continue
        if w in STATES:
            if cur is None:
                raise StructuredError("Say the state after the substance.")
            cur["state"] = STATES[w]
            i += 1
            continue
        if w in NUMBER_WORDS or re.fullmatch(r"\d+", w):
            val, used = (parse_number_words(low, i) if w in NUMBER_WORDS else (w, 1))
            n = int(float(val))
            if cur is None or not cur["atoms"]:
                cur = cur or new()
                cur["coef"] = n
            else:
                s, _ = cur["atoms"][-1]
                cur["atoms"][-1] = (s, n)
            i += used
            continue
        tf = _typed_formula(words[i]) if len(words[i]) > 1 and words[i] not in SYMBOLS else None
        if tf:
            pending = cur["coef"] if cur is not None and not cur["atoms"] else 1   # "2 H2" -> coefficient 2
            if cur is not None and cur["atoms"]:
                close()   # a typed formula is a whole substance
            cur = new()
            cur["coef"], cur["atoms"] = (tf[0] if tf[0] > 1 else pending), tf[1]
            i += 1
            continue
        el = _element(words[i], words[i + 1] if i + 1 < len(words) else None)
        if el:
            cur = cur or new()
            cur["atoms"].append((el[0], 1))
            i += el[1]
            continue
        raise StructuredError(f"I did not understand {words[i]}. Chemistry supports element names or symbols, "
                              "numbers, plus, gives, charge plus or minus, and states such as aqueous.")
    close()
    if not species:
        raise StructuredError("The chemical equation is empty.")
    return ChemEquation(text, " ".join(parts_linear), ", ".join(parts_spoken), species)


# ======================================================================= tables
@dataclass
class Table:
    columns: list[str]
    rows: list[list[str]] = field(default_factory=list)

    @classmethod
    def create(cls, n: int) -> "Table":
        if not 1 <= n <= 12:
            raise StructuredError("A table can have between 1 and 12 columns.")
        return cls([f"Column {i}" for i in range(1, n + 1)], [[""] * n])

    @classmethod
    def from_block(cls, b: dict) -> "Table":
        return cls(list(b["columns"]), [list(r) for r in b["rows"]])

    def to_block(self, block_id: str) -> dict:
        return {"id": block_id, "type": "table", "columns": self.columns, "rows": self.rows}

    def _col(self, c: int) -> int:
        if not 1 <= c <= len(self.columns):
            raise StructuredError(f"The table has {len(self.columns)} columns.")
        return c - 1

    def _row(self, r: int) -> int:
        if not 1 <= r <= len(self.rows):
            raise StructuredError(f"The table has {len(self.rows)} rows.")
        return r - 1

    def set_heading(self, c: int, text: str) -> str:
        self.columns[self._col(c)] = text.strip()
        return f"Column {c} heading is now {text.strip()}."

    def add_row(self) -> str:
        self.rows.append([""] * len(self.columns))
        return f"Added row {len(self.rows)}."

    def set_cell(self, r: int, c: int, value: str) -> str:
        while r == len(self.rows) + 1:   # dictating into the next row adds it
            self.rows.append([""] * len(self.columns))
        self.rows[self._row(r)][self._col(c)] = value.strip()
        return f"Row {r}, {self.columns[c - 1]}: {value.strip()}."

    def delete_row(self, r: int) -> str:
        self.rows.pop(self._row(r))
        return f"Deleted row {r}."

    def read_row(self, r: int) -> str:
        row = self.rows[self._row(r)]
        return f"Row {r}: " + "; ".join(f"{h}: {v or 'empty'}" for h, v in zip(self.columns, row)) + "."

    def summary(self) -> str:
        head = f"Table with {len(self.columns)} columns, {', '.join(self.columns)}, and {len(self.rows)} rows."
        return head + " " + " ".join(self.read_row(i + 1) for i in range(len(self.rows)))

    def linear(self) -> str:
        lines = [" | ".join(self.columns), " | ".join("---" for _ in self.columns)]
        lines += [" | ".join(v or " " for v in r) for r in self.rows]
        return "\n".join(lines)


# ======================================================================= pseudocode
@dataclass
class Pseudocode:
    lines: list[dict] = field(default_factory=list)   # {"indent": int, "text": str}
    indent: int = 0

    @classmethod
    def from_block(cls, b: dict) -> "Pseudocode":
        return cls([dict(x) for x in b["lines"]], int(b.get("indent", 0)))

    def to_block(self, block_id: str) -> dict:
        return {"id": block_id, "type": "pseudocode", "lines": copy.deepcopy(self.lines), "indent": self.indent,
                "note": "explicit lines only; never executed"}

    def add_line(self, text: str) -> str:
        self.lines.append({"indent": self.indent, "text": text.strip()})
        return f"Line {len(self.lines)}, indent {self.indent}: {text.strip()}."

    def change_indent(self, delta: int) -> str:
        self.indent = max(0, min(8, self.indent + delta))
        return f"Indent level {self.indent}."

    def delete_line(self, n: int) -> str:
        if not 1 <= n <= len(self.lines):
            raise StructuredError(f"There are {len(self.lines)} lines.")
        self.lines.pop(n - 1)
        return f"Deleted line {n}."

    def read(self) -> str:
        if not self.lines:
            return "The pseudocode is empty."
        return " ".join(f"Line {i}, indent {l['indent']}: {l['text']}." for i, l in enumerate(self.lines, 1))

    def linear(self) -> str:
        return "\n".join("    " * l["indent"] + l["text"] for l in self.lines)


def parse_count(text: str) -> int:
    n = parse_int(text)
    if n is None:
        raise StructuredError(f"I did not understand the number {text}.")
    return n
