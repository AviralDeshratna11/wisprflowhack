"""SwarLekhak native application entry point: python -m swarlekhak [options]

  --demo-setup         write the demo exam package (+ examiner key) to the user data folder and exit
  --fixture            run this session in LABELLED demo fixture mode (no live Wispr Flow)
  --examiner [PATH]    open the native examiner review window (optionally a .swarsub)
  --selftest           headless dependency/pipeline self-test; prints JSON; exit code 0 = pass
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from swarlekhak import APP_NAME, __version__
from swarlekhak.config import Preferences, app_root, data_dir, is_frozen, setup_logging, vosk_model_path

log = logging.getLogger("swarlekhak")


def fixtures_dir() -> Path:
    return app_root() / ("fixtures" if is_frozen() else "tests/fixtures/audio")


def load_fixtures() -> list[dict]:
    d = fixtures_dir()
    path = d / "demo_fixtures.json"
    if not path.exists():
        return []
    out = json.loads(path.read_text(encoding="utf-8"))
    for f in out:
        f["audio"] = str(d / f["audio"])
    return out


def demo_setup() -> dict:
    from swarlekhak.examination.demo import make_demo_package
    pkg, key = make_demo_package(data_dir() / "exams")
    examiner_dir = data_dir() / "examiner"
    examiner_dir.mkdir(exist_ok=True)
    final_key = examiner_dir / key.name
    os.replace(key, final_key)
    return {"exam_package": str(pkg), "examiner_key": str(final_key),
            "note": "In a real deployment the examiner key stays with the examiner, never on the student machine."}


def enforce_retention(store, prefs: Preferences) -> int:
    """Delete local encrypted evidence older than the retention period, for submitted sessions only."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=prefs.retain_audio_days)).isoformat()
    n = 0
    for r in store.all("SELECT a.id, a.path, a.turn_id, t.session_id FROM audio_assets a JOIN turns t ON t.id=a.turn_id "
                       "JOIN sessions s ON s.id=t.session_id WHERE s.status='submitted' AND a.created_utc < ?", (cutoff,)):
        p = Path(r["path"])
        if p.exists():
            p.unlink()
            store.audit("evidence_deleted_retention", {"turn_id": r["turn_id"]}, r["session_id"])
            n += 1
    return n


def selftest() -> int:
    """Headless checks usable on a clean machine / packaged build."""
    import numpy as np
    from swarlekhak.audio.recorder import read_wav
    from swarlekhak.engines.proof import ProofEngine
    from swarlekhak.examination.demo import make_demo_package
    from swarlekhak.examination.package import load_exam_package
    from swarlekhak.security import crypto
    from swarlekhak.storage.db import Store
    from swarlekhak.ui.settings import health_checks
    results: dict = {"version": __version__, "frozen": is_frozen(), "checks": []}

    def check(name, fn):
        t0 = time.perf_counter()
        try:
            detail = fn()
            results["checks"].append({"name": name, "ok": True, "detail": detail,
                                      "ms": round((time.perf_counter() - t0) * 1000)})
        except Exception as exc:
            results["checks"].append({"name": name, "ok": False, "detail": repr(exc)})

    tmp = data_dir() / "tmp" / "selftest"
    tmp.mkdir(parents=True, exist_ok=True)
    check("sqlite_migrations", lambda: Store(tmp / "st.db").migrate())
    check("demo_package", lambda: len(load_exam_package(make_demo_package(tmp)[0]).questions))
    check("crypto_roundtrip", lambda: crypto.decrypt(b"k" * 32, crypto.encrypt(b"k" * 32, b"ok")).decode())
    check("dpapi_key", lambda: len(crypto.LocalKeyStore(tmp / "k.key").key()))

    def asr():
        from swarlekhak.audio.asr import VoskTranscriber
        mp = vosk_model_path()
        if not mp:
            raise RuntimeError("Vosk model missing")
        fx = load_fixtures()
        if not fx:
            raise RuntimeError("demo fixtures missing")
        f = next(x for x in fx if x["id"] == "demo-q2")
        pcm, sr = read_wav(Path(f["audio"]).read_bytes())
        res = ProofEngine(VoskTranscriber(mp)).verify(pcm, f["inserted_text"])
        return {"status": res.status.value, "flags": [(d.category, d.written, d.heard) for d in res.discrepancies],
                "elapsed_s": round(res.elapsed_s, 2)}
    check("asr_verification_fixture", asr)
    try:
        from swarlekhak.audio.tts import SapiTTS
        tts = SapiTTS()
        prefs = Preferences()
        results["health"] = [{"name": n, "ok": ok, "detail": d} for n, ok, d in health_checks(prefs, None, tts)]
        tts.shutdown()
    except Exception as exc:
        results["health"] = [{"name": "health", "ok": False, "detail": repr(exc)}]
    text = json.dumps(results, indent=2)
    (data_dir() / "logs" / "selftest.json").write_text(text, encoding="utf-8")  # windowed builds have no console
    try:
        print(text)
    except Exception:
        pass
    return 0 if all(c["ok"] for c in results["checks"]) else 1


class CompositeUi:
    """Fans orchestrator UI events out to the workspace, conversation orb, overlay and tray."""

    def __init__(self, ws, tray, orch_getter, orb=None) -> None:
        self.ws, self.tray, self.orch_getter, self.orb = ws, tray, orch_getter, orb

    def __getattr__(self, name):
        return getattr(self.ws, name)

    def state(self, s) -> None:
        from swarlekhak.ui.theme import STATE_LABELS
        self.ws.state(s)
        if self.orb:
            self.orb.set_state(s.value)
        o = self.orch_getter()
        self.tray.set_state(STATE_LABELS.get(s.value, s.value), o.muted if o else True)

    def message(self, text: str) -> None:
        self.ws.message(text)
        if self.orb:
            self.orb.assistant_said(text)

    def user_said(self, text: str) -> None:
        if self.orb:
            self.orb.user_said(text)

    def partial(self, text: str) -> None:
        if self.orb:
            self.orb.partial(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swarlekhak")
    ap.add_argument("--demo-setup", action="store_true")
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--examiner", nargs="?", const="", default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    setup_logging()
    log.info("SwarLekhak %s starting (frozen=%s)", __version__, is_frozen())
    if args.selftest:
        return selftest()
    if args.demo_setup:
        print(json.dumps(demo_setup(), indent=2))
        return 0

    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QTextBrowser, QVBoxLayout

    from swarlekhak.audio.player import Player
    from swarlekhak.ui import theme

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(theme.STYLESHEET)
    app.setWindowIcon(theme.app_icon())
    player = Player()

    if args.examiner is not None:
        from swarlekhak.ui.examiner import ExaminerWindow
        w = ExaminerWindow(player)
        w.show()
        if args.examiner:
            w.open_path(args.examiner)
        return app.exec()

    lock = QLockFile(str(data_dir() / "swarlekhak.lock"))
    if not lock.tryLock(200):
        QMessageBox.information(None, APP_NAME, "SwarLekhak is already running. Look for its icon in the tray.")
        return 0
    app.setQuitOnLastWindowClosed(False)

    from swarlekhak.assistant.orchestrator import Orchestrator
    from swarlekhak.assistant.verification import VerificationService
    from swarlekhak.audio.asr import VoskListener, VoskTranscriber
    from swarlekhak.audio.recorder import EvidenceRecorder, EvidenceVault
    from swarlekhak.audio.session import AudioSessionManager
    from swarlekhak.audio.speech import ConversationVoice
    from swarlekhak.audio.turns import FixtureTurnRecorder, LiveTurnRecorder
    from swarlekhak.export.submission import build_submission
    from swarlekhak.integrations.flow_bridge import FixtureFlowBridge, WisprFlowBridge
    from swarlekhak.security.crypto import LocalKeyStore
    from swarlekhak.security.secrets import load_secret
    from swarlekhak.ui.orb import ConversationOrb
    from swarlekhak.storage.db import Store
    from swarlekhak.ui.examiner import ExaminerWindow
    from swarlekhak.ui.hotkey import GlobalHotkeys
    from swarlekhak.ui.overlay import Overlay
    from swarlekhak.ui.qt_ports import Invoker, QtScheduler
    from swarlekhak.ui.settings import SettingsDialog, health_checks, health_html
    from swarlekhak.ui.tray import Tray
    from swarlekhak.ui.workspace import QtEditorPort, WorkspaceWindow

    dd = data_dir()
    store = Store(dd / "swarlekhak.db")
    prefs = Preferences.from_json(store.load_preferences_json())
    if args.fixture:
        prefs.flow_mode = "fixture"

    def save_prefs(p: Preferences) -> None:
        store.save_preferences_json(p.to_json())

    keystore = LocalKeyStore(dd / "local.key")
    vault = EvidenceVault(dd / "audio", dd / "quarantine", keystore.key)
    quarantined = vault.quarantine_partials()
    if quarantined:
        store.audit("partials_quarantined", {"count": len(quarantined)})
    enforce_retention(store, prefs)

    invoker = Invoker()
    scheduler = QtScheduler()
    holder: dict = {}
    tts = ConversationVoice(
        provider=prefs.voice_provider, voice_id=prefs.eleven_voice_id, model_id=prefs.eleven_model,
        api_key=lambda: load_secret("elevenlabs_api_key"), consent=lambda: prefs.eleven_consent,
        sapi_voice=prefs.tts_voice, rate=prefs.tts_rate,
        gemini_key=lambda: load_secret("gemini_api_key"), gemini_consent=lambda: prefs.gemini_consent,
        gemini_model=prefs.gemini_model, gemini_voice=prefs.gemini_voice,
        on_notice=lambda m: invoker.post(lambda: holder["ui"].message(m)))
    capture = AudioSessionManager(prefs.input_device or None)
    flow_label = "fixture" if prefs.flow_mode == "fixture" else "live"
    ws = WorkspaceWindow(invoker, flow_label)
    editor = QtEditorPort(ws)

    if flow_label == "fixture":
        fixtures = load_fixtures()
        flow = FixtureFlowBridge(fixtures, ws.insert_text_at_end, scheduler)
        recorder = FixtureTurnRecorder(vault, flow)
    else:
        flow = WisprFlowBridge(prefs.flow_handsfree_shortcut, prefs.flow_cancel_shortcut, editor.is_foreground)
        recorder = LiveTurnRecorder(capture, EvidenceRecorder(vault))

    model = vosk_model_path()
    listener = None
    if model:
        listener = VoskListener(model, on_final=lambda t: invoker.post(lambda: holder["orch"].on_heard(t)),
                                on_partial=lambda t: invoker.post(lambda: holder["orch"].on_partial(t)))
        capture.add_consumer(listener)
    verifier = VerificationService(vault, (lambda: VoskTranscriber(model)) if model else (lambda: None),
                                   invoker.post, prefs.review_sensitivity)

    overlay = Overlay(lambda: capture.level, prefs.reduced_motion)
    overlay.flow_label = flow_label
    ws.overlay = overlay
    orb = ConversationOrb(lambda: capture.level, lambda: tts.level, prefs.reduced_motion) \
        if prefs.conversation_orb else None
    overlay.conversation_elsewhere = orb is not None

    def toggle_mute():
        o = holder["orch"]
        o.unmute() if o.muted else o.handle_command_text("mute", "keyboard")
        ui.state(o.state)

    def show_ws():
        ws.showNormal()
        ws.raise_()
        ws.activateWindow()

    examiner_holder: dict = {}

    def show_examiner():
        examiner_holder.setdefault("w", ExaminerWindow(player))
        examiner_holder["w"].show()
        examiner_holder["w"].raise_()

    def show_settings(first_run: bool = False):
        def saved(p):
            save_prefs(p)
            tts.set_rate(p.tts_rate)
            tts.set_voice(p.tts_voice)
            tts.provider, tts.voice_id, tts.model_id = p.voice_provider, p.eleven_voice_id, p.eleven_model
            tts.gemini_model, tts.gemini_voice = p.gemini_model, p.gemini_voice
            overlay.reduced_motion = p.reduced_motion
            if orb:
                orb.reduced_motion = p.reduced_motion
            holder["orch"]._sync_listener()
            if p.passive_wake_consent and holder["orch"].muted and first_run:
                holder["orch"].unmute()
        SettingsDialog(prefs, tts, capture, saved, first_run, ws).exec()

    def show_diagnostics():
        d = QDialog(ws)
        d.setWindowTitle("SwarLekhak diagnostics")
        d.resize(760, 520)
        v = QVBoxLayout(d)
        tb = QTextBrowser()
        tb.setAccessibleName("Diagnostics")
        cap = (f"<p>Capture: {'running' if capture.running else 'stopped'}; device {capture.device_name or '-'}; "
               f"{capture.native_rate} Hz; samples {capture.samples}; dropped blocks {capture.dropped_blocks}; "
               f"status flags {capture.status_flags}</p>")
        voice = (f"<p>Voice: provider {prefs.voice_provider}, cloud active {tts.cloud_active()}, "
                 f"last engine {tts.engine_in_use}, last voice error: {tts.last_error or 'none'}</p>")
        tb.setHtml(f"<h3>SwarLekhak {__version__}</h3>{health_html(health_checks(prefs, capture, tts))}{cap}{voice}"
                   f"<p>Dictation bridge: {flow.status()}</p><p>Data folder: {dd}</p>"
                   f"<p>Logs (no speech content): {dd / 'logs'}</p>")
        v.addWidget(tb)
        d.exec()

    def quit_app():
        o = holder["orch"]
        if o.turn is not None:
            o.finish_writing(via="quit")
        hotkeys.unregister_all()
        capture.stop()
        tts.shutdown()
        verifier.shutdown()
        app.quit()

    tray = Tray({"activate": lambda: holder["orch"].activate(), "toggle_mute": toggle_mute, "workspace": show_ws,
                 "examiner": show_examiner, "settings": show_settings, "diagnostics": show_diagnostics,
                 "quit": quit_app})
    tray.show()
    ui = CompositeUi(ws, tray, lambda: holder.get("orch"), orb)
    holder["ui"] = ui

    orch = Orchestrator(store, prefs, tts=tts, flow=flow, recorder=recorder, player=player, vault=vault,
                        verifier=verifier, editor=editor, ui=ui, scheduler=scheduler,
                        submitter=lambda sid, exam: build_submission(store, sid, exam, vault, dd / "submissions"),
                        capture=capture, listener=listener, save_prefs=save_prefs, exams_dir=dd / "exams")
    holder["orch"] = orch
    capture.on_error = lambda msg: invoker.post(lambda: orch.on_audio_error(msg))
    ws.bind(orch)
    ws.on_open_package = lambda p: orch.open_package(p, announce=True)
    ws.on_settings = show_settings
    ws.on_examiner = show_examiner
    ws.on_diagnostics = show_diagnostics
    ws.on_quit = quit_app
    ws.on_toggle_mute = toggle_mute

    hotkeys = GlobalHotkeys()
    app.installNativeEventFilter(hotkeys)
    if not hotkeys.register(prefs.activation_hotkey, orch.activate):
        ui.message(f"The activation shortcut {prefs.activation_hotkey} is used by another program. "
                   "Change it in Settings.")

    tick = QTimer()
    tick.timeout.connect(lambda: (orch.tick(), ws.update_timer()))
    tick.start(1000)

    ws.show()
    if not model:
        ui.message("The local speech model is missing, so spoken commands and verification are unavailable. "
                   "Run scripts\\download_models.ps1. Keyboard commands still work.")
    if not prefs.extra.get("setup_done"):
        QTimer.singleShot(300, lambda: show_settings(first_run=True))
    orch.start()
    if flow_label == "fixture":
        ui.message("DEMO FIXTURE MODE: dictation uses prerecorded synthetic audio and scripted text with planted "
                   "test errors. This is not live Wispr Flow.")
    # resume an unfinished session automatically (restores last question)
    path = orch.find_package()
    if path:
        try:
            from swarlekhak.examination.package import load_exam_package
            pkg = load_exam_package(path)
            s = store.active_session(pkg.exam_id)
            if s and s["status"] == "active":
                orch.open_package(path, announce=True)
        except Exception:
            log.exception("could not resume session")
    n = orch.requeue_verification()
    if n:
        log.info("re-queued %d verification jobs after restart", n)
    rc = app.exec()
    lock.unlock()
    store.close()
    logging.shutdown()
    # native ASR/COM worker threads can block interpreter teardown; all state is already committed
    os._exit(rc)


if __name__ == "__main__":
    sys.exit(main())
