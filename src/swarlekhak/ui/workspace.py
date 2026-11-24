"""Accessible native workspace. Every control has an accessible name, a keyboard
path and a text (not colour-only) status. Buttons route through the same
allow-listed command grammar as voice, so the command firewall is identical."""
from __future__ import annotations

import ctypes
from datetime import datetime, timezone
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAccessible, QAccessibleEvent, QAction, QColor, QFont, QKeySequence, QPainter, QPen, \
    QTextCursor
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTextBrowser,
                               QVBoxLayout, QWidget)

from swarlekhak.engines.diagram import Diagram
from swarlekhak.integrations import keys
from swarlekhak.ui import theme


class DiagramView(QWidget):
    """Visual rendering only; the adjacency list (accessible description) is the source of truth."""

    def __init__(self) -> None:
        super().__init__()
        self.diagram: Diagram | None = None
        self.setMinimumHeight(170)
        self.setAccessibleName("Diagram")
        self.setFocusPolicy(Qt.StrongFocus)

    def set_block(self, block: dict | None) -> None:
        self.diagram = Diagram.from_block(block) if block else None
        self.setAccessibleDescription(self.diagram.summary() if self.diagram else "No diagram")
        self.setVisible(self.diagram is not None)
        self.update()

    def paintEvent(self, _):
        if not self.diagram:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        pos = self.diagram.layout(self.width(), self.height())
        p.setPen(QPen(QColor(theme.TEAL_DARK), 2))
        p.setFont(QFont("Segoe UI", 9))
        hw, hh = 48.0, 16.0

        def border(c: QPointF, u: QPointF) -> QPointF:
            # point where the ray from the box centre along u leaves the 96x32 box
            sx = hw / abs(u.x()) if abs(u.x()) > 1e-6 else 1e9
            sy = hh / abs(u.y()) if abs(u.y()) > 1e-6 else 1e9
            return c + u * min(sx, sy)
        labels = []
        for e in self.diagram.edges:
            a, b = QPointF(*pos[e["src"]]), QPointF(*pos[e["dst"]])
            d = b - a
            ln = max(1.0, (d.x() ** 2 + d.y() ** 2) ** 0.5)
            u = QPointF(d.x() / ln, d.y() / ln)
            start, tip = border(a, u), border(b, QPointF(-u.x(), -u.y()))
            p.drawLine(start, tip)
            n = QPointF(-u.y(), u.x())
            p.drawLine(tip, tip - u * 10 + n * 5)
            p.drawLine(tip, tip - u * 10 - n * 5)
            if e.get("label"):
                labels.append(((start + tip) / 2, e["label"]))
        for nid, label in self.diagram.nodes.items():
            x, y = pos[nid]
            r = QRectF(x - 48, y - 16, 96, 32)
            p.setBrush(QColor(theme.TEAL_LIGHT))
            p.drawRoundedRect(r, 8, 8)
            p.drawText(r, Qt.AlignCenter, label)
        for mid, text in labels:  # edge labels last, on a background, so nodes never hide them
            br = p.fontMetrics().boundingRect(text).adjusted(-4, -2, 4, 2)
            br.moveCenter(mid.toPoint())
            p.setBrush(QColor(theme.SURFACE))
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.drawRoundedRect(br, 4, 4)
            p.setPen(QColor(theme.INK))
            p.drawText(br, Qt.AlignCenter, text)
        p.end()


class AnswerEditor(QPlainTextEdit):
    pass


class WorkspaceWindow(QMainWindow):
    def __init__(self, invoker, flow_label: str) -> None:
        super().__init__()
        self.invoker = invoker
        self.flow_label = flow_label
        self.orch = None
        self.on_open_package: Callable[[str], None] | None = None
        self.on_settings: Callable[[], None] | None = None
        self.on_examiner: Callable[[], None] | None = None
        self.on_diagnostics: Callable[[], None] | None = None
        self.on_quit: Callable[[], None] | None = None
        self.on_toggle_mute: Callable[[], None] | None = None
        self.overlay = None
        self.deadline: datetime | None = None
        self.setWindowTitle("SwarLekhak - Examination workspace")
        self.setWindowIcon(theme.app_icon())
        self.resize(1200, 780)
        self._build()
        self._menus()

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        # header
        head = QHBoxLayout()
        self.title = QLabel("No examination open")
        self.title.setObjectName("Title")
        self.title.setAccessibleName("Examination title")
        head.addWidget(self.title, 1)
        self.mode_badge = QLabel("Dictation: Wispr Flow (live)" if self.flow_label == "live"
                                 else "DEMO FIXTURE MODE - not live Wispr Flow")
        self.mode_badge.setObjectName("Badge" if self.flow_label == "live" else "BadgeWarn")
        self.mode_badge.setAccessibleName("Dictation mode")
        self.state_badge = QLabel("Microphone off")
        self.state_badge.setObjectName("Badge")
        self.state_badge.setAccessibleName("Assistant state")
        self.rec_badge = QLabel("")
        self.rec_badge.setObjectName("Recording")
        self.rec_badge.setAccessibleName("Recording indicator")
        self.rec_badge.hide()
        self.timer_label = QLabel("")
        self.timer_label.setObjectName("Badge")
        self.timer_label.setAccessibleName("Time remaining")
        self.timer_label.hide()
        for w in (self.rec_badge, self.mode_badge, self.state_badge, self.timer_label):
            head.addWidget(w)
        outer.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        # left: questions
        left = QWidget()
        lv = QVBoxLayout(left)
        ql = QLabel("&Questions")
        self.qlist = QListWidget()
        self.qlist.setAccessibleName("Questions")
        self.qlist.setWordWrap(True)
        ql.setBuddy(self.qlist)
        self.qlist.itemActivated.connect(lambda it: self._cmd(f"go to question {it.data(Qt.UserRole)}"))
        lv.addWidget(ql)
        lv.addWidget(self.qlist)
        split.addWidget(left)
        # middle: question, answer, structured items
        mid = QWidget()
        mv = QVBoxLayout(mid)
        lq = QLabel("Approved &question")
        self.qtext = QTextBrowser()
        self.qtext.setAccessibleName("Approved question text")
        self.qtext.setMaximumHeight(140)
        lq.setBuddy(self.qtext)
        la = QLabel("&Answer (Wispr Flow writes here; you may also type)")
        self.editor = AnswerEditor()
        self.editor.setObjectName("Answer")
        self.editor.setAccessibleName("Answer editor")
        self.editor.setAccessibleDescription("Your answer for the current question. Dictated text from Wispr Flow "
                                             "appears here.")
        la.setBuddy(self.editor)
        ls = QLabel("&Structured items (equations and diagram, with text equivalents)")
        self.items = QListWidget()
        self.items.setAccessibleName("Structured items")
        self.items.setMaximumHeight(110)
        ls.setBuddy(self.items)
        self.diagram_view = DiagramView()
        self.diagram_view.hide()
        for w in (lq, self.qtext, la, self.editor, ls, self.items, self.diagram_view):
            mv.addWidget(w)
        split.addWidget(mid)
        # right: review queue
        right = QWidget()
        rv = QVBoxLayout(right)
        lr = QLabel("&Review queue (neutral checks, not accusations)")
        lr.setWordWrap(True)
        self.rlist = QListWidget()
        self.rlist.setAccessibleName("Review queue")
        self.rlist.setWordWrap(True)
        lr.setBuddy(self.rlist)
        rv.addWidget(lr)
        rv.addWidget(self.rlist)
        for text, cmd in (("Read review", "read review"), ("Play original", "play the original"),
                          ("Keep original", "keep original"), ("Skip for now", "skip review")):
            b = QPushButton(text)
            b.setObjectName("Secondary")
            b.setAccessibleName(text)
            b.clicked.connect(lambda _=False, c=cmd: self._cmd(c))
            rv.addWidget(b)
        split.addWidget(right)
        split.setSizes([240, 700, 260])
        outer.addWidget(split, 1)

        # caption + command line
        self.caption = QLabel("Welcome. Open the tray menu or press Control Alt Space to begin.")
        self.caption.setObjectName("Caption")
        self.caption.setWordWrap(True)
        self.caption.setAccessibleName("Assistant says")
        self.caption.setTextInteractionFlags(Qt.TextSelectableByKeyboard | Qt.TextSelectableByMouse)
        self.caption.setFocusPolicy(Qt.StrongFocus)
        outer.addWidget(self.caption)
        cl = QHBoxLayout()
        lc = QLabel("&Command:")
        self.cmdline = QLineEdit()
        self.cmdline.setPlaceholderText("Type a command, e.g. read question, start writing, replace 15 with 10")
        self.cmdline.setAccessibleName("Type a command")
        lc.setBuddy(self.cmdline)
        self.cmdline.returnPressed.connect(self._submit_cmdline)
        cl.addWidget(lc)
        cl.addWidget(self.cmdline, 1)
        outer.addLayout(cl)

        bl = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for key, text, sc, cmd in (
                ("read", "Read question (F2)", "F2", "read question"),
                ("start", "Start writing (F5)", "F5", "start writing"),
                ("finish", "Finish writing (F6)", "F6", "finish writing"),
                ("prev", "Previous (Ctrl+PgUp)", "Ctrl+PgUp", "previous question"),
                ("next", "Next (Ctrl+PgDown)", "Ctrl+PgDown", "next question"),
                ("time", "Time left (F7)", "F7", "time left"),
                ("submit", "Submit examination...", None, "submit examination")):
            b = QPushButton(text)
            b.setAccessibleName(text)
            b.clicked.connect(lambda _=False, c=cmd: self._cmd(c))
            if sc:
                act = QAction(self)
                act.setShortcut(QKeySequence(sc))
                act.setShortcutContext(Qt.ApplicationShortcut)
                act.triggered.connect(lambda _=False, c=cmd: self._cmd(c))
                self.addAction(act)
            self.buttons[key] = b
            bl.addWidget(b)
        self.mute_btn = QPushButton("Unmute microphone (F8)")
        self.mute_btn.setObjectName("Secondary")
        self.mute_btn.setAccessibleName("Toggle microphone")
        self.mute_btn.clicked.connect(lambda: self.on_toggle_mute and self.on_toggle_mute())
        act = QAction(self)
        act.setShortcut(QKeySequence("F8"))
        act.triggered.connect(lambda: self.on_toggle_mute and self.on_toggle_mute())
        self.addAction(act)
        bl.addWidget(self.mute_btn)
        outer.addLayout(bl)
        self.setCentralWidget(root)
        self.setTabOrder(self.qlist, self.qtext)
        self.setTabOrder(self.qtext, self.editor)
        self.setTabOrder(self.editor, self.items)
        self.setTabOrder(self.items, self.rlist)
        self.setTabOrder(self.rlist, self.caption)
        self.setTabOrder(self.caption, self.cmdline)

    def _menus(self) -> None:
        m = self.menuBar().addMenu("&File")
        for text, sc, fn in (("&Open examination package...", "Ctrl+O", self._open_pkg),
                             ("Examiner &review...", None, lambda: self.on_examiner and self.on_examiner()),
                             ("&Settings...", "Ctrl+,", lambda: self.on_settings and self.on_settings()),
                             ("&Diagnostics...", None, lambda: self.on_diagnostics and self.on_diagnostics()),
                             ("E&xit SwarLekhak", "Ctrl+Q", lambda: self.on_quit and self.on_quit())):
            a = m.addAction(text)
            if sc:
                a.setShortcut(QKeySequence(sc))
            a.triggered.connect(fn)
        h = self.menuBar().addMenu("&Help")
        a = h.addAction("&Spoken commands")
        a.setShortcut(QKeySequence("F1"))
        a.triggered.connect(lambda: self._cmd("help"))

    # ---------------------------------------------------------------- binding
    def bind(self, orch) -> None:
        self.orch = orch
        self.editor.textChanged.connect(orch.on_editor_changed)

    def _cmd(self, text: str) -> None:
        if self.orch:
            self.orch.handle_command_text(text, "keyboard")

    def _submit_cmdline(self) -> None:
        t = self.cmdline.text().strip()
        self.cmdline.clear()
        if t:
            self._cmd(t)

    def _open_pkg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open examination package", "", "SwarLekhak exam (*.swar)")
        if path and self.on_open_package:
            self.on_open_package(path)

    # ---------------------------------------------------------------- UiPort
    def post(self, fn) -> None:
        self.invoker.post(fn)

    def state(self, s) -> None:
        label = theme.STATE_LABELS.get(s.value, s.value)
        self.state_badge.setText(label)
        self.buttons["finish"].setEnabled(s.value == "DICTATING")
        self.buttons["start"].setEnabled(s.value not in ("DICTATING", "DICTATION_STARTING", "DICTATION_STOPPING",
                                                         "SUBMITTED"))
        if self.overlay:
            self.overlay.set_state(s.value)
        if self.orch:
            self.mute_btn.setText("Unmute microphone (F8)" if self.orch.muted else "Mute microphone (F8)")

    def message(self, text: str) -> None:
        self.caption.setText(text)
        self.caption.setAccessibleDescription(text)
        QAccessible.updateAccessibility(QAccessibleEvent(self.caption, QAccessible.Event.NameChanged))
        if self.overlay:
            self.overlay.set_caption(text)

    def session(self, info: dict) -> None:
        if info.get("submitted"):
            self.title.setText(self.title.text() + " - SUBMITTED (locked)")
            self.editor.setReadOnly(True)
            return
        self.title.setText(info["title"])
        self.deadline = info["deadline"]
        self.editor.setReadOnly(not info.get("allow_typed", True))
        self.qlist.clear()
        for q in info["questions"]:
            it = QListWidgetItem(f"{q['ordinal']}. {q['text'][:70]}...")
            it.setData(Qt.UserRole, q["ordinal"])
            self.qlist.addItem(it)

    def question(self, q: dict, idx: int, total: int) -> None:
        html = f"<p><b>Question {idx + 1} of {total}</b>" + (f" ({q['marks']} marks)" if q.get("marks") else "") + "</p>"
        html += f"<p>{q['text']}</p>"
        if q.get("visual"):
            html += f"<p><i>Approved figure description:</i> {q['visual'].get('description', '')}</p>"
        self.qtext.setHtml(html)
        self.qlist.setCurrentRow(idx)

    def blocks(self, blocks: list[dict]) -> None:
        self.items.clear()
        dg = None
        eq_n = 0
        for b in blocks:
            if b["type"] == "equation":
                eq_n += 1
                it = QListWidgetItem(f"Equation {eq_n}: {b['linear']}    (spoken: {b['spoken']})")
                it.setData(Qt.AccessibleTextRole, f"Equation {eq_n}: {b['spoken']}")
                self.items.addItem(it)
            elif b["type"] == "diagram":
                dg = b
                self.items.addItem(QListWidgetItem("Diagram: " + b.get("adjacency", "").replace("\n", "; ")))
        self.diagram_view.set_block(dg)

    def reviews(self, reviews: list[dict]) -> None:
        self.rlist.clear()
        for r in reviews:
            status = {"check_passage": "CHECK", "student_confirmed": "kept", "student_corrected": "corrected",
                      "pending": "pending"}.get(r["status"], r["status"])
            self.rlist.addItem(f"[{status}] {r['category']}: written '{r['written'] or '-'}', heard '{r['heard'] or '-'}'")

    def recording(self, on: bool) -> None:
        self.rec_badge.setText("RECORDING evidence" if on else "")
        self.rec_badge.setVisible(on)

    def update_timer(self) -> None:
        if not self.deadline:
            return
        self.timer_label.show()
        secs = int((self.deadline - datetime.now(timezone.utc)).total_seconds())
        if secs <= 0:
            self.timer_label.setText("Time is up")
        else:
            self.timer_label.setText(f"{secs // 60:02d}:{secs % 60:02d} left")

    # ---------------------------------------------------------------- fixture insertion
    def insert_text_at_end(self, text: str) -> None:
        cur = self.editor.textCursor()
        cur.movePosition(QTextCursor.End)
        existing = self.editor.toPlainText()
        if existing and not existing.endswith((" ", "\n")):
            text = " " + text
        cur.insertText(text)
        self.editor.setTextCursor(cur)

    def closeEvent(self, ev):
        # closing the window keeps the assistant in the tray
        ev.ignore()
        self.hide()
        if self.orch:
            self.orch.ui.message("The workspace is hidden. SwarLekhak is still running in the tray.")


class QtEditorPort:
    def __init__(self, win: WorkspaceWindow) -> None:
        self.win = win

    def get_text(self) -> str:
        return self.win.editor.toPlainText()

    def set_text(self, text: str) -> None:
        cur_pos = self.win.editor.textCursor().position()
        self.win.editor.setPlainText(text)
        c = self.win.editor.textCursor()
        c.setPosition(min(cur_pos, len(text)))
        self.win.editor.setTextCursor(c)

    def focus_for_dictation(self) -> bool:
        w = self.win
        w.showNormal()
        w.raise_()
        w.activateWindow()
        try:
            ctypes.windll.user32.SetForegroundWindow(int(w.winId()))
        except Exception:
            pass
        w.editor.setReadOnly(False)
        w.editor.setFocus(Qt.OtherFocusReason)
        c = w.editor.textCursor()
        c.movePosition(QTextCursor.End)
        if w.editor.toPlainText() and not w.editor.toPlainText().endswith((" ", "\n")):
            pass  # Flow inserts at the caret; spacing is Flow's formatting decision
        w.editor.setTextCursor(c)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        return self.is_foreground()

    def is_foreground(self) -> bool:
        return keys.foreground_hwnd() == int(self.win.winId()) and self.win.editor.hasFocus()

    def set_readonly(self, ro: bool) -> None:
        self.win.editor.setReadOnly(ro)

    def select(self, start: int, end: int) -> None:
        c = self.win.editor.textCursor()
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        self.win.editor.setTextCursor(c)


def confirm_dialog(parent, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text) == QMessageBox.Yes
