"""Wispr Flow bridge.

Verified against Wispr Flow's help centre (docs.wisprflow.ai, Nov 2026):
  * Windows defaults: push-to-talk Ctrl+Win (hold); hands-free Ctrl+Win+Space
    (press to start, press again to stop and paste into the active text field);
    Esc cancels. Shortcuts are user-configurable under Settings > General >
    Shortcuts. Pressing hands-free again within ~0.5 s of starting cancels.
  * No public desktop API/SDK/CLI for starting/stopping dictation is documented.

Therefore this bridge (a) presses the user's configured hands-free shortcut via
SendInput only when our editor is the foreground window, and (b) learns about
insertion solely by observing text arriving in our own editor. status() never
claims Flow's internal state; it reports what we did and observed.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Protocol

from swarlekhak.contracts import TaskResult
from swarlekhak.integrations import keys

log = logging.getLogger(__name__)

MIN_TOGGLE_INTERVAL_S = 0.9   # Flow treats a second press within ~0.5 s as cancel


class FlowBridge(Protocol):
    label: str

    def probe(self) -> dict: ...
    def start(self, turn_id: str) -> TaskResult: ...
    def request_stop(self) -> TaskResult: ...
    def cancel(self) -> TaskResult: ...
    def status(self) -> str: ...


def find_flow_process() -> str | None:
    """Heuristic: look for a running process whose name mentions Wispr/Flow. Not authoritative."""
    try:
        import psutil
    except ImportError:
        return None
    for p in psutil.process_iter(["name"]):
        name = (p.info.get("name") or "").lower()
        if "wispr" in name or name in ("flow.exe", "wispr flow.exe"):
            return p.info["name"]
    return None


class WisprFlowBridge:
    label = "live"

    def __init__(self, handsfree_shortcut: str, cancel_shortcut: str,
                 editor_is_foreground: Callable[[], bool], require_process: bool = True) -> None:
        self.handsfree = handsfree_shortcut
        self.cancel_key = cancel_shortcut
        self.editor_is_foreground = editor_is_foreground
        self.require_process = require_process
        self._state = "idle"
        self._started_at = 0.0
        self._turn: str | None = None

    def probe(self) -> dict:
        proc = find_flow_process()
        ok_shortcut = True
        err = ""
        try:
            keys.parse_shortcut(self.handsfree)
            keys.parse_shortcut(self.cancel_key)
        except keys.ShortcutError as exc:
            ok_shortcut, err = False, str(exc)
        return {"process": proc, "process_detected": proc is not None, "shortcut": self.handsfree,
                "shortcut_valid": ok_shortcut, "shortcut_error": err, "api": "none documented",
                "ready": ok_shortcut and (proc is not None or not self.require_process)}

    def start(self, turn_id: str) -> TaskResult:
        pr = self.probe()
        if not pr["shortcut_valid"]:
            return TaskResult(False, "flow_shortcut_invalid",
                              f"The Wispr Flow shortcut in settings is not valid: {pr['shortcut_error']}.")
        if self.require_process and not pr["process_detected"]:
            return TaskResult(False, "flow_not_running",
                              "Wispr Flow does not appear to be running. Start Wispr Flow and try again, "
                              "or type your answer.")
        if not self.editor_is_foreground():
            return TaskResult(False, "focus", "The answer editor is not in the foreground, so dictation was not "
                                              "started. Nothing was sent to another window.")
        try:
            keys.send_chord(self.handsfree)
        except OSError as exc:
            return TaskResult(False, "sendinput_failed", f"The Wispr Flow shortcut could not be pressed: {exc}.")
        self._state = "start_requested"
        self._started_at = time.monotonic()
        self._turn = turn_id
        log.info("flow hands-free shortcut pressed (turn %s)", turn_id)
        return TaskResult(True, "start_requested")

    def request_stop(self) -> TaskResult:
        if self._state != "start_requested":
            return TaskResult(False, "not_started", "Dictation was not started.")
        wait = MIN_TOGGLE_INTERVAL_S - (time.monotonic() - self._started_at)
        if wait > 0:
            time.sleep(wait)
        if not self.editor_is_foreground():
            # Flow pastes into the active field: warn, but still stop so the microphone is released.
            log.warning("editor not foreground at stop; Flow may paste elsewhere")
        try:
            keys.send_chord(self.handsfree)
        except OSError as exc:
            return TaskResult(False, "sendinput_failed", f"The stop shortcut could not be pressed: {exc}.")
        self._state = "stop_requested"
        return TaskResult(True, "stop_requested")

    def cancel(self) -> TaskResult:
        try:
            keys.send_chord(self.cancel_key)
        except OSError as exc:
            return TaskResult(False, "sendinput_failed", str(exc))
        self._state = "cancel_requested"
        return TaskResult(True, "cancel_requested")

    def status(self) -> str:
        # Only what we did. Flow's internal state is unknown without a supported interface.
        return f"{self._state} (Flow internal state: unknown)"


class FixtureFlowBridge:
    """LABELLED DEMO FIXTURE. Inserts scripted text that pairs with a prerecorded
    audio file, so the verification pipeline runs on real audio when Flow is not
    available. Never used unless the user selects fixture mode; always labelled
    in the UI, the database (turn source=fixture) and the submission package."""

    label = "fixture"

    def __init__(self, fixtures: list[dict], insert_text: Callable[[str], None],
                 call_later: Callable[[float, Callable[[], None]], object], delay_s: float = 1.2) -> None:
        self.fixtures = fixtures
        self.insert_text = insert_text
        self.call_later = call_later
        self.delay_s = delay_s
        self.index = 0
        self.current: dict | None = None
        self._state = "idle"

    def probe(self) -> dict:
        return {"process": None, "process_detected": False, "shortcut": "n/a (fixture)", "shortcut_valid": True,
                "shortcut_error": "", "api": "fixture", "ready": bool(self.fixtures)}

    def select_for(self, question_ordinal: int) -> dict | None:
        for i, f in enumerate(self.fixtures):
            if f.get("question_ordinal") == question_ordinal and not f.get("_used"):
                return f
        unused = [f for f in self.fixtures if not f.get("_used")]
        return unused[0] if unused else (self.fixtures[0] if self.fixtures else None)

    def start(self, turn_id: str, question_ordinal: int = 0) -> TaskResult:
        if not self.fixtures:
            return TaskResult(False, "no_fixtures", "No demo fixtures are installed. Run the fixture generator first.")
        self.current = self.select_for(question_ordinal)
        self._state = "fixture_started"
        return TaskResult(True, "fixture_started")

    def request_stop(self) -> TaskResult:
        if not self.current:
            return TaskResult(False, "not_started", "Dictation was not started.")
        fx = self.current
        fx["_used"] = True
        self.call_later(self.delay_s, lambda: self.insert_text(fx["inserted_text"]))
        self._state = "fixture_inserting"
        return TaskResult(True, "stop_requested")

    def cancel(self) -> TaskResult:
        self.current = None
        self._state = "cancelled"
        return TaskResult(True, "cancelled")

    def status(self) -> str:
        return f"DEMO FIXTURE ({self._state})"
