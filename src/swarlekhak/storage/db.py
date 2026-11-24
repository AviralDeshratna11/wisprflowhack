"""SQLite storage: WAL, foreign keys, versioned migrations, transactional saves."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from swarlekhak.engines.evidence_chain import GENESIS, record_hash

MIGRATIONS: list[str] = [
    # v1
    """
    CREATE TABLE profiles(id TEXT PRIMARY KEY, preferences_json TEXT NOT NULL, updated_utc TEXT NOT NULL);
    CREATE TABLE exams(id TEXT PRIMARY KEY, title TEXT NOT NULL, manifest_hash TEXT NOT NULL,
        policy_json TEXT NOT NULL, manifest_json TEXT NOT NULL, graph_data_json TEXT NOT NULL DEFAULT '{}',
        package_path TEXT, imported_utc TEXT NOT NULL);
    CREATE TABLE questions(id TEXT PRIMARY KEY, exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL, content_json TEXT NOT NULL, UNIQUE(exam_id, ordinal));
    CREATE TABLE sessions(id TEXT PRIMARY KEY, exam_id TEXT NOT NULL REFERENCES exams(id),
        status TEXT NOT NULL CHECK(status IN ('active','submitted')), start_utc TEXT NOT NULL,
        deadline_utc TEXT NOT NULL, last_question_ordinal INTEGER NOT NULL DEFAULT 1, cursor_json TEXT);
    CREATE TABLE answers(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        question_id TEXT NOT NULL REFERENCES questions(id), current_revision TEXT, text TEXT NOT NULL DEFAULT '',
        blocks_json TEXT NOT NULL DEFAULT '[]', UNIQUE(session_id, question_id));
    CREATE TABLE revisions(id TEXT PRIMARY KEY, answer_id TEXT NOT NULL REFERENCES answers(id),
        session_id TEXT NOT NULL REFERENCES sessions(id), seq INTEGER NOT NULL, parent_id TEXT,
        event_type TEXT NOT NULL, payload_json TEXT NOT NULL, asset_hashes_json TEXT NOT NULL DEFAULT '[]',
        created_utc TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL UNIQUE, UNIQUE(session_id, seq));
    CREATE TABLE turns(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
        answer_id TEXT NOT NULL REFERENCES answers(id), source TEXT NOT NULL, start_sample INTEGER,
        end_sample INTEGER, start_mono REAL, end_mono REAL, revision_before TEXT, revision_after TEXT,
        status TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}', created_utc TEXT NOT NULL);
    CREATE TABLE audio_assets(id TEXT PRIMARY KEY, turn_id TEXT NOT NULL UNIQUE REFERENCES turns(id),
        path TEXT NOT NULL, sha256 TEXT NOT NULL, codec TEXT NOT NULL, sample_rate INTEGER NOT NULL,
        num_samples INTEGER NOT NULL, encrypted INTEGER NOT NULL, created_utc TEXT NOT NULL);
    CREATE TABLE alignments(id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id),
        revision_id TEXT NOT NULL REFERENCES revisions(id), model_version TEXT NOT NULL,
        spans_json TEXT NOT NULL, created_utc TEXT NOT NULL);
    CREATE TABLE reviews(id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions(id),
        turn_id TEXT NOT NULL REFERENCES turns(id), answer_id TEXT NOT NULL REFERENCES answers(id),
        span_json TEXT NOT NULL, category TEXT NOT NULL, clip_json TEXT NOT NULL, status TEXT NOT NULL,
        written TEXT NOT NULL, heard TEXT NOT NULL, methods_json TEXT NOT NULL, scores_json TEXT NOT NULL,
        resolution_revision TEXT, created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL);
    CREATE TABLE consents(id TEXT PRIMARY KEY, session_id TEXT, kind TEXT NOT NULL, granted INTEGER NOT NULL,
        text TEXT NOT NULL, created_utc TEXT NOT NULL);
    CREATE TABLE audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, kind TEXT NOT NULL,
        detail_json TEXT NOT NULL, created_utc TEXT NOT NULL);
    CREATE TABLE jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref_id TEXT NOT NULL, status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, error TEXT, created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL);
    CREATE TABLE submissions(id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id),
        path TEXT NOT NULL, sha256 TEXT NOT NULL, created_utc TEXT NOT NULL);
    CREATE INDEX ix_rev_answer ON revisions(answer_id, seq);
    CREATE INDEX ix_turn_answer ON turns(answer_id);
    CREATE INDEX ix_review_answer ON reviews(answer_id, status);
    CREATE INDEX ix_jobs_status ON jobs(status);
    """,
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, isolation_level=None)  # explicit transactions
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    # ---------------------------------------------------------------- infra
    def migrate(self) -> int:
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
        row = self.conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()
        current = row["v"] or 0
        for v, sql in enumerate(MIGRATIONS, start=1):
            if v <= current:
                continue
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                    self.conn.execute(stmt)
                self.conn.execute("INSERT INTO schema_version(version) VALUES (?)", (v,))
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return len(MIGRATIONS)

    def tx(self):
        store = self

        class _Tx:
            def __enter__(self):
                store.conn.execute("BEGIN IMMEDIATE")
                return store.conn

            def __exit__(self, et, ev, tb):
                store.conn.execute("ROLLBACK" if et else "COMMIT")
                return False
        return _Tx()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(args)).fetchone()

    def all(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(args)).fetchall()

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- profile
    def load_preferences_json(self, profile: str = "default") -> str:
        r = self.one("SELECT preferences_json FROM profiles WHERE id=?", (profile,))
        return r["preferences_json"] if r else ""

    def save_preferences_json(self, text: str, profile: str = "default") -> None:
        with self.tx() as c:
            c.execute("INSERT INTO profiles(id, preferences_json, updated_utc) VALUES(?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET preferences_json=excluded.preferences_json, "
                      "updated_utc=excluded.updated_utc", (profile, text, utc_now()))

    # ---------------------------------------------------------------- exams
    def import_exam(self, manifest: dict, manifest_hash: str, questions: list[dict], graph_data: dict,
                    package_path: str) -> str:
        exam_id = manifest["exam_id"]
        with self.tx() as c:
            existing = c.execute("SELECT manifest_hash FROM exams WHERE id=?", (exam_id,)).fetchone()
            if existing and existing["manifest_hash"] != manifest_hash:
                has_session = c.execute("SELECT 1 FROM sessions WHERE exam_id=?", (exam_id,)).fetchone()
                if has_session:
                    raise ValueError("A different version of this examination already has a session on this computer.")
                c.execute("DELETE FROM questions WHERE exam_id=?", (exam_id,))
                c.execute("DELETE FROM exams WHERE id=?", (exam_id,))
                existing = None
            if not existing:
                c.execute("INSERT INTO exams(id,title,manifest_hash,policy_json,manifest_json,graph_data_json,"
                          "package_path,imported_utc) VALUES(?,?,?,?,?,?,?,?)",
                          (exam_id, manifest.get("title", exam_id), manifest_hash, json.dumps(manifest.get("policy", {})),
                           json.dumps(manifest), json.dumps(graph_data), package_path, utc_now()))
                for q in questions:
                    c.execute("INSERT INTO questions(id, exam_id, ordinal, content_json) VALUES(?,?,?,?)",
                              (f"{exam_id}:{q['id']}", exam_id, int(q["ordinal"]), json.dumps(q)))
        return exam_id

    def exam(self, exam_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM exams WHERE id=?", (exam_id,))

    def questions(self, exam_id: str) -> list[dict]:
        rows = self.all("SELECT id, ordinal, content_json FROM questions WHERE exam_id=? ORDER BY ordinal", (exam_id,))
        out = []
        for r in rows:
            q = json.loads(r["content_json"])
            q["db_id"] = r["id"]
            out.append(q)
        return out

    # ---------------------------------------------------------------- sessions
    def active_session(self, exam_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM sessions WHERE exam_id=? ORDER BY start_utc DESC LIMIT 1", (exam_id,))

    def create_session(self, exam_id: str, start_utc: str, deadline_utc: str) -> str:
        sid = new_id("ses")
        with self.tx() as c:
            c.execute("INSERT INTO sessions(id, exam_id, status, start_utc, deadline_utc) VALUES(?,?,?,?,?)",
                      (sid, exam_id, "active", start_utc, deadline_utc))
        return sid

    def session(self, sid: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM sessions WHERE id=?", (sid,))

    def set_position(self, sid: str, ordinal: int, cursor: dict | None = None) -> None:
        with self.tx() as c:
            c.execute("UPDATE sessions SET last_question_ordinal=?, cursor_json=? WHERE id=?",
                      (ordinal, json.dumps(cursor or {}), sid))

    def set_session_status(self, sid: str, status: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE sessions SET status=? WHERE id=?", (status, sid))

    # ---------------------------------------------------------------- answers & revisions
    def answer(self, sid: str, question_db_id: str) -> sqlite3.Row:
        r = self.one("SELECT * FROM answers WHERE session_id=? AND question_id=?", (sid, question_db_id))
        if r:
            return r
        aid = new_id("ans")
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO answers(id, session_id, question_id) VALUES(?,?,?)", (aid, sid, question_db_id))
        return self.one("SELECT * FROM answers WHERE session_id=? AND question_id=?", (sid, question_db_id))

    def answers(self, sid: str) -> list[sqlite3.Row]:
        return self.all("SELECT a.*, q.ordinal FROM answers a JOIN questions q ON q.id=a.question_id "
                        "WHERE a.session_id=? ORDER BY q.ordinal", (sid,))

    def commit_revision(self, answer_id: str, event_type: str, text: str, blocks: list[dict],
                        change: dict | None = None, asset_hashes: list[str] | None = None) -> dict:
        """Atomically append a hash-chained revision and update the answer's current state."""
        with self.tx() as c:
            a = c.execute("SELECT * FROM answers WHERE id=?", (answer_id,)).fetchone()
            if a is None:
                raise KeyError(answer_id)
            sid = a["session_id"]
            st = c.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()
            if st["status"] != "active":
                raise PermissionError("This examination has been submitted and is locked.")
            last = c.execute("SELECT seq, hash FROM revisions WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                             (sid,)).fetchone()
            seq = (last["seq"] + 1) if last else 1
            prev_hash = last["hash"] if last else GENESIS
            rec = {
                "id": new_id("rev"), "answer_id": answer_id, "session_id": sid, "seq": seq,
                "parent_id": a["current_revision"], "event_type": event_type,
                "payload": {"text": text, "blocks": blocks, "change": change or {}},
                "created_utc": utc_now(), "asset_hashes": sorted(asset_hashes or []),
            }
            rec["prev_hash"] = prev_hash
            rec["hash"] = record_hash(rec, prev_hash)
            c.execute("INSERT INTO revisions(id, answer_id, session_id, seq, parent_id, event_type, payload_json,"
                      " asset_hashes_json, created_utc, prev_hash, hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (rec["id"], answer_id, sid, seq, rec["parent_id"], event_type, json.dumps(rec["payload"]),
                       json.dumps(rec["asset_hashes"]), rec["created_utc"], prev_hash, rec["hash"]))
            c.execute("UPDATE answers SET current_revision=?, text=?, blocks_json=? WHERE id=?",
                      (rec["id"], text, json.dumps(blocks), answer_id))
        return rec

    def revisions(self, sid: str) -> list[dict]:
        out = []
        for r in self.all("SELECT * FROM revisions WHERE session_id=? ORDER BY seq", (sid,)):
            out.append({"id": r["id"], "answer_id": r["answer_id"], "session_id": r["session_id"], "seq": r["seq"],
                        "parent_id": r["parent_id"], "event_type": r["event_type"],
                        "payload": json.loads(r["payload_json"]), "created_utc": r["created_utc"],
                        "asset_hashes": json.loads(r["asset_hashes_json"]), "prev_hash": r["prev_hash"],
                        "hash": r["hash"]})
        return out

    # ---------------------------------------------------------------- turns / audio
    def create_turn(self, turn_id: str, sid: str, answer_id: str, source: str, start_sample: int,
                    start_mono: float, revision_before: str | None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO turns(id, session_id, answer_id, source, start_sample, start_mono, revision_before,"
                      " status, created_utc) VALUES(?,?,?,?,?,?,?,?,?)",
                      (turn_id, sid, answer_id, source, start_sample, start_mono, revision_before, "recording", utc_now()))

    def finish_turn(self, turn_id: str, status: str, end_sample: int | None, end_mono: float | None,
                    revision_after: str | None, detail: dict | None = None) -> None:
        with self.tx() as c:
            c.execute("UPDATE turns SET status=?, end_sample=?, end_mono=?, revision_after=?, detail_json=? WHERE id=?",
                      (status, end_sample, end_mono, revision_after, json.dumps(detail or {}), turn_id))

    def turn(self, turn_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM turns WHERE id=?", (turn_id,))

    def turns(self, sid: str) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM turns WHERE session_id=? ORDER BY created_utc", (sid,))

    def add_audio_asset(self, turn_id: str, path: str, sha256: str, sample_rate: int, num_samples: int,
                        encrypted: bool, codec: str = "wav-pcm16") -> str:
        aid = new_id("aud")
        with self.tx() as c:
            c.execute("INSERT INTO audio_assets(id, turn_id, path, sha256, codec, sample_rate, num_samples, encrypted,"
                      " created_utc) VALUES(?,?,?,?,?,?,?,?,?)",
                      (aid, turn_id, path, sha256, codec, sample_rate, num_samples, int(encrypted), utc_now()))
        return aid

    def audio_for_turn(self, turn_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM audio_assets WHERE turn_id=?", (turn_id,))

    def audio_assets(self, sid: str) -> list[sqlite3.Row]:
        return self.all("SELECT a.* FROM audio_assets a JOIN turns t ON t.id=a.turn_id WHERE t.session_id=?", (sid,))

    # ---------------------------------------------------------------- verification
    def add_alignment(self, turn_id: str, revision_id: str, model_version: str, spans: dict) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO alignments(id, turn_id, revision_id, model_version, spans_json, created_utc)"
                      " VALUES(?,?,?,?,?,?)", (new_id("aln"), turn_id, revision_id, model_version, json.dumps(spans), utc_now()))

    def add_review(self, rv: dict) -> None:
        now = utc_now()
        with self.tx() as c:
            c.execute("INSERT INTO reviews(id, revision_id, turn_id, answer_id, span_json, category, clip_json, status,"
                      " written, heard, methods_json, scores_json, created_utc, updated_utc)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (rv["id"], rv["revision_id"], rv["turn_id"], rv["answer_id"], json.dumps(rv["span"]),
                       rv["category"], json.dumps(rv["clip"]), rv["status"], rv["written"], rv["heard"],
                       json.dumps(rv["methods"]), json.dumps(rv["scores"]), now, now))

    def update_review(self, review_id: str, status: str, resolution_revision: str | None = None) -> None:
        with self.tx() as c:
            c.execute("UPDATE reviews SET status=?, resolution_revision=COALESCE(?, resolution_revision), updated_utc=?"
                      " WHERE id=?", (status, resolution_revision, utc_now(), review_id))

    def reviews(self, sid: str | None = None, answer_id: str | None = None) -> list[dict]:
        if answer_id:
            rows = self.all("SELECT * FROM reviews WHERE answer_id=? ORDER BY created_utc", (answer_id,))
        else:
            rows = self.all("SELECT r.* FROM reviews r JOIN answers a ON a.id=r.answer_id WHERE a.session_id=?"
                            " ORDER BY r.created_utc", (sid,))
        return [self._review_dict(r) for r in rows]

    @staticmethod
    def _review_dict(r: sqlite3.Row) -> dict:
        return {"id": r["id"], "revision_id": r["revision_id"], "turn_id": r["turn_id"], "answer_id": r["answer_id"],
                "span": json.loads(r["span_json"]), "category": r["category"], "clip": json.loads(r["clip_json"]),
                "status": r["status"], "written": r["written"], "heard": r["heard"],
                "methods": json.loads(r["methods_json"]), "scores": json.loads(r["scores_json"]),
                "resolution_revision": r["resolution_revision"], "created_utc": r["created_utc"]}

    # ---------------------------------------------------------------- jobs / audit / consent / submission
    def job(self, kind: str, ref_id: str, status: str, error: str | None = None) -> None:
        now = utc_now()
        with self.tx() as c:
            r = c.execute("SELECT id, attempts FROM jobs WHERE kind=? AND ref_id=?", (kind, ref_id)).fetchone()
            if r:
                c.execute("UPDATE jobs SET status=?, error=?, attempts=attempts+?, updated_utc=? WHERE id=?",
                          (status, error, 1 if status == "running" else 0, now, r["id"]))
            else:
                c.execute("INSERT INTO jobs(id, kind, ref_id, status, attempts, error, created_utc, updated_utc)"
                          " VALUES(?,?,?,?,?,?,?,?)", (new_id("job"), kind, ref_id, status,
                                                         1 if status == "running" else 0, error, now, now))

    def pending_jobs(self, kind: str) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM jobs WHERE kind=? AND status IN ('queued','running')", (kind,))

    def audit(self, kind: str, detail: dict | None = None, session_id: str | None = None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO audit_events(session_id, kind, detail_json, created_utc) VALUES(?,?,?,?)",
                      (session_id, kind, json.dumps(detail or {}), utc_now()))

    def audit_events(self, sid: str) -> list[dict]:
        return [{"kind": r["kind"], "detail": json.loads(r["detail_json"]), "created_utc": r["created_utc"]}
                for r in self.all("SELECT * FROM audit_events WHERE session_id=? ORDER BY id", (sid,))]

    def add_consent(self, kind: str, granted: bool, text: str, session_id: str | None = None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO consents(id, session_id, kind, granted, text, created_utc) VALUES(?,?,?,?,?,?)",
                      (new_id("con"), session_id, kind, int(granted), text, utc_now()))

    def consents(self, sid: str | None = None) -> list[dict]:
        rows = self.all("SELECT * FROM consents WHERE session_id IS ? OR session_id=? ORDER BY created_utc", (None, sid))
        return [{"kind": r["kind"], "granted": bool(r["granted"]), "text": r["text"], "created_utc": r["created_utc"]}
                for r in rows]

    def submission(self, sid: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM submissions WHERE session_id=?", (sid,))

    def record_submission(self, sub_id: str, sid: str, path: str, sha256: str) -> None:
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO submissions(id, session_id, path, sha256, created_utc) VALUES(?,?,?,?,?)",
                      (sub_id, sid, path, sha256, utc_now()))
            c.execute("UPDATE sessions SET status='submitted' WHERE id=?", (sid,))
