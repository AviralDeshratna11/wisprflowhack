import json
import zipfile

import numpy as np
import pytest

from swarlekhak.assistant import intents
from swarlekhak.assistant.orchestrator import build_wake_regex
from swarlekhak.assistant.states import IllegalTransition, S, StateMachine
from swarlekhak.audio.recorder import EvidenceVault, read_wav, wav_bytes
from swarlekhak.audio.resample import StreamingResampler, resample_array
from swarlekhak.engines.evidence_chain import verify_chain
from swarlekhak.examination.demo import make_demo_package
from swarlekhak.examination.package import PackageError, load_exam_package, write_exam_package
from swarlekhak.integrations.keys import ShortcutError, parse_shortcut
from swarlekhak.security import crypto
from swarlekhak.storage.db import Store


# ---------------------------------------------------------------- intents / command firewall
@pytest.mark.parametrize("text,intent,args", [
    ("Start my examination.", "start_exam", {}),
    ("read question one", "read_question", {"n": 1}),
    ("Read question 3", "read_question", {"n": 3}),
    ("go to question two", "go_question", {"n": 2}),
    ("start writing", "start_writing", {}),
    ("finish writing", "finish_writing", {}),
    ("replace that word with decreases", "replace", {"old": "that word", "new": "decreases"}),
    ("replace the second occurrence of increases with decreases", "replace",
     {"old": "increases", "new": "decreases", "ord": 2}),
    ("equation x squared plus two x end equation", "equation", {"body": "x squared plus two x"}),
    ("create node A", "diagram_create", {"name": "a"}),
    ("connect A to B labelled feedback", "diagram_connect", {"a": "a", "b": "b", "label": "feedback"}),
    ("play the graph", "play_graph", {}),
    ("submit examination", "submit", {}),
    ("go to sleep", "go_to_sleep", {}),
    ("yes", "confirm_yes", {}),
])
def test_intent_grammar(text, intent, args):
    c = intents.parse(text)
    assert c is not None and c.intent == intent
    for k, v in args.items():
        assert c.args[k] == v


@pytest.mark.parametrize("text", ["what is the answer to question two", "search the web for newton's law",
                                  "open notepad", "run cmd", "write my answer for me", "solve x squared"])
def test_no_academic_help_web_or_shell_intents(text):
    assert intents.parse(text) is None


def test_submission_and_destructive_edits_require_confirmation():
    assert intents.parse("submit examination").confirmation_required
    assert intents.parse("delete node A").confirmation_required
    assert intents.parse("delete the word beam").confirmation_required


def test_during_dictation_only_stop_commands_allowed():
    for text in ("read question one", "submit examination", "next question", "replace a with b"):
        assert S.DICTATING.value not in intents.parse(text).allowed_states
    for text in ("finish writing", "cancel writing", "mute"):
        assert S.DICTATING.value in intents.parse(text).allowed_states


def test_wake_regex_variants():
    rx = build_wake_regex("hey swar")
    for heard in ("hey swar start my examination", "he swore finish writing", "hey swear", "hay sore"):
        assert rx.search(heard), heard
    for heard in ("the star is bright", "hey start the engine", "say hello"):
        assert not rx.search(heard), heard


# ---------------------------------------------------------------- state machine
def test_dictation_pipeline_is_guarded():
    sm = StateMachine(S.PASSIVE_WAKE)
    with pytest.raises(IllegalTransition):
        sm.go(S.DICTATING)
    sm.go(S.DICTATION_STARTING)
    with pytest.raises(IllegalTransition):
        sm.go(S.SPEAKING)          # cannot talk over the start of dictation
    sm.go(S.DICTATING)
    with pytest.raises(IllegalTransition):
        sm.go(S.PASSIVE_WAKE)      # must go through STOPPING
    sm.go(S.DICTATION_STOPPING)
    sm.go(S.VERIFY_PENDING)


def test_submitted_is_terminal_for_work():
    sm = StateMachine(S.CONFIRM)
    sm.go(S.SUBMITTED)
    assert not sm.can(S.DICTATION_STARTING) and not sm.can(S.CONFIRM)
    sm.go(S.SPEAKING)
    sm.go(S.SUBMITTED)


# ---------------------------------------------------------------- audio utils
def test_streaming_resampler_matches_rate():
    sr = StreamingResampler(48000, 16000)
    x = (np.sin(np.arange(48000) * 2 * np.pi * 440 / 48000) * 10000).astype(np.int16)
    out = np.concatenate([sr.process(x[i:i + 2400]) for i in range(0, len(x), 2400)])
    assert abs(len(out) - 16000) <= 2
    out2 = resample_array(x[:44100], 44100, 16000)
    assert abs(len(out2) - 16000) <= 3


def test_vault_encrypts_and_roundtrips(tmp_path):
    (tmp_path / "a").mkdir()
    v = EvidenceVault(tmp_path / "a", tmp_path, lambda: b"x" * 32)
    pcm = (np.random.default_rng(0).standard_normal(1600) * 1000).astype(np.int16)
    data = wav_bytes(pcm, 16000)
    asset = v.store_wav("turn_1", data)
    raw = open(asset.path, "rb").read()
    assert b"RIFF" not in raw
    back, rate = read_wav(v.load_wav(asset.path))
    assert rate == 16000 and np.array_equal(back, pcm)


def test_partial_recordings_are_quarantined(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "q").mkdir()
    (tmp_path / "a" / "turn_x.part").write_bytes(b"RIFFpartial")
    v = EvidenceVault(tmp_path / "a", tmp_path / "q", lambda: b"x" * 32)
    moved = v.quarantine_partials()
    assert len(moved) == 1 and not list((tmp_path / "a").glob("*.part"))


# ---------------------------------------------------------------- crypto
def test_examiner_key_wrap_roundtrip_and_wrong_key():
    pem, pub = crypto.generate_examiner_keypair()
    ck = b"c" * 32
    w = crypto.wrap_key(ck, pub)
    assert crypto.unwrap_key(w, crypto.load_private_key(pem)) == ck
    other, _ = crypto.generate_examiner_keypair()
    with pytest.raises(ValueError):
        crypto.unwrap_key(w, crypto.load_private_key(other))


def test_tampered_ciphertext_rejected():
    blob = bytearray(crypto.encrypt(b"k" * 32, b"secret audio"))
    blob[-1] ^= 1
    with pytest.raises(Exception):
        crypto.decrypt(b"k" * 32, bytes(blob))


def test_dpapi_local_key(tmp_path):
    pytest.importorskip("win32crypt")
    ks = crypto.LocalKeyStore(tmp_path / "local.key")
    k1 = ks.key()
    assert len(k1) == 32 and (tmp_path / "local.key").read_bytes() != k1
    assert crypto.LocalKeyStore(tmp_path / "local.key").key() == k1


# ---------------------------------------------------------------- package validation
def test_demo_package_loads(tmp_path):
    pkg, key = make_demo_package(tmp_path)
    p = load_exam_package(pkg)
    assert len(p.questions) == 4 and p.graph_data["g1"]["series"][0]["points"][2] == [2, 4]


def _zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for n, d in entries.items():
            z.writestr(n, d)


def test_path_traversal_rejected(tmp_path):
    _zip(tmp_path / "bad.swar", {"../evil.txt": b"x", "manifest.json": b"{}"})
    with pytest.raises(PackageError, match="unsafe"):
        load_exam_package(tmp_path / "bad.swar")


def test_hash_mismatch_rejected(tmp_path):
    pkg, _ = make_demo_package(tmp_path)
    with zipfile.ZipFile(pkg) as z:
        files = {n: z.read(n) for n in z.namelist()}
    qs = json.loads(files["questions.json"])
    qs[0]["text"] = "tampered"
    files["questions.json"] = json.dumps(qs).encode()
    _zip(tmp_path / "t.swar", files)
    with pytest.raises(PackageError, match="hash mismatch"):
        load_exam_package(tmp_path / "t.swar")


def test_decompression_bomb_rejected(tmp_path):
    p = tmp_path / "bomb.swar"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", b"{}")
        z.writestr("questions.json", b"0" * (5 * 1024 * 1024))
    with pytest.raises(PackageError, match="ratio"):
        load_exam_package(p)


def test_undeclared_file_rejected(tmp_path):
    out = write_exam_package(tmp_path / "x.swar", {"exam_id": "x", "title": "t", "duration_minutes": 5},
                             [{"id": "q", "ordinal": 1, "text": "Q"}])
    with zipfile.ZipFile(out, "a") as z:
        z.writestr("extra.exe", b"MZ")
    with pytest.raises(PackageError, match="undeclared"):
        load_exam_package(out)


# ---------------------------------------------------------------- storage + hash chain
def _setup_store(tmp_path):
    pkg, _ = make_demo_package(tmp_path)
    p = load_exam_package(pkg)
    s = Store(tmp_path / "db.sqlite")
    s.import_exam(p.manifest, p.manifest_hash, p.questions, p.graph_data, str(pkg))
    sid = s.create_session(p.exam_id, "2026-11-25T09:00:00+00:00", "2026-11-25T09:30:00+00:00")
    q = s.questions(p.exam_id)[0]
    a = s.answer(sid, q["db_id"])
    return s, sid, a


def test_revisions_form_valid_chain_and_tamper_detected(tmp_path):
    s, sid, a = _setup_store(tmp_path)
    s.commit_revision(a["id"], "dictation_insertion", "first", [], {"x": 1})
    s.commit_revision(a["id"], "voice_replace", "second", [], {"x": 2})
    revs = s.revisions(sid)
    assert verify_chain(revs)[0]
    revs[0]["payload"]["text"] = "edited later"
    ok, idx, _ = verify_chain(revs)
    assert not ok and idx == 0


def test_answer_unique_per_session_question_and_restore_after_reopen(tmp_path):
    s, sid, a = _setup_store(tmp_path)
    s.commit_revision(a["id"], "keyboard_edit", "persisted text", [], {})
    q = s.questions("physics-mock-01")[0]
    assert s.answer(sid, q["db_id"])["id"] == a["id"]
    s.set_position(sid, 3)
    s.close()
    s2 = Store(tmp_path / "db.sqlite")
    assert s2.answer(sid, q["db_id"])["text"] == "persisted text"
    assert s2.session(sid)["last_question_ordinal"] == 3


def test_locked_after_submission(tmp_path):
    s, sid, a = _setup_store(tmp_path)
    s.record_submission("sub_1", sid, "p", "h")
    s.record_submission("sub_2", sid, "p2", "h2")   # idempotent: second insert ignored
    assert s.submission(sid)["id"] == "sub_1"
    with pytest.raises(PermissionError):
        s.commit_revision(a["id"], "keyboard_edit", "late edit", [], {})


def test_shortcut_parsing():
    assert parse_shortcut("ctrl+win+space") == [0xA2, 0x5B, 0x20]
    with pytest.raises(ShortcutError):
        parse_shortcut("ctrl+banana")
