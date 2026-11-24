"""Conversation voice: Gemini TTS or ElevenLabs (opt-in, cloud) with Windows SAPI as the local fallback.

All engines produce 16-bit mono PCM that is played through one SpeechOutput, so the
conversation orb is driven by the *real* level of the audio being spoken (no fake animation).

ElevenLabs (docs.elevenlabs.io, checked 2026-11-25):
  POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?output_format=pcm_22050
  header xi-api-key; body {text, model_id, voice_settings{stability, similarity_boost, speed}}.
  Library voices may need to be added to "My Voices" first. Text sent is processed (and by default
  logged) by ElevenLabs - only used after explicit consent.

Gemini TTS (aistudio.google.com/docs/speech-generation, checked 2026-11-25):
  POST https://generativelanguage.googleapis.com/v1beta/interactions, header x-goog-api-key,
  body {model, input:[{type:user_input, content:[{type:text, text, annotations:[{type:speech_metadata,
  style}]}]}], response_format:{type:audio}, generation_config:{speech_config:[{voice}]}}.
  Unary responses are WAV (24 kHz mono 16-bit LE). TTS reads `text` "strictly as a verbatim transcript",
  so approved question text is spoken exactly. Gemini *Live* is deliberately not used: it is a conversational
  model that generates its own replies, which exam mode forbids.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import wave
import queue
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

API = "https://api.elevenlabs.io"
DEFAULT_VOICE_ID = "EMxdghWQV7gqV33j4J3F"     # "Abhi" (ElevenLabs Voice Library)
DEFAULT_MODEL = "eleven_flash_v2_5"           # documented ultra-low-latency model
PCM_RATE = 22050
MODELS = ["eleven_flash_v2_5", "eleven_v4_turbo", "eleven_multilingual_v2", "eleven_v3"]


GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_DEFAULT_MODEL = "gemini-3.8-flash-lite-tts"   # documented low-latency TTS model
GEMINI_MODELS = ["gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts"]
GEMINI_DEFAULT_VOICE = "Sulafat"
GEMINI_VOICES = {  # prebuilt voices and the descriptors Google lists for them
    "Zephyr": "Bright", "Puck": "Upbeat", "Charon": "Informative", "Kore": "Firm", "Fenrir": "Excitable",
    "Leda": "Youthful", "Orus": "Firm", "Aoede": "Breezy", "Callirrhoe": "Easy-going", "Autonoe": "Bright",
    "Enceladus": "Breathy", "Iapetus": "Clear", "Umbriel": "Easy-going", "Algieba": "Smooth", "Despina": "Smooth",
    "Erinome": "Clear", "Algenib": "Gravelly", "Rasalgethi": "Informative", "Laomedeia": "Upbeat",
    "Achernar": "Soft", "Alnilam": "Firm", "Schedar": "Even", "Gacrux": "Mature", "Pulcherrima": "Forward",
    "Achird": "Friendly", "Zubenelgenubi": "Casual", "Vindemiatrix": "Gentle", "Sadachbia": "Lively",
    "Sadaltager": "Knowledgeable", "Sulafat": "Warm",
}


class ElevenLabsError(RuntimeError):
    pass


class CloudVoiceError(ElevenLabsError):
    """Any cloud voice failure (ElevenLabs or Gemini) -> fall back to the local voice."""


def gemini_style(rate: int) -> str:
    pace = "at a calm, measured pace" if -3 <= rate <= 3 else ("slowly" if rate < -3 else "briskly")
    return f"warm, clear and patient, like a helpful exam assistant, speaking {pace}"


def gemini_request(api_key: str, text: str, model: str, voice: str, rate: int) -> urllib.request.Request:
    body = {"model": model,
            "input": [{"type": "user_input", "content": [{"type": "text", "text": text, "annotations": [
                {"type": "speech_metadata", "style": gemini_style(rate)}]}]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice}]}}
    return urllib.request.Request(GEMINI_API, data=json.dumps(body).encode("utf-8"), method="POST",
                                  headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})


def _find_audio(obj) -> str | None:
    """Locate base64 audio in an Interactions (steps[].content[] type=audio) or generateContent
    (inlineData) response without depending on one exact nesting."""
    if isinstance(obj, dict):
        if obj.get("type") == "audio" and isinstance(obj.get("data"), str):
            return obj["data"]
        inline = obj.get("inlineData") or obj.get("inline_data")
        if isinstance(inline, dict) and isinstance(inline.get("data"), str):
            return inline["data"]
        for v in obj.values():
            found = _find_audio(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_audio(v)
            if found:
                return found
    return None


def decode_gemini_audio(payload: dict) -> tuple[np.ndarray, int]:
    b64 = _find_audio(payload)
    if not b64:
        raise CloudVoiceError("Gemini response contained no audio")
    raw = base64.b64decode(b64)
    if raw[:4] == b"RIFF":
        with wave.open(io.BytesIO(raw), "rb") as w:
            if w.getsampwidth() != 2:
                raise CloudVoiceError("unexpected Gemini audio sample width")
            rate, ch = w.getframerate(), w.getnchannels()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.int16)
        if ch > 1:
            pcm = pcm.reshape(-1, ch)[:, 0].copy()
        return pcm, rate
    return np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.int16), 24000  # documented raw L16


def _gemini_error(e: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(e.read().decode("utf-8", "replace"))
        err = body.get("error", body)
        return str(err.get("message") or err) if isinstance(err, dict) else str(err)
    except Exception:
        return e.reason or "error"


def gemini_check(api_key: str, model: str = GEMINI_DEFAULT_MODEL, voice: str = GEMINI_DEFAULT_VOICE,
                 timeout: float = 20.0, opener=urllib.request.urlopen) -> tuple[bool, str]:
    """Synthesise a tiny phrase - the only check that proves key, model and voice together."""
    try:
        with opener(gemini_request(api_key, "OK.", model, voice, 0), timeout=timeout) as r:
            pcm, rate = decode_gemini_audio(json.loads(r.read().decode("utf-8")))
        return True, f"Gemini OK: {model}, voice {voice} ({len(pcm) / rate:.2f} s test audio)."
    except urllib.error.HTTPError as e:
        return False, f"Gemini rejected the request ({e.code}: {_gemini_error(e)})"
    except CloudVoiceError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Gemini not reachable: {e}"


def rate_to_speed(rate: int) -> float:
    """SwarLekhak rate -10..10 -> ElevenLabs speed 0.7..1.2 (1.0 at rate 0)."""
    rate = max(-10, min(10, int(rate)))
    return round(1.0 + (rate / 10) * (0.3 if rate < 0 else 0.2), 3)


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read().decode("utf-8", "replace"))
        d = body.get("detail", body)
        if isinstance(d, dict):
            return str(d.get("message") or d.get("status") or d)
        return str(d)
    except Exception:
        return exc.reason or "error"


def _missing_permission(e: urllib.error.HTTPError, detail: str) -> bool:
    return e.code in (401, 403) and "permission" in detail.lower()


def eleven_check(api_key: str, voice_id: str, model_id: str = DEFAULT_MODEL, timeout: float = 10.0,
                 opener=urllib.request.urlopen) -> tuple[bool, str]:
    """Key + voice check that also works with *scoped* API keys (e.g. text-to-speech only).

    1. GET /v1/user (account/quota; skipped if the key lacks user_read)
    2. GET /v1/voices/{id} (skipped if the key lacks voices_read)
    3. if neither could confirm the voice: a tiny real synthesis request ("OK.", ~3 characters of quota).
    """
    def get(path):
        req = urllib.request.Request(API + path, headers={"xi-api-key": api_key})
        with opener(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    notes: list[str] = []
    try:
        sub = get("/v1/user").get("subscription", {}) or {}
        notes.append(f"{sub.get('tier', '?')} tier, {sub.get('character_count', '?')}/"
                     f"{sub.get('character_limit', '?')} characters used")
    except urllib.error.HTTPError as e:
        d = _http_error_detail(e)
        if not _missing_permission(e, d):
            return False, f"API key rejected ({e.code}: {d})"
        notes.append("scoped key: account details not readable")
    except Exception as e:
        return False, f"ElevenLabs not reachable: {e}"
    try:
        v = get(f"/v1/voices/{voice_id}")
        return True, f"Key OK ({'; '.join(notes)}). Voice: {v.get('name', voice_id)}."
    except urllib.error.HTTPError as e:
        d = _http_error_detail(e)
        if not _missing_permission(e, d):
            return False, (f"Voice {voice_id} is not available to this account ({e.code}: {d}). "
                           "Add it from the Voice Library to My Voices.")
        notes.append("voice details not readable")
    except Exception as e:
        return False, f"Voice check failed: {e}"
    # scoped key: prove it the only way that matters - synthesise a tiny phrase
    req = urllib.request.Request(
        f"{API}/v1/text-to-speech/{voice_id}/stream?output_format=pcm_{PCM_RATE}",
        data=json.dumps({"text": "OK.", "model_id": model_id}).encode("utf-8"), method="POST",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=timeout) as r:
            n = len(r.read())
        if n < 200:
            return False, f"Synthesis returned only {n} bytes."
        return True, f"Key OK ({'; '.join(notes)}). Test synthesis with voice {voice_id} succeeded ({n} bytes)."
    except urllib.error.HTTPError as e:
        return False, f"Synthesis failed ({e.code}: {_http_error_detail(e)})"
    except Exception as e:
        return False, f"Synthesis failed: {e}"


class SpeechOutput:
    """Plays int16 mono PCM in small slices, exposing the real RMS level and supporting interruption."""

    def __init__(self) -> None:
        self.level = 0.0
        self._stream = None
        self._rate = 0

    def _open(self, rate: int) -> None:
        if self._stream is not None and self._rate == rate:
            return
        self.close()
        import sounddevice as sd
        self._stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16", blocksize=0)
        self._stream.start()
        self._rate = rate

    def play_chunk(self, pcm: np.ndarray, rate: int, interrupted: threading.Event) -> bool:
        """Blocking write; returns False if interrupted."""
        self._open(rate)
        step = max(256, rate // 40)   # 25 ms slices -> smooth level updates
        for i in range(0, len(pcm), step):
            if interrupted.is_set():
                self.level = 0.0
                return False
            s = pcm[i:i + step]
            f = s.astype(np.float32)
            self.level = float(min(1.0, np.sqrt(np.mean(f * f)) / 9000.0)) if len(s) else 0.0
            self._stream.write(s.tobytes())
        return True

    def finish(self) -> None:
        # let the device buffer drain so the orb does not stop before the voice does
        if self._stream is not None:
            time.sleep(min(0.3, float(self._stream.latency or 0.1)))
        self.level = 0.0

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self.level = 0.0


def sapi_synthesize(text: str, voice: str, rate: int) -> tuple[np.ndarray, int]:
    """Local Windows SAPI -> 22 kHz 16-bit mono PCM in memory (must run on a COM-initialised thread)."""
    import win32com.client
    v = win32com.client.Dispatch("SAPI.SpVoice")
    if voice:
        for tok in v.GetVoices():
            if voice.lower() in tok.GetDescription().lower():
                v.Voice = tok
    v.Rate = max(-10, min(10, rate))
    ms = win32com.client.Dispatch("SAPI.SpMemoryStream")
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 22  # SAFT22kHz16BitMono
    ms.Format = fmt
    v.AudioOutputStream = ms
    v.Speak(text, 16)  # SVSFIsNotXML
    data = bytes(ms.GetData())
    return np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16), 22050


class ConversationVoice:
    """Drop-in replacement for SapiTTS: speak(text, on_done) / stop() / set_rate / set_voice / shutdown."""

    def __init__(self, provider: str = "elevenlabs", voice_id: str = DEFAULT_VOICE_ID, model_id: str = DEFAULT_MODEL,
                 api_key: Callable[[], str | None] = lambda: None, consent: Callable[[], bool] = lambda: False,
                 sapi_voice: str = "", rate: int = 0, on_notice: Callable[[str], None] | None = None,
                 output: SpeechOutput | None = None, opener=urllib.request.urlopen,
                 sapi=sapi_synthesize, gemini_key: Callable[[], str | None] = lambda: None,
                 gemini_consent: Callable[[], bool] = lambda: False, gemini_model: str = GEMINI_DEFAULT_MODEL,
                 gemini_voice: str = GEMINI_DEFAULT_VOICE) -> None:
        self.provider, self.voice_id, self.model_id = provider, voice_id, model_id
        self.api_key, self.consent = api_key, consent
        self.gemini_key, self.gemini_consent = gemini_key, gemini_consent
        self.gemini_model, self.gemini_voice = gemini_model, gemini_voice
        self.sapi_voice, self.rate = sapi_voice, rate
        self.on_notice = on_notice or (lambda m: None)
        self.out = output or SpeechOutput()
        self._open = opener
        self._sapi = sapi
        self._q: queue.Queue = queue.Queue()
        self._interrupt = threading.Event()
        self._stop = threading.Event()
        self._cache: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._cache_bytes = 0
        self._noticed: set[str] = set()
        self.speaking = False
        self.available = True
        self.engine_in_use = "none"
        self.last_error = ""
        self.voices: list[str] = []
        self._ready = threading.Event()
        threading.Thread(target=self._run, name="voice", daemon=True).start()
        self._ready.wait(5)

    # ---- public API (same as SapiTTS)
    @property
    def level(self) -> float:
        return self.out.level

    def speak(self, text: str, on_done: Callable[[], None] | None = None) -> None:
        self._interrupt.clear()
        self._q.put((text, on_done))

    def stop(self) -> None:
        self._interrupt.set()

    def set_rate(self, rate: int) -> None:
        self.rate = max(-10, min(10, rate))

    def set_voice(self, name: str) -> None:
        self.sapi_voice = name

    def shutdown(self) -> None:
        self._stop.set()
        self._interrupt.set()
        self._q.put((None, None))

    def cloud_active(self) -> bool:
        if self.provider == "elevenlabs":
            return bool(self.consent()) and bool(self.api_key())
        if self.provider == "gemini":
            return bool(self.gemini_consent()) and bool(self.gemini_key())
        return False

    # ---- worker
    def _run(self) -> None:
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            self.voices = [t.GetDescription() for t in win32com.client.Dispatch("SAPI.SpVoice").GetVoices()]
        except Exception:
            pass
        self._ready.set()
        while not self._stop.is_set():
            text, cb = self._q.get()
            if text is None:
                break
            if not self._interrupt.is_set() and text.strip():
                self.speaking = True
                try:
                    self._speak_one(text)
                except Exception as exc:
                    log.exception("speech failed")
                    self.last_error = str(exc)
                finally:
                    self.out.finish()
                    self.speaking = False
            if cb:
                try:
                    cb()
                except Exception:
                    log.exception("speech callback failed")
        self.out.close()

    def _speak_one(self, text: str) -> None:
        if self.cloud_active():
            name = {"elevenlabs": "ElevenLabs", "gemini": "Gemini"}[self.provider]
            try:
                (self._eleven if self.provider == "elevenlabs" else self._gemini)(text)
                self.engine_in_use = self.provider
                return
            except ElevenLabsError as exc:
                self.last_error = str(exc)
                key = str(exc)[:60]
                if key not in self._noticed:
                    self._noticed.add(key)
                    self.on_notice(f"{name} voice unavailable ({exc}); using the local Windows voice.")
        pcm, rate = self._sapi(text, self.sapi_voice, self.rate)
        self.engine_in_use = "sapi"
        self.out.play_chunk(pcm, rate, self._interrupt)

    def _gemini(self, text: str) -> None:
        key = ("gemini", self.gemini_voice, self.gemini_model, gemini_style(self.rate), text)
        if key not in self._cache:
            try:
                req = gemini_request(self.gemini_key() or "", text, self.gemini_model, self.gemini_voice, self.rate)
                with self._open(req, timeout=20) as resp:
                    pcm, rate = decode_gemini_audio(json.loads(resp.read().decode("utf-8")))
            except urllib.error.HTTPError as e:
                raise CloudVoiceError(f"HTTP {e.code}: {_gemini_error(e)}") from e
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                raise CloudVoiceError(f"network: {e}") from e
            self._cache[key] = (pcm, rate)
            self._cache_bytes += pcm.nbytes
            while self._cache_bytes > 24_000_000 and self._cache:
                _, old = self._cache.popitem(last=False)
                self._cache_bytes -= (old[0] if isinstance(old, tuple) else old).nbytes
        else:
            self._cache.move_to_end(key)
        pcm, rate = self._cache[key]
        self.out.play_chunk(pcm, rate, self._interrupt)

    def _eleven(self, text: str) -> None:
        speed = rate_to_speed(self.rate)
        key = (self.voice_id, self.model_id, speed, text)
        if key in self._cache:
            self._cache.move_to_end(key)
            self.out.play_chunk(self._cache[key], PCM_RATE, self._interrupt)
            return
        url = f"{API}/v1/text-to-speech/{self.voice_id}/stream?output_format=pcm_{PCM_RATE}"
        body = {"text": text, "model_id": self.model_id,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": speed}}
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"xi-api-key": self.api_key() or "", "Content-Type": "application/json",
                                              "Accept": "audio/pcm"})
        parts: list[np.ndarray] = []
        try:
            with self._open(req, timeout=12) as resp:
                rest = b""
                while True:
                    chunk = resp.read(4410)  # ~100 ms of 22.05 kHz int16
                    if not chunk:
                        break
                    chunk = rest + chunk
                    n = len(chunk) // 2 * 2
                    rest = chunk[n:]
                    pcm = np.frombuffer(chunk[:n], dtype="<i2").astype(np.int16)  # PCM is 16-bit little-endian
                    parts.append(pcm)
                    if not self.out.play_chunk(pcm, PCM_RATE, self._interrupt):
                        return  # interrupted: do not cache a partial utterance
        except urllib.error.HTTPError as e:
            raise ElevenLabsError(f"HTTP {e.code}: {_http_error_detail(e)}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if parts:   # network dropped mid-utterance: finish locally rather than go silent
                raise ElevenLabsError(f"stream interrupted: {e}") from e
            raise ElevenLabsError(f"network: {e}") from e
        if not parts:
            raise ElevenLabsError("empty audio response")
        full = np.concatenate(parts)
        self._cache[key] = full
        self._cache_bytes += full.nbytes
        while self._cache_bytes > 24_000_000 and self._cache:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= (old[0] if isinstance(old, tuple) else old).nbytes
