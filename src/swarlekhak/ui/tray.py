"""System tray: status, activate, mute/unmute, workspace, settings, diagnostics, quit."""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from swarlekhak.ui import theme


class Tray(QSystemTrayIcon):
    def __init__(self, actions: dict[str, Callable[[], None]]) -> None:
        super().__init__(theme.app_icon(muted=True))
        self.setToolTip("SwarLekhak - microphone off")
        m = QMenu()
        self.status = m.addAction("Status: starting")
        self.status.setEnabled(False)
        m.addSeparator()
        m.addAction("Activate (Ctrl+Alt+Space)").triggered.connect(actions["activate"])
        self.mute_action = m.addAction("Unmute microphone")
        self.mute_action.triggered.connect(actions["toggle_mute"])
        m.addAction("Open workspace").triggered.connect(actions["workspace"])
        m.addAction("Examiner review...").triggered.connect(actions["examiner"])
        m.addAction("Settings...").triggered.connect(actions["settings"])
        m.addAction("Diagnostics...").triggered.connect(actions["diagnostics"])
        m.addSeparator()
        m.addAction("Quit SwarLekhak").triggered.connect(actions["quit"])
        self.setContextMenu(m)
        self._menu = m
        self.activated.connect(lambda reason: reason == QSystemTrayIcon.Trigger and actions["workspace"]())

    def set_state(self, label: str, muted: bool) -> None:
        self.status.setText(f"Status: {label}")
        self.mute_action.setText("Unmute microphone" if muted else "Mute microphone")
        self.setIcon(theme.app_icon(muted=muted))
        self.setToolTip(f"SwarLekhak - {label}")
