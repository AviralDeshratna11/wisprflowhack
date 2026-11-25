"""Deterministic English number-word parsing shared by intents, normalization and math."""
from __future__ import annotations

SMALL = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SCALES = {"thousand": 1000, "million": 1_000_000}
ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "last": -1,
}
NUMBER_WORDS = set(SMALL) | set(TENS) | set(SCALES) | {"hundred"}


def parse_number_words(tokens: list[str], start: int) -> tuple[str | None, int]:
    """Parse a number spoken as words starting at tokens[start].

    Returns (canonical digit string, tokens consumed) or (None, 0).
    "twenty five" -> "25", "one hundred and five" -> "105", "nine point eight" -> "9.8".
    """
    n = len(tokens)
    i = start
    total = 0
    current = 0
    got = False
    last: str | None = None
    while i < n:
        t = tokens[i]
        if t in SMALL:
            v = SMALL[t]
            if last == "small" or (last == "tens" and (v >= 10 or v == 0)):
                break
            current += v
            last = "small"
            got = True
        elif t in TENS:
            if last in ("small", "tens"):
                break
            current += TENS[t]
            last = "tens"
            got = True
        elif t == "hundred":
            if not got or last == "hundred":
                break
            current *= 100
            last = "hundred"
        elif t in SCALES:
            if not got or last == "scale":
                break
            total += current * SCALES[t]
            current = 0
            last = "scale"
        elif (t == "and" and got and last in ("hundred", "scale") and i + 1 < n
              and (tokens[i + 1] in SMALL or tokens[i + 1] in TENS)):
            i += 1
            continue
        else:
            break
        i += 1
    if not got:
        return None, 0
    text = str(total + current)
    if i + 1 < n and tokens[i] == "point" and tokens[i + 1] in SMALL and SMALL[tokens[i + 1]] < 10:
        digits = []
        j = i + 1
        while j < n and tokens[j] in SMALL and SMALL[tokens[j]] < 10:
            digits.append(str(SMALL[tokens[j]]))
            j += 1
        text += "." + "".join(digits)
        i = j
    return text, i - start


_SMALL_W = {v: k for k, v in SMALL.items()}
_TENS_W = {v: k for k, v in TENS.items()}


def int_to_words(n: int) -> list[str]:
    if n < 0:
        return ["minus"] + int_to_words(-n)
    if n < 20:
        return [_SMALL_W[n]]
    if n < 100:
        t, u = divmod(n, 10)
        return [_TENS_W[t * 10]] + ([_SMALL_W[u]] if u else [])
    if n < 1000:
        h, r = divmod(n, 100)
        return [_SMALL_W[h], "hundred"] + (["and"] + int_to_words(r) if r else [])
    if n < 1_000_000:
        th, r = divmod(n, 1000)
        return int_to_words(th) + ["thousand"] + (int_to_words(r) if r else [])
    m, r = divmod(n, 1_000_000)
    return int_to_words(m) + ["million"] + (int_to_words(r) if r else [])


def number_to_words(s: str) -> list[str]:
    """'15' -> ['fifteen']; '9.8' -> ['nine', 'point', 'eight']."""
    if "." in s:
        a, b = s.split(".", 1)
        return int_to_words(int(a or 0)) + ["point"] + [_SMALL_W[int(d)] for d in b if d.isdigit()]
    return int_to_words(int(s))


def parse_int(text: str) -> int | None:
    """Parse '3', 'three', 'third', 'twenty one' -> int."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    if text in ORDINALS:
        return ORDINALS[text]
    toks = text.replace("-", " ").split()
    value, used = parse_number_words(toks, 0)
    if value is not None and used == len(toks) and "." not in value:
        return int(value)
    return None
