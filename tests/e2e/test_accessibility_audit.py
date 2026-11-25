"""Automated accessibility audit over the Qt accessibility tree (what screen readers query via UIA/IA2).

Checks every focusable control in the workspace, settings and examiner windows has a non-empty accessible
name, that Tab reaches every focusable control in the workspace, and that status is never colour-only.
Automated checks cannot prove accessibility; NVDA testing with users is still required.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAccessible  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QAbstractSlider, QAbstractSpinBox, QComboBox, QLineEdit,  # noqa: E402
                               QListWidget, QPlainTextEdit, QTabWidget, QTextBrowser, QWidget)

INTERACTIVE = (QAbstractButton, QLineEdit, QPlainTextEdit, QComboBox, QListWidget, QTextBrowser, QAbstractSlider,
               QAbstractSpinBox, QTabWidget)


def unnamed_controls(root: QWidget) -> list[str]:
    bad = []
    for w in root.findChildren(QWidget):
        if not isinstance(w, INTERACTIVE) or w.focusPolicy() == Qt.NoFocus or not w.isVisibleTo(root):
            continue
        if w.objectName().startswith("qt_"):      # Qt-internal helpers (scrollbars, viewport)
            continue
        iface = QAccessible.queryAccessibleInterface(w)
        name = iface.text(QAccessible.Name).strip() if iface else ""
        if not name:
            bad.append(f"{type(w).__name__}({w.objectName() or w.toolTip() or id(w)})")
    return bad


@pytest.fixture
def workspace(qtbot):
    from swarlekhak.ui.qt_ports import Invoker
    from swarlekhak.ui.workspace import WorkspaceWindow
    w = WorkspaceWindow(Invoker(), "live")
    qtbot.addWidget(w)
    w.show()
    return w


def test_workspace_controls_are_named(workspace):
    assert unnamed_controls(workspace) == []


def test_workspace_tab_order_reaches_every_control(workspace, qtbot):
    focusable = [w for w in workspace.findChildren(QWidget)
                 if isinstance(w, INTERACTIVE) and w.isVisible() and w.isEnabled() and w.focusPolicy() & Qt.TabFocus]
    seen = set()
    w = workspace.qlist
    for _ in range(len(workspace.findChildren(QWidget)) * 2 + 10):   # walk the whole focus chain once
        seen.add(w)
        w = w.nextInFocusChain()
        if w is workspace.qlist:
            break
    missing = [type(x).__name__ for x in focusable if x not in seen]
    assert missing == []


def test_settings_controls_are_named(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARLEKHAK_DATA_DIR", str(tmp_path))
    from swarlekhak.config import Preferences
    from swarlekhak.ui.settings import SettingsDialog

    class T:
        voices = ["Microsoft David Desktop"]
        available = True
    d = SettingsDialog(Preferences(), T(), None, lambda p: None)
    qtbot.addWidget(d)
    d.show()
    assert unnamed_controls(d) == []


def test_examiner_controls_are_named(qtbot):
    from swarlekhak.ui.examiner import ExaminerWindow
    w = ExaminerWindow(None)
    qtbot.addWidget(w)
    w.show()
    assert unnamed_controls(w) == []


def test_status_is_text_not_colour_only(workspace):
    from swarlekhak.assistant.states import S
    for s in S:
        workspace.state(s)
        assert workspace.state_badge.text().strip()          # every state has a text label
    workspace.recording(True)
    assert "RECORDING" in workspace.rec_badge.text()


def test_examiner_opens_real_submission_and_decrypts_audio(env, qtbot):
    from conftest import ScriptedTranscriber, demo_fixture
    from swarlekhak.ui.examiner import ExaminerWindow
    o = env.orch
    o.handle_command_text("start my examination")
    o.handle_command_text("yes")
    o.handle_command_text("go to question 2")
    fx = demo_fixture("demo-q2")
    env.rec.wav_path, env.flow.next_text = fx["audio_path"], fx["inserted_text"]
    env.transcriber["t"] = ScriptedTranscriber(fx["spoken_text"].lower().replace(",", "").replace(".", ""))
    o.handle_command_text("start writing")
    env.clock.t += 3
    o.handle_command_text("finish writing")
    env.sched.advance(5)
    path = o.submitter(o.session_id, o.exam)
    w = ExaminerWindow(None)
    qtbot.addWidget(w)
    w.show()
    w.open_path(path)
    assert "ALL CHECKS PASSED" in w.integrity.toPlainText()
    assert "gives 15" in w.answers.toPlainText()
    w.key_pem = env.key.read_bytes()
    w.turns.setCurrentRow(1)                        # the review marker under the turn
    assert "Written: 15" in w.detail.toPlainText()
    assert w.wave.pcm is not None and w.wave.clip is not None
    assert unnamed_controls(w) == []
