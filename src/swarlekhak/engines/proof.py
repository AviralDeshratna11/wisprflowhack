"""Acoustic Proof Engine: compare the exact written text of a dictation turn with an
independent local ASR hypothesis of the turn's recorded audio.

What this is: deterministic token alignment (Levenshtein) between normalized
written tokens and ASR tokens with word timestamps, followed by rule-based
categorization of meaning-critical differences (numeric, negation, unit,
operator, term). Each discrepancy becomes a neutral "check passage" review
marker with an audio clip interval.

What this is NOT: proof that every word was spoken, an identity check, a
cheating score, or an academic-correctness check. ASR disagreement may be the
ASR's error; the student decides.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from swarlekhak.contracts import ReviewStatus
from swarlekhak.engines.normalize import NORMALIZER_VERSION, Tok, is_number, is_unit, normalize_text, normalize_words

ALIGNER_VERSION = "levenshtein-token-v3+nbest+constrained"

NEGATIONS = {"not", "no", "never", "nor", "none", "nothing", "neither", "without", "nobody", "nowhere"}
OPERATORS = {"plus", "minus", "times", "divided", "equals", "greater", "less", "power", "squared",
             "cubed", "root", "over", "per"}
STOPWORDS = {"a", "an", "the", "of", "to", "and", "in", "on", "at", "it", "that", "this", "for", "as",
             "with", "be", "so", "its", "by", "from", "or", "then", "which", "there", "here", "we", "i", "am"}
# Meaning-reversing word pairs: always meaning-critical even when they sound alike.
ANTONYM_STEMS = [
    ("increas", "decreas"), ("rise", "fall"), ("rises", "falls"), ("rais", "lower"), ("maxim", "minim"),
    ("high", "low"), ("more", "less"), ("greater", "smaller"), ("positiv", "negativ"), ("hot", "cold"),
    ("before", "after"), ("insid", "outsid"), ("above", "below"), ("input", "output"), ("true", "false"),
    ("gain", "los"), ("expan", "contract"), ("attract", "repel"), ("accelerat", "decelerat"), ("strong", "weak"),
    ("doubl", "halv"), ("direct", "invers"), ("parallel", "series"), ("absorb", "emit"), ("acid", "base"),
    ("oxidis", "reduc"), ("oxidiz", "reduc"), ("open", "clos"), ("left", "right"), ("upper", "lower"),
    ("transverse", "longitudinal"), ("start", "end"), ("first", "last"), ("north", "south"), ("east", "west"),
    ("anode", "cathode"), ("proton", "electron"), ("kinetic", "potential"), ("same", "different"),
]
_VOWELS = set("aeiouy")


def _skeleton(text: str) -> str:
    """Coarse consonant skeleton for 'sounds alike' checks between two recognisers' outputs."""
    t = "".join(ch for ch in text.lower() if ch.isalpha())
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("x", "ks"), ("c", "k"), ("q", "k"), ("z", "s"),
                 ("d", "t"), ("g", "k"), ("v", "b"), ("w", "")):
        t = t.replace(a, b)
    out = []
    for i, ch in enumerate(t):
        if ch in _VOWELS and i > 0:
            continue
        if out and out[-1] == ch:
            continue
        out.append(ch)
    return "".join(out)


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def sounds_alike(written: list[str], heard: list[str]) -> bool:
    a, b = _skeleton(" ".join(written)), _skeleton(" ".join(heard))
    if not a or not b:
        return False
    return _lev(a, b) / max(len(a), len(b)) <= 0.34


def is_antonym_swap(written: list[str], heard: list[str]) -> bool:
    for w in written:
        for h in heard:
            for x, y in ANTONYM_STEMS:
                if (w.startswith(x) and h.startswith(y)) or (w.startswith(y) and h.startswith(x)):
                    return True
    return False
CRITICAL = ("numeric", "negation", "unit", "operator")


class Transcriber(Protocol):
    version: str

    def transcribe(self, pcm16k) -> dict:  # {"text": str, "words": [{"word","start","end","conf"}]}
        ...


@dataclass
class Discrepancy:
    category: str
    written: str
    heard: str
    span: tuple[int, int]          # in the written turn text
    clip: tuple[float, float]
    approximate: bool
    hyp_conf: float | None


@dataclass
class VerificationResult:
    status: ReviewStatus
    discrepancies: list[Discrepancy] = field(default_factory=list)
    hypothesis: str = ""
    wer: float | None = None
    mean_conf: float | None = None
    methods: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    elapsed_s: float = 0.0
    duration_s: float = 0.0


def levenshtein_align(ref: list[str], hyp: list[str]) -> list[tuple[str, int | None, int | None]]:
    """Return ops: ('eq'|'sub'|'del'|'ins', ref_index, hyp_index). del = written but not heard."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            c = 0 if ref[i - 1] == hyp[j - 1] else 1
            d[i][j] = min(d[i - 1][j - 1] + c, d[i - 1][j] + 1, d[i][j - 1] + 1)
    ops: list[tuple[str, int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (0 if ref[i - 1] == hyp[j - 1] else 1):
            ops.append(("eq" if ref[i - 1] == hyp[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            ops.append(("ins", None, j - 1))
            j -= 1
    ops.reverse()
    return ops


def word_error_rate(ref: list[str], hyp: list[str]) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    ops = levenshtein_align(ref, hyp)
    return sum(1 for o in ops if o[0] != "eq") / len(ref)


def categorize(written: list[str], heard: list[str]) -> str | None:
    both = written + heard
    if any(is_number(w) for w in both):
        return "numeric"
    if set(written) & NEGATIONS ^ set(heard) & NEGATIONS:
        return "negation"
    if any(is_unit(w) for w in both):
        return "unit"
    content = [w for w in both if w not in STOPWORDS]
    if not content:
        return None          # function-word-only difference: not meaning-critical, not flagged
    if is_antonym_swap(written, heard):
        return "term"
    # The written text came from one recogniser (Flow) and the hypothesis from another (local ASR).
    # When the two sound alike, the likeliest explanation is a mishearing by the local ASR, not a
    # change of meaning, so it is not raised as a review (counted in WER only).
    if written and heard and sounds_alike(written, heard):
        return None
    if any(w in OPERATORS for w in both):
        return "operator"
    return "term"


class ProofEngine:
    def __init__(self, transcriber: Transcriber | None, sensitivity: str = "standard",
                 clip_pad_s: float = 0.35) -> None:
        self.transcriber = transcriber
        self.sensitivity = sensitivity
        self.clip_pad_s = clip_pad_s

    def methods(self) -> dict[str, str]:
        return {
            "asr": getattr(self.transcriber, "version", "none"),
            "alignment": ALIGNER_VERSION,
            "timestamps": "asr-word-timestamps; text-constrained (grammar) alignment for unheard spans",
            "rescoring": "n-best: a flag survives only if no recogniser alternative reads like the text",
            "normalizer": NORMALIZER_VERSION,
            "sensitivity": self.sensitivity,
        }

    def verify(self, pcm16k, written_text: str) -> VerificationResult:
        t_start = time.perf_counter()
        duration = len(pcm16k) / 16000.0 if pcm16k is not None else 0.0
        if self.transcriber is None:
            return VerificationResult(ReviewStatus.UNAVAILABLE, methods=self.methods(),
                                      error="no local ASR model available", duration_s=duration)
        if pcm16k is None or len(pcm16k) < 1600:
            return VerificationResult(ReviewStatus.UNAVAILABLE, methods=self.methods(),
                                      error="audio missing or shorter than 0.1 s", duration_s=duration)
        try:
            hyp = self.transcriber.transcribe(pcm16k)
        except Exception as exc:  # model failure must not lose data: report unavailable
            return VerificationResult(ReviewStatus.UNAVAILABLE, methods=self.methods(),
                                      error=f"ASR failed: {exc}", duration_s=duration)
        res = self.compare(written_text, hyp.get("words", []), duration)
        res.hypothesis = hyp.get("text", "")
        if res.discrepancies and hasattr(self.transcriber, "alternatives"):
            try:
                res = self._rescore_nbest(pcm16k, written_text, res, duration)
            except Exception:  # rescoring is an optimisation; never lose the primary result
                pass
        if any(d.approximate for d in res.discrepancies) and hasattr(self.transcriber, "constrained_times"):
            try:
                self._refine_clips(pcm16k, written_text, res, duration)
            except Exception:
                pass
        res.elapsed_s = time.perf_counter() - t_start
        return res

    def _rescore_nbest(self, pcm16k, written_text: str, res: VerificationResult, duration: float):
        """Most charitable reading: per segment pick the recogniser alternative closest to the written text.
        If the text is a plausible reading of the audio, the disagreement was the recogniser's top guess,
        not the text. Flags that persist in the closest reading are kept."""
        segs = self.transcriber.alternatives(pcm16k)
        if not segs:
            return res
        ref = [t.text for t in normalize_text(written_text)]
        chosen: list[dict] = []
        for alts in segs:
            best = min(alts, key=lambda ws: (
                word_error_rate(ref, [t.text for t in normalize_words(chosen + ws)])
                if ref else 0.0, alts.index(ws)))
            chosen += best
        alt = self.compare(written_text, chosen, duration)
        if len(alt.discrepancies) >= len(res.discrepancies):
            return res
        kept = []
        for d in res.discrepancies:   # keep original flags (with confidences) that still overlap a flag
            if any(a.span[0] <= d.span[1] and d.span[0] <= a.span[1] for a in alt.discrepancies):
                kept.append(d)
        res.discrepancies = kept
        res.status = ReviewStatus.CHECK if kept else ReviewStatus.STRONG
        return res

    def _refine_clips(self, pcm16k, written_text: str, res: VerificationResult, duration: float) -> None:
        """Locate written-but-unheard spans with text-constrained alignment instead of padded neighbours."""
        from swarlekhak.engines.numbers import number_to_words
        toks = normalize_text(written_text)
        words: list[str] = []
        owner: list[int] = []
        for i, t in enumerate(toks):
            ws = number_to_words(t.text) if is_number(t.text) else [t.text]
            words += ws
            owner += [i] * len(ws)
        aligned = self.transcriber.constrained_times(pcm16k, words)
        # map aligned words back to token indices in order (grammar output follows the written order)
        times: dict[int, tuple[float, float]] = {}
        j = 0
        for w in aligned:
            while j < len(words) and words[j] != w["word"]:
                j += 1
            if j >= len(words):
                break
            k = owner[j]
            a, b = times.get(k, (w["start"], w["end"]))
            times[k] = (min(a, w["start"]), max(b, w["end"]))
            j += 1
        for d in res.discrepancies:
            if not d.approximate:
                continue
            idx = [i for i, t in enumerate(toks) if t.start >= d.span[0] and t.end <= d.span[1] and i in times]
            if not idx:
                # deletion with no written tokens: use the aligned neighbours either side of the gap
                before = [times[i] for i, t in enumerate(toks) if t.end <= d.span[0] and i in times]
                after = [times[i] for i, t in enumerate(toks) if t.start >= d.span[1] and i in times]
                if before and after:
                    c0, c1 = before[-1][1], after[0][0]
                else:
                    continue
            else:
                c0, c1 = times[idx[0]][0], times[idx[-1]][1]
            c0 = max(0.0, c0 - self.clip_pad_s)
            c1 = min(duration, c1 + self.clip_pad_s)
            if 0 < c1 - c0 <= 6.0:
                d.clip = (round(c0, 3), round(c1, 3))
                d.approximate = False

    def compare(self, written_text: str, words: list[dict], duration: float) -> VerificationResult:
        ref = normalize_text(written_text)
        hyp = normalize_words(words)
        ref_w = [t.text for t in ref]
        hyp_w = [t.text for t in hyp]
        ops = levenshtein_align(ref_w, hyp_w)
        wer = sum(1 for o in ops if o[0] != "eq") / max(1, len(ref_w))
        confs = [t.conf for t in hyp if t.conf is not None]
        mean_conf = sum(confs) / len(confs) if confs else None

        # group consecutive non-eq ops into regions, remembering neighbouring matched tokens
        regions: list[tuple[list, Tok | None, Tok | None, Tok | None, Tok | None]] = []
        cur: list = []
        last_eq: tuple[Tok, Tok] | None = None
        pending_prev: tuple[Tok, Tok] | None = None
        for op in ops:
            if op[0] == "eq":
                if cur:
                    prev_r, prev_h = pending_prev if pending_prev else (None, None)
                    regions.append((cur, prev_r, prev_h, ref[op[1]], hyp[op[2]]))
                    cur = []
                last_eq = (ref[op[1]], hyp[op[2]])
            else:
                if not cur:
                    pending_prev = last_eq
                cur.append(op)
        if cur:
            prev_r, prev_h = pending_prev if pending_prev else (None, None)
            regions.append((cur, prev_r, prev_h, None, None))

        discrepancies: list[Discrepancy] = []
        for ops_r, prev_r, prev_h, next_r, next_h in regions:
            r_toks = [ref[o[1]] for o in ops_r if o[1] is not None]
            h_toks = [hyp[o[2]] for o in ops_r if o[2] is not None]
            cat = categorize([t.text for t in r_toks], [t.text for t in h_toks])
            if cat is None:
                continue
            h_conf = min((t.conf for t in h_toks if t.conf is not None), default=None)
            if cat == "term" and self.sensitivity == "standard":
                # a lone low-confidence ASR guess on a non-critical word is weak evidence
                if h_toks and h_conf is not None and h_conf < 0.5 and len(r_toks) <= 1:
                    continue
            # written span
            if r_toks:
                span = (r_toks[0].start, r_toks[-1].end)
            else:
                a = prev_r.end if prev_r else 0
                b = next_r.start if next_r else len(written_text)
                span = (a, max(a, b))
            written = written_text[span[0]:span[1]].strip()
            heard = " ".join(t.text for t in h_toks)
            # clip interval
            if h_toks and h_toks[0].t0 is not None:
                c0, c1 = h_toks[0].t0, h_toks[-1].t1 or h_toks[-1].t0
                approx = False
            else:
                c0 = prev_h.t1 if prev_h and prev_h.t1 is not None else 0.0
                c1 = next_h.t0 if next_h and next_h.t0 is not None else duration
                approx = True
                c0, c1 = c0 - 0.5, c1 + 0.5
            c0 = max(0.0, c0 - self.clip_pad_s)
            c1 = min(duration, c1 + self.clip_pad_s) if duration else c1 + self.clip_pad_s
            if c1 - c0 > 6.0:
                approx = True
            discrepancies.append(Discrepancy(cat, written, heard, span, (round(c0, 3), round(c1, 3)),
                                             approx, h_conf))
        status = ReviewStatus.CHECK if discrepancies else ReviewStatus.STRONG
        if not hyp_w and ref_w:
            status = ReviewStatus.UNAVAILABLE
        return VerificationResult(status, discrepancies, wer=wer, mean_conf=mean_conf,
                                  methods=self.methods(), duration_s=duration,
                                  error="ASR returned no words" if status == ReviewStatus.UNAVAILABLE else None)
