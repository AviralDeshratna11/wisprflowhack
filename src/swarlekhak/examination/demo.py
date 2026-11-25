"""Demo examination package (synthetic content, no personal data)."""
from __future__ import annotations

from pathlib import Path

from swarlekhak.examination.package import write_exam_package
from swarlekhak.security.crypto import generate_examiner_keypair

DEMO_QUESTIONS = [
    {"id": "q1", "ordinal": 1, "marks": 5,
     "text": "State the bending stress formula for a beam and explain what happens to the bending stress "
             "when the moment of inertia of the cross-section increases."},
    {"id": "q2", "ordinal": 2, "marks": 5,
     "text": "A block of mass 2 kilograms is pulled by a net force of 20 newtons on a frictionless surface. "
             "Calculate its acceleration and state the unit."},
    {"id": "q3", "ordinal": 3, "marks": 5,
     "text": "The graph shows the velocity of a cart over the first five seconds. "
             "Describe how the velocity changes over this interval.",
     "visual": {"type": "graph", "data_ref": "g1",
                "description": "Line graph. Horizontal axis: time in seconds, from 0 to 5. "
                               "Vertical axis: velocity in metres per second, from 0 to 4. "
                               "One series, labelled cart velocity, with six data points."}},
    {"id": "q4", "ordinal": 4, "marks": 5,
     "text": "Draw a block diagram of a closed-loop control system with an input, a controller, a plant and a "
             "sensor feeding back to the controller."},
]

DEMO_GRAPH = {
    "g1": {"title": "Cart velocity", "x_label": "time in seconds", "y_label": "velocity in metres per second",
           "series": [{"label": "cart velocity",
                       "points": [[0, 0], [1, 2], [2, 4], [3, 4], [4, 3], [5, 1]]}]},
}


def make_demo_package(out_dir: Path, exam_id: str = "physics-mock-01",
                      audio_disclosure: str = "full") -> tuple[Path, Path]:
    """Write <out_dir>/<exam_id>.swar and <out_dir>/<exam_id>.examiner-key.pem. Returns both paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pem, pub = generate_examiner_keypair()
    manifest = {
        "exam_id": exam_id,
        "title": "Physics and Engineering Mock Test (demo)",
        "duration_minutes": 30,
        "examiner_public_key": pub,
        "policy": {
            "mode": "mock",
            "recording": "Each dictation turn is recorded as evidence and encrypted. Passive listening is never saved.",
            "allow_typed_input": True,
            "audio_disclosure": audio_disclosure,  # full | review_clips (selective disclosure) | none
            "allow_structured_math": True,
            "allow_diagrams": True,
            "flow_external_processing_disclosed": True,
        },
    }
    pkg = write_exam_package(out_dir / f"{exam_id}.swar", manifest, DEMO_QUESTIONS, DEMO_GRAPH)
    key_path = out_dir / f"{exam_id}.examiner-key.pem"
    key_path.write_bytes(pem)
    return pkg, key_path
