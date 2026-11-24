"""Voice Compiler (math MVP): spoken words -> tokens -> AST -> validated renderings.

Constrained grammar, recursive descent. Never evaluates or simplifies. The
student's wording is the only source of content; anything unknown raises a
MathError with a short clarification.

  equation := expr ("equals" expr)?
  expr     := term (("plus"|"minus") term)*
  term     := unary (("times"|"divided by"|"over") unary | <implicit> unary)*
  unary    := "minus" unary | power
  power    := atom ("squared" | "cubed" | "to the power of" unary)?
  atom     := NUMBER | VAR | GREEK | "open bracket" equation-free expr "close bracket"
            | "fraction" expr "over" expr "end fraction" | "square root of" atom
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from swarlekhak.engines.numbers import NUMBER_WORDS, parse_number_words

COMPILER_VERSION = "math-rd-v1"

GREEK = {
    "alpha": ("α", r"\alpha"), "beta": ("β", r"\beta"), "gamma": ("γ", r"\gamma"), "delta": ("δ", r"\delta"),
    "epsilon": ("ε", r"\epsilon"), "zeta": ("ζ", r"\zeta"), "eta": ("η", r"\eta"), "theta": ("θ", r"\theta"),
    "iota": ("ι", r"\iota"), "kappa": ("κ", r"\kappa"), "lambda": ("λ", r"\lambda"), "mu": ("μ", r"\mu"),
    "nu": ("ν", r"\nu"), "xi": ("ξ", r"\xi"), "pi": ("π", r"\pi"), "rho": ("ρ", r"\rho"),
    "sigma": ("σ", r"\sigma"), "tau": ("τ", r"\tau"), "phi": ("φ", r"\phi"), "chi": ("χ", r"\chi"),
    "psi": ("ψ", r"\psi"), "omega": ("ω", r"\omega"),
}
LETTER_NAMES = {
    "ex": "x", "why": "y", "zee": "z", "zed": "z", "bee": "b", "see": "c", "dee": "d", "em": "m",
    "en": "n", "pee": "p", "tee": "t", "vee": "v", "eye": "i", "kay": "k", "el": "l", "jay": "j",
    "gee": "g", "aitch": "h", "cue": "q", "queue": "q", "ess": "s", "eff": "f", "ay": "a",
}
PHRASES = [  # multiword keywords, longest first
    ("to the power of", "POW"), ("raised to the power of", "POW"), ("raised to", "POW"),
    ("square root of", "SQRT"), ("the square root of", "SQRT"), ("root of", "SQRT"),
    ("divided by", "DIV"), ("multiplied by", "MUL"), ("is equal to", "EQ"), ("equal to", "EQ"),
    ("open bracket", "LP"), ("open parenthesis", "LP"), ("open paren", "LP"), ("left bracket", "LP"),
    ("close bracket", "RP"), ("close parenthesis", "RP"), ("close paren", "RP"), ("right bracket", "RP"),
    ("end fraction", "ENDFRAC"), ("the fraction", "FRAC"),
]
SINGLE = {
    "plus": "PLUS", "minus": "MINUS", "negative": "MINUS", "times": "MUL", "into": "MUL", "over": "OVER",
    "equals": "EQ", "equal": "EQ", "squared": "SQ", "cubed": "CUBE", "fraction": "FRAC",
    "+": "PLUS", "-": "MINUS", "*": "MUL", "×": "MUL", "/": "DIV", "=": "EQ", "(": "LP", ")": "RP", "^": "POW",
}


class MathError(ValueError):
    pass


@dataclass
class Node:
    kind: str                       # num var bin neg pow frac sqrt eq group
    value: str = ""
    latex: str = ""
    op: str = ""
    children: list["Node"] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind}
        if self.value:
            d["value"] = self.value
        if self.op:
            d["op"] = self.op
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


def tokenize(text: str) -> list[tuple[str, str]]:
    t = text.replace("−", "-")
    t = re.sub(r"([+*/=()^×-])", r" \1 ", t)
    words = [w for w in re.split(r"[\s,;:]+", t.strip()) if w]
    out: list[tuple[str, str]] = []
    i = 0
    lw = [w.lower() for w in words]
    while i < len(words):
        matched = False
        for phrase, kind in sorted(PHRASES, key=lambda p: -len(p[0])):
            parts = phrase.split()
            if lw[i:i + len(parts)] == parts:
                out.append((kind, phrase))
                i += len(parts)
                matched = True
                break
        if matched:
            continue
        w, low = words[i], lw[i]
        if low in ("capital", "uppercase", "big") and i + 1 < len(words):
            nxt = LETTER_NAMES.get(lw[i + 1], lw[i + 1])
            if len(nxt) == 1 and nxt.isalpha():
                out.append(("VAR", nxt.upper()))
                i += 2
                continue
        if low in SINGLE:
            out.append((SINGLE[low], w))
        elif low in NUMBER_WORDS:
            value, used = parse_number_words(lw, i)
            if value is None:
                raise MathError(f"I did not understand the number near {w}.")
            out.append(("NUM", value))
            i += used
            continue
        elif re.fullmatch(r"\d+(\.\d+)?", w):
            out.append(("NUM", w))
        elif low in GREEK:
            out.append(("GREEK", low))
        elif low in LETTER_NAMES:
            out.append(("VAR", LETTER_NAMES[low]))
        elif len(w) == 1 and w.isalpha():
            out.append(("VAR", w))
        elif low in ("the", "a", "of", "by") and out:  # harmless glue words between tokens
            pass
        else:
            raise MathError(f"I did not understand {w}. Equations support numbers, single-letter variables, "
                            "Greek letters, plus, minus, times, divided by, fraction, squared, power and brackets.")
        i += 1
    if not out:
        raise MathError("The equation is empty.")
    return out


class _Parser:
    def __init__(self, toks: list[tuple[str, str]]):
        self.t = toks
        self.i = 0

    def peek(self) -> str | None:
        return self.t[self.i][0] if self.i < len(self.t) else None

    def take(self, kind: str | None = None) -> tuple[str, str]:
        if self.i >= len(self.t):
            raise MathError("The equation ended too early.")
        tok = self.t[self.i]
        if kind and tok[0] != kind:
            raise MathError(f"Expected {kind.lower()} but heard {tok[1]}.")
        self.i += 1
        return tok

    def equation(self) -> Node:
        left = self.expr()
        if self.peek() == "EQ":
            self.take()
            right = self.expr()
            node = Node("eq", children=[left, right])
        else:
            node = left
        if self.peek() is not None:
            raise MathError(f"I could not place {self.t[self.i][1]} in the equation.")
        return node

    def expr(self) -> Node:
        node = self.term()
        while self.peek() in ("PLUS", "MINUS"):
            op = self.take()[0]
            node = Node("bin", op="+" if op == "PLUS" else "-", children=[node, self.term()])
        return node

    _ATOM_START = ("NUM", "VAR", "GREEK", "LP", "FRAC", "SQRT")

    def term(self) -> Node:
        node = self.unary()
        while True:
            k = self.peek()
            if k == "MUL":
                self.take()
                node = Node("bin", op="*", children=[node, self.unary()])
            elif k in ("DIV", "OVER"):
                self.take()
                node = Node("bin", op="/", children=[node, self.unary()])
            elif k in self._ATOM_START:
                node = Node("bin", op="implicit", children=[node, self.unary()])
            else:
                return node

    def unary(self) -> Node:
        if self.peek() == "MINUS":
            self.take()
            return Node("neg", children=[self.unary()])
        return self.power()

    def power(self) -> Node:
        base = self.atom()
        k = self.peek()
        if k == "SQ":
            self.take()
            return Node("pow", children=[base, Node("num", "2")])
        if k == "CUBE":
            self.take()
            return Node("pow", children=[base, Node("num", "3")])
        if k == "POW":
            self.take()
            return Node("pow", children=[base, self.unary()])
        return base

    def atom(self) -> Node:
        k = self.peek()
        if k == "NUM":
            return Node("num", self.take()[1])
        if k == "VAR":
            return Node("var", self.take()[1])
        if k == "GREEK":
            name = self.take()[1]
            return Node("var", GREEK[name][0], latex=GREEK[name][1], op=name)
        if k == "LP":
            self.take()
            inner = self.expr()
            self.take("RP")
            return Node("group", children=[inner])
        if k == "SQRT":
            self.take()
            return Node("sqrt", children=[self.atom()])
        if k == "FRAC":
            self.take()
            num = self._until_over()
            den = self.expr()
            if self.peek() == "ENDFRAC":
                self.take()
            return Node("frac", children=[num, den])
        if k is None:
            raise MathError("The equation ended too early.")
        raise MathError(f"I did not expect {self.t[self.i][1]} there.")

    def _until_over(self) -> Node:
        # numerator of an explicit fraction extends to the matching "over"
        node = self.unary()
        while self.peek() not in ("OVER", "DIV", None):
            k = self.peek()
            if k in ("PLUS", "MINUS"):
                op = self.take()[0]
                node = Node("bin", op="+" if op == "PLUS" else "-", children=[node, self.term_no_div()])
            elif k == "MUL":
                self.take()
                node = Node("bin", op="*", children=[node, self.unary()])
            elif k in self._ATOM_START:
                node = Node("bin", op="implicit", children=[node, self.unary()])
            else:
                raise MathError("A fraction needs the word over between numerator and denominator.")
        if self.peek() is None:
            raise MathError("A fraction needs the word over between numerator and denominator.")
        self.take()
        return node

    def term_no_div(self) -> Node:
        node = self.unary()
        while self.peek() in ("MUL",) or self.peek() in self._ATOM_START:
            if self.peek() == "MUL":
                self.take()
            node = Node("bin", op="*", children=[node, self.unary()])
        return node


PREC = {"eq": 0, "+": 1, "-": 1, "*": 2, "implicit": 2, "/": 2, "neg": 3, "pow": 4}


def _prec(n: Node) -> int:
    if n.kind == "bin":
        return PREC[n.op]
    if n.kind == "neg":
        return 3
    if n.kind == "pow":
        return 4
    return 9


def to_linear(n: Node) -> str:
    k = n.kind
    if k in ("num", "var"):
        return n.value
    if k == "group":
        return f"({to_linear(n.children[0])})"
    if k == "eq":
        return f"{to_linear(n.children[0])} = {to_linear(n.children[1])}"
    if k == "neg":
        c = n.children[0]
        s = to_linear(c)
        return f"−({s})" if _prec(c) < 3 else f"−{s}"
    if k == "pow":
        b, e = n.children
        bs = to_linear(b)
        if _prec(b) < 9:
            bs = f"({bs})"
        es = to_linear(e)
        sup = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
        if re.fullmatch(r"\d+", es):
            return bs + es.translate(sup)
        return f"{bs}^({es})" if _prec(e) < 9 else f"{bs}^{es}"
    if k == "sqrt":
        return f"√({to_linear(n.children[0])})"
    if k == "frac":
        a, b = (to_linear(c) for c in n.children)
        if _prec(n.children[0]) < 9:
            a = f"({a})"
        if _prec(n.children[1]) < 9:
            b = f"({b})"
        return f"{a}/{b}"
    if k == "bin":
        a, b = n.children
        sa, sb = to_linear(a), to_linear(b)
        p = PREC[n.op]
        if n.op == "/":
            if _prec(a) < 9:
                sa = f"({sa})"
            if _prec(b) < 9:
                sb = f"({sb})"
            return f"{sa}/{sb}"
        if _prec(a) < p:
            sa = f"({sa})"
        if _prec(b) < p or (n.op == "-" and _prec(b) == p):
            sb = f"({sb})"
        if n.op == "implicit":
            return f"{sa}{sb}" if not (sa[-1].isdigit() and sb[0].isdigit()) else f"{sa}·{sb}"
        sym = {"+": " + ", "-": " − ", "*": "·"}[n.op]
        return f"{sa}{sym}{sb}"
    raise MathError(f"cannot render {k}")


def to_latex(n: Node) -> str:
    k = n.kind
    if k == "num":
        return n.value
    if k == "var":
        return n.latex or n.value
    if k == "group":
        return rf"\left({to_latex(n.children[0])}\right)"
    if k == "eq":
        return f"{to_latex(n.children[0])} = {to_latex(n.children[1])}"
    if k == "neg":
        return f"-{to_latex(n.children[0])}"
    if k == "pow":
        b = to_latex(n.children[0])
        if _prec(n.children[0]) < 9:
            b = rf"\left({b}\right)"
        return f"{b}^{{{to_latex(n.children[1])}}}"
    if k == "sqrt":
        return rf"\sqrt{{{to_latex(n.children[0])}}}"
    if k == "frac" or (k == "bin" and n.op == "/"):
        return rf"\frac{{{to_latex(n.children[0])}}}{{{to_latex(n.children[1])}}}"
    if k == "bin":
        a, b = (to_latex(c) for c in n.children)
        if _prec(n.children[0]) < PREC[n.op]:
            a = rf"\left({a}\right)"
        if _prec(n.children[1]) < PREC[n.op] or (n.op == "-" and _prec(n.children[1]) == 1):
            b = rf"\left({b}\right)"
        return {"+": f"{a} + {b}", "-": f"{a} - {b}", "*": rf"{a} \cdot {b}", "implicit": f"{a} {b}"}[n.op]
    raise MathError(k)


def to_spoken(n: Node) -> str:
    k = n.kind
    if k == "num":
        return n.value
    if k == "var":
        if n.op:
            return n.op
        return f"capital {n.value}" if n.value.isupper() else n.value
    if k == "group":
        return f"open bracket, {to_spoken(n.children[0])}, close bracket"
    if k == "eq":
        return f"{to_spoken(n.children[0])}, equals, {to_spoken(n.children[1])}"
    if k == "neg":
        return f"minus {to_spoken(n.children[0])}"
    if k == "pow":
        e = n.children[1]
        if e.kind == "num" and e.value == "2":
            return f"{to_spoken(n.children[0])} squared"
        if e.kind == "num" and e.value == "3":
            return f"{to_spoken(n.children[0])} cubed"
        return f"{to_spoken(n.children[0])} to the power {to_spoken(e)}"
    if k == "sqrt":
        return f"square root of {to_spoken(n.children[0])}"
    if k == "frac" or (k == "bin" and n.op == "/"):
        return f"the fraction: numerator {to_spoken(n.children[0])}; denominator {to_spoken(n.children[1])}; end fraction"
    if k == "bin":
        word = {"+": "plus", "-": "minus", "*": "times", "implicit": ""}[n.op]
        a, b = to_spoken(n.children[0]), to_spoken(n.children[1])
        return f"{a} {word} {b}".replace("  ", " ")
    raise MathError(k)


@dataclass
class CompiledEquation:
    source: str
    ast: Node
    linear: str
    latex: str
    spoken: str
    version: str = COMPILER_VERSION

    def to_block(self, block_id: str) -> dict:
        return {"id": block_id, "type": "equation", "source": self.source, "ast": self.ast.to_dict(),
                "linear": self.linear, "latex": self.latex, "spoken": self.spoken, "compiler": self.version}


def compile_spoken(text: str) -> CompiledEquation:
    ast = _Parser(tokenize(text)).equation()
    return CompiledEquation(text, ast, to_linear(ast), to_latex(ast), to_spoken(ast))
