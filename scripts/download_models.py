"""Download models listed in models/manifest.json, verify SHA-256, extract. Re-runnable."""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def main() -> int:
    manifest = json.loads((ROOT / "models" / "manifest.json").read_text(encoding="utf-8"))
    dl = ROOT / "models" / "_download"
    dl.mkdir(parents=True, exist_ok=True)
    for m in manifest["models"]:
        target = ROOT / "models" / m["name"]
        if (target / "am" / "final.mdl").exists():
            print(f"[ok] {m['name']} already present")
            continue
        z = dl / f"{m['name']}.zip"
        if not z.exists() or sha256(z) != m["sha256"].upper():
            print(f"downloading {m['url']} ...")
            urllib.request.urlretrieve(m["url"], z)
        got = sha256(z)
        if got != m["sha256"].upper():
            print(f"[FAIL] checksum mismatch for {m['name']}: {got}")
            return 1
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                if name.startswith("/") or ".." in Path(name).parts:
                    print(f"[FAIL] unsafe path in archive: {name}")
                    return 1
            zf.extractall(ROOT / "models")
        print(f"[ok] {m['name']} verified ({got[:12]}...) and extracted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
