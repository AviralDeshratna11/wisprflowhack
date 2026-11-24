"""Comparison-copy normalization with a mapping back to original character spans.

The original text is never modified. Normalization only produces tokens for
comparison. Negation, operators, quantities and units are canonicalized but
never removed, so "fifteen" vs "fifty" or "is" vs "is not" survive as differences
while "twenty newtons" == "20 N".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from swarlekhak.engines.numbers import NUMBER_WORDS, parse_number_words

NORMALIZER_VERSION = "norm-v2"

FILLERS = {"um", "uh", "er", "erm", "ah", "hmm", "uhm", "umm", "uhh"}
CONTRACTIONS = {
    "can't": ["can", "not"], "cannot": ["can", "not"], "won't": ["will", "not"],
    "shan't": ["shall", "not"], "ain't": ["is", "not"],
}
# "'s" after these is "is"; after other words it is treated as a possessive and dropped
IS_CONTRACTORS = {"it", "that", "there", "what", "he", "she", "here", "who", "where", "let", "this"}
SUFFIX_EXPANSIONS = {"'m": "am", "'re": "are", "'ll": "will", "'ve": "have"}
GREEK_SYMBOLS = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "θ": "theta", "λ": "lambda",
    "μ": "mu", "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi", "ω": "omega", "Δ": "delta",
    "Σ": "sigma", "Ω": "omega",
}
# spoken names of single letters, as a recogniser writes them, mapped on BOTH sides
LETTER_NAMES = {"why": "y", "ex": "x", "zee": "z", "zed": "z", "aitch": "h", "ess": "s", "eff": "f"}
# unit words (any position) -> canonical
UNIT_WORDS = {
    "newton": "newton", "newtons": "newton", "kilogram": "kilogram", "kilograms": "kilogram",
    "gram": "gram", "grams": "gram", "metre": "metre", "metres": "metre", "meter": "metre",
    "meters": "metre", "second": "second", "seconds": "second", "joule": "joule", "joules": "joule",
    "watt": "watt", "watts": "watt", "volt": "volt", "volts": "volt", "ampere": "ampere",
    "amperes": "ampere", "amp": "ampere", "amps": "ampere", "hertz": "hertz", "pascal": "pascal",
    "pascals": "pascal", "kelvin": "kelvin", "centimetre": "centimetre", "centimetres": "centimetre",
    "centimeter": "centimetre", "centimeters": "centimetre", "kilometre": "kilometre",
    "kilometres": "kilometre", "kilometer": "kilometre", "kilometers": "kilometre",
    "percent": "percent", "degree": "degree", "degrees": "degree", "ohm": "ohm", "ohms": "ohm",
    "litre": "litre", "litres": "litre", "liter": "litre", "liters": "litre", "mole": "mole", "moles": "mole",
    "millimetre": "millimetre", "millimetres": "millimetre", "millimeter": "millimetre",
    "millimeters": "millimetre",
}
# symbols only treated as units directly after a number
UNIT_SYMBOLS = {
    "n": "newton", "kg": "kilogram", "g": "gram", "m": "metre", "s": "second", "j": "joule",
    "w": "watt", "v": "volt", "a": "ampere", "hz": "hertz", "pa": "pascal", "k": "kelvin",
    "cm": "centimetre", "km": "kilometre", "%": "percent", "°": "degree", "ω": "ohm", "l": "litre",
    "mol": "mole", "sec": "second", "secs": "second", "mm": "millimetre",
}
UNITS = set(UNIT_WORDS.values())
OPERATOR_SYMBOLS = {
    "+": ["plus"], "=": ["equals"], "×": ["times"], "*": ["times"], "÷": ["divided", "by"],
    "/": ["divided", "by"], "-": ["minus"], "^": ["to", "the", "power", "of"], "<": ["less", "than"],
    ">": ["greater", "than"], "²": ["squared"], "³": ["cubed"],
}
WORD_SYNONYMS = {"equal": "equals", "multiplied": "times", "into": "times"}

_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_²³]+(?:'[^\W\d_²³]+)?|[+\-=×÷*/^<>%°Ω−–²³]", re.UNICODE)


@dataclass(frozen=True)
class Tok:
    text: str
    start: int          # char span in the original string
    end: int
    t0: float | None = None   # optional audio time (hypothesis tokens)
    t1: float | None = None
    conf: float | None = None


def _raw_tokens(text: str) -> list[Tok]:
    out: list[Tok] = []
    for m in _TOKEN_RE.finditer(text):
        s = m.group(0)
        if s in "−–":
            s = "-"
        if s == "-":
            a, b = m.start() - 1, m.end()
            # hyphen joining two words ("twenty-five", "well-known") is not a minus sign
            if a >= 0 and b < len(text) and text[a].isalpha() and text[b].isalpha():
                continue
        if s in GREEK_SYMBOLS:
            s = GREEK_SYMBOLS[s]
        elif s not in "²³":
            s = unicodedata.normalize("NFKC", s).casefold()
        if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):
            s = s.replace(",", "")
        out.append(Tok(s, m.start(), m.end()))
    return out


def normalize_tokens(toks: list[Tok]) -> list[Tok]:
    # 1. contractions / fillers / operator symbols
    stage: list[Tok] = []
    for t in toks:
        if t.text in FILLERS:
            continue
        suffix = next((suf for suf in SUFFIX_EXPANSIONS if t.text.endswith(suf) and len(t.text) > len(suf)), None)
        if t.text in CONTRACTIONS:
            stage.extend(replace(t, text=w) for w in CONTRACTIONS[t.text])
        elif suffix:
            stage.append(replace(t, text=t.text[:-len(suffix)]))
            stage.append(replace(t, text=SUFFIX_EXPANSIONS[suffix]))
        elif t.text.endswith("'s") and len(t.text) > 2:
            base = t.text[:-2]
            stage.append(replace(t, text=UNIT_WORDS.get(base, base) if base not in IS_CONTRACTORS else base))
            if base in IS_CONTRACTORS:
                stage.append(replace(t, text="is"))
        elif t.text in LETTER_NAMES:
            stage.append(replace(t, text=LETTER_NAMES[t.text]))
        elif t.text.endswith("n't"):
            stage.append(replace(t, text=t.text[:-3]))
            stage.append(replace(t, text="not"))
        elif t.text in OPERATOR_SYMBOLS:
            stage.extend(replace(t, text=w) for w in OPERATOR_SYMBOLS[t.text])
        else:
            stage.append(replace(t, text=WORD_SYNONYMS.get(t.text, t.text)))
    # 2. number words -> digits (merging spans / times)
    words = [t.text for t in stage]
    merged: list[Tok] = []
    i = 0
    while i < len(stage):
        if words[i] in NUMBER_WORDS:
            value, used = parse_number_words(words, i)
            if value is not None and used > 0:
                first, last = stage[i], stage[i + used - 1]
                confs = [t.conf for t in stage[i:i + used] if t.conf is not None]
                merged.append(Tok(value, first.start, last.end, first.t0, last.t1,
                                  min(confs) if confs else None))
                i += used
                continue
        merged.append(stage[i])
        i += 1
    # 3. units: words anywhere, symbols only after a number; "/" between units -> "per"
    out: list[Tok] = []
    for idx, t in enumerate(merged):
        prev_is_num = bool(out) and _is_number(out[-1].text)
        prev_is_unit = bool(out) and out[-1].text in UNITS
        if t.text in UNIT_WORDS:
            out.append(replace(t, text=UNIT_WORDS[t.text]))
        elif t.text in UNIT_SYMBOLS and (prev_is_num or (out and out[-1].text == "per")):
            out.append(replace(t, text=UNIT_SYMBOLS[t.text]))
        elif t.text == "divided" and prev_is_unit and idx + 2 < len(merged) and merged[idx + 1].text == "by" \
                and (merged[idx + 2].text in UNIT_SYMBOLS or merged[idx + 2].text in UNIT_WORDS):
            out.append(replace(t, text="per"))
        elif t.text == "by" and out and out[-1].text == "per":
            continue
        else:
            out.append(t)
    return out


def _is_number(s: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?", s))


def normalize_text(text: str) -> list[Tok]:
    return normalize_tokens(_raw_tokens(text))


def normalize_words(words: list[dict]) -> list[Tok]:
    """Normalize ASR word dicts {word,start,end,conf}. Spans index into the joined hypothesis string."""
    toks: list[Tok] = []
    pos = 0
    for w in words:
        word = str(w["word"]).casefold()
        for rt in _raw_tokens(word):
            toks.append(Tok(rt.text, pos + rt.start, pos + rt.end, float(w["start"]), float(w["end"]),
                            float(w.get("conf", 1.0))))
        pos += len(word) + 1
    return normalize_tokens(toks)


def is_number(s: str) -> bool:
    return _is_number(s)
