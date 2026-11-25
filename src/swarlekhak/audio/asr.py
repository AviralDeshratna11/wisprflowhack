"""Local Vosk speech recognition: (a) live passive/command listener, (b) offline turn transcriber.

Vosk runs fully on-device. The passive listener keeps only the recognizer's
in-memory state; nothing it hears is written to disk or logs.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

_MODEL_CACHE: dict[str, object] = {}
_MODEL_LOCK = threading.Lock()


def load_model(path: Path):
    from vosk import Model, SetLogLevel
    SetLogLevel(-1)
    key = str(path)
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            _MODEL_CACHE[key] = Model(key)
        return _MODEL_CACHE[key]


class VoskTranscriber:
    """Independent offline hypothesis with word timestamps for the Acoustic Proof Engine."""

    def __init__(self, model_path: Path) -> None:
        self.model_path = Path(model_path)
        self.version = f"vosk-0.3.45/{self.model_path.name}"
        self._model = load_model(self.model_path)

    def transcribe(self, pcm16k: np.ndarray) -> dict:
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(self._model, 16000)
        rec.SetWords(True)
        words: list[dict] = []
        texts: list[str] = []
        data = pcm16k.astype(np.int16).tobytes()
        step = 8000
        for i in range(0, len(data), step):
            if rec.AcceptWaveform(data[i:i + step]):
                self._collect(json.loads(rec.Result()), words, texts)
        self._collect(json.loads(rec.FinalResult()), words, texts)
        return {"text": " ".join(t for t in texts if t), "words": words}

    def alternatives(self, pcm16k: np.ndarray, n: int = 5) -> list[list[list[dict]]]:
        """N-best per recognised segment: [segment][alternative] -> word dicts (start, end; no conf)."""
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(self._model, 16000)
        rec.SetWords(True)
        rec.SetMaxAlternatives(n)
        segs: list[list[list[dict]]] = []

        def take(res: dict) -> None:
            alts = []
            for a in res.get("alternatives", []):
                ws = [{"word": w["word"], "start": w["start"], "end": w["end"], "conf": 1.0}
                      for w in a.get("result", [])]
                if ws:
                    alts.append(ws)
            if alts:
                segs.append(alts)
        data = pcm16k.astype(np.int16).tobytes()
        for i in range(0, len(data), 8000):
            if rec.AcceptWaveform(data[i:i + 8000]):
                take(json.loads(rec.Result()))
        take(json.loads(rec.FinalResult()))
        return segs

    def constrained_times(self, pcm16k: np.ndarray, words: list[str]) -> list[dict]:
        """Text-constrained alignment: decode with a grammar that only allows the written words (Kaldi FST),
        returning word times for the written text. Out-of-vocabulary words are skipped by Vosk."""
        from vosk import KaldiRecognizer
        phrase = " ".join(w for w in words if w)
        if not phrase:
            return []
        rec = KaldiRecognizer(self._model, 16000, json.dumps([phrase, "[unk]"]))
        rec.SetWords(True)
        out: list[dict] = []
        data = pcm16k.astype(np.int16).tobytes()
        for i in range(0, len(data), 8000):
            if rec.AcceptWaveform(data[i:i + 8000]):
                out += json.loads(rec.Result()).get("result", [])
        out += json.loads(rec.FinalResult()).get("result", [])
        return [w for w in out if w.get("word") != "[unk]"]

    @staticmethod
    def _collect(res: dict, words: list, texts: list) -> None:
        texts.append(res.get("text", ""))
        for w in res.get("result", []):
            words.append({"word": w["word"], "start": w["start"], "end": w["end"], "conf": w.get("conf", 1.0)})


class VoskListener:
    """Continuous free-form recognizer on the 16 kHz stream. Emits final utterances.

    Called on the audio worker thread; on_final is invoked there too, so the
    app marshals it to the GUI thread. When disabled (muted / TTS speaking) audio
    is discarded and the recognizer is reset so speech playback can never
    become a command.
    """

    def __init__(self, model_path: Path, on_final: Callable[[str], None],
                 on_partial: Callable[[str], None] | None = None) -> None:
        self.model = load_model(model_path)
        self.on_final = on_final
        self.on_partial = on_partial
        self._enabled = False
        self._reset = threading.Event()
        self._rec = None
        self._last_partial = ""

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self._enabled:
            self._reset.set()
        self._enabled = enabled
        if not enabled:
            self._reset.set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __call__(self, block16k: np.ndarray) -> None:
        from vosk import KaldiRecognizer
        if self._rec is None or self._reset.is_set():
            self._rec = KaldiRecognizer(self.model, 16000)
            self._reset.clear()
            self._last_partial = ""
        if not self._enabled:
            return
        if self._rec.AcceptWaveform(block16k.tobytes()):
            text = json.loads(self._rec.Result()).get("text", "").strip()
            self._last_partial = ""
            if text:
                self.on_final(text)
        elif self.on_partial:
            p = json.loads(self._rec.PartialResult()).get("partial", "")
            if p and p != self._last_partial:
                self._last_partial = p
                self.on_partial(p)
