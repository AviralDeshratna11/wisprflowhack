"""Voice engine (ElevenLabs HTTP mocked - no network), secrets, conversation follow-up, orb rendering."""
import io
import json
import os
import threading
import urllib.error

import numpy as np
import pytest

from swarlekhak.assistant.states import S
from swarlekhak.audio.speech import ConversationVoice, rate_to_speed


class FakeOut:
    def __init__(self):
        self.level = 0.0
        self.played = []

    def play_chunk(self, pcm, rate, interrupted):
        self.played.append((len(pcm), rate))
        return not interrupted.is_set()

    def finish(self):
        pass

    def close(self):
        pass


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_voice(opener, consent=True, key="k-123", provider="elevenlabs"):
    out = FakeOut()
    sapi_calls = []

    def sapi(text, voice, rate):
        sapi_calls.append(text)
        return np.zeros(2205, dtype=np.int16), 22050
    notices = []
    v = ConversationVoice(provider=provider, api_key=lambda: key, consent=lambda: consent, output=out,
                          opener=opener, sapi=sapi, on_notice=notices.append)
    return v, out, sapi_calls, notices


def speak_and_wait(v, text):
    done = threading.Event()
    v.speak(text, on_done=done.set)
    assert done.wait(5)


def test_elevenlabs_request_shape_and_pcm_streaming():
    seen = {}
    pcm = (np.arange(4000, dtype=np.int16) - 2000).astype("<i2").tobytes() + b"\x01"  # odd trailing byte

    def opener(req, timeout):
        seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        return FakeResp(pcm)
    v, out, sapi_calls, _ = make_voice(opener)
    speak_and_wait(v, "Question one.")
    assert seen["url"] == ("https://api.elevenlabs.io/v1/text-to-speech/EMxdghWQV7gqV33j4J3F/stream"
                           "?output_format=pcm_22050")
    assert seen["headers"]["Xi-api-key"] == "k-123"
    assert seen["body"]["model_id"] == "eleven_flash_v2_5" and seen["body"]["text"] == "Question one."
    assert sum(n for n, _ in out.played) == 4000 and all(r == 22050 for _, r in out.played)
    assert not sapi_calls and v.engine_in_use == "elevenlabs"
    # second identical utterance comes from the in-memory cache (no new HTTP request)
    seen.clear()
    speak_and_wait(v, "Question one.")
    assert "url" not in seen
    v.shutdown()


def test_http_error_falls_back_to_local_voice_and_notices_once():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b'{"detail":{"message":"Invalid API key"}}'))
    v, out, sapi_calls, notices = make_voice(opener)
    speak_and_wait(v, "One.")
    speak_and_wait(v, "Two.")
    assert sapi_calls == ["One.", "Two."] and v.engine_in_use == "sapi"
    assert len(notices) == 1 and "Invalid API key" in notices[0]
    v.shutdown()


@pytest.mark.parametrize("consent,key,provider", [(False, "k", "elevenlabs"), (True, None, "elevenlabs"),
                                                  (True, "k", "sapi")])
def test_no_cloud_without_consent_key_or_provider(consent, key, provider):
    def opener(req, timeout):
        raise AssertionError("must not contact ElevenLabs")
    v, out, sapi_calls, _ = make_voice(opener, consent=consent, key=key, provider=provider)
    speak_and_wait(v, "Local only.")
    assert sapi_calls == ["Local only."]
    v.shutdown()


def test_speed_mapping():
    assert rate_to_speed(0) == 1.0 and rate_to_speed(-10) == 0.7 and rate_to_speed(10) == 1.2


def test_secret_roundtrip_dpapi(tmp_path, monkeypatch):
    pytest.importorskip("win32crypt")
    monkeypatch.setenv("SWARLEKHAK_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("XI_API_KEY", raising=False)
    from swarlekhak.security import secrets
    secrets.save_secret("elevenlabs_api_key", "sk_test_value")
    raw = (tmp_path / "secrets" / "elevenlabs_api_key.dpapi").read_bytes()
    assert b"sk_test_value" not in raw
    assert secrets.load_secret("elevenlabs_api_key") == "sk_test_value"
    secrets.delete_secret("elevenlabs_api_key")
    assert secrets.load_secret("elevenlabs_api_key") is None


# ---------------------------------------------------------------- conversation follow-up (orchestrator)
def test_wake_conversation_keeps_listening_then_times_out(env):
    o = env.orch
    o.on_heard("hey swar status")
    assert o.state == S.COMMAND_LISTEN            # follow-up window after the reply
    o.on_heard("help")                             # no wake phrase needed inside the conversation
    assert "start my examination" in env.tts.last and o.state == S.COMMAND_LISTEN
    env.sched.advance(o.prefs.follow_up_seconds + 0.5)
    assert o.state == S.PASSIVE_WAKE


def test_thanks_ends_conversation(env):
    o = env.orch
    o.on_heard("hey swar status")
    o.on_heard("thank you")
    assert env.tts.last == "Okay." and o.state == S.PASSIVE_WAKE


def test_keyboard_commands_do_not_open_the_microphone(env):
    env.orch.handle_command_text("status", "keyboard")
    assert env.orch.state == S.PASSIVE_WAKE


def test_follow_up_can_be_disabled(env):
    env.orch.prefs.follow_up_listening = False
    env.orch.on_heard("hey swar status")
    assert env.orch.state == S.PASSIVE_WAKE


def test_partials_only_shown_while_awake(env):
    shown = []
    env.ui.partial = shown.append
    env.orch.on_partial("private passive speech")
    assert shown == []
    env.orch.activate()
    env.orch.on_partial("read quest")
    assert shown == ["read quest"]


def test_user_utterance_reaches_ui(env):
    said = []
    env.ui.user_said = said.append
    env.orch.on_heard("hey swar status")
    assert said == ["status"]


# ---------------------------------------------------------------- orb
def test_orb_renders_and_follows_real_level(qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from swarlekhak.ui.orb import ConversationOrb
    lv = {"mic": 0.0, "voice": 0.0}
    orb = ConversationOrb(lambda: lv["mic"], lambda: lv["voice"])
    qtbot.addWidget(orb)
    orb.set_state("COMMAND_LISTEN")
    assert orb.isVisible()
    for _ in range(10):
        orb._tick()
    assert orb.level == pytest.approx(0.0)        # silence -> still orb (no fake listening)
    lv["mic"] = 0.3
    for _ in range(10):
        orb._tick()
    assert orb.level > 0.4
    orb.user_said("read question one")
    orb.set_state("SPEAKING")
    lv["voice"] = 0.0
    for _ in range(40):
        orb._tick()
    assert orb.level < 0.05                        # speaking state follows the voice level, not the mic
    orb.assistant_said("Question 1 of 4. State the bending stress formula.")
    img = orb.grab()
    assert not img.isNull()


# ---------------------------------------------------------------- scoped API keys
def _err(code, msg):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(json.dumps({"detail": {"message": msg}}).encode()))


def test_check_accepts_tts_only_key():
    from swarlekhak.audio.speech import eleven_check

    def opener(req, timeout):
        if req.full_url.endswith("/v1/user"):
            raise _err(401, "The API key you used is missing the permission user_read to execute this operation.")
        if "/v1/voices/" in req.full_url:
            raise _err(401, "The API key you used is missing the permission voices_read to execute this operation.")
        return FakeResp(b"\x00\x01" * 2000)
    ok, msg = eleven_check("k", "EMxdghWQV7gqV33j4J3F", opener=opener)
    assert ok and "Test synthesis" in msg


def test_check_reports_missing_tts_permission():
    from swarlekhak.audio.speech import eleven_check

    def opener(req, timeout):
        raise _err(401, "The API key you used is missing the permission text_to_speech to execute this operation."
                   if "text-to-speech" in req.full_url else "The API key you used is missing the permission x_read")
    ok, msg = eleven_check("k", "v", opener=opener)
    assert not ok and "text_to_speech" in msg


def test_check_rejects_invalid_key():
    from swarlekhak.audio.speech import eleven_check

    def opener(req, timeout):
        raise _err(401, "Invalid API key")
    ok, msg = eleven_check("bad", "v", opener=opener)
    assert not ok and "rejected" in msg


# ---------------------------------------------------------------- Gemini TTS (HTTP mocked)
def _wav_b64(n=2400, rate=24000):
    import base64
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.arange(n, dtype=np.int16) % 300).astype("<i2").tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def make_gemini_voice(opener, consent=True, key="g-key"):
    out = FakeOut()
    sapi_calls = []
    notices = []
    v = ConversationVoice(provider="gemini", gemini_key=lambda: key, gemini_consent=lambda: consent, output=out,
                          opener=opener, sapi=lambda t, vv, r: (sapi_calls.append(t) or (np.zeros(10, np.int16), 22050)),
                          on_notice=notices.append)
    return v, out, sapi_calls, notices


def test_gemini_request_shape_and_wav_decode():
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        return FakeResp(json.dumps({"steps": [{"type": "model_output", "content": [
            {"type": "audio", "data": _wav_b64()}]}]}).encode())
    v, out, sapi_calls, _ = make_gemini_voice(opener)
    speak_and_wait(v, "Question two of four.")
    b = seen["body"]
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert seen["headers"]["X-goog-api-key"] == "g-key"
    assert b["model"] == "gemini-3.8-flash-lite-tts" and b["generation_config"]["speech_config"][0]["voice"] == "Sulafat"
    assert b["input"][0]["content"][0]["text"] == "Question two of four."     # verbatim transcript
    assert b["input"][0]["content"][0]["annotations"][0]["type"] == "speech_metadata"
    assert out.played == [(2400, 24000)] and not sapi_calls and v.engine_in_use == "gemini"
    seen.clear()
    speak_and_wait(v, "Question two of four.")   # cached
    assert "url" not in seen
    v.shutdown()


def test_gemini_raw_l16_and_inline_data_shapes():
    import base64
    from swarlekhak.audio.speech import decode_gemini_audio
    raw = base64.b64encode(np.ones(480, dtype="<i2").tobytes()).decode()
    pcm, rate = decode_gemini_audio({"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/l16",
                                                                                          "data": raw}}]}}]})
    assert len(pcm) == 480 and rate == 24000


def test_gemini_error_falls_back_locally():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                     io.BytesIO(b'{"error":{"message":"API key not valid"}}'))
    v, out, sapi_calls, notices = make_gemini_voice(opener)
    speak_and_wait(v, "Hello.")
    assert sapi_calls == ["Hello."] and "Gemini" in notices[0] and "API key not valid" in notices[0]
    v.shutdown()


def test_gemini_needs_its_own_consent():
    def opener(req, timeout):
        raise AssertionError("must not contact Google without consent")
    v, out, sapi_calls, _ = make_gemini_voice(opener, consent=False)
    speak_and_wait(v, "Local.")
    assert sapi_calls == ["Local."]
    v.shutdown()


def test_gemini_check_reports_result():
    from swarlekhak.audio.speech import gemini_check

    def ok_opener(req, timeout):
        return FakeResp(json.dumps({"steps": [{"content": [{"type": "audio", "data": _wav_b64(12000)}]}]}).encode())
    ok, msg = gemini_check("k", opener=ok_opener)
    assert ok and "0.50 s" in msg
