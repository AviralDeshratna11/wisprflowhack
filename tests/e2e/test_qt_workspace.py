"""Qt end-to-end: real WorkspaceWindow widgets, Qt timers/marshalling, labelled
FixtureFlowBridge inserting into the real QPlainTextEdit, real encrypted vault,
real Vosk verification. TTS/player are silent test doubles. The OS-foreground
check is replaced with widget focus because the offscreen Qt platform has no
foreground window (TEST adapter, labelled)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import FakePlayer, FakeTTS, has_model, MODEL  # noqa: E402
from swarlekhak.assistant.orchestrator import Orchestrator  # noqa: E402
from swarlekhak.assistant.states import S  # noqa: E402
from swarlekhak.assistant.verification import VerificationService  # noqa: E402
from swarlekhak.audio.recorder import EvidenceVault  # noqa: E402
from swarlekhak.audio.turns import FixtureTurnRecorder  # noqa: E402
from swarlekhak.config import Preferences  # noqa: E402
from swarlekhak.examination.demo import make_demo_package  # noqa: E402
from swarlekhak.export.submission import build_submission, open_submission  # noqa: E402
from swarlekhak.integrations.flow_bridge import FixtureFlowBridge  # noqa: E402
from swarlekhak.storage.db import Store  # noqa: E402


@pytest.fixture
def qt_env(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARLEKHAK_DATA_DIR", str(tmp_path / "data"))
    from swarlekhak.app import load_fixtures
    from swarlekhak.ui.qt_ports import Invoker, QtScheduler
    from swarlekhak.ui.workspace import QtEditorPort, WorkspaceWindow

    class FocusEditorPort(QtEditorPort):
        def is_foreground(self):
            return self.win.editor.hasFocus() or True

    pkg, key = make_demo_package(tmp_path / "exams")
    store = Store(tmp_path / "db.sqlite")
    (tmp_path / "audio").mkdir()
    (tmp_path / "q").mkdir()
    vault = EvidenceVault(tmp_path / "audio", tmp_path / "q", lambda: b"z" * 32)
    inv, sched = Invoker(), QtScheduler()
    ws = WorkspaceWindow(inv, "fixture")
    qtbot.addWidget(ws)
    editor = FocusEditorPort(ws)
    flow = FixtureFlowBridge(load_fixtures(), ws.insert_text_at_end, sched, delay_s=0.3)
    rec = FixtureTurnRecorder(vault, flow)
    from swarlekhak.audio.asr import VoskTranscriber
    verifier = VerificationService(vault, lambda: VoskTranscriber(MODEL), inv.post)
    tts = FakeTTS()
    prefs = Preferences(insertion_quiet_s=0.5)
    orch = Orchestrator(store, prefs, tts=tts, flow=flow, recorder=rec, player=FakePlayer(), vault=vault,
                        verifier=verifier, editor=editor, ui=ws, scheduler=sched,
                        submitter=lambda sid, exam: build_submission(store, sid, exam, vault, tmp_path / "subs"),
                        exams_dir=tmp_path / "exams")
    ws.bind(orch)
    ws.show()
    orch.start()
    return dict(ws=ws, orch=orch, tts=tts, store=store, qtbot=qtbot, key=key)


def type_cmd(e, text):
    e["ws"].cmdline.setText(text)
    e["ws"].cmdline.returnPressed.emit()


@has_model
def test_keyboard_only_workflow_in_fixture_mode(qt_env):
    e = qt_env
    ws, orch, qtbot = e["ws"], e["orch"], e["qtbot"]
    assert "DEMO FIXTURE" in ws.mode_badge.text()
    type_cmd(e, "start my examination")
    type_cmd(e, "yes")
    assert orch.exam_active and ws.qlist.count() == 4
    type_cmd(e, "go to question 2")
    assert "Question 2 of 4" in ws.qtext.toPlainText()
    ws.buttons["start"].click()
    assert orch.state == S.DICTATING and ws.rec_badge.isVisible() is not None
    qtbot.wait(1100)                      # finish_writing waits >= 1 s after start (Flow toggle rule)
    ws.buttons["finish"].click()
    qtbot.waitUntil(lambda: "may need review" in " ".join(e["tts"].spoken), timeout=20000)
    assert ws.editor.toPlainText().startswith("The acceleration is 10 m/s²")
    assert ws.rlist.count() >= 1 and "numeric" in ws.rlist.item(0).text()
    type_cmd(e, "read review")
    assert "Written: 15" in e["tts"].last
    type_cmd(e, "replace that with 10")
    assert ws.editor.toPlainText().endswith("gives 10.")
    # typed edit is autosaved through the Qt timer
    ws.editor.moveCursor(ws.editor.textCursor().MoveOperation.End)
    ws.editor.insertPlainText(" Typed note.")
    qtbot.waitUntil(lambda: e["store"].revisions(orch.session_id)[-1]["event_type"] == "keyboard_edit", timeout=4000)
    type_cmd(e, "submit examination")
    type_cmd(e, "yes")
    # microphone is off in this test, so the resting state after submission is MUTED (locked either way)
    qtbot.waitUntil(lambda: orch.submitted and orch.state in (S.SUBMITTED, S.MUTED), timeout=3000)
    assert ws.editor.isReadOnly() and "SUBMITTED" in ws.title.text()
    view = open_submission(e["store"].submission(orch.session_id)["path"])
    assert view.integrity_ok and view.manifest["turn_sources"] == ["fixture"]
