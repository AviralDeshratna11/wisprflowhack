import numpy as np
import pytest

from swarlekhak.engines.cursor import AudioCursor, CursorError
from swarlekhak.engines.diagram import Diagram, DiagramError
from swarlekhak.engines.math_compiler import MathError, compile_spoken
from swarlekhak.engines.normalize import normalize_text
from swarlekhak.engines.numbers import parse_int, parse_number_words
from swarlekhak.engines.proof import ProofEngine, levenshtein_align, sounds_alike, word_error_rate
from swarlekhak.engines import sonify


# ---------------------------------------------------------------- numbers / normalization
@pytest.mark.parametrize("words,expected", [
    ("twenty five", "25"), ("one hundred and five", "105"), ("nine point eight", "9.8"),
    ("fifteen", "15"), ("fifty", "50"), ("two thousand twenty six", "2026"), ("zero", "0"),
])
def test_number_words(words, expected):
    assert parse_number_words(words.split(), 0)[0] == expected


def test_number_words_do_not_merge_digit_sequences():
    assert parse_number_words("five five".split(), 0) == ("5", 1)


def test_parse_int_variants():
    assert parse_int("three") == 3 and parse_int("3") == 3 and parse_int("second") == 2 and parse_int("x") is None


def toks(s):
    return [t.text for t in normalize_text(s)]


def test_equivalent_number_unit_forms_normalize_identically():
    assert toks("twenty newtons") == toks("20 N")
    assert toks("ten metres per second squared") == toks("10 m/s²")
    assert toks("sixty percent") == toks("60%")
    assert toks("one thousand kilograms") == toks("1,000 kg")


def test_meaning_critical_differences_survive_normalization():
    assert toks("fifteen") != toks("fifty")
    assert toks("is not") != toks("is")
    assert toks("isn't") == toks("is not")
    assert toks("2 kg") != toks("2 g")
    assert toks("a + b") != toks("a - b")


def test_normalized_tokens_map_back_to_original_spans():
    text = "The force is 20 N."
    for t in normalize_text(text):
        assert text[t.start:t.end]


def test_fillers_removed_and_hyphenated_words_not_minus():
    assert "minus" not in toks("twenty-five well-known")
    assert toks("um the energy") == toks("the energy")


# ---------------------------------------------------------------- proof engine (no model)
def words(s, t0=0.0):
    out, t = [], t0
    for w in s.split():
        out.append({"word": w, "start": t, "end": t + 0.3, "conf": 0.95})
        t += 0.35
    return out


@pytest.mark.parametrize("written,heard,category", [
    ("The force is 50 N.", "the force is fifteen newtons", "numeric"),
    ("The reaction is reversible.", "the reaction is not reversible", "negation"),
    ("The mass is 2 g.", "the mass is two kilograms", "unit"),
    ("Force equals mass plus acceleration.", "force equals mass times acceleration", "operator"),
    ("Stress increases.", "stress decreases", "term"),
])
def test_planted_discrepancies_are_flagged(written, heard, category):
    r = ProofEngine(None).compare(written, words(heard), 5.0)
    assert r.status.value == "check_passage"
    assert category in [d.category for d in r.discrepancies]


@pytest.mark.parametrize("written,heard", [
    ("20 N is the net force.", "twenty newtons is the net force"),
    ("The current is 3.5 A.", "the current is three point five amperes"),
    ("The gas is ideal.", "the guess is ideal"),            # ASR mishearing, sounds alike
    ("The energy is conserved.", "um the energy is conserved"),
])
def test_equivalent_or_soundalike_not_flagged(written, heard):
    r = ProofEngine(None).compare(written, words(heard), 5.0)
    assert r.discrepancies == []


def test_clip_interval_localizes_discrepancy():
    r = ProofEngine(None).compare("a b c d 50 e", words("a b c d fifteen e"), 3.0)
    (d,) = r.discrepancies
    assert d.clip[0] <= 1.4 <= d.clip[1] and d.clip[1] - d.clip[0] < 1.5 and not d.approximate


def test_deleted_word_gets_approximate_clip():
    r = ProofEngine(None).compare("it does increase", words("it does not increase"), 2.0)
    assert r.discrepancies[0].category == "negation"


def test_no_model_means_unavailable_not_pass():
    r = ProofEngine(None).verify(np.zeros(16000, dtype=np.int16), "text")
    assert r.status.value == "verification_unavailable"


def test_soundalike_heuristic():
    assert sounds_alike(["gas"], ["guess"]) and sounds_alike(["mass", "is"], ["masses"])
    assert not sounds_alike(["hot"], ["cold"])


def test_antonyms_always_flagged():
    r = ProofEngine(None).compare("It increases.", words("it decreases"), 2.0)
    assert r.discrepancies and r.discrepancies[0].category == "term"


def test_wer_and_alignment():
    assert word_error_rate(["a", "b", "c"], ["a", "x", "c"]) == pytest.approx(1 / 3)
    ops = levenshtein_align(["a", "b"], ["a", "b", "c"])
    assert [o[0] for o in ops] == ["eq", "eq", "ins"]


# ---------------------------------------------------------------- math compiler
@pytest.mark.parametrize("spoken,linear", [
    ("x squared plus two x", "x² + 2x"),
    ("sigma equals M times Y divided by I", "σ = (M·Y)/I"),
    ("the fraction a plus b over c end fraction equals one", "(a + b)/c = 1"),
    ("y equals open bracket x plus one close bracket cubed", "y = (x + 1)³"),
    ("capital F equals m a", "F = ma"),
    ("a minus open bracket b minus c close bracket", "a − (b − c)"),
])
def test_math_compiles(spoken, linear):
    assert compile_spoken(spoken).linear == linear


def test_math_does_not_evaluate_or_simplify():
    eq = compile_spoken("two plus two")
    assert eq.linear == "2 + 2"


@pytest.mark.parametrize("bad", ["x equals banana", "open bracket x plus one", "x plus", "equals"])
def test_math_rejects_unsupported(bad):
    with pytest.raises(MathError):
        compile_spoken(bad)


def test_math_latex_and_spoken_readback():
    eq = compile_spoken("sigma equals M times Y divided by I")
    assert eq.latex == r"\sigma = \frac{M \cdot Y}{I}"
    assert "numerator" in eq.spoken and "denominator" in eq.spoken


# ---------------------------------------------------------------- diagram
def test_diagram_operations_and_readback():
    d = Diagram()
    d.create("a")
    d.create("B")
    d.connect("A", "b", "signal")
    assert "A -> B (signal)" in d.adjacency_text()
    with pytest.raises(DiagramError):
        d.create("A")
    with pytest.raises(DiagramError):
        d.connect("A", "C")
    d.rename("B", "Plant")
    assert "Plant" in d.list_nodes()
    d.delete("A")
    assert d.edges == []
    d.undo()
    assert len(d.edges) == 1
    assert "->" in d.to_dot()


def test_diagram_layout_adds_no_edges():
    d = Diagram()
    for n in "ABCD":
        d.create(n)
    d.layout(400, 300)
    assert d.edges == []


# ---------------------------------------------------------------- cursor
def test_cursor_navigation_and_edits():
    c = AudioCursor("Stress increases. Strain increases too.\n\nSecond paragraph.")
    assert c.read_sentence("next").startswith("Sentence 1 of 3")
    assert "Strain" in c.read_sentence("next")
    assert c.read_paragraph(2).endswith("Second paragraph.")
    with pytest.raises(CursorError):
        c.find("increases")  # ambiguous: needs an ordinal
    sp = c.find("increases", 2)
    p = c.propose_replace("that word", "decreases")
    assert p.new_text.startswith("Stress increases. Strain decreases too.")
    assert sp.start == p.span.start


def test_cursor_insert_and_delete():
    c = AudioCursor("The beam bends.")
    assert c.propose_insert("steel", "before", "beam").new_text == "The steel beam bends."
    assert c.propose_delete("beam").new_text == "The bends."
    c.find("bends")
    assert c.spell_current_word().startswith("bends. b, e, n, d, s")


# ---------------------------------------------------------------- sonification
def test_sonification_frequency_mapping():
    assert sonify.frequencies([0, 5, 10]) == [220.0, 550.0, 880.0]
    assert sonify.frequencies([3, 3]) == [550.0, 550.0]
    pcm = sonify.render_series([0, 1, 2])
    assert pcm.dtype == np.float32 and np.abs(pcm).max() <= 0.26
    t = sonify.tone(440, 100)
    assert abs(t[0]) < 0.01 and abs(t[-1]) < 0.01  # envelope: no clicks
