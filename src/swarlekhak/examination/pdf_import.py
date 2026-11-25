"""Examiner-side authoring: build questions from a PDF question paper (TEXT ONLY).

Per the blueprint, PDFs are used for text only. Figures, graphs and tables in the PDF are never
interpreted automatically (that could reveal or distort a solution); every question whose page holds an
image or vector drawing is listed so the examiner adds an approved description or data before packaging.
Requires PyMuPDF (optional authoring dependency, not shipped in the student app).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

Q_START = re.compile(r"^\s*(?:Q(?:uestion)?\.?\s*)?(\d{1,2})\s*[.):]\s+(.*)$", re.IGNORECASE)
MARKS = re.compile(r"[\[(]\s*(\d{1,3})\s*marks?\s*[\])]", re.IGNORECASE)


@dataclass
class ImportReport:
    questions: list[dict]
    needs_figure_description: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def extract_questions(pdf_path: str | Path) -> ImportReport:
    import fitz  # PyMuPDF
    doc = fitz.open(str(pdf_path))
    current: dict | None = None
    questions: list[dict] = []
    figure_pages: set[int] = set()
    warnings: list[str] = []
    for pno, page in enumerate(doc, 1):
        if page.get_images(full=True) or len(page.get_drawings()) > 8:
            figure_pages.add(pno)
        for raw in page.get_text("text").splitlines():
            line = raw.strip()
            if not line:
                continue
            m = Q_START.match(line)
            if m and (not questions or int(m.group(1)) == questions[-1]["ordinal"] + 1):
                current = {"id": f"q{m.group(1)}", "ordinal": int(m.group(1)), "text": m.group(2).strip(),
                           "_pages": {pno}}
                questions.append(current)
            elif current is not None:
                current["text"] = (current["text"] + " " + line).strip()
                current["_pages"].add(pno)
    if not questions:
        warnings.append("No numbered questions found (expected lines like '1. ...' or 'Q2) ...').")
    needs = []
    for q in questions:
        mk = MARKS.search(q["text"])
        if mk:
            q["marks"] = int(mk.group(1))
            q["text"] = MARKS.sub("", q["text"]).strip()
        q["text"] = re.sub(r"\s+", " ", q["text"])
        if q["_pages"] & figure_pages:
            needs.append(q["id"])
            q["visual"] = {"type": "figure", "description": "",
                           "note": "PDF page contains a figure: the examiner must write an approved description "
                                   "or supply data in approved_graph_data.json before packaging."}
        del q["_pages"]
    return ImportReport(questions, needs, warnings)


def build_package_from_pdf(pdf_path: str | Path, out_dir: Path, exam_id: str, title: str, duration_minutes: int,
                           allow_missing_descriptions: bool = False) -> tuple[Path, Path, ImportReport]:
    """Write <exam_id>.questions.json (for examiner review) and, if every figure has a description,
    the .swar package plus examiner key."""
    from swarlekhak.examination.package import write_exam_package
    from swarlekhak.security.crypto import generate_examiner_keypair
    rep = extract_questions(pdf_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    qjson = out_dir / f"{exam_id}.questions.json"
    qjson.write_text(json.dumps(rep.questions, indent=2, ensure_ascii=False), encoding="utf-8")
    missing = [q["id"] for q in rep.questions if q.get("visual") and not q["visual"].get("description")]
    if missing and not allow_missing_descriptions:
        raise ValueError(f"Questions {', '.join(missing)} contain figures without an approved description. "
                         f"Edit {qjson.name}, then build with --questions-json.")
    pem, pub = generate_examiner_keypair()
    manifest = {"exam_id": exam_id, "title": title, "duration_minutes": duration_minutes,
                "examiner_public_key": pub,
                "policy": {"mode": "mock", "recording": "Each dictation turn is recorded as evidence and encrypted.",
                           "allow_typed_input": True, "audio_disclosure": "full", "source": "pdf text import"}}
    pkg = write_exam_package(out_dir / f"{exam_id}.swar", manifest, rep.questions)
    key = out_dir / f"{exam_id}.examiner-key.pem"
    key.write_bytes(pem)
    return pkg, key, rep
