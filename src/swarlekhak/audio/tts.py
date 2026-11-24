"""Local text-to-speech via Windows SAPI 5 (on-device, no network), on its own COM thread."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

log = logging.getLogger(__name__)

SVSF_ASYNC = 1
SVSF_PURGE = 2
SVSF_IS_NOT_XML = 16


class SapiTTS:
    def __init__(self, voice: str = "", rate: int = 0) -> None:
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._interrupt = threading.Event()
        self.voice_name = voice
        self.rate = rate
        self.speaking = False
        self.available = True
        self.voices: list[str] = []
        self._ready = threading.Event()
        self._t = threading.Thread(target=self._run, name="tts", daemon=True)
        self._t.start()
        self._ready.wait(5)

    def speak(self, text: str, on_done: Callable[[], None] | None = None) -> None:
        self._interrupt.clear()
        self._q.put((text, on_done))

    def stop(self) -> None:
        """Interrupt current speech and drop queued speech (callbacks still fire)."""
        self._interrupt.set()

    def set_rate(self, rate: int) -> None:
        self.rate = max(-10, min(10, rate))
        self._q.put(("__rate__", None))

    def set_voice(self, name: str) -> None:
        self.voice_name = name
        self._q.put(("__voice__", None))

    def shutdown(self) -> None:
        self._stop.set()
        self._interrupt.set()
        self._q.put((None, None))

    def _apply_voice(self, v) -> None:
        if not self.voice_name:
            return
        for tok in v.GetVoices():
            if self.voice_name.lower() in tok.GetDescription().lower():
                v.Voice = tok
                return

    def _run(self) -> None:
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            v = win32com.client.Dispatch("SAPI.SpVoice")
            self.voices = [t.GetDescription() for t in v.GetVoices()]
            self._apply_voice(v)
            v.Rate = self.rate
        except Exception as exc:
            log.error("SAPI unavailable: %s", exc)
            self.available = False
            self._ready.set()
            while not self._stop.is_set():
                text, cb = self._q.get()
                if cb:
                    cb()
            return
        self._ready.set()
        while not self._stop.is_set():
            text, cb = self._q.get()
            if text is None:
                break
            if text == "__rate__":
                v.Rate = self.rate
                continue
            if text == "__voice__":
                self._apply_voice(v)
                continue
            if not self._interrupt.is_set():
                self.speaking = True
                try:
                    v.Speak(text, SVSF_ASYNC | SVSF_PURGE | SVSF_IS_NOT_XML)
                    while not v.WaitUntilDone(40):
                        if self._interrupt.is_set():
                            v.Speak("", SVSF_ASYNC | SVSF_PURGE)
                            break
                except Exception:
                    log.exception("speech failed")
                self.speaking = False
            if cb:
                try:
                    cb()
                except Exception:
                    log.exception("tts callback failed")


class NullTTS:
    """Silent TTS used in tests (explicitly injected, never a silent production fallback)."""

    available = True
    voices: list[str] = []

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.rate = 0

    def speak(self, text: str, on_done=None) -> None:
        self.spoken.append(text)
        if on_done:
            on_done()

    def stop(self) -> None:
        pass

    def set_rate(self, rate: int) -> None:
        self.rate = rate

    def set_voice(self, name: str) -> None:
        pass

    def shutdown(self) -> None:
        pass
