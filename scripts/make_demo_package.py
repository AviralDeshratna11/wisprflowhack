"""Write the demo examination package and a separate examiner key.

    python scripts/make_demo_package.py [--out DIR]

Default: the per-user SwarLekhak exams folder (key goes to the examiner folder).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from swarlekhak.app import demo_setup  # noqa: E402
from swarlekhak.examination.demo import make_demo_package  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out")
a = ap.parse_args()
if a.out:
    pkg, key = make_demo_package(Path(a.out))
    print(json.dumps({"exam_package": str(pkg), "examiner_key": str(key)}, indent=2))
else:
    print(json.dumps(demo_setup(), indent=2))
