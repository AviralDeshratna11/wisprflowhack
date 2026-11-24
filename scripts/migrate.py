"""Apply SQLite schema migrations to the per-user database (idempotent)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from swarlekhak.config import data_dir  # noqa: E402
from swarlekhak.storage.db import Store  # noqa: E402

if __name__ == "__main__":
    db = data_dir() / "swarlekhak.db"
    s = Store(db)
    v = s.one("SELECT MAX(version) v FROM schema_version")["v"]
    print(f"{db}: schema version {v}")
