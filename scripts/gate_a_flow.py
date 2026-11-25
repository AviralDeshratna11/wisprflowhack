"""Gate A (+ integrated Gate B/D): real Wispr Flow dictating into a Qt editor.

Interactive: a person must read sentences aloud. Trials:
  T0 manual      - the person presses the Flow hands-free shortcut themselves (baseline: does Flow paste into Qt?)
  T1-T3 injected - SwarLekhak presses the shortcut via SendInput (does Flow accept injected input?)
  T4 cancel      - injected start, then Esc (expect no insertion)
  T5 quick toggle- injected start + stop within 0.3 s (Flow docs: cancels; expect no insertion)
During injected trials our own capture runs concurrently (shared mic) and the recording is verified against
Flow's inserted text with the real Acoustic Proof Engine. Results -> docs/gate_a_results.json

Usage: python scripts/gate_a_flow.py [--shortcut ctrl+win+space] [--speak-seconds 8]
"""
from __future__ import annotations

import argparse
import ctypes
import json
import platform
import sys
import tempfile
import threading
import time
import winsound
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
from PySide6.QtCore import QObject, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,  # noqa: E402
                               QPushButton, QVBoxLayout, QWidget)

from swarlekhak.audio.asr import VoskTranscriber  # noqa: E402
from swarlekhak.audio.recorder import EvidenceRecorder, EvidenceVault, read_wav  # noqa: E402
from swarlekhak.audio.resample import resample_array  # noqa: E402
from swarlekhak.audio.session import AudioSessionManager  # noqa: E402
from swarlekhak.audio.turns import LiveTurnRecorder  # noqa: E402
from swarlekhak.config import VOSK_MODEL_NAME  # noqa: E402
from swarlekhak.engines.proof import ProofEngine  # noqa: E402
from swarlekhak.integrations import keys  # noqa: E402
from swarlekhak.integrations.flow_bridge import find_flow_process  # noqa: E402

SENTENCES = [
    "The force on the block is twenty newtons, and it does not move.",
    "The acceleration is ten metres per second squared.",
    "Bending stress decreases when the moment of inertia increases.",
]
CANCEL_SENTENCE = "This sentence should not appear in the box."


def speech_stats(pcm: np.ndarray, sr: int) -> dict:
    """Speech presence relative to the recording's own noise floor (a fixed threshold missed quiet
    EarPods speech in run 2: speech peaked ~600-1200 RMS over a ~200 floor)."""
    w = max(1, sr // 10)
    n = len(pcm) // w
    if n == 0:
        return {"max_window_rms": 0.0, "noise_floor_rms": 0.0, "speech_windows": 0, "speech_detected": False}
    r = np.sqrt((pcm[: n * w].astype(np.float64).reshape(n, w) ** 2).mean(axis=1))
    floor = float(np.percentile(r, 20))
    thr = max(3.0 * floor, 350.0)
    k = int((r > thr).sum())
    return {"max_window_rms": round(float(r.max()), 1), "noise_floor_rms": round(floor, 1),
            "speech_windows": k, "speech_detected": k >= 5}


def flow_windows() -> list[dict]:
    """Visible top-level windows owned by Wispr Flow processes (OS-observable UI only; heuristic)."""
    import psutil
    import win32gui
    import win32process
    pids = {p.pid for p in psutil.process_iter(["name"]) if "wispr" in (p.info.get("name") or "").lower()}
    out = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h):
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid in pids:
                l, t, r, b = win32gui.GetWindowRect(h)
                out.append({"title": win32gui.GetWindowText(h), "rect": [l, t, r - l, b - t]})
        return True
    win32gui.EnumWindows(cb, None)
    return out


class Bus(QObject):
    instr = Signal(str, str)
    activate = Signal()
    clear = Signal()
    done = Signal()


class Win(QWidget):
    def __init__(self, bus: Bus):
        super().__init__()
        self.setWindowTitle("SwarLekhak Gate A - Wispr Flow test")
        self.resize(980, 560)
        v = QVBoxLayout(self)
        self.instr = QLabel("Starting...")
        self.instr.setWordWrap(True)
        self.instr.setFont(QFont("Segoe UI", 16, QFont.DemiBold))
        self.sentence = QLabel("")
        self.sentence.setWordWrap(True)
        self.sentence.setFont(QFont("Segoe UI", 20))
        self.sentence.setStyleSheet("color:#0A4343; background:#D7EAE7; padding:10px; border-radius:8px;")
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Segoe UI", 14))
        self.editor.setAccessibleName("Gate A test editor")
        row = QHBoxLayout()
        row.addWidget(QLabel("Microphone you will speak into:"))
        self.dev = QComboBox()
        import sounddevice as sd
        apis = [i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"]]
        self.dev.addItem("System default", "")
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and d["hostapi"] in apis:
                self.dev.addItem(d["name"], str(i))
        row.addWidget(self.dev, 1)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setFormat("mic level")
        row.addWidget(self.meter, 1)
        self.start_btn = QPushButton("Start Gate A")
        self.start_btn.setFont(QFont("Segoe UI", 14, QFont.DemiBold))
        row.addWidget(self.start_btn)
        v.addLayout(row)
        for w in (self.instr, self.sentence, self.editor):
            v.addWidget(w)
        self.started = threading.Event()
        self.cap = None
        self._open_cap()
        self.dev.currentIndexChanged.connect(lambda _: self._open_cap())
        self.start_btn.clicked.connect(self._start)
        self._mt = QTimer(self)
        self._mt.timeout.connect(lambda: self.meter.setValue(int(min(1.0, (self.cap.level if self.cap else 0) * 3) * 100)))
        self._mt.start(60)
        self.instr.setText("Choose the microphone you will speak into (the bar should move when you talk), make sure "
                           "Wispr Flow uses the same microphone, then click Start Gate A.")
        self.lock = threading.Lock()
        self.changes: list[tuple[float, str]] = []
        self.editor.textChanged.connect(self._changed)
        bus.instr.connect(self._instr)
        bus.activate.connect(self._activate)
        bus.clear.connect(lambda: self.editor.setPlainText(""))
        bus.done.connect(QApplication.instance().quit)

    def _open_cap(self):
        if self.cap:
            self.cap.stop()
        self.cap = AudioSessionManager(self.dev.currentData() or None)
        self.cap.start()

    def _start(self):
        self.dev.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.started.set()

    def _changed(self):
        with self.lock:
            self.changes.append((time.monotonic(), self.editor.toPlainText()))

    def _instr(self, a, b):
        self.instr.setText(a)
        self.sentence.setText(b)
        self.sentence.setVisible(bool(b))

    def _activate(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        try:  # Alt tap grants foreground rights to SetForegroundWindow (documented Windows rule workaround)
            keys.send_chord("alt", hold_s=0.01)
        except OSError:
            pass
        ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
        self.editor.setFocus()
        c = self.editor.textCursor()
        c.movePosition(c.MoveOperation.End)
        self.editor.setTextCursor(c)

    def snapshot(self) -> list[tuple[float, str]]:
        with self.lock:
            return list(self.changes)


def say(text: str) -> None:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    v = win32com.client.Dispatch("SAPI.SpVoice")
    v.Speak(text)  # synchronous: finishes before Flow is started


def run(win: Win, bus: Bus, args) -> None:
    res: dict = {"utc": datetime.now(timezone.utc).isoformat(), "os": platform.platform(),
                 "flow_process": find_flow_process(), "flow_version_dir": None, "shortcut": args.shortcut,
                 "trials": []}
    hwnd = int(win.winId())

    def fg() -> bool:
        return keys.foreground_hwnd() == hwnd

    def ensure_fg(label: str) -> dict:
        bus.activate.emit()
        time.sleep(0.6)
        info = {"auto_foreground": fg(), "needed_click": False, "waited_s": 0.0}
        t0 = time.monotonic()
        while not fg() and time.monotonic() - t0 < 30:
            info["needed_click"] = True
            bus.instr.emit(f"{label}: please CLICK inside the white text box to bring this window to the front.", "")
            time.sleep(0.3)
        info["waited_s"] = round(time.monotonic() - t0, 2)
        info["foreground_ok"] = fg()
        return info

    def wait_insert(since: float, baseline: str, timeout: float, quiet: float = 1.5) -> dict:
        first = last = None
        events = 0
        text = baseline
        t_end = time.monotonic() + timeout
        while time.monotonic() < t_end:
            ch = [c for c in win.snapshot() if c[0] >= since]
            if ch:
                events = len(ch)
                first = ch[0][0]
                last = ch[-1][0]
                text = ch[-1][1]
                if time.monotonic() - last >= quiet:
                    break
            time.sleep(0.1)
        inserted = text[len(baseline):] if text.startswith(baseline) else text
        timeline = [{"t_after_ref_s": round(c[0] - since, 3), "len": len(c[1]), "head": c[1][:80]}
                    for c in win.snapshot() if c[0] >= since]
        return {"inserted": inserted.strip(), "change_timeline": timeline, "inserted_any": text != baseline, "change_events": events,
                "first_change_after_stop_s": round(first - since, 3) if first else None,
                "last_change_after_stop_s": round(last - since, 3) if last else None,
                "foreground_at_end": fg(), "text_unchanged_prefix": text.startswith(baseline)}

    win.started.wait()
    tmp = Path(tempfile.mkdtemp(prefix="swar_gatea_"))
    (tmp / "a").mkdir()
    (tmp / "q").mkdir()
    vault = EvidenceVault(tmp / "a", tmp / "q", lambda: b"a" * 32)
    cap = win.cap
    res["our_capture_device"] = cap.device_name
    res["our_capture_rate"] = cap.native_rate
    rec = LiveTurnRecorder(cap, EvidenceRecorder(vault))
    engine = ProofEngine(VoskTranscriber(ROOT / "models" / VOSK_MODEL_NAME))

    # ---------------- T0 manual baseline
    bus.clear.emit()
    info = ensure_fg("Trial 0")
    say("Trial zero. Press Control, Windows, Space yourself, read the sentence, then press Control, Windows, Space again.")
    bus.instr.emit(f"Trial 0 (manual): press {keys.describe(args.shortcut)} YOURSELF, read this sentence, "
                   f"then press {keys.describe(args.shortcut)} again.", SENTENCES[0])
    rec.start("t0")
    t0 = time.monotonic()
    r = wait_insert(t0, "", timeout=45, quiet=2.0)
    asset, _ = rec.stop()
    pcm, sr = read_wav(vault.load_wav(asset.path))
    ver = engine.verify(resample_array(pcm, sr, 16000), r["inserted"]) if r["inserted"] else None
    res["trials"].append({"id": "T0_manual", **info, **r, "expected": SENTENCES[0], **speech_stats(pcm, sr),
                          "verification": None if ver is None else {
                              "status": ver.status.value, "local_asr": ver.hypothesis,
                              "flags": [(d.category, d.written, d.heard) for d in ver.discrepancies]}})

    # ---------------- T1..T3 injected
    for i, sentence in enumerate(SENTENCES, start=1):
        bus.clear.emit()
        time.sleep(0.3)
        info = ensure_fg(f"Trial {i}")
        bus.instr.emit(f"Trial {i} (automatic): after the beep, read the sentence aloud. "
                       "Do NOT press any keys.", sentence)
        say(f"Trial {i}. After the beep, read the sentence on screen. Do not press any keys.")
        baseline = ""
        dropped0 = cap.dropped_blocks
        rec.start(f"t{i}")
        winsound.Beep(880, 150)
        t_start = time.monotonic()
        err = None
        fw_before = flow_windows()
        try:
            keys.send_chord(args.shortcut)
        except OSError as exc:
            err = str(exc)
        bus.instr.emit(f"Trial {i}: SPEAK NOW", sentence)
        time.sleep(1.2)
        fw_after = flow_windows()
        time.sleep(max(0.0, args.speak_seconds - 1.2))
        bus.instr.emit(f"Trial {i}: stopping... please stay quiet", "")
        try:
            keys.send_chord(args.shortcut)
        except OSError as exc:
            err = (err or "") + str(exc)
        t_stop = time.monotonic()
        asset, _ = rec.stop()
        r = wait_insert(t_stop, baseline, timeout=25)
        pcm, sr = read_wav(vault.load_wav(asset.path))
        pcm16 = resample_array(pcm, sr, 16000)
        rms = float((pcm.astype("float64") ** 2).mean() ** 0.5)
        ver = engine.verify(pcm16, r["inserted"]) if r["inserted"] else None
        res["trials"].append({
            "id": f"T{i}_injected", **info, **r, "expected": sentence, "sendinput_error": err,
            "turn_seconds": round(t_stop - t_start, 2), "our_capture_samples": len(pcm), "our_capture_rms": round(rms, 1),
            "our_capture_dropped_blocks": cap.dropped_blocks - dropped0, **speech_stats(pcm, sr),
            "flow_windows_before": fw_before, "flow_windows_1s_after_shortcut": fw_after,
            "verification": None if ver is None else {
                "status": ver.status.value, "local_asr": ver.hypothesis, "wer": ver.wer,
                "flags": [(d.category, d.written, d.heard, d.clip) for d in ver.discrepancies]},
        })

    # ---------------- T4 cancel via Esc
    bus.clear.emit()
    info = ensure_fg("Trial 4")
    say("Trial four. After the beep, read the sentence. I will cancel it.")
    winsound.Beep(880, 150)
    keys.send_chord(args.shortcut)
    bus.instr.emit("Trial 4: SPEAK NOW (this one will be cancelled)", CANCEL_SENTENCE)
    time.sleep(5)
    keys.send_chord("esc")
    t_stop = time.monotonic()
    r = wait_insert(t_stop, "", timeout=10)
    res["trials"].append({"id": "T4_cancel_esc", **info, **r, "expected": "(nothing)"})

    # ---------------- T5 quick toggle (documented as cancel)
    bus.clear.emit()
    info = ensure_fg("Trial 5")
    bus.instr.emit("Trial 5: please stay SILENT.", "")
    say("Trial five. Please stay silent.")
    keys.send_chord(args.shortcut)
    time.sleep(0.3)
    keys.send_chord(args.shortcut)
    t_stop = time.monotonic()
    r = wait_insert(t_stop, "", timeout=8)
    res["trials"].append({"id": "T5_quick_toggle", **info, **r, "expected": "(nothing)"})
    try:
        keys.send_chord("esc")   # leave Flow idle whatever happened
    except OSError:
        pass

    t = {x["id"]: x for x in res["trials"]}
    inj = [t[k] for k in ("T1_injected", "T2_injected", "T3_injected")]
    res["verdict"] = {
        "flow_pastes_into_qt_editor_manual": t["T0_manual"]["inserted_any"],
        "manual_speech_detected": t["T0_manual"]["speech_detected"],
        "injected_trials_with_speech": sum(x["speech_detected"] for x in inj),
        "injected_trials_with_insertion": sum(x["inserted_any"] for x in inj),
        "esc_cancel_prevented_insertion": not t["T4_cancel_esc"]["inserted_any"],
        "quick_toggle_prevented_insertion": not t["T5_quick_toggle"]["inserted_any"],
        "our_capture_dropped_blocks_total": cap.dropped_blocks,
    }
    cap.stop()
    res["our_capture_total_dropped_blocks"] = cap.dropped_blocks
    res["our_capture_status_flags"] = cap.status_flags
    out = ROOT / "docs" / "gate_a_results.json"
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    bus.instr.emit("Gate A finished. Thank you! This window will close.", "")
    say("Gate A finished. Thank you.")
    time.sleep(1.5)
    bus.done.emit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shortcut", default="ctrl+win+space")
    ap.add_argument("--speak-seconds", type=float, default=8.0)
    ap.add_argument("--device", default="")
    args = ap.parse_args()
    app = QApplication(sys.argv[:1])
    bus = Bus()
    win = Win(bus)
    win.show()
    t = threading.Thread(target=run, args=(win, bus, args), daemon=True)
    t.start()
    app.exec()
    print((ROOT / "docs" / "gate_a_results.json").read_text(encoding="utf-8")
          if (ROOT / "docs" / "gate_a_results.json").exists() else "no results")
    import os
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
