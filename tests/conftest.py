"""Shared test doubles. These are explicitly injected TEST adapters; production
code never falls back to them silently."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIX = ROOT / "tests" / "fixtures" / "audio"

from swarlekhak.audio.recorder import EvidenceVault  # noqa: E402
from swarlekhak.config import Preferences, VOSK_MODEL_NAME  # noqa: E402
from swarlekhak.contracts import TaskResult  # noqa: E402
from swarlekhak.examination.demo import make_demo_package  # noqa: E402
from swarlekhak.storage.db import Store  # noqa: E402

MODEL = ROOT / "models" / VOSK_MODEL_NAME
has_model = pytest.mark.skipif(not (MODEL / "am" / "final.mdl").exists(), reason="Vosk model not downloaded")


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.now = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def utc(self):
        return self.now


class _Handle:
    def __init__(self, t, fn):
        self.t, self.fn, self.cancelled = t, fn, False

    def cancel(self):
        self.cancelled = True


class FakeScheduler:
    def __init__(self, clock: FakeClock):
        self.clock, self.q = clock, []

    def __call__(self, secs, fn):
        h = _Handle(self.clock.t + secs, fn)
        self.q.append(h)
        return h

    def advance(self, secs: float) -> None:
        end = self.clock.t + secs
        while True:
            due = sorted((h for h in self.q if not h.cancelled and h.t <= end), key=lambda h: h.t)
            if not due:
                break
            h = due[0]
            self.q.remove(h)
            self.clock.t = max(self.clock.t, h.t)
            h.fn()
        self.clock.t = end


class FakeTTS:
    def __init__(self):
        self.spoken: list[str] = []
        self.rate = 0

    def speak(self, text, on_done=None):
        self.spoken.append(text)
        if on_done:
            on_done()

    def stop(self):
        pass

    def set_rate(self, r):
        self.rate = r

    @property
    def last(self) -> str:
        return self.spoken[-1] if self.spoken else ""


class FakePlayer:
    def __init__(self):
        self.played = []

    def play(self, pcm, rate, on_done=None):
        self.played.append((len(pcm), rate))
        if on_done:
            on_done()

    def stop(self):
        pass

    def earcon(self, kind):
        pass


class FakeEditor:
    def __init__(self):
        self.text = ""
        self.readonly = False
        self.foreground = True
        self.selection = None
        self.on_change = None

    def get_text(self):
        return self.text

    def set_text(self, t):
        self.text = t
        if self.on_change:
            self.on_change()

    def type(self, t):  # simulate keyboard typing
        self.text += t
        if self.on_change:
            self.on_change()

    def focus_for_dictation(self):
        return self.foreground

    def is_foreground(self):
        return self.foreground

    def set_readonly(self, ro):
        self.readonly = ro

    def select(self, a, b):
        self.selection = (a, b)


class FakeUI:
    def __init__(self):
        self.states, self.messages, self.reviews_seen, self.blocks_seen = [], [], [], []
        self.session_info = {}

    def post(self, fn):
        fn()

    def state(self, s):
        self.states.append(s)

    def message(self, t):
        self.messages.append(t)

    def session(self, info):
        self.session_info.update(info)

    def question(self, q, idx, total):
        pass

    def blocks(self, b):
        self.blocks_seen.append(b)

    def reviews(self, r):
        self.reviews_seen.append(r)

    def recording(self, on):
        pass


class FakeFlow:
    """Simulates Wispr Flow pasting text after the stop shortcut (TEST ONLY)."""
    label = "live"

    def __init__(self, editor: FakeEditor, scheduler: FakeScheduler):
        self.editor, self.scheduler = editor, scheduler
        self.next_text = ""
        self.calls = []
        self.fail_start = None

    def probe(self):
        return {"ready": True}

    def start(self, turn_id):
        self.calls.append("start")
        if self.fail_start:
            return TaskResult(False, "flow_not_running", self.fail_start)
        return TaskResult(True, "start_requested")

    def request_stop(self):
        self.calls.append("stop")
        text = self.next_text
        if text:
            self.scheduler(0.8, lambda: self.editor.type(text))
        return TaskResult(True, "stop_requested")

    def cancel(self):
        self.calls.append("cancel")
        return TaskResult(True, "cancel_requested")

    def status(self):
        return "test"


class FakeRecorder:
    """Stores a given WAV as the turn evidence through the real (encrypting) vault."""

    def __init__(self, vault: EvidenceVault):
        self.vault = vault
        self.wav_path: Path | None = None
        self.turn = None

    def start(self, turn_id):
        self.turn = turn_id
        return 0

    def stop(self):
        if not self.wav_path:
            return None, 0
        a = self.vault.store_wav(self.turn, self.wav_path.read_bytes())
        return a, a.num_samples


class SyncVerifier:
    def __init__(self, service):
        self.service = service

    def submit(self, job, on_done):
        on_done(job, self.service.run_sync(job["audio_path"], job["written_text"]))


class ScriptedTranscriber:
    """Returns a fixed hypothesis (used when the Vosk model is unavailable). TEST ONLY."""
    version = "scripted-test"

    def __init__(self, text: str):
        self.text = text

    def transcribe(self, pcm):
        words, t = [], 0.0
        for w in self.text.split():
            words.append({"word": w, "start": t, "end": t + 0.3, "conf": 0.95})
            t += 0.35
        return {"text": self.text, "words": words}


@pytest.fixture
def env(tmp_path):
    """A fully wired orchestrator over a real SQLite store, real vault and demo package."""
    from swarlekhak.assistant.orchestrator import Orchestrator
    from swarlekhak.assistant.verification import VerificationService
    from swarlekhak.export.submission import build_submission

    exams = tmp_path / "exams"
    pkg, key = make_demo_package(exams)
    store = Store(tmp_path / "test.db")
    vault_dir = tmp_path / "audio"
    vault_dir.mkdir()
    (tmp_path / "q").mkdir()
    vault = EvidenceVault(vault_dir, tmp_path / "q", lambda: b"k" * 32)
    clock = FakeClock()
    sched = FakeScheduler(clock)
    editor, ui, tts, player = FakeEditor(), FakeUI(), FakeTTS(), FakePlayer()
    flow = FakeFlow(editor, sched)
    rec = FakeRecorder(vault)
    transcriber = {"t": None}

    def factory():
        if transcriber["t"] is not None:
            return transcriber["t"]
        from swarlekhak.audio.asr import VoskTranscriber
        return VoskTranscriber(MODEL)
    service = VerificationService(vault, factory, lambda fn: fn())
    prefs = Preferences(wake_enabled=True, passive_wake_consent=True)
    orch = Orchestrator(store, prefs, tts=tts, flow=flow, recorder=rec, player=player, vault=vault,
                        verifier=SyncVerifier(service), editor=editor, ui=ui, scheduler=sched,
                        submitter=lambda sid, exam: build_submission(store, sid, exam, vault, tmp_path / "subs"),
                        clock=clock, utcnow=clock.utc, exams_dir=exams)
    editor.on_change = orch.on_editor_changed
    orch.muted = False
    orch.sm.go(orch._rest())

    class Env:
        pass
    e = Env()
    e.__dict__.update(dict(orch=orch, store=store, vault=vault, clock=clock, sched=sched, editor=editor, ui=ui,
                           tts=tts, player=player, flow=flow, rec=rec, pkg=pkg, key=key, tmp=tmp_path,
                           transcriber=transcriber, service=service))
    return e


def demo_fixture(qid: str) -> dict:
    for f in json.loads((FIX / "demo_fixtures.json").read_text(encoding="utf-8")):
        if f["id"] == qid:
            f["audio_path"] = FIX / f["audio"]
            return f
    raise KeyError(qid)
