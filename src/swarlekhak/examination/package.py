"""Local .swar examination package: zip with manifest.json, questions.json,
approved_graph_data.json and optional assets. Strict validation: safe paths,
size / entry / ratio limits, declared SHA-256 hashes, minimal schema checks."""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

FORMAT_VERSION = 1
MAX_ENTRIES = 200
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_RATIO = 100
REQUIRED = ("manifest.json", "questions.json")


class PackageError(ValueError):
    pass


@dataclass
class ExamPackage:
    path: str
    manifest: dict
    manifest_hash: str
    questions: list[dict]
    graph_data: dict = field(default_factory=dict)

    @property
    def exam_id(self) -> str:
        return self.manifest["exam_id"]


def safe_member_name(name: str) -> bool:
    if not name or "\\" in name or name.startswith("/") or ":" in name:
        return False
    p = PurePosixPath(name)
    return not any(part in ("..", "") for part in p.parts) and not p.is_absolute()


def read_zip_safely(path: str | Path, allowed_prefixes: tuple[str, ...] = ()) -> dict[str, bytes]:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageError(f"not a valid package: {exc}") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise PackageError("package has too many files")
        total = 0
        out: dict[str, bytes] = {}
        for info in infos:
            if info.is_dir():
                continue
            if not safe_member_name(info.filename):
                raise PackageError(f"unsafe path in package: {info.filename!r}")
            if info.file_size > MAX_FILE_BYTES:
                raise PackageError(f"file too large: {info.filename}")
            if info.compress_size and info.file_size / max(1, info.compress_size) > MAX_RATIO:
                raise PackageError(f"suspicious compression ratio: {info.filename}")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise PackageError("package is too large")
            with zf.open(info) as f:
                data = f.read(MAX_FILE_BYTES + 1)
            if len(data) != info.file_size or len(data) > MAX_FILE_BYTES:
                raise PackageError(f"size mismatch: {info.filename}")
            out[info.filename] = data
        return out


def load_exam_package(path: str | Path) -> ExamPackage:
    files = read_zip_safely(path)
    for req in REQUIRED:
        if req not in files:
            raise PackageError(f"missing {req}")
    manifest_bytes = files["manifest.json"]
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise PackageError(f"manifest.json is not valid JSON: {exc}") from exc
    _check_manifest(manifest)
    for name, digest in manifest.get("assets", {}).items():
        if name not in files:
            raise PackageError(f"declared asset missing: {name}")
        if hashlib.sha256(files[name]).hexdigest() != digest:
            raise PackageError(f"hash mismatch for {name}")
    for name in files:
        if name != "manifest.json" and name not in manifest.get("assets", {}):
            raise PackageError(f"undeclared file in package: {name}")
    questions = json.loads(files["questions.json"])
    _check_questions(questions)
    graph = json.loads(files["approved_graph_data.json"]) if "approved_graph_data.json" in files else {}
    for q in questions:
        ref = (q.get("visual") or {}).get("data_ref")
        if ref and ref not in graph:
            raise PackageError(f"question {q['id']} references missing graph data {ref}")
    return ExamPackage(str(path), manifest, hashlib.sha256(manifest_bytes).hexdigest(), questions, graph)


def _check_manifest(m: dict) -> None:
    if not isinstance(m, dict):
        raise PackageError("manifest must be an object")
    if m.get("format_version") != FORMAT_VERSION:
        raise PackageError(f"unsupported format_version {m.get('format_version')}")
    for k, t in (("exam_id", str), ("title", str), ("duration_minutes", (int, float)), ("assets", dict)):
        if not isinstance(m.get(k), t):
            raise PackageError(f"manifest.{k} missing or wrong type")
    if not m["exam_id"].replace("-", "").replace("_", "").isalnum():
        raise PackageError("exam_id may contain only letters, digits, - and _")
    if not 1 <= float(m["duration_minutes"]) <= 600:
        raise PackageError("duration_minutes out of range")
    pk = m.get("examiner_public_key")
    if pk is not None and (not isinstance(pk, str) or len(pk) < 40):
        raise PackageError("examiner_public_key invalid")


def _check_questions(qs) -> None:
    if not isinstance(qs, list) or not qs:
        raise PackageError("questions.json must be a non-empty list")
    seen_ids, seen_ord = set(), set()
    for q in qs:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not isinstance(q.get("text"), str):
            raise PackageError("each question needs string id and text")
        if not isinstance(q.get("ordinal"), int):
            raise PackageError(f"question {q.get('id')} needs integer ordinal")
        if q["id"] in seen_ids or q["ordinal"] in seen_ord:
            raise PackageError("duplicate question id or ordinal")
        seen_ids.add(q["id"])
        seen_ord.add(q["ordinal"])


def write_exam_package(out_path: str | Path, manifest: dict, questions: list[dict],
                       graph_data: dict | None = None, extra_files: dict[str, bytes] | None = None) -> Path:
    files: dict[str, bytes] = {"questions.json": json.dumps(questions, indent=2, ensure_ascii=False).encode("utf-8")}
    if graph_data:
        files["approved_graph_data.json"] = json.dumps(graph_data, indent=2, ensure_ascii=False).encode("utf-8")
    files.update(extra_files or {})
    manifest = dict(manifest)
    manifest.setdefault("format_version", FORMAT_VERSION)
    manifest["assets"] = {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
    files["manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    out_path = Path(out_path)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    os.replace(tmp, out_path)
    return out_path
