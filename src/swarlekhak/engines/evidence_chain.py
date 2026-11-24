"""SHA-256 hash chain over canonical revision records.

Detects accidental or casual later modification of the history. It cannot stop
a privileged attacker from rewriting the whole chain; an examiner-held
signature/anchor is a later institutional feature.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = "0" * 64
HASHED_FIELDS = ("id", "answer_id", "session_id", "seq", "parent_id", "event_type", "payload", "created_utc",
                 "asset_hashes")


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def record_hash(record: dict, prev_hash: str) -> str:
    body = {k: record.get(k) for k in HASHED_FIELDS}
    return hashlib.sha256(prev_hash.encode("ascii") + canonical_bytes(body)).hexdigest()


def verify_chain(records: list[dict]) -> tuple[bool, int | None, str]:
    """records ordered by seq; each has prev_hash and hash. Returns (ok, bad_index, message)."""
    prev = GENESIS
    for i, r in enumerate(records):
        if r.get("prev_hash") != prev:
            return False, i, f"revision {r.get('id')} does not link to the previous revision"
        if record_hash(r, prev) != r.get("hash"):
            return False, i, f"revision {r.get('id')} content does not match its hash"
        prev = r["hash"]
    return True, None, f"{len(records)} revisions verified"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
