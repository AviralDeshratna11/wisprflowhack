"""Conversation orb: appears on wake, animates with the REAL audio level, shows the conversation, fades out.

Original SwarLekhak artwork (teal/ink/amber "ink drop" forms), not a copy of any vendor's assistant.
Deformation amplitude comes only from measured levels: the microphone while listening, the voice
output while speaking. With no sound the orb is still (only a slow colour drift), so it never pretends
to listen. Click-through and non-activating so it cannot steal focus from the answer editor or Flow.
"""
from __future__ import annotations

import ctypes
import math
import time
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QWidget

CONVERSATION_STATES = {"COMMAND_LISTEN", "CONFIRM", "SPEAKING"}
STATE_TEXT = {"COMMAND_LISTEN": "Listening", "CONFIRM": "Say yes or no", "SPEAKING": "Swar"}

# (base colour, radius factor, harmonic, phase speed, deformation gain)
LAYERS = [
    ("#0F5C5C", 1.00, 3, 0.55, 0.16),
    ("#2BB3A3", 0.86, 4, -0.75, 0.20),
    ("#E0A100", 0.62, 5, 1.05, 0.24),
    ("#D7EAE7", 0.38, 2, -1.30, 0.18),
]


class ConversationOrb(QWidget):
    def __init__(self, mic_level: Callable[[], float], voice_level: Callable[[], float],
                 reduced_motion: bool = False) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("SwarLekhak conversation")
        self.mic_level, self.voice_level = mic_level, voice_level
        self.reduced_motion = reduced_motion
        self.state = "PASSIVE_WAKE"
        self.user_text = ""
        self.partial_text = ""
        self.reply_text = ""
        self.level = 0.0
        self._t0 = time.monotonic()
        self.resize(520, 360)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(self._fade_done)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)
        self._fading_out = False

    # ------------------------------------------------------------ public
    def set_state(self, state: str) -> None:
        self.state = state
        if state in CONVERSATION_STATES:
            self._hide_timer.stop()
            self.summon()
        elif state.startswith("DICTATION") or state == "DICTATING":
            self.dismiss(fast=True)          # Flow's own bar + the compact overlay take over while writing
        else:
            self._hide_timer.start(1800)     # let the last reply stay readable briefly
        self.setAccessibleDescription(STATE_TEXT.get(state, ""))
        self.update()

    def user_said(self, text: str) -> None:
        self.user_text, self.partial_text, self.reply_text = text, "", ""
        self.update()

    def partial(self, text: str) -> None:
        self.partial_text = text
        self.update()

    def assistant_said(self, text: str) -> None:
        self.reply_text = text
        self.update()

    def summon(self) -> None:
        if self.isVisible() and not self._fading_out:
            return
        if not self.isVisible():
            self.user_text = self.partial_text = ""
        self._place()
        self._fading_out = False
        self.setWindowOpacity(0.0 if not self.isVisible() else self.windowOpacity())
        self.show()
        self._no_activate()
        self._timer.start(16)
        self._fade.stop()
        self._fade.setDuration(220)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()

    def dismiss(self, fast: bool = False) -> None:
        if not self.isVisible() or self.state in CONVERSATION_STATES:
            return
        self._fading_out = True
        self._fade.stop()
        self._fade.setDuration(120 if fast else 450)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    # ------------------------------------------------------------ internals
    def _fade_done(self) -> None:
        if self._fading_out:
            self._fading_out = False
            self.hide()
            self._timer.stop()

    def _place(self) -> None:
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2, scr.bottom() - self.height() - 30)

    def _no_activate(self) -> None:
        try:  # WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED
            hwnd = int(self.winId())
            u = ctypes.windll.user32
            style = u.GetWindowLongW(hwnd, -20)
            u.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000020 | 0x00000080 | 0x00080000)
        except Exception:
            pass

    def _tick(self) -> None:
        if self.state == "SPEAKING":
            target = float(self.voice_level())
        elif self.state in ("COMMAND_LISTEN", "CONFIRM"):
            target = float(self.mic_level())
        else:
            target = 0.0
        # fast attack, slower release: looks alive while staying faithful to the signal
        k = 0.45 if target > self.level else 0.12
        self.level += (min(1.0, target * 2.4) - self.level) * k
        self.update()

    def _blob(self, c: QPointF, r: float, harmonic: int, phase: float, amp: float) -> QPainterPath:
        path = QPainterPath()
        n = 72
        for i in range(n + 1):
            th = 2 * math.pi * i / n
            d = 1 + amp * (0.6 * math.sin(harmonic * th + phase) + 0.4 * math.sin((harmonic + 2) * th - 1.7 * phase))
            p = QPointF(c.x() + r * d * math.cos(th), c.y() + r * d * math.sin(th))
            path.moveTo(p) if i == 0 else path.lineTo(p)
        path.closeSubpath()
        return path

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = time.monotonic() - self._t0
        w, h = self.width(), self.height()
        c = QPointF(w / 2, 118)
        R = 74.0 * (1.0 + 0.10 * self.level)
        # soft glow (intensity follows the level)
        glow = QRadialGradient(c, R * 2.1)
        g = QColor("#2BB3A3")
        g.setAlpha(int(40 + 110 * self.level))
        glow.setColorAt(0.0, g)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, R * 2.1, R * 2.1)
        # ink base disc
        p.setBrush(QColor("#14262B"))
        p.drawEllipse(c, R * 1.02, R * 1.02)
        p.setCompositionMode(QPainter.CompositionMode_Screen)
        for color, rf, harm, speed, gain in LAYERS:
            phase = 0.0 if self.reduced_motion else t * speed
            amp = 0.0 if self.reduced_motion else gain * self.level
            grad = QRadialGradient(c + QPointF(-R * 0.25, -R * 0.3), R * rf * 1.3)
            col = QColor(color)
            col.setAlpha(210)
            edge = QColor(color)
            edge.setAlpha(40)
            grad.setColorAt(0.0, col)
            grad.setColorAt(1.0, edge)
            p.setBrush(grad)
            p.drawPath(self._blob(c, R * rf, harm, phase, amp))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self.reduced_motion:  # static ring whose thickness is the real level
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#E0A100"), 2 + 10 * self.level))
            p.drawEllipse(c, R * 1.1, R * 1.1)
        # state label (text, never colour alone)
        p.setPen(QColor("#FAF7F2"))
        f = QFont("Segoe UI", 10)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.drawText(QRectF(0, 118 + R * 1.18, w, 20), Qt.AlignHCenter, STATE_TEXT.get(self.state, ""))
        # conversation panel
        lines: list[tuple[str, QColor, bool]] = []
        you = self.partial_text or self.user_text
        if you:
            lines.append((f"“{you}”", QColor("#CFE3E0"), True))
        if self.reply_text:
            lines.append((self.reply_text, QColor("#FAF7F2"), False))
        if lines:
            top = 118 + R * 1.18 + 26
            inner_w = w - 32 - 28
            max_h = h - top - 8
            laid = []
            total = 20.0
            for text, col, italic in lines:
                fnt = QFont("Segoe UI", 11)
                fnt.setItalic(italic)
                fm = QFontMetrics(fnt)
                hh = min(fm.boundingRect(0, 0, int(inner_w), 2000, Qt.TextWordWrap, text).height(), fm.height() * 4)
                laid.append((text, col, fnt, fm, hh))
                total += hh + 6
            panel = QRectF(16, top, w - 32, min(max_h, total))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 38, 43, 225))
            p.drawRoundedRect(panel, 14, 14)
            y = panel.top() + 10
            for text, col, fnt, fm, hh in laid:
                hh = min(hh, panel.bottom() - y - 6)
                if hh < fm.height():
                    break
                p.setFont(fnt)
                p.setPen(col)
                box = QRectF(panel.left() + 14, y, inner_w, hh)
                full = fm.boundingRect(box.toRect(), Qt.TextWordWrap, text).height()
                if full > hh:   # keep the end of long replies readable as they are spoken
                    text = fm.elidedText(text, Qt.ElideRight, int(inner_w * (hh // fm.height())) - 20)
                p.drawText(box, Qt.TextWordWrap, text)
                y += hh + 6
        p.end()
