"""Original SwarLekhak branding: restrained deep-teal / ink palette on warm neutral surfaces."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

INK = "#14262B"
TEAL = "#0F5C5C"
TEAL_DARK = "#0A4343"
TEAL_LIGHT = "#D7EAE7"
SURFACE = "#FAF7F2"
SURFACE_ALT = "#F1ECE3"
BORDER = "#CFC6B8"
AMBER = "#9A5B00"      # used with text labels, never colour alone
RED = "#A12C2C"
FOCUS = "#E0A100"

STATE_LABELS = {
    "MUTED": "Microphone off",
    "PASSIVE_WAKE": "Ready",
    "COMMAND_LISTEN": "Listening for a command",
    "CONFIRM": "Waiting for yes or no",
    "DICTATION_STARTING": "Starting dictation",
    "DICTATING": "Writing with Wispr Flow",
    "DICTATION_STOPPING": "Saving answer",
    "VERIFY_PENDING": "Checking against recording",
    "SPEAKING": "Speaking",
    "RECOVERABLE_ERROR": "Needs attention",
    "SUBMITTED": "Submitted",
}

STYLESHEET = f"""
QWidget {{ background: {SURFACE}; color: {INK}; font-family: 'Segoe UI'; font-size: 11pt; }}
QMainWindow, QDialog {{ background: {SURFACE}; }}
QLabel#Title {{ font-size: 16pt; font-weight: 600; color: {TEAL_DARK}; }}
QLabel#Badge {{ background: {TEAL_LIGHT}; color: {TEAL_DARK}; border-radius: 6px; padding: 3px 8px; font-weight: 600; }}
QLabel#BadgeWarn {{ background: #FCE9C8; color: {AMBER}; border-radius: 6px; padding: 3px 8px; font-weight: 700; }}
QLabel#Recording {{ background: #F6D9D9; color: {RED}; border-radius: 6px; padding: 3px 8px; font-weight: 700; }}
QLabel#Caption {{ background: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 8px; }}
QPlainTextEdit, QTextBrowser, QListWidget, QLineEdit, QTableWidget, QComboBox, QSpinBox {{
    background: white; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}
QPlainTextEdit#Answer {{ font-size: 13pt; }}
QPushButton {{ background: {TEAL}; color: white; border: none; border-radius: 6px; padding: 7px 14px; font-weight: 600; }}
QPushButton:hover {{ background: {TEAL_DARK}; }}
QPushButton:disabled {{ background: #9DB5B2; }}
QPushButton#Secondary {{ background: white; color: {TEAL_DARK}; border: 1px solid {TEAL}; }}
*:focus {{ outline: none; border: 2px solid {FOCUS}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 2px solid {TEAL_DARK}; border-radius: 3px; background: white; }}
QCheckBox::indicator:checked {{ background: {TEAL}; border: 2px solid {TEAL_DARK}; image: none; }}
QCheckBox::indicator:checked:disabled, QCheckBox::indicator:disabled {{ border-color: #9DB5B2; }}
QCheckBox:focus {{ border: 2px solid {FOCUS}; border-radius: 4px; }}
QListWidget::item:selected {{ background: {TEAL_LIGHT}; color: {INK}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding-top: 8px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEAL_DARK}; }}
"""


def app_icon(size: int = 64, muted: bool = False) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#6B7B7E" if muted else TEAL))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), size * 0.28, size * 0.28)
    # stylised pen nib + sound wave: the writing hand that listens
    pen = QPen(QColor(SURFACE), size * 0.07, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    path = QPainterPath(QPointF(size * 0.2, size * 0.55))
    for i, h in enumerate((0.0, -0.18, 0.2, -0.26, 0.16, -0.08, 0.0)):
        path.lineTo(QPointF(size * (0.2 + i * 0.1), size * (0.55 + h)))
    p.drawPath(path)
    p.setPen(QPen(QColor(SURFACE), size * 0.05, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(size * 0.25, size * 0.8), QPointF(size * 0.75, size * 0.8))
    if muted:
        p.setPen(QPen(QColor("#F6D9D9"), size * 0.08, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(size * 0.2, size * 0.2), QPointF(size * 0.8, size * 0.85))
    p.end()
    return QIcon(pm)


def heading_font(pt: int = 16) -> QFont:
    f = QFont("Segoe UI", pt)
    f.setWeight(QFont.DemiBold)
    return f
