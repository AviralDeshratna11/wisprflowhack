"""Orchestrator: serializes state transitions and routes allow-listed commands.

Independent of Qt and hardware. Every side effect goes through an injected port
(TTS, Flow bridge, turn recorder, player, vault, verifier, editor, UI,
scheduler, clock, submitter) so the whole workflow is testable headless.
All methods are expected to be called on one thread (the GUI thread in the app).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from swarlekhak.assistant import intents
from swarlekhak.assistant.states import S, StateMachine
from swarlekhak.audio.player import clip
from swarlekhak.audio.recorder import read_wav
from swarlekhak.config import Preferences
from swarlekhak.contracts import Command, DictationTurn, ReviewStatus, TurnSource
from swarlekhak.engines import sonify
from swarlekhak.engines.cursor import AudioCursor, CursorError, Span
from swarlekhak.engines.diagram import Diagram, DiagramError
from swarlekhak.engines.math_compiler import MathError, compile_spoken
from swarlekhak.examination.package import ExamPackage, PackageError, load_exam_package
from swarlekhak.storage.db import Store, new_id

log = logging.getLogger(__name__)

WAKE_VARIANTS = ("swar", "swear", "swore", "sewer", "soar", "sour", "star", "swara", "swa", "suar", "swar's",
                 "sar", "sore", "sware", "four", "swat")
POST_SUBMIT_OK = {"help", "status", "go_to_sleep", "mute", "speech_rate", "cancel", "dismiss"}


class EditorPort(Protocol):
    def get_text(self) -> str: ...
    def set_text(self, text: str) -> None: ...
    def focus_for_dictation(self) -> bool: ...
    def is_foreground(self) -> bool: ...
    def set_readonly(self, ro: bool) -> None: ...
    def select(self, start: int, end: int) -> None: ...


class UiPort(Protocol):
    def post(self, fn: Callable[[], None]) -> None: ...
    def state(self, s: S) -> None: ...
    def message(self, text: str) -> None: ...
    def session(self, info: dict) -> None: ...
    def question(self, q: dict, idx: int, total: int) -> None: ...
    def blocks(self, blocks: list[dict]) -> None: ...
    def reviews(self, reviews: list[dict]) -> None: ...
    def recording(self, on: bool) -> None: ...


@dataclass
class Pending:
    description: str
    on_yes: Callable[[], None]
    on_no: Callable[[], None] | None = None


def build_wake_regex(phrase: str) -> re.Pattern:
    words = phrase.lower().split()
    second = set(WAKE_VARIANTS)
    if len(words) >= 2:
        second.add(words[1])
    first = words[0] if words else "hey"
    return re.compile(rf"\b(?:{re.escape(first)}|hay|hi|he|a)\s+(?:{'|'.join(map(re.escape, sorted(second)))})\b")


def human_count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class Orchestrator:
    def __init__(self, store: Store, prefs: Preferences, *, tts, flow, recorder, player, vault, verifier,
                 editor: EditorPort, ui: UiPort, scheduler: Callable[[float, Callable[[], None]], Any],
                 submitter: Callable[[str, ExamPackage], str], capture=None, listener=None,
                 save_prefs: Callable[[Preferences], None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 exams_dir: Path | None = None) -> None:
        self.store, self.prefs = store, prefs
        self.tts, self.flow, self.recorder, self.player = tts, flow, recorder, player
        self.vault, self.verifier, self.editor, self.ui = vault, verifier, editor, ui
        self.call_later, self.submitter, self.capture, self.listener = scheduler, submitter, capture, listener
        self.save_prefs = save_prefs or (lambda p: None)
        self.clock, self.utcnow, self.exams_dir = clock, utcnow, exams_dir
        self.sm = StateMachine(S.MUTED)
        self.muted = True
        self.wake_rx = build_wake_regex(prefs.wake_phrase)
        # exam context
        self.exam: ExamPackage | None = None
        self.session_id: str | None = None
        self.questions: list[dict] = []
        self.qidx = 0
        self.deadline: datetime | None = None
        self.answer_id: str | None = None
        self.answer_text = ""
        self.blocks: list[dict] = []
        self.cursor = AudioCursor("")
        self.undo_stack: dict[str, list[tuple[str, list]]] = {}
        # interaction
        self.pending: Pending | None = None
        self.turn: DictationTurn | None = None
        self.equation_buffer: list[str] | None = None
        self.active_review: dict | None = None
        self.skipped_reviews: set[str] = set()
        self.graph_point = -1
        self._after_speech = S.PASSIVE_WAKE
        self._speech_seq = 0
        self._listen_timer = None
        self._typed_timer = None
        self._applying = False
        self._mute_after_turn = False
        self._after_stop_command: str | None = None
        self._warned: set[int] = set()
        self._turn_asset = None
        self._insert_watch: dict = {}
        self._locate_before_edit: Span | None = None
        self._after_speech_cb: Callable[[], None] | None = None
        self._conversing = False          # a voice/hotkey conversation is in progress (follow-up listening)
        self._follow_up_window = False

    # ================================================================== helpers
    @property
    def exam_active(self) -> bool:
        return self.session_id is not None

    @property
    def submitted(self) -> bool:
        return self.sm.submitted

    @property
    def state(self) -> S:
        return self.sm.state

    def _rest(self) -> S:
        if self.submitted:
            return S.MUTED if self.muted else S.SUBMITTED
        return S.MUTED if self.muted else S.PASSIVE_WAKE

    def _go(self, st: S, reason: str = "") -> None:
        self.sm.go(st, reason)
        self._sync_listener()
        self.ui.state(st)

    def _sync_listener(self) -> None:
        if not self.listener:
            return
        st = self.state
        on = (not self.muted and st not in (S.SPEAKING, S.DICTATION_STARTING, S.DICTATION_STOPPING)
              and (self.prefs.wake_enabled or st in (S.COMMAND_LISTEN, S.CONFIRM)))
        self.listener.set_enabled(on)

    def _ui(self, name: str, *args) -> None:
        fn = getattr(self.ui, name, None)
        if fn:
            fn(*args)

    def _follow_up(self) -> S:
        """After a reply in an active conversation, keep listening briefly (no wake phrase needed)."""
        if (self._conversing and self.prefs.follow_up_listening and not self.muted and not self.submitted
                and self.turn is None):
            return S.COMMAND_LISTEN
        return self._rest()

    def end_conversation(self) -> None:
        self._conversing = False

    def say(self, text: str, then: S | None = None) -> None:
        """Speak and show a caption. Never speaks over an active dictation."""
        self.ui.message(text)
        if then is None:
            then = self._follow_up()
        if self.state in (S.DICTATING, S.DICTATION_STARTING, S.DICTATION_STOPPING):
            return
        self._cancel_listen_timer()
        self._after_speech = then
        self._after_speech_cb = None
        self._speech_seq += 1
        seq = self._speech_seq
        self._go(S.SPEAKING)
        self.tts.speak(text, on_done=lambda: self.ui.post(lambda: self._speech_done(seq)))

    def _speech_done(self, seq: int) -> None:
        if seq != self._speech_seq or self.state != S.SPEAKING:
            return
        target = self._after_speech
        if not self.sm.can(target):
            target = self._rest()
        self._go(target)
        if target in (S.COMMAND_LISTEN, S.CONFIRM):
            self._start_listen_timer(target)
        cb, self._after_speech_cb = self._after_speech_cb, None
        if cb:
            cb()

    def _play(self, pcm, rate: int, then: S | None = None, after: Callable[[], None] | None = None) -> None:
        """Play audio with the listener suppressed (recordings can never trigger commands)."""
        self._cancel_listen_timer()
        self._after_speech = then or self._follow_up()
        self._after_speech_cb = after
        self._speech_seq += 1
        seq = self._speech_seq
        self._go(S.SPEAKING)
        self.player.play(pcm, rate, on_done=lambda: self.ui.post(lambda: self._speech_done(seq)))

    def _start_listen_timer(self, st: S) -> None:
        self._cancel_listen_timer()
        if st == S.CONFIRM or self.equation_buffer is not None:
            secs = 20.0
        elif self._follow_up_window:
            secs = float(self.prefs.follow_up_seconds)
        else:
            secs = 8.0
        self._follow_up_window = True
        self._listen_timer = self.call_later(secs, self._listen_timeout)

    def _cancel_listen_timer(self) -> None:
        if self._listen_timer is not None:
            try:
                self._listen_timer.cancel()
            except Exception:
                pass
            self._listen_timer = None

    def _listen_timeout(self) -> None:
        self._listen_timer = None
        if self.state == S.CONFIRM and self.pending:
            self.pending = None
            self._conversing = False
            self.say("No answer heard, so nothing was changed.")
        elif self.state == S.COMMAND_LISTEN:
            if self.equation_buffer is not None:
                self.say("Equation mode is still on. Say the rest of the equation, or say end equation.",
                         then=S.COMMAND_LISTEN)
                return
            self._conversing = False
            self.player.earcon("done")
            self._go(self._rest(), "listen timeout")

    def ask(self, text: str, on_yes: Callable[[], None], on_no: Callable[[], None] | None = None) -> None:
        self.pending = Pending(text, on_yes, on_no)
        self.say(text + " Say yes or no.", then=S.CONFIRM)

    def audit(self, kind: str, **detail) -> None:
        self.store.audit(kind, detail, self.session_id)

    # ================================================================== inputs
    def start(self) -> None:
        """Called once by the app after wiring."""
        self.ui.state(self.state)
        if self.capture is not None and self.prefs.passive_wake_consent:
            self.unmute(announce=False)

    def activate(self) -> None:
        """Labelled keyboard / tray activation (works with or without the wake phrase)."""
        st = self.state
        if st == S.DICTATING:
            self.finish_writing(via="hotkey")
            return
        if st in (S.DICTATION_STARTING, S.DICTATION_STOPPING):
            return
        if self.muted:
            self.ui.message("Microphone is muted: type a command, or unmute from the tray.")
            return
        if st == S.SPEAKING:
            self.tts.stop()
            self.player.stop()
        self._conversing = True
        self.listen_for_command()

    def listen_for_command(self) -> None:
        self._follow_up_window = False    # a fresh activation gets the full listening window
        self.player.earcon("listen")
        target = S.CONFIRM if self.pending else S.COMMAND_LISTEN
        if self.state != target:
            self._go(target, "activate")
        self._start_listen_timer(target)

    def on_heard(self, text: str) -> None:
        """Final utterance from the local listener (already on the GUI thread)."""
        t = intents.clean(text)
        st = self.state
        m = self.wake_rx.search(t)
        if st in (S.SPEAKING, S.DICTATION_STARTING, S.DICTATION_STOPPING, S.MUTED):
            return
        if st == S.DICTATING:
            if m:  # "Hey Swar ..." during dictation always ends the turn first
                rest = t[m.end():].strip()
                cmd = intents.parse(rest) if rest else None
                self._after_stop_command = rest if (cmd and cmd.intent not in ("finish_writing",)) else None
                self.finish_writing(via="wake")
            return  # everything else is answer content for Wispr Flow
        if st in (S.PASSIVE_WAKE, S.SUBMITTED):
            if not (m and self.prefs.wake_enabled):
                return
            self._conversing = st == S.PASSIVE_WAKE
            rest = t[m.end():].strip()
            if rest:
                self.handle_command_text(rest, "voice")
            elif st == S.PASSIVE_WAKE:
                self.listen_for_command()
            return
        # COMMAND_LISTEN, CONFIRM, VERIFY_PENDING, RECOVERABLE_ERROR
        if m:
            t = t[m.end():].strip()
            if not t:
                self.listen_for_command()
                return
        self._cancel_listen_timer()
        self.handle_command_text(t, "voice")

    def on_partial(self, text: str) -> None:
        """Live words while awake (shown in the conversation orb). Ignored while passive: privacy."""
        if self.state in (S.COMMAND_LISTEN, S.CONFIRM):
            t = intents.clean(text)
            m = self.wake_rx.search(t)
            self._ui("partial", t[m.end():].strip() if m else t)

    def handle_command_text(self, text: str, source: str = "keyboard") -> None:
        self._ui("user_said", text)
        if self.equation_buffer is not None:
            cmd = intents.parse(text, source)
            if cmd and cmd.intent == "end_equation":
                body = " ".join(self.equation_buffer)
                self.equation_buffer = None
                self._compile_equation(body)
                return
            if cmd and cmd.intent == "cancel":
                self.equation_buffer = None
                self.say("Equation cancelled.")
                return
            if cmd and cmd.intent == "equation":
                text = cmd.args["body"]
            self.equation_buffer.append(text)
            self.ui.message(f"Equation so far: {' '.join(self.equation_buffer)}")
            if source == "voice":
                self._go(S.COMMAND_LISTEN) if self.state != S.COMMAND_LISTEN else None
                self._start_listen_timer(S.COMMAND_LISTEN)
            return
        cmd = intents.parse(text, source)
        if cmd is None:
            self.say("Sorry, that is not a command I know. Say help to hear the commands.")
            return
        self.execute(cmd)

    # ================================================================== dispatch
    def execute(self, cmd: Command) -> None:
        st = self.state
        if st.value not in cmd.allowed_states:
            if st in (S.DICTATING, S.DICTATION_STARTING):
                self.ui.message("While writing, only finish writing, cancel writing and mute are available.")
                return
            if st == S.DICTATION_STOPPING:
                self.ui.message("Please wait, the answer is being saved.")
                return
            if st == S.SUBMITTED and cmd.intent not in POST_SUBMIT_OK:
                self.say("The examination has been submitted. Answers are locked.")
                return
            if st != S.SUBMITTED:
                self.say("That is not available right now.")
                return
        if self.submitted and cmd.intent not in POST_SUBMIT_OK:
            self.say("The examination has been submitted. Answers are locked.")
            return
        sp = intents.spec(cmd.intent)
        if sp.needs_exam and not self.exam_active:
            self.say("No examination is open. Say start my examination.")
            return
        if self.pending and cmd.intent not in ("confirm_yes", "cancel"):
            self.pending = None
            self.ui.message("Previous question cancelled.")
        self.audit("command", intent=cmd.intent, source=cmd.source)
        handler = getattr(self, f"_do_{cmd.intent}")
        try:
            handler(cmd)
        except (CursorError, DiagramError, MathError) as exc:
            self.say(str(exc))
        except PermissionError as exc:
            self.say(str(exc))

    # ================================================================== general intents
    def _do_help(self, cmd: Command) -> None:
        if not self.exam_active:
            self.say("Say start my examination to begin. You can also say status, mute, or go to sleep.")
            return
        self.say("You can say: read question; next question; start writing, then Hey Swar finish writing; "
                 "read my answer; next sentence; find a word; replace a word with another; undo; read review; "
                 "play the original; keep original; equation followed by the equation; create node, connect A to B; "
                 "play the graph; time left; and submit examination.")

    def _do_confirm_yes(self, cmd: Command) -> None:
        p, self.pending = self.pending, None
        if not p:
            self.say("There is nothing to confirm.")
            return
        p.on_yes()

    def _do_cancel(self, cmd: Command) -> None:
        self.tts.stop()
        self.player.stop()
        if self.pending:
            p, self.pending = self.pending, None
            if p.on_no:
                p.on_no()
            else:
                self.say("Cancelled. Nothing was changed.")
            return
        if self.state in (S.COMMAND_LISTEN, S.SPEAKING):
            self._conversing = False
            self._go(self._rest())

    def _do_status(self, cmd: Command) -> None:
        mic = "muted" if self.muted else ("listening for the wake phrase" if self.prefs.wake_enabled
                                          else "on, activate with Control Alt Space")
        mode = "Wispr Flow" if self.flow.label == "live" else "demo fixture mode, not live Wispr Flow"
        if not self.exam_active:
            self.say(f"No examination is open. Microphone {mic}. Dictation uses {mode}.")
            return
        answered = sum(1 for a in self.store.answers(self.session_id) if a["text"].strip() or a["blocks_json"] != "[]")
        pending = self._unresolved_reviews()
        self.say(f"Question {self.qidx + 1} of {len(self.questions)}. {answered} answered. "
                 f"{human_count(len(pending), 'passage')} waiting for review. Microphone {mic}. Dictation uses {mode}."
                 + (" The examination is submitted." if self.submitted else ""))

    def _do_time_left(self, cmd: Command) -> None:
        self.say(self._time_left_text())

    def _time_left_text(self) -> str:
        if not self.deadline:
            return "No timer is running."
        secs = int((self.deadline - self.utcnow()).total_seconds())
        if secs <= 0:
            return "Time is up."
        m, s = divmod(secs, 60)
        return f"{human_count(m, 'minute')} and {human_count(s, 'second')} remaining."

    def _do_dismiss(self, cmd: Command) -> None:
        self._conversing = False
        self.say("Okay.")

    def _do_go_to_sleep(self, cmd: Command) -> None:
        self._conversing = False
        if self.prefs.wake_enabled:
            self.say("Going to sleep. I am still listening for Hey Swar. To switch the microphone off completely, say mute.")
        else:
            self.say("Going to sleep. Press Control Alt Space when you need me. To switch the microphone off, say mute.")

    def _do_mute(self, cmd: Command) -> None:
        if self.turn is not None:
            self._mute_after_turn = True
            self.finish_writing(via="mute")
            return
        self.mute()

    def mute(self) -> None:
        self._conversing = False
        self.muted = True
        if self.capture is not None:
            self.capture.stop()
        self.audit("microphone_muted")
        self.say("Microphone muted. The wake phrase is off. To unmute, use the tray icon or the Unmute button.")

    def unmute(self, announce: bool = True) -> None:
        if self.capture is not None:
            try:
                self.capture.start()
            except Exception as exc:
                self.muted = True
                self._go(S.MUTED) if self.state != S.MUTED else None
                self.say(f"The microphone could not be opened: {exc}. You can still type commands.")
                return
        self.muted = False
        self.audit("microphone_unmuted")
        if self.state in (S.MUTED,):
            self._go(self._rest())
        if announce:
            self.say("Microphone on. " + ("Listening for Hey Swar." if self.prefs.wake_enabled
                                          else "Press Control Alt Space to give a command."))
        else:
            self._sync_listener()

    def _do_speech_rate(self, cmd: Command) -> None:
        delta = -2 if cmd.args.get("dir") == "slower" else 2
        self.prefs.tts_rate = max(-10, min(10, self.prefs.tts_rate + delta))
        self.tts.set_rate(self.prefs.tts_rate)
        self.save_prefs(self.prefs)
        self.say(f"Speech rate {self.prefs.tts_rate}.")

    # ================================================================== exam lifecycle
    def find_package(self) -> str | None:
        if self.prefs.exam_package and Path(self.prefs.exam_package).exists():
            return self.prefs.exam_package
        if self.exams_dir and self.exams_dir.exists():
            pk = sorted(self.exams_dir.glob("*.swar"), key=lambda p: p.stat().st_mtime, reverse=True)
            if pk:
                return str(pk[0])
        return None

    def _do_start_exam(self, cmd: Command) -> None:
        if self.exam_active:
            self.say(f"The examination is already open. You are on question {self.qidx + 1}.")
            return
        path = self.find_package()
        if not path:
            self.say("I could not find an assigned examination package. Use Open examination in the workspace "
                     "to choose a dot swar file.")
            return
        self.open_package(path, announce=True)

    def open_package(self, path: str, announce: bool = True) -> bool:
        try:
            pkg = load_exam_package(path)
            self.store.import_exam(pkg.manifest, pkg.manifest_hash, pkg.questions, pkg.graph_data, path)
        except (PackageError, ValueError) as exc:
            self.audit("package_rejected", reason=str(exc))
            self.say(f"The examination package could not be opened. {exc}")
            return False
        sess = self.store.active_session(pkg.exam_id)
        if sess and sess["status"] == "submitted":
            self.say("This examination has already been submitted on this computer.")
            return False
        if sess:
            self._open_session(pkg, sess["id"])
            if announce:
                self.say(f"Resuming {pkg.manifest['title']}. {self._time_left_text()} "
                         f"You are on question {self.qidx + 1} of {len(self.questions)}.")
            return True
        policy = pkg.manifest.get("policy", {})
        n = len(pkg.questions)
        text = (f"{pkg.manifest['title']}. {human_count(n, 'question')}. "
                f"{int(pkg.manifest['duration_minutes'])} minutes. Recording policy: {policy.get('recording', 'not stated')} "
                "Your answers are written by Wispr Flow, which may send audio to its own servers for processing. "
                "Do you consent, and do you want to start now?")
        self.ask(text, on_yes=lambda: self._begin_session(pkg),
                 on_no=lambda: self.say("The examination has not started. Nothing was recorded."))
        return True

    def _begin_session(self, pkg: ExamPackage) -> None:
        now = self.utcnow()
        deadline = now + timedelta(minutes=float(pkg.manifest["duration_minutes"]))
        sid = self.store.create_session(pkg.exam_id, now.isoformat(), deadline.isoformat())
        policy = pkg.manifest.get("policy", {})
        self.store.add_consent("exam_recording", True, policy.get("recording", ""), sid)
        self.store.add_consent("flow_external_processing", True,
                               "Wispr Flow may process dictation audio on external servers.", sid)
        self._open_session(pkg, sid)
        self.audit("session_started", exam_id=pkg.exam_id)
        self.say("Examination started. Say read question, or start writing.")

    def _open_session(self, pkg: ExamPackage, sid: str) -> None:
        self.exam, self.session_id = pkg, sid
        self.questions = self.store.questions(pkg.exam_id)
        row = self.store.session(sid)
        self.deadline = datetime.fromisoformat(row["deadline_utc"])
        self.qidx = max(0, min(len(self.questions) - 1, int(row["last_question_ordinal"]) - 1))
        self.ui.session({"exam_id": pkg.exam_id, "title": pkg.manifest["title"], "questions": self.questions,
                         "session_id": sid, "deadline": self.deadline, "flow_label": self.flow.label,
                         "allow_typed": pkg.manifest.get("policy", {}).get("allow_typed_input", True)})
        self._load_answer()
        self.tick()

    def on_audio_error(self, msg: str) -> None:
        """Microphone failure: stop safely, keep the partial turn, mark verification unavailable."""
        self.audit("microphone_error", error=msg)
        if self.turn is not None and self.state == S.DICTATING:
            self.finish_writing(via="microphone failure")
            self.ui.message(f"Microphone problem: {msg}. The partial recording and your answer are kept.")
            return
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:
                pass
        self.muted = True
        if self.state not in (S.DICTATION_STARTING, S.DICTATION_STOPPING):
            self.say(f"The microphone stopped working: {msg}. Check the device, then unmute from the tray to retry. "
                     "You can still type commands and answers.")

    def tick(self) -> None:
        """Called every second by the app."""
        if self.capture is not None and not self.muted and getattr(self.capture, "stalled", lambda: False)():
            self.on_audio_error("no audio has arrived for three seconds")
        if not self.deadline or self.submitted:
            return
        secs = (self.deadline - self.utcnow()).total_seconds()
        for mark, text in ((300, "Five minutes remaining."), (60, "One minute remaining.")):
            if secs <= mark and mark not in self._warned and secs > 0:
                self._warned.add(mark)
                if self.state not in (S.DICTATING, S.DICTATION_STOPPING):
                    self.say(text)
                else:
                    self.ui.message(text)
        if secs <= 0 and 0 not in self._warned:
            self._warned.add(0)
            if self.turn is not None:
                self.finish_writing(via="timer")
            self.say("Time is up. Your answers are saved. Say submit examination to submit.")

    # ================================================================== question navigation
    @property
    def question(self) -> dict:
        return self.questions[self.qidx]

    def _load_answer(self) -> None:
        q = self.question
        row = self.store.answer(self.session_id, q["db_id"])
        self.answer_id = row["id"]
        self.answer_text = row["text"]
        self.blocks = json.loads(row["blocks_json"])
        self._set_editor(self.answer_text)
        self.cursor = AudioCursor(self.answer_text)
        self.active_review = None
        self.graph_point = -1
        self.ui.question(q, self.qidx, len(self.questions))
        self.ui.blocks(self.blocks)
        self.ui.reviews(self.store.reviews(sid=self.session_id))
        self.store.set_position(self.session_id, self.qidx + 1)

    def _set_editor(self, text: str) -> None:
        self._applying = True
        try:
            self.editor.set_text(text)
        finally:
            self._applying = False

    def goto(self, idx: int) -> bool:
        if not 0 <= idx < len(self.questions):
            self.say(f"There {'is' if len(self.questions) == 1 else 'are'} {human_count(len(self.questions), 'question')}.")
            return False
        self.flush_typed()
        self.qidx = idx
        self._load_answer()
        return True

    def _question_text(self) -> str:
        q = self.question
        text = f"Question {self.qidx + 1} of {len(self.questions)}"
        if q.get("marks"):
            text += f", {human_count(q['marks'], 'mark')}"
        text += f". {q['text']}"
        if q.get("visual"):
            text += " This question has an approved figure. Say describe the figure, read the graph, or play the graph."
        return text

    def _do_read_question(self, cmd: Command) -> None:
        n = cmd.args.get("n")
        if n is not None and n != self.qidx + 1:
            if not self.goto(len(self.questions) - 1 if n == -1 else n - 1):
                return
        self.say(self._question_text())

    def _do_go_question(self, cmd: Command) -> None:
        n = cmd.args["n"]
        if self.goto(len(self.questions) - 1 if n == -1 else n - 1):
            self.say(self._question_text())

    def _do_next_question(self, cmd: Command) -> None:
        if self.qidx + 1 >= len(self.questions):
            self.say("This is the last question.")
            return
        self.goto(self.qidx + 1)
        self.say(self._question_text())

    def _do_previous_question(self, cmd: Command) -> None:
        if self.qidx == 0:
            self.say("This is the first question.")
            return
        self.goto(self.qidx - 1)
        self.say(self._question_text())

    # ================================================================== typed edits
    def on_editor_changed(self) -> None:
        if self._applying or not self.exam_active or self.submitted:
            return
        if self.state in (S.DICTATION_STARTING, S.DICTATING, S.DICTATION_STOPPING):
            return  # attributed to the dictation turn by the before/after diff
        if self._typed_timer is not None:
            try:
                self._typed_timer.cancel()
            except Exception:
                pass
        self._typed_timer = self.call_later(1.2, self.flush_typed)

    def flush_typed(self) -> None:
        self._typed_timer = None
        if not self.exam_active or self.submitted or self.turn is not None:
            return
        text = self.editor.get_text()
        if text == self.answer_text:
            return
        self._commit(text, "keyboard_edit", {"source": TurnSource.TYPED.value})

    # ================================================================== commits / undo
    def _commit(self, text: str, event_type: str, change: dict, blocks: list[dict] | None = None,
                asset_hashes: list[str] | None = None, push_undo: bool = True) -> dict:
        if push_undo:
            stack = self.undo_stack.setdefault(self.answer_id, [])
            stack.append((self.answer_text, json.loads(json.dumps(self.blocks))))
            del stack[:-100]
        new_blocks = self.blocks if blocks is None else blocks
        rev = self.store.commit_revision(self.answer_id, event_type, text, new_blocks, change, asset_hashes)
        self.answer_text = text
        self.blocks = new_blocks
        if self.editor.get_text() != text:
            self._set_editor(text)
        self.cursor.set_text(text)
        self.ui.blocks(self.blocks)
        return rev

    def _do_undo(self, cmd: Command) -> None:
        stack = self.undo_stack.get(self.answer_id) or []
        if not stack:
            self.say("There is nothing to undo for this answer.")
            return
        text, blocks = stack.pop()
        self._commit(text, "undo", {"restored": True}, blocks=blocks, push_undo=False)
        self.say("Undone.")

    # ================================================================== dictation
    def _do_start_writing(self, cmd: Command) -> None:
        self.start_writing()

    def start_writing(self) -> None:
        if self.turn is not None:
            self.ui.message("Already writing.")
            return
        if not self.exam_active or self.submitted:
            self.say("No open examination to write in.")
            return
        self.flush_typed()
        self.tts.stop()
        self.player.stop()
        self._cancel_listen_timer()
        self._conversing = False
        self._go(S.DICTATION_STARTING, "start writing")
        if not self.editor.focus_for_dictation():
            self._fail("I could not bring the answer editor to the front, so dictation was not started.")
            return
        turn_id = new_id("turn")
        before = self.editor.get_text()
        try:
            start_sample = self.recorder.start(turn_id)
        except Exception as exc:
            self._fail(f"Evidence recording could not start: {exc}. Dictation was not started.")
            return
        source = TurnSource.FLOW_LIVE if self.flow.label == "live" else TurnSource.FIXTURE
        row = self.store.answer(self.session_id, self.question["db_id"])
        self.store.create_turn(turn_id, self.session_id, self.answer_id, source.value, start_sample,
                               self.clock(), row["current_revision"])
        if self.flow.label == "fixture":
            res = self.flow.start(turn_id, question_ordinal=self.question["ordinal"])
        else:
            res = self.flow.start(turn_id)
        if not res.ok:
            asset, end = self.recorder.stop()
            if asset:
                self.store.add_audio_asset(turn_id, asset.path, asset.sha256, asset.sample_rate, asset.num_samples, True)
            self.store.finish_turn(turn_id, "flow_start_failed", end, self.clock(), None, {"code": res.code})
            self._fail(res.spoken_message)
            return
        self.turn = DictationTurn(turn_id, source, self.answer_id, self.clock(), start_sample=start_sample,
                                  revision_before=row["current_revision"], text_before=before)
        self.editor.set_readonly(False)
        self._go(S.DICTATING, "flow started")
        self.ui.recording(True)
        self.audit("dictation_started", turn_id=turn_id, source=source.value)

    def _fail(self, msg: str) -> None:
        if self.state != S.RECOVERABLE_ERROR:
            self._go(S.RECOVERABLE_ERROR, "failure")
        self.ui.recording(False)
        self.say(msg)

    def _do_finish_writing(self, cmd: Command) -> None:
        self.finish_writing(via=cmd.source)

    def finish_writing(self, via: str = "voice") -> None:
        if self.turn is None or self.state != S.DICTATING:
            if self.state != S.DICTATION_STOPPING:
                self.say("You are not writing at the moment.")
            return
        elapsed = self.clock() - self.turn.start_mono
        if elapsed < 1.0:  # Flow treats a quick second press as cancel
            self.call_later(1.0 - elapsed, lambda: self.finish_writing(via))
            return
        self._go(S.DICTATION_STOPPING, f"finish via {via}")
        res = self.flow.request_stop()
        try:
            asset, end_sample = self.recorder.stop()
        except Exception as exc:
            log.exception("recorder stop failed")
            asset, end_sample = None, None
            self.ui.message(f"Recording could not be finalised: {exc}")
        self.turn.end_mono = self.clock()
        self.turn.end_sample = end_sample
        self._turn_asset = asset
        self.ui.recording(False)
        if asset:
            self.store.add_audio_asset(self.turn.id, asset.path, asset.sha256, asset.sample_rate, asset.num_samples, True)
        if not res.ok:
            self.ui.message(res.spoken_message)
        self._insert_watch = {"stop_mono": self.clock(), "last_text": self.editor.get_text(), "last_change": None}
        self.call_later(0.25, self._poll_insertion)

    def _do_cancel_writing(self, cmd: Command) -> None:
        if self.turn is None:
            self.say("You are not writing at the moment.")
            return
        self.flow.cancel()
        asset, end = self.recorder.stop()
        if asset:
            self.store.add_audio_asset(self.turn.id, asset.path, asset.sha256, asset.sample_rate, asset.num_samples, True)
        self.store.finish_turn(self.turn.id, "cancelled", end, self.clock(), None)
        self.turn = None
        self.ui.recording(False)
        self._go(S.DICTATION_STOPPING, "cancel")
        self._go(S.SPEAKING)
        self._go(self._rest())
        self.say("Dictation cancelled. The recording is kept as evidence; the answer was not changed by me.")

    def _poll_insertion(self) -> None:
        if self.turn is None or self.state != S.DICTATION_STOPPING:
            return
        w = self._insert_watch
        now = self.clock()
        text = self.editor.get_text()
        if text != w["last_text"]:
            w["last_text"] = text
            w["last_change"] = now
        if w["last_change"] is not None and now - w["last_change"] >= self.prefs.insertion_quiet_s:
            self._reconcile()
            return
        if w["last_change"] is None and now - w["stop_mono"] > self.prefs.insertion_timeout_s:
            self._reconcile()
            return
        self.call_later(0.25, self._poll_insertion)

    _TRAILING_CMD = re.compile(
        r"\s*(?:\b(?:hey|hay|hi|he)\b[\s,]+\w+[\s,.!]*)?(?:\b(?:finish|stop|end|done)\s+(?:writing|dictation|dictating)\b)?[\s.!?]*$",
        re.IGNORECASE)

    def _trailing_command(self, inserted: str) -> tuple[int, str] | None:
        m = self._TRAILING_CMD.search(inserted)
        if not m:
            return None
        frag = inserted[m.start():]
        lowered = intents.clean(frag)
        if not lowered or not (self.wake_rx.search(lowered) or re.search(r"(finish|stop|end|done) (writing|dictation)", lowered)):
            return None
        return m.start(), frag.strip()

    def _reconcile(self) -> None:
        turn = self.turn
        before, after = turn.text_before, self.editor.get_text()
        asset_hashes = [self._turn_asset.sha256] if self._turn_asset else []
        if after == before:
            self.store.finish_turn(turn.id, "no_insertion", turn.end_sample, turn.end_mono, None,
                                   {"editor_foreground": self.editor.is_foreground()})
            self.turn = None
            self._go(S.RECOVERABLE_ERROR, "no insertion")
            fg = "" if self.editor.is_foreground() else " The answer editor lost focus, so Flow may have typed somewhere else."
            self._finish_turn_followups()
            self.say("No text arrived from Wispr Flow." + fg +
                     " Your recording is kept. Say start writing to try again, or type the answer.")
            return
        p = 0
        while p < min(len(before), len(after)) and before[p] == after[p]:
            p += 1
        s = 0
        while s < min(len(before), len(after)) - p and before[len(before) - 1 - s] == after[len(after) - 1 - s]:
            s += 1
        removed = before[p:len(before) - s]
        inserted = after[p:len(after) - s]
        change = {"turn_id": turn.id, "source": turn.source.value, "offset": p, "inserted": inserted,
                  "removed": removed, "flow_bridge": self.flow.label}
        self._go(S.VERIFY_PENDING)
        if removed.strip():
            # existing text changed during the turn: never guess, ask
            self.store.finish_turn(turn.id, "ambiguous", turn.end_sample, turn.end_mono, None, change)
            self.turn = None

            def keep():
                rev = self._commit(after, "dictation_insertion_ambiguous", change, asset_hashes=asset_hashes)
                self.say("Kept the new version. Verification is skipped for this turn because the change was not a single insertion.")
                self.audit("ambiguous_turn_kept", turn_id=change["turn_id"], revision=rev["id"])

            def restore():
                self._set_editor(before)
                self.say("Restored the previous version. The recording is kept as evidence.")
            self.ask("The answer changed in more than one place while you were dictating. Keep the new version?",
                     keep, restore)
            self._finish_turn_followups()
            return
        rev = self._commit(after, "dictation_insertion", change, asset_hashes=asset_hashes)
        turn.revision_after = rev["id"]
        self.store.finish_turn(turn.id, "inserted", turn.end_sample, turn.end_mono, rev["id"],
                               {"offset": p, "length": len(inserted)})
        self.audit("dictation_saved", turn_id=turn.id, revision=rev["id"], chars=len(inserted))
        self.turn = None
        if self._turn_asset:
            self.store.job("verify", turn.id, "queued")
            self.verifier.submit({"turn_id": turn.id, "revision_id": rev["id"], "answer_id": self.answer_id,
                                  "offset": p, "written_text": inserted, "audio_path": self._turn_asset.path},
                                 self.on_verified)
            msg = "Answer saved. Checking it against your recording."
        else:
            msg = "Answer saved, but no recording was captured, so verification is unavailable for this turn."
        tail = self._trailing_command(inserted)
        if tail:
            cut_at = p + tail[0]
            new_text = after[:cut_at].rstrip(" ,;:") + after[p + len(inserted):]

            def remove():
                self._commit(new_text, "command_phrase_removed", {"turn_id": turn.id, "removed": tail[1]})
                self.say("Removed.")
            self.ask(f"{msg} Your answer ends with the spoken command: {tail[1]}. Remove it from the answer?",
                     remove, lambda: self.say("Kept as written."))
        else:
            self.say(msg)
        self._finish_turn_followups()

    def _finish_turn_followups(self) -> None:
        if self._mute_after_turn:
            self._mute_after_turn = False
            self.call_later(0.1, self.mute)
        cmd = self._after_stop_command
        self._after_stop_command = None
        if cmd:
            self.call_later(0.1, lambda: self.handle_command_text(cmd, "voice"))

    # ================================================================== verification
    def on_verified(self, job: dict, res) -> None:
        turn_id = job["turn_id"]
        if res.status == ReviewStatus.UNAVAILABLE:
            self.store.job("verify", turn_id, "failed", res.error)
            self.store.add_alignment(turn_id, job["revision_id"], res.methods.get("asr", "none"),
                                     {"status": res.status.value, "error": res.error})
            self._announce(f"Verification is unavailable for that turn: {res.error}. Your answer and recording are kept.")
            return
        self.store.add_alignment(turn_id, job["revision_id"], res.methods.get("asr", ""),
                                 {"status": res.status.value, "hypothesis": res.hypothesis, "wer": res.wer,
                                  "mean_conf": res.mean_conf, "elapsed_s": res.elapsed_s,
                                  "duration_s": res.duration_s, "methods": res.methods})
        for d in res.discrepancies:
            self.store.add_review({
                "id": new_id("rvw"), "revision_id": job["revision_id"], "turn_id": turn_id,
                "answer_id": job["answer_id"],
                "span": [job["offset"] + d.span[0], job["offset"] + d.span[1]], "category": d.category,
                "clip": {"start": d.clip[0], "end": d.clip[1], "approximate": d.approximate},
                "status": ReviewStatus.CHECK.value, "written": d.written, "heard": d.heard,
                "methods": res.methods, "scores": {"asr_conf_min": d.hyp_conf, "turn_wer": res.wer,
                                                   "turn_mean_conf": res.mean_conf}})
        self.store.job("verify", turn_id, "done")
        if self.session_id:
            self.ui.reviews(self.store.reviews(sid=self.session_id))
        n = len(res.discrepancies)
        if n:
            self._announce(f"{'One passage' if n == 1 else f'{n} passages'} may need review. Say read review when ready.")
        else:
            self._announce("Verification finished. The written text corresponds with your recording.")

    def _announce(self, text: str) -> None:
        if self.state in (S.DICTATING, S.DICTATION_STARTING, S.DICTATION_STOPPING, S.CONFIRM):
            self.ui.message(text)
        else:
            self.say(text)

    def requeue_verification(self) -> int:
        """Crash recovery: re-run verification jobs that never finished."""
        n = 0
        for j in self.store.pending_jobs("verify"):
            t = self.store.turn(j["ref_id"])
            a = self.store.audio_for_turn(j["ref_id"])
            if not t or not a or not t["revision_after"]:
                continue
            detail = json.loads(t["detail_json"])
            revs = {r["id"]: r for r in self.store.revisions(t["session_id"])}
            rev = revs.get(t["revision_after"])
            if not rev:
                continue
            inserted = rev["payload"]["change"].get("inserted", "")
            self.verifier.submit({"turn_id": t["id"], "revision_id": rev["id"], "answer_id": t["answer_id"],
                                  "offset": detail.get("offset", 0), "written_text": inserted, "audio_path": a["path"]},
                                 self.on_verified)
            n += 1
        return n

    # ================================================================== review
    def _unresolved_reviews(self, answer_only: bool = False) -> list[dict]:
        if not self.session_id:
            return []
        rs = self.store.reviews(answer_id=self.answer_id) if answer_only else self.store.reviews(sid=self.session_id)
        return [r for r in rs if ReviewStatus(r["status"]).unresolved]

    def _locate(self, r: dict) -> Span | None:
        a, b = r["span"]
        if r["written"] and self.answer_text[a:b].strip() == r["written"]:
            return Span(a, b)
        if r["written"]:
            occ = self.cursor.occurrences(r["written"])
            if occ:
                return min(occ, key=lambda sp: abs(sp.start - a))
            return None
        return Span(min(a, len(self.answer_text)), min(a, len(self.answer_text)))

    def _do_read_review(self, cmd: Command) -> None:
        here = [r for r in self._unresolved_reviews(answer_only=True)]
        fresh = [r for r in here if r["id"] not in self.skipped_reviews] or here
        if not fresh:
            others = len(self._unresolved_reviews())
            self.say("There are no passages waiting for review in this answer."
                     + (f" {human_count(others, 'passage')} in other answers." if others else ""))
            return
        r = fresh[0]
        self.active_review = r
        sp = self._locate(r)
        if sp:
            self.cursor.selection = sp
            self.editor.select(sp.start, sp.end)
        idx = here.index(r) + 1
        written = r["written"] or "nothing"
        heard = r["heard"] or "nothing"
        approx = " The clip position is approximate." if r["clip"].get("approximate") else ""
        self.say(f"Review {idx} of {len(here)}, {r['category']}. Written: {written}. "
                 f"The local check heard: {heard}.{approx} Say play the original, keep original, "
                 f"replace that with the correct words, or skip review.")

    def _do_keep_original(self, cmd: Command) -> None:
        r = self.active_review
        if not r:
            self.say("Say read review first.")
            return
        self.store.update_review(r["id"], ReviewStatus.CONFIRMED.value)
        self.audit("review_confirmed", review_id=r["id"])
        self.active_review = None
        self.ui.reviews(self.store.reviews(sid=self.session_id))
        left = len(self._unresolved_reviews(answer_only=True))
        self.say(f"Kept as written. {human_count(left, 'passage')} left in this answer.")

    def _do_skip_review(self, cmd: Command) -> None:
        r = self.active_review
        if not r:
            self.say("Say read review first.")
            return
        self.skipped_reviews.add(r["id"])
        self.active_review = None
        self.say("Skipped for now. It stays unresolved, which does not block submission.")

    def _resolve_review_if_edited(self, edit_span: Span, rev_id: str) -> str:
        r = self.active_review
        if not r:
            return ""
        sp = self._locate_before_edit
        if sp and edit_span.start <= sp.end and edit_span.end >= sp.start:
            self.store.update_review(r["id"], ReviewStatus.CORRECTED.value, rev_id)
            self.audit("review_corrected", review_id=r["id"], revision=rev_id)
            self.active_review = None
            self.ui.reviews(self.store.reviews(sid=self.session_id))
            return " Review marked as corrected."
        return ""

    def _do_play_original(self, cmd: Command) -> None:
        r = self.active_review
        if r:
            turn_id, c0, c1 = r["turn_id"], r["clip"]["start"], r["clip"]["end"]
        else:
            turns = [t for t in self.store.turns(self.session_id) if t["answer_id"] == self.answer_id]
            if not turns:
                self.say("There is no recording for this answer yet.")
                return
            turn_id, c0, c1 = turns[-1]["id"], 0.0, 60.0
        a = self.store.audio_for_turn(turn_id)
        if not a:
            self.say("The recording for that passage is not available.")
            return
        pcm, rate = read_wav(self.vault.load_wav(a["path"]))
        seg = clip(pcm, rate, c0, c1)
        self.audit("evidence_played", turn_id=turn_id, start=c0, end=c1)
        self._play(seg, rate)

    # ================================================================== audio cursor
    def _do_read_answer(self, cmd: Command) -> None:
        if not self.answer_text.strip() and not self.blocks:
            self.say("This answer is empty.")
            return
        parts = [self.answer_text.strip()] if self.answer_text.strip() else []
        for i, b in enumerate(self.blocks, 1):
            if b["type"] == "equation":
                parts.append(f"Equation: {b['spoken']}.")
            elif b["type"] == "diagram":
                parts.append("Diagram. " + Diagram.from_block(b).summary())
        self.say(" ".join(parts))

    def _do_read_sentence(self, cmd: Command) -> None:
        which = {"this": "current"}.get(cmd.args.get("which", "next"), cmd.args.get("which", "next"))
        text = self.cursor.read_sentence(which)
        if self.cursor.selection:
            self.editor.select(self.cursor.selection.start, self.cursor.selection.end)
        self.say(text)

    def _do_read_paragraph(self, cmd: Command) -> None:
        self.say(self.cursor.read_paragraph(cmd.args["n"]))

    def _do_spell_word(self, cmd: Command) -> None:
        self.say(self.cursor.spell_current_word())

    def _do_find_word(self, cmd: Command) -> None:
        target, occ = cmd.args["target"], None
        m = re.match(r"^(first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th)) (?:occurrence of )?(.+)$", target)
        if m:
            occ, target = intents.ordinal_to_int(m.group(1)), m.group(2)
        sp = self.cursor.find(target, occ)
        self.editor.select(sp.start, sp.end)
        self.say(f"Found {self.cursor.slice(sp)}, in: {self.cursor.context(sp)}")

    def _edit(self, proposal, event_type: str, change: dict, confirm: bool) -> None:
        self._locate_before_edit = self.cursor.selection
        if self.active_review:
            self._locate_before_edit = self._locate(self.active_review)

        def commit():
            rev = self._commit(proposal.new_text, event_type, change)
            self.cursor.selection = proposal.span
            self.editor.select(proposal.span.start, proposal.span.end)
            extra = self._resolve_review_if_edited(proposal.span, rev["id"])
            self.say(f"Done. {proposal.description}.{extra} Say undo to revert.")
        if confirm:
            self.ask(proposal.description + ".", commit)
        else:
            commit()

    def _do_replace(self, cmd: Command) -> None:
        p = self.cursor.propose_replace(cmd.args["old"], cmd.args["new"], cmd.args.get("ord"))
        self._edit(p, "voice_replace", {"old": cmd.args["old"], "new": cmd.args["new"],
                                        "occurrence": cmd.args.get("ord")}, confirm=False)

    def _do_insert_text(self, cmd: Command) -> None:
        p = self.cursor.propose_insert(cmd.args["new"], cmd.args["where"], cmd.args["target"])
        self._edit(p, "voice_insert", {"new": cmd.args["new"], "where": cmd.args["where"],
                                       "target": cmd.args["target"]}, confirm=False)

    def _do_delete_text(self, cmd: Command) -> None:
        p = self.cursor.propose_delete(cmd.args["target"], cmd.args.get("ord"))
        self._edit(p, "voice_delete", {"target": cmd.args["target"]}, confirm=True)

    # ================================================================== equations
    def _do_equation_mode(self, cmd: Command) -> None:
        self.equation_buffer = []
        self.say("Equation mode. Say the equation, then say end equation.", then=S.COMMAND_LISTEN)

    def _do_end_equation(self, cmd: Command) -> None:
        self.say("Equation mode is not on. Say start equation first.")

    def _do_equation(self, cmd: Command) -> None:
        self._compile_equation(cmd.args["body"])

    def _compile_equation(self, body: str) -> None:
        eq = compile_spoken(body)
        block_id = new_id("eq")

        def insert():
            blocks = self.blocks + [eq.to_block(block_id)]
            self._commit(self.answer_text, "equation_added", {"block_id": block_id, "source": body}, blocks=blocks)
            n = sum(1 for b in blocks if b["type"] == "equation")
            self.say(f"Inserted as equation {n}.")
        self.ask(f"I heard: {eq.spoken}. Insert this equation?", insert)

    # ================================================================== diagrams
    def _diagram(self) -> tuple[Diagram, int | None]:
        for i, b in enumerate(self.blocks):
            if b["type"] == "diagram":
                return Diagram.from_block(b), i
        return Diagram(), None

    def _diagram_op(self, fn: Callable[[Diagram], str], op: str) -> None:
        d, idx = self._diagram()
        msg = fn(d)
        blocks = list(self.blocks)
        if idx is None:
            blocks.append(d.to_block(new_id("dg")))
        else:
            blocks[idx] = d.to_block(blocks[idx]["id"])
        self._commit(self.answer_text, f"diagram_{op}", {"op": op}, blocks=blocks)
        self.say(msg)

    def _do_diagram_create(self, cmd: Command) -> None:
        self._diagram_op(lambda d: d.create(cmd.args["name"]), "create")

    def _do_diagram_rename(self, cmd: Command) -> None:
        self._diagram_op(lambda d: d.rename(cmd.args["old"], cmd.args["new"]), "rename")

    def _do_diagram_delete(self, cmd: Command) -> None:
        d, _ = self._diagram()
        d.find(cmd.args["name"])  # validate before asking
        self.ask(f"Delete node {cmd.args['name']} and its connections?",
                 lambda: self._diagram_op(lambda dd: dd.delete(cmd.args["name"]), "delete"))

    def _do_diagram_connect(self, cmd: Command) -> None:
        self._diagram_op(lambda d: d.connect(cmd.args["a"], cmd.args["b"], cmd.args.get("label")), "connect")

    def _do_diagram_disconnect(self, cmd: Command) -> None:
        self._diagram_op(lambda d: d.disconnect(cmd.args["a"], cmd.args["b"]), "disconnect")

    def _do_diagram_list_nodes(self, cmd: Command) -> None:
        self.say(self._diagram()[0].list_nodes())

    def _do_diagram_list_edges(self, cmd: Command) -> None:
        self.say(self._diagram()[0].list_edges())

    def _do_diagram_read(self, cmd: Command) -> None:
        self.say(self._diagram()[0].summary())

    # ================================================================== approved visuals / sonification
    def _graph(self) -> dict | None:
        v = self.question.get("visual") or {}
        ref = v.get("data_ref")
        return self.exam.graph_data.get(ref) if ref and self.exam else None

    def _do_describe_visual(self, cmd: Command) -> None:
        v = self.question.get("visual")
        if not v:
            self.say("This question has no figure.")
            return
        self.say(v.get("description") or "No approved description was supplied for this figure.")

    def _do_read_graph(self, cmd: Command) -> None:
        g = self._graph()
        if not g:
            self.say("This question has no approved graph data.")
            return
        parts = [f"{g.get('title', 'Graph')}. Horizontal axis: {g['x_label']}. Vertical axis: {g['y_label']}."]
        for s in g["series"]:
            pts = "; ".join(f"{sonify._fmt(x)}, {sonify._fmt(y)}" for x, y in s["points"])
            parts.append(f"Series {s['label']}, {human_count(len(s['points']), 'point')}: {pts}.")
        self.say(" ".join(parts))

    def _do_play_graph(self, cmd: Command) -> None:
        g = self._graph()
        if not g:
            self.say("This question has no approved graph data.")
            return
        s = g["series"][0]
        ys = [float(y) for _, y in s["points"]]
        pcm = sonify.render_series(ys)
        self.ui.message(f"Playing {s['label']}: {len(ys)} points, low pitch = {min(ys):g}, high pitch = {max(ys):g}.")
        self.graph_point = -1
        self._play(pcm, sonify.SAMPLE_RATE)

    def _step_point(self, delta: int) -> None:
        g = self._graph()
        if not g:
            self.say("This question has no approved graph data.")
            return
        s = g["series"][0]
        n = len(s["points"])
        self.graph_point = max(0, min(n - 1, self.graph_point + delta))
        ys = [float(y) for _, y in s["points"]]
        f = sonify.frequencies(ys)[self.graph_point]
        desc = sonify.describe_point(s, self.graph_point, g["x_label"], g["y_label"])
        self.ui.message(desc)
        self._play(sonify.tone(f, 300), sonify.SAMPLE_RATE, after=lambda: self.say(desc))

    def _do_graph_next_point(self, cmd: Command) -> None:
        self._step_point(+1)

    def _do_graph_previous_point(self, cmd: Command) -> None:
        self._step_point(-1)

    # ================================================================== submission
    def _do_submit(self, cmd: Command) -> None:
        self.flush_typed()
        existing = self.store.submission(self.session_id)
        if existing:
            self.say("This examination was already submitted.")
            return
        answers = {a["question_id"]: a for a in self.store.answers(self.session_id)}
        unanswered = sum(1 for q in self.questions
                         if not (answers.get(q["db_id"]) and (answers[q["db_id"]]["text"].strip()
                                                               or answers[q["db_id"]]["blocks_json"] != "[]")))
        unresolved = len(self._unresolved_reviews())
        self.ask(f"You have {human_count(unanswered, 'unanswered question')} and "
                 f"{human_count(unresolved, 'passage')} not yet reviewed. Unreviewed passages do not block "
                 "submission and do not suggest any wrongdoing. After submitting, answers are locked. Submit now?",
                 self._submit, lambda: self.say("Not submitted. You can keep working."))

    def _submit(self) -> None:
        try:
            path = self.submitter(self.session_id, self.exam)
        except Exception as exc:
            log.exception("submission failed")
            self.say(f"Submission failed: {exc}. Your answers are still saved. Try again.")
            return
        self.audit("submitted", path=Path(path).name)
        self.editor.set_readonly(True)
        self._go(S.SUBMITTED, "submitted")
        self.ui.session({"submitted": True, "path": path})
        self.say("Examination submitted and locked. The submission package is in your SwarLekhak submissions folder.")
