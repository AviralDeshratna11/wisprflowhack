"""Chemistry / table / pseudocode grammars and inline placement."""
import pytest

from swarlekhak.assistant import intents
from swarlekhak.engines.layout import anchor_for, interleave
from swarlekhak.engines.structured import Pseudocode, StructuredError, Table, compile_chemistry


@pytest.mark.parametrize("spoken,linear", [
    ("two hydrogen two plus oxygen two gives two hydrogen two oxygen", "2H₂ + O₂ → 2H₂O"),
    ("n a c l aqueous", "NaCl(aq)"),
    ("S O four charge two minus", "SO₄²⁻"),
    ("2 H2 + O2 -> 2 H2O", "2H₂ + O₂ → 2H₂O"),
    ("hydrogen two plus chlorine two reversibly gives two h c l", "H₂ + Cl₂ ⇌ 2HCl"),
    ("c o two", "CO₂"),                     # carbon dioxide, not cobalt
])
def test_chemistry_compiles_exactly_as_dictated(spoken, linear):
    assert compile_chemistry(spoken).linear == linear


def test_chemistry_never_balances():
    assert compile_chemistry("hydrogen two plus oxygen two gives hydrogen two oxygen").linear == "H₂ + O₂ → H₂O"


@pytest.mark.parametrize("bad", ["methane", "plus", "charge minus"])
def test_chemistry_rejects_unknown(bad):
    with pytest.raises(StructuredError):
        compile_chemistry(bad)


def test_table_operations():
    t = Table.create(2)
    t.set_heading(1, "time")
    t.set_heading(2, "velocity")
    t.set_cell(1, 1, "0")
    t.set_cell(2, 2, "4")              # dictating into the next row adds it
    assert t.rows == [["0", ""], ["", "4"]]
    assert "Row 2: time: empty; velocity: 4." in t.summary()
    with pytest.raises(StructuredError):
        t.set_cell(9, 1, "x")
    t.delete_row(1)
    assert len(t.rows) == 1


def test_pseudocode_lines_and_indent():
    p = Pseudocode()
    p.add_line("for each reading")
    p.change_indent(+1)
    p.add_line("add reading to total")
    p.change_indent(-1)
    p.add_line("print total")
    assert p.linear() == "for each reading\n    add reading to total\nprint total"
    assert "Line 2, indent 1" in p.read()


@pytest.mark.parametrize("text,intent,args", [
    ("chemical equation two hydrogen two plus oxygen two", "chemistry", {"body": "two hydrogen two plus oxygen two"}),
    ("create a table with three columns", "table_create", {"n": 3}),
    ("set heading two to Velocity", "table_heading", {"c": 2, "text": "Velocity"}),
    ("row two column one is 4.5", "table_set", {"r": 2, "c": 1, "text": "4.5"}),
    ("read row two", "table_read_row", {"r": 2}),
    ("delete row three", "table_delete_row", {"r": 3}),
    ("add line If total exceeds limit", "code_add_line", {"text": "If total exceeds limit"}),
    ("indent", "code_indent", {}),
    ("delete line two", "code_delete_line", {"r": 2}),
    ("read the pseudocode", "code_read", {}),
])
def test_structured_intents(text, intent, args):
    c = intents.parse(text)
    assert c and c.intent == intent
    for k, v in args.items():
        assert c.args[k] == v


def test_inline_anchor_and_interleave():
    text = "First paragraph.\n\nSecond paragraph."
    assert anchor_for(text, 3) == 1 and anchor_for(text, None) == 2 and anchor_for("", None) == 0
    seq = interleave(text, [{"type": "equation", "anchor_paragraph": 1}, {"type": "table"}])
    assert [k for k, _ in seq] == ["text", "block", "text", "block"]
