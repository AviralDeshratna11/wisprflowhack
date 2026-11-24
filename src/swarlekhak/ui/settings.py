"""Settings / first-run setup ("Voice Passport") with real dependency health checks."""
from __future__ import annotations

import shutil
import sys
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QTextBrowser, QVBoxLayout,
                               QWidget)

from swarlekhak.audio.speech import GEMINI_MODELS, GEMINI_VOICES, MODELS, eleven_check, gemini_check
from swarlekhak.config import Preferences, vosk_model_path
from swarlekhak.security.secrets import has_secret, load_secret, save_secret
from swarlekhak.integrations import keys
from swarlekhak.integrations.flow_bridge import find_flow_process
from swarlekhak.ui import theme


class WrapCheck(QWidget):
    """Checkbox whose long label word-wraps (QCheckBox text cannot). Clicking the text toggles it;
    the full sentence is the checkbox's accessible name, so screen readers read it as one control."""

    def __init__(self, text: str) -> None:
        super().__init__()
        from PySide6.QtWidgets import QHBoxLayout
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        self.box = QCheckBox()
        self.box.setAccessibleName(text)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.mousePressEvent = lambda e: self.box.isEnabled() and self.box.toggle()
        lay.addWidget(self.box, 0, Qt.AlignTop)
        lay.addWidget(self.label, 1)
        self.toggled = self.box.toggled

    def isChecked(self) -> bool:
        return self.box.isChecked()

    def setChecked(self, on: bool) -> None:
        self.box.setChecked(on)

    def setEnabled(self, on: bool) -> None:
        self.box.setEnabled(on)
        self.label.setEnabled(on)

    def setAccessibleName(self, name: str) -> None:
        self.box.setAccessibleName(name)


def health_checks(prefs: Preferences, capture=None, tts=None) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    mp = vosk_model_path()
    out.append(("Local speech model (Vosk)", mp is not None,
                str(mp) if mp else "missing - run scripts\\download_models.ps1; commands and verification unavailable"))
    out.append(("Windows speech (SAPI)", bool(tts and tts.available),
                ", ".join(getattr(tts, "voices", [])[:3]) or "unavailable"))
    try:
        import sounddevice as sd
        dev = sd.query_devices(None if not prefs.input_device else int(prefs.input_device), "input")
        out.append(("Microphone", True, f"{dev['name']} @ {int(dev['default_samplerate'])} Hz"
                    + (" (capturing)" if capture and capture.running else "")))
    except Exception as exc:
        out.append(("Microphone", False, str(exc)))
    proc = find_flow_process()
    out.append(("Wispr Flow running", proc is not None,
                proc or "not detected - install/start Wispr Flow (heuristic process check)"))
    try:
        keys.parse_shortcut(prefs.flow_handsfree_shortcut)
        out.append(("Flow hands-free shortcut", True, keys.describe(prefs.flow_handsfree_shortcut)
                    + " (must match Flow: Settings > General > Shortcuts)"))
    except keys.ShortcutError as exc:
        out.append(("Flow hands-free shortcut", False, str(exc)))
    out.append(("Graphviz (optional diagram export)", shutil.which("dot") is not None,
                "found" if shutil.which("dot") else "not installed - built-in layout used"))
    try:
        import win32crypt  # noqa: F401
        out.append(("DPAPI key protection", True, "available"))
    except ImportError:
        out.append(("DPAPI key protection", False, "pywin32 missing"))
    return out


def health_html(rows) -> str:
    lines = ["<table cellspacing=4>"]
    for name, ok, detail in rows:
        lines.append(f"<tr><td><b>{'OK' if ok else 'PROBLEM'}</b></td><td>{name}</td><td>{detail}</td></tr>")
    return "\n".join(lines + ["</table>"])


class SettingsDialog(QDialog):
    def __init__(self, prefs: Preferences, tts, capture, on_save: Callable[[Preferences], None],
                 first_run: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.prefs, self.tts, self.capture, self.on_save = prefs, tts, capture, on_save
        self.setWindowTitle("SwarLekhak setup" if first_run else "SwarLekhak settings")
        self.setWindowIcon(theme.app_icon())
        self.resize(780, 760)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        if first_run:
            intro = QLabel("Welcome to SwarLekhak. Install and sign in to Wispr Flow separately, then set the options "
                           "below. Every option can be changed later. Nothing is recorded until you consent.")
            intro.setWordWrap(True)
            v.addWidget(intro)

        g = QGroupBox("Listening and consent")
        f = QFormLayout(g)
        self.consent = WrapCheck("I consent to passive wake-phrase detection on this computer. Passive audio is "
                                 "processed in memory only and is never saved.")
        self.consent.setChecked(prefs.passive_wake_consent)
        self.consent.setAccessibleName("Consent to passive wake detection")
        self.wake = WrapCheck("Respond to the spoken wake phrase \"Hey Swar\" (experimental: approximated with a "
                              "general speech model, see Known limitations)")
        self.wake.setChecked(prefs.wake_enabled)
        self.wake.setEnabled(prefs.passive_wake_consent)
        self.consent.toggled.connect(self.wake.setEnabled)
        self.consent.toggled.connect(lambda on: (not on) and self.wake.setChecked(False))
        self.hotkey = QLineEdit(prefs.activation_hotkey)
        self.hotkey.setAccessibleName("Keyboard activation shortcut")
        f.addRow(self.consent)
        f.addRow(self.wake)
        f.addRow("Keyboard activation (always available):", self.hotkey)
        v.addWidget(g)

        g = QGroupBox("Wispr Flow")
        f = QFormLayout(g)
        self.flow_sc = QLineEdit(prefs.flow_handsfree_shortcut)
        self.flow_sc.setAccessibleName("Wispr Flow hands-free shortcut")
        self.flow_cancel = QLineEdit(prefs.flow_cancel_shortcut)
        self.flow_cancel.setAccessibleName("Wispr Flow cancel shortcut")
        self.mode = QComboBox()
        self.mode.addItem("Live: Wispr Flow writes the answer", "live")
        self.mode.addItem("DEMO FIXTURE: prerecorded synthetic audio + scripted text (labelled, for demos only)",
                          "fixture")
        self.mode.setCurrentIndex(0 if prefs.flow_mode == "live" else 1)
        self.mode.setAccessibleName("Dictation mode")
        f.addRow("Hands-free shortcut (as set in Flow):", self.flow_sc)
        f.addRow("Cancel shortcut:", self.flow_cancel)
        f.addRow("Dictation mode (restart required):", self.mode)
        note = QLabel("Flow may process audio on its own servers and needs an internet connection. SwarLekhak presses "
                      "only this shortcut, and only while its own answer editor is in front.")
        note.setWordWrap(True)
        f.addRow(note)
        v.addWidget(g)

        g = QGroupBox("Audio and speech")
        f = QFormLayout(g)
        self.device = QComboBox()
        self.device.addItem("System default microphone", "")
        try:
            import sounddevice as sd
            wasapi = [i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"]]
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and (not wasapi or d["hostapi"] in wasapi):
                    self.device.addItem(f"{d['name']}", str(i))
        except Exception:
            pass
        idx = self.device.findData(prefs.input_device)
        self.device.setCurrentIndex(max(0, idx))
        self.device.setAccessibleName("Microphone")
        self.voice = QComboBox()
        self.voice.addItem("Default voice", "")
        for name in getattr(tts, "voices", []):
            self.voice.addItem(name, name)
        self.voice.setCurrentIndex(max(0, self.voice.findData(prefs.tts_voice)))
        self.voice.setAccessibleName("Speech voice")
        self.rate = QSlider(Qt.Horizontal)
        self.rate.setRange(-10, 10)
        self.rate.setValue(prefs.tts_rate)
        self.rate.setAccessibleName("Speech rate")
        test = QPushButton("Test speech")
        test.clicked.connect(lambda: self._test_speech())
        self.earcons = WrapCheck("Play short listening tones")
        self.earcons.setChecked(prefs.earcons)
        f.addRow("Microphone (restart required):", self.device)
        f.addRow("Voice:", self.voice)
        f.addRow("Speech rate:", self.rate)
        f.addRow(test)
        f.addRow(self.earcons)
        v.addWidget(g)

        g = QGroupBox("Assistant voice and conversation")
        f = QFormLayout(g)
        self.provider = QComboBox()
        self.provider.addItem("Gemini TTS (Google cloud) - falls back to Windows voice", "gemini")
        self.provider.addItem("ElevenLabs voice (cloud) - falls back to Windows voice", "elevenlabs")
        self.provider.addItem("Windows voice only (local, offline)", "sapi")
        self.provider.setCurrentIndex(max(0, self.provider.findData(prefs.voice_provider)))
        self.provider.setAccessibleName("Assistant voice provider")
        self.voice_id = QLineEdit(prefs.eleven_voice_id)
        self.voice_id.setAccessibleName("ElevenLabs voice ID")
        self.model = QComboBox()
        for m in MODELS:
            self.model.addItem(m, m)
        self.model.setCurrentIndex(max(0, self.model.findData(prefs.eleven_model)))
        self.model.setAccessibleName("ElevenLabs model")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("saved (encrypted with Windows DPAPI)" if has_secret("elevenlabs_api_key")
                                        else "paste your ElevenLabs API key")
        self.api_key.setAccessibleName("ElevenLabs API key")
        self.eleven_consent = WrapCheck("I agree that the text SwarLekhak speaks - including question text and "
                                        "read-backs of my answers - is sent to ElevenLabs for speech synthesis "
                                        "(ElevenLabs may log it).")
        self.eleven_consent.setChecked(prefs.eleven_consent)
        self.eleven_consent.setAccessibleName("Consent to ElevenLabs cloud voice")
        # Gemini
        self.g_key = QLineEdit()
        self.g_key.setEchoMode(QLineEdit.Password)
        self.g_key.setPlaceholderText("saved (encrypted with Windows DPAPI)" if has_secret("gemini_api_key")
                                      else "paste your Gemini API key (AI Studio)")
        self.g_key.setAccessibleName("Gemini API key")
        self.g_model = QComboBox()
        for m in GEMINI_MODELS:
            self.g_model.addItem(m, m)
        self.g_model.setCurrentIndex(max(0, self.g_model.findData(prefs.gemini_model)))
        self.g_model.setAccessibleName("Gemini TTS model")
        self.g_voice = QComboBox()
        for name, desc in GEMINI_VOICES.items():
            self.g_voice.addItem(f"{name} - {desc}", name)
        self.g_voice.setCurrentIndex(max(0, self.g_voice.findData(prefs.gemini_voice)))
        self.g_voice.setAccessibleName("Gemini voice")
        self.g_consent = WrapCheck("I agree that the text SwarLekhak speaks - including question text and read-backs "
                                   "of my answers - is sent to Google (Gemini API) for speech synthesis.")
        self.g_consent.setChecked(prefs.gemini_consent)
        self.g_consent.setAccessibleName("Consent to Gemini cloud voice")
        self.eleven_status = QLabel("")
        self.eleven_status.setWordWrap(True)
        chk = QPushButton("Check selected voice")
        chk.clicked.connect(self._check_eleven)
        self.follow = WrapCheck("Conversation mode: after I reply, keep listening a few seconds for a follow-up "
                                "(say \"thanks\" to end)")
        self.follow.setChecked(prefs.follow_up_listening)
        self.orb_on = WrapCheck("Show the animated conversation orb when I wake the assistant (restart required)")
        self.orb_on.setChecked(prefs.conversation_orb)
        f.addRow("Voice:", self.provider)
        f.addRow("Gemini API key:", self.g_key)
        f.addRow("Gemini model:", self.g_model)
        f.addRow("Gemini voice:", self.g_voice)
        f.addRow(self.g_consent)
        f.addRow("ElevenLabs voice ID:", self.voice_id)
        f.addRow("ElevenLabs model:", self.model)
        f.addRow("ElevenLabs API key:", self.api_key)
        f.addRow(self.eleven_consent)
        f.addRow(chk, self.eleven_status)
        f.addRow(self.follow)
        f.addRow(self.orb_on)
        v.addWidget(g)

        g = QGroupBox("Review, accessibility and data")
        f = QFormLayout(g)
        self.sens = QComboBox()
        self.sens.addItem("Standard (fewer prompts)", "standard")
        self.sens.addItem("High (also weak term differences)", "high")
        self.sens.setCurrentIndex(0 if prefs.review_sensitivity == "standard" else 1)
        self.reduced = WrapCheck("Reduced motion (static overlay)")
        self.reduced.setChecked(prefs.reduced_motion)
        self.autostart = WrapCheck("Start SwarLekhak when I sign in to Windows (opt-in)")
        self.autostart.setChecked(prefs.autostart)
        self.retain = QSpinBox()
        self.retain.setRange(1, 365)
        self.retain.setValue(prefs.retain_audio_days)
        self.retain.setSuffix(" days")
        f.addRow("Review sensitivity:", self.sens)
        f.addRow(self.reduced)
        f.addRow(self.autostart)
        f.addRow("Keep local evidence audio for:", self.retain)
        v.addWidget(g)

        g = QGroupBox("Dependency health")
        gv = QVBoxLayout(g)
        self.health = QTextBrowser()
        self.health.setAccessibleName("Dependency health")
        btn = QPushButton("Run checks")
        btn.clicked.connect(self.run_checks)
        gv.addWidget(self.health)
        gv.addWidget(btn)
        v.addWidget(g)
        self.run_checks()

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)
        # fit the window width: long combo items elide instead of forcing horizontal scrolling
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(18)
        self.health.setMinimumWidth(0)

    def run_checks(self) -> None:
        self.health.setHtml(health_html(health_checks(self.prefs, self.capture, self.tts)))

    def _store_key(self) -> None:
        for field, name in ((self.api_key, "elevenlabs_api_key"), (self.g_key, "gemini_api_key")):
            k = field.text().strip()
            if k:
                save_secret(name, k)
                field.clear()
                field.setPlaceholderText("saved (encrypted with Windows DPAPI)")

    def _check_eleven(self) -> None:
        self._store_key()
        prov = self.provider.currentData()
        if prov == "sapi":
            self.eleven_status.setText("OK: the local Windows voice needs no key.")
            return
        key = load_secret("gemini_api_key" if prov == "gemini" else "elevenlabs_api_key")
        if not key:
            self.eleven_status.setText("No API key saved for the selected voice.")
            return
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if prov == "gemini":
                ok, msg = gemini_check(key, self.g_model.currentData(), self.g_voice.currentData())
            else:
                ok, msg = eleven_check(key, self.voice_id.text().strip())
        finally:
            QApplication.restoreOverrideCursor()
        self.eleven_status.setText(("OK: " if ok else "PROBLEM: ") + msg)

    def _test_speech(self) -> None:
        if self.tts:
            self._store_key()
            self.tts.set_voice(self.voice.currentData() or "")
            self.tts.set_rate(self.rate.value())
            if hasattr(self.tts, "provider"):
                self.tts.provider = self.provider.currentData()
                self.tts.voice_id = self.voice_id.text().strip()
                self.tts.model_id = self.model.currentData()
                self.tts.gemini_model = self.g_model.currentData()
                self.tts.gemini_voice = self.g_voice.currentData()
                prev = (self.tts.consent, self.tts.gemini_consent)
                self.tts.consent = lambda: self.eleven_consent.isChecked()
                self.tts.gemini_consent = lambda: self.g_consent.isChecked()

                def restore():
                    self.tts.consent, self.tts.gemini_consent = prev
                self.tts.speak("Hello, I am Swar. This is how I will sound.", on_done=restore)
            else:
                self.tts.speak("This is how SwarLekhak will sound.")

    def _save(self) -> None:
        for sc in (self.hotkey.text(), self.flow_sc.text(), self.flow_cancel.text()):
            try:
                keys.parse_shortcut(sc.replace("ctrl", "ctrl"))
            except keys.ShortcutError as exc:
                self.health.setHtml(f"<b>Invalid shortcut:</b> {exc}")
                return
        p = self.prefs
        p.passive_wake_consent = self.consent.isChecked()
        p.wake_enabled = self.wake.isChecked() and p.passive_wake_consent
        p.activation_hotkey = self.hotkey.text().strip().lower()
        p.flow_handsfree_shortcut = self.flow_sc.text().strip().lower()
        p.flow_cancel_shortcut = self.flow_cancel.text().strip().lower()
        p.flow_mode = self.mode.currentData()
        p.input_device = self.device.currentData()
        p.tts_voice = self.voice.currentData()
        p.tts_rate = self.rate.value()
        p.earcons = self.earcons.isChecked()
        p.review_sensitivity = self.sens.currentData()
        self._store_key()
        p.voice_provider = self.provider.currentData()
        p.eleven_voice_id = self.voice_id.text().strip() or p.eleven_voice_id
        p.eleven_model = self.model.currentData()
        p.eleven_consent = self.eleven_consent.isChecked()
        p.gemini_model = self.g_model.currentData()
        p.gemini_voice = self.g_voice.currentData()
        p.gemini_consent = self.g_consent.isChecked()
        p.follow_up_listening = self.follow.isChecked()
        p.conversation_orb = self.orb_on.isChecked()
        p.reduced_motion = self.reduced.isChecked()
        p.retain_audio_days = self.retain.value()
        if p.autostart != self.autostart.isChecked():
            p.autostart = self.autostart.isChecked()
            set_autostart(p.autostart)
        p.extra["setup_done"] = True
        self.on_save(p)
        self.accept()


def set_autostart(on: bool) -> None:
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                         winreg.KEY_SET_VALUE)
    with key:
        if on:
            cmd = f'"{sys.executable}"' if getattr(sys, "frozen", False) else \
                f'"{sys.executable.replace("python.exe", "pythonw.exe")}" -m swarlekhak'
            winreg.SetValueEx(key, "SwarLekhak", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "SwarLekhak")
            except FileNotFoundError:
                pass
