"""Examiner tool: PDF question paper -> reviewed questions JSON -> .swar exam package (text only).

  Step 1:  python scripts/make_exam_from_pdf.py paper.pdf --exam-id chem-mock-02 --title "Chemistry mock" --minutes 45
           -> writes <exam-id>.questions.json; lists questions whose page has a figure
  Step 2:  edit the JSON: fix any text, write an approved "description" for each figure (or add graph data)
  Step 3:  python scripts/make_exam_from_pdf.py --questions-json out/<exam-id>.questions.json --exam-id ... --title ...
           -> writes <exam-id>.swar and <exam-id>.examiner-key.pem (keep the key with the examiner)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from swarlekhak.examination.package import load_exam_package, write_exam_package  # noqa: E402
from swarlekhak.examination.pdf_import import build_package_from_pdf  # noqa: E402
from swarlekhak.security.crypto import generate_examiner_keypair  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("pdf", nargs="?")
ap.add_argument("--questions-json")
ap.add_argument("--exam-id", required=True)
ap.add_argument("--title", required=True)
ap.add_argument("--minutes", type=int, default=30)
ap.add_argument("--out", default="exam_build")
a = ap.parse_args()
out = Path(a.out)
if a.questions_json:
    qs = json.loads(Path(a.questions_json).read_text(encoding="utf-8"))
    missing = [q["id"] for q in qs if q.get("visual") and not q["visual"].get("description")]
    if missing:
        sys.exit(f"Approved descriptions still missing for: {', '.join(missing)}")
    pem, pub = generate_examiner_keypair()
    manifest = {"exam_id": a.exam_id, "title": a.title, "duration_minutes": a.minutes, "examiner_public_key": pub,
                "policy": {"mode": "mock", "recording": "Each dictation turn is recorded as evidence and encrypted.",
                           "allow_typed_input": True, "audio_disclosure": "full"}}
    out.mkdir(parents=True, exist_ok=True)
    pkg = write_exam_package(out / f"{a.exam_id}.swar", manifest, qs)
    (out / f"{a.exam_id}.examiner-key.pem").write_bytes(pem)
    load_exam_package(pkg)   # validate what we wrote
    print(f"package: {pkg}\nexaminer key: {out / (a.exam_id + '.examiner-key.pem')} (keep it with the examiner)")
elif a.pdf:
    try:
        pkg, key, rep = build_package_from_pdf(a.pdf, out, a.exam_id, a.title, a.minutes)
        print(f"{len(rep.questions)} questions. package: {pkg}\nexaminer key: {key}")
    except ValueError as exc:
        print(exc)
        print(f"Questions JSON written to {out / (a.exam_id + '.questions.json')}")
else:
    ap.error("give a PDF or --questions-json")
