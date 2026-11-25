"""PDF text-only question import (examiner authoring tool)."""
import json

import pytest

fitz = pytest.importorskip("fitz")

from swarlekhak.examination.package import load_exam_package  # noqa: E402
from swarlekhak.examination.pdf_import import build_package_from_pdf, extract_questions  # noqa: E402


def make_pdf(path, with_figure: bool):
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "Physics Mock Paper", fontsize=14)
    p1.insert_text((72, 110), "1. State Newton's second law of motion. (3 marks)")
    p1.insert_text((72, 130), "2. A ball is dropped from a height of 20 m. Calculate the time")
    p1.insert_text((72, 145), "taken to reach the ground. [4 marks]")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "3. The graph below shows velocity against time. Describe the motion.")
    if with_figure:
        p2.draw_rect(fitz.Rect(72, 100, 300, 250))
        for i in range(12):
            p2.draw_line((80 + i * 15, 240), (90 + i * 15, 240 - i * 10))
    doc.save(str(path))


def test_extracts_numbered_questions_and_marks(tmp_path):
    make_pdf(tmp_path / "p.pdf", with_figure=False)
    rep = extract_questions(tmp_path / "p.pdf")
    assert [q["ordinal"] for q in rep.questions] == [1, 2, 3]
    assert rep.questions[0]["marks"] == 3 and rep.questions[1]["marks"] == 4
    assert rep.questions[1]["text"].endswith("taken to reach the ground.")
    assert rep.needs_figure_description == []


def test_figures_are_never_interpreted_and_block_packaging(tmp_path):
    make_pdf(tmp_path / "p.pdf", with_figure=True)
    with pytest.raises(ValueError, match="q3"):
        build_package_from_pdf(tmp_path / "p.pdf", tmp_path / "out", "phys-pdf", "Physics", 30)
    qs = json.loads((tmp_path / "out" / "phys-pdf.questions.json").read_text(encoding="utf-8"))
    assert qs[2]["visual"]["description"] == ""          # nothing auto-generated about the figure


def test_package_from_pdf_without_figures(tmp_path):
    make_pdf(tmp_path / "p.pdf", with_figure=False)
    pkg, key, rep = build_package_from_pdf(tmp_path / "p.pdf", tmp_path / "out", "phys-pdf", "Physics", 30)
    p = load_exam_package(pkg)
    assert len(p.questions) == 3 and key.exists()
