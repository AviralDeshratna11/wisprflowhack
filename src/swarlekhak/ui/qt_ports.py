"""Qt adapters for the orchestrator ports: thread marshalling and cancellable timers."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal


class Invoker(QObject):
    """post(fn) from any thread -> fn() runs on the GUI thread (queued signal)."""
    _sig = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._sig.connect(self._run, Qt.QueuedConnection)

    def post(self, fn: Callable[[], None]) -> None:
        self._sig.emit(fn)

    @staticmethod
    def _run(fn) -> None:
        fn()


class _TimerHandle:
    def __init__(self, timer: QTimer, owner: "QtScheduler") -> None:
        self.timer, self.owner = timer, owner

    def cancel(self) -> None:
        self.timer.stop()
        self.owner._live.discard(self)


class QtScheduler:
    def __init__(self) -> None:
        self._live: set[_TimerHandle] = set()

    def __call__(self, secs: float, fn: Callable[[], None]) -> _TimerHandle:
        t = QTimer()
        t.setSingleShot(True)
        h = _TimerHandle(t, self)

        def fire():
            self._live.discard(h)
            fn()
        t.timeout.connect(fire)
        t.start(max(0, int(secs * 1000)))
        self._live.add(h)
        return h
