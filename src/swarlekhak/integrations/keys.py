"""Send a user-configured keyboard shortcut through the supported Win32 SendInput API.

Used only to press the shortcut the student configured in Wispr Flow, and only
after confirming our own answer editor is the foreground window. No global
keyboard hook, no keystroke logging.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

VK = {
    "ctrl": 0xA2, "control": 0xA2, "lctrl": 0xA2, "rctrl": 0xA3, "alt": 0xA4, "lalt": 0xA4, "ralt": 0xA5,
    "shift": 0xA0, "lshift": 0xA0, "rshift": 0xA1, "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "space": 0x20, "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "tab": 0x09,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
}
EXTENDED = {0x5B, 0x5C, 0xA3, 0xA5}
MODIFIERS = {"ctrl", "control", "lctrl", "rctrl", "alt", "lalt", "ralt", "shift", "lshift", "rshift", "win", "lwin", "rwin"}

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


class ShortcutError(ValueError):
    pass


def parse_shortcut(text: str) -> list[int]:
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ShortcutError("empty shortcut")
    codes = []
    for p in parts:
        if p not in VK:
            raise ShortcutError(f"unsupported key {p!r}")
        codes.append(VK[p])
    return codes


def describe(text: str) -> str:
    return " ".join(p.strip().capitalize() for p in text.split("+"))


if sys.platform == "win32":
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    _SendInput = ctypes.windll.user32.SendInput
    _SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _SendInput.restype = wintypes.UINT

    def _key(vk: int, up: bool) -> INPUT:
        flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)
        return INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)))

    def send_chord(text: str, hold_s: float = 0.06) -> None:
        codes = parse_shortcut(text)
        downs = (INPUT * len(codes))(*[_key(c, False) for c in codes])
        ups = (INPUT * len(codes))(*[_key(c, True) for c in reversed(codes)])
        if _SendInput(len(codes), downs, ctypes.sizeof(INPUT)) != len(codes):
            raise OSError("SendInput was blocked (another app may have higher integrity or a secure desktop is active)")
        time.sleep(hold_s)
        if _SendInput(len(codes), ups, ctypes.sizeof(INPUT)) != len(codes):
            raise OSError("SendInput key-up failed")

    def foreground_hwnd() -> int:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
else:  # pragma: no cover
    def send_chord(text: str, hold_s: float = 0.06) -> None:
        raise OSError("SendInput is only available on Windows")

    def foreground_hwnd() -> int:
        return 0
