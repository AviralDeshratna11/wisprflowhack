"""System-wide activation hotkey via the supported Win32 RegisterHotKey API (no keyboard hook)."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray

from swarlekhak.integrations.keys import VK

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000


class GlobalHotkeys(QAbstractNativeEventFilter):
    def __init__(self) -> None:
        super().__init__()
        self._handlers: dict[int, Callable[[], None]] = {}
        self._next = 0xB000

    def register(self, shortcut: str, handler: Callable[[], None]) -> bool:
        parts = [p.strip().lower() for p in shortcut.split("+")]
        mods = 0
        vk = None
        for p in parts:
            if p in MOD:
                mods |= MOD[p]
            elif p in VK:
                vk = VK[p]
        if vk is None:
            return False
        hid = self._next
        self._next += 1
        ok = bool(ctypes.windll.user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk))
        if ok:
            self._handlers[hid] = handler
        else:
            log.warning("could not register hotkey %s (in use by another app?)", shortcut)
        return ok

    def unregister_all(self) -> None:
        for hid in list(self._handlers):
            ctypes.windll.user32.UnregisterHotKey(None, hid)
        self._handlers.clear()

    def nativeEventFilter(self, event_type: QByteArray | bytes, message: int):
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG") or bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self._handlers:
                self._handlers[msg.wParam]()
                return True, 0
        return False, 0
