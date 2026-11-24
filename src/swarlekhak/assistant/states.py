"""Assistant audio/interaction state machine. Transitions are explicit and serialized."""
from __future__ import annotations

import logging
from enum import Enum
from typing import Callable

log = logging.getLogger(__name__)


class S(str, Enum):
    MUTED = "MUTED"
    PASSIVE_WAKE = "PASSIVE_WAKE"
    COMMAND_LISTEN = "COMMAND_LISTEN"
    CONFIRM = "CONFIRM"
    DICTATION_STARTING = "DICTATION_STARTING"
    DICTATING = "DICTATING"
    DICTATION_STOPPING = "DICTATION_STOPPING"
    VERIFY_PENDING = "VERIFY_PENDING"
    SPEAKING = "SPEAKING"
    RECOVERABLE_ERROR = "RECOVERABLE_ERROR"
    SUBMITTED = "SUBMITTED"


REST = {S.MUTED, S.PASSIVE_WAKE}
_INTERACTIVE = {S.MUTED, S.PASSIVE_WAKE, S.COMMAND_LISTEN, S.CONFIRM, S.SPEAKING, S.RECOVERABLE_ERROR}

TRANSITIONS: dict[S, set[S]] = {
    S.MUTED: {S.PASSIVE_WAKE, S.COMMAND_LISTEN, S.CONFIRM, S.SPEAKING, S.DICTATION_STARTING, S.RECOVERABLE_ERROR, S.SUBMITTED},
    S.PASSIVE_WAKE: {S.MUTED, S.COMMAND_LISTEN, S.CONFIRM, S.SPEAKING, S.DICTATION_STARTING, S.RECOVERABLE_ERROR, S.SUBMITTED},
    S.COMMAND_LISTEN: {S.MUTED, S.PASSIVE_WAKE, S.CONFIRM, S.SPEAKING, S.DICTATION_STARTING, S.RECOVERABLE_ERROR, S.SUBMITTED},
    S.CONFIRM: {S.MUTED, S.PASSIVE_WAKE, S.COMMAND_LISTEN, S.SPEAKING, S.DICTATION_STARTING, S.RECOVERABLE_ERROR, S.SUBMITTED},
    S.SPEAKING: {S.MUTED, S.PASSIVE_WAKE, S.COMMAND_LISTEN, S.CONFIRM, S.DICTATION_STARTING, S.RECOVERABLE_ERROR, S.SUBMITTED},
    # Dictation is a guarded pipeline: it can only move forward or fail.
    S.DICTATION_STARTING: {S.DICTATING, S.RECOVERABLE_ERROR},
    S.DICTATING: {S.DICTATION_STOPPING, S.RECOVERABLE_ERROR},
    S.DICTATION_STOPPING: {S.VERIFY_PENDING, S.RECOVERABLE_ERROR, S.CONFIRM, S.SPEAKING},
    S.VERIFY_PENDING: _INTERACTIVE | {S.DICTATION_STARTING},
    S.RECOVERABLE_ERROR: {S.MUTED, S.PASSIVE_WAKE, S.SPEAKING, S.COMMAND_LISTEN, S.CONFIRM},
    # After submission only resting / speaking states remain; no dictation.
    S.SUBMITTED: {S.MUTED, S.SPEAKING},
}


class IllegalTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self, initial: S = S.MUTED) -> None:
        self._state = initial
        self._listeners: list[Callable[[S, S, str], None]] = []
        self.submitted = False

    @property
    def state(self) -> S:
        return self._state

    def subscribe(self, fn: Callable[[S, S, str], None]) -> None:
        self._listeners.append(fn)

    def can(self, to: S) -> bool:
        if self.submitted and to not in (S.MUTED, S.SPEAKING, S.SUBMITTED):
            return False
        return to == self._state or to in TRANSITIONS[self._state]

    def go(self, to: S, reason: str = "") -> None:
        if to == self._state:
            return
        if not self.can(to):
            raise IllegalTransition(f"{self._state.value} -> {to.value} ({reason})")
        old = self._state
        self._state = to
        if to == S.SUBMITTED:
            self.submitted = True
        log.info("state %s -> %s %s", old.value, to.value, reason)
        for fn in list(self._listeners):
            fn(old, to, reason)
