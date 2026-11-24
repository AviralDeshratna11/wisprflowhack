"""Compact, non-activating assistant overlay. The animation is driven by the real
microphone level; when the mic is off the bars are flat (no fake listening)."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from swarlekhak.ui import theme

ACTIVE_STATES = {"COMMAND_LISTEN", "CONFIRM", "DICTATING", "DICTATION_STARTING", "DICTATION_STOPPING",
                 "SPEAKING", "RECOVERABLE_ERROR", "VERIFY_PENDING"}


class Overlay(QWidget):
    def __init__(self, level_source: Callable[[], float], reduced_motion: bool = False) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("SwarLekhak status")
        self.level_source = level_source
        self.reduced_motion = reduced_motion
        self.state = "MUTED"
        self.caption = ""
        self.flow_label = "live"
        self.conversation_elsewhere = False   # the conversation orb handles listen/confirm/speak states
        self.levels = [0.0] * 24
        self.resize(380, 96)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._maybe_hide)
        self._place()

    def _place(self) -> None:
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - self.width() - 24, scr.bottom() - self.height() - 24)

    def set_state(self, state: str) -> None:
        self.state = state
        self.setAccessibleDescription(theme.STATE_LABELS.get(state, state))
        shown = ACTIVE_STATES - ({"COMMAND_LISTEN", "CONFIRM", "SPEAKING"} if self.conversation_elsewhere else set())
        if state in shown:
            self._hide_timer.stop()
            if not self.isVisible():
                self._place()
                self.show()
        else:
            self._hide_timer.start(3500)
        self.update()

    def set_caption(self, text: str) -> None:
        self.caption = text
        self.update()

    def _maybe_hide(self) -> None:
        if self.state not in ACTIVE_STATES:
            self.hide()

    def _tick(self) -> None:
        if not self.isVisible():
            return
        lvl = float(self.level_source()) if self.state not in ("MUTED", "SPEAKING") else 0.0
        self.levels = self.levels[1:] + [lvl]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        p.setBrush(QColor(theme.INK))
        p.setPen(QPen(QColor(theme.TEAL), 2))
        p.drawRoundedRect(r, 16, 16)
        # state label (text, never colour alone)
        p.setPen(QColor(theme.SURFACE))
        f = QFont("Segoe UI", 11)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        label = theme.STATE_LABELS.get(self.state, self.state)
        if self.flow_label == "fixture" and self.state.startswith("DICTAT"):
            label += " (DEMO FIXTURE)"
        p.drawText(QRectF(16, 8, self.width() - 32, 24), Qt.AlignLeft | Qt.AlignVCenter, label)
        # caption
        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QColor("#CFE3E0"))
        cap = self.caption if len(self.caption) < 110 else self.caption[:107] + "..."
        p.drawText(QRectF(16, 30, self.width() - 32, 30), Qt.AlignLeft | Qt.TextWordWrap, cap)
        # level bars from the real microphone signal
        n = len(self.levels)
        bw = (self.width() - 32) / n
        base = self.height() - 12
        color = QColor("#F6D9D9") if self.state == "DICTATING" else QColor(theme.TEAL_LIGHT)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for i, v in enumerate(self.levels):
            h = 3 + (min(1.0, v * 2.2) * 22 if not self.reduced_motion else (6 if v > 0.02 else 0))
            p.drawRoundedRect(QRectF(16 + i * bw + 1, base - h, bw - 2, h), 1.5, 1.5)
        p.end()
