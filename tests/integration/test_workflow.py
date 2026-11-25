"""Headless end-to-end student workflow over the real store, vault, proof engine and
submission builder. Flow/mic/TTS are TEST doubles (labelled); audio is the real
synthetic-speech fixture WAV."""
import json
import zipfile

import pytest

from conftest import ScriptedTranscriber, demo_fixture, has_model
from swarlekhak.assistant.states import S
from swarlekhak.export.submission import decrypt_turn_audio, open_submission


def start_exam(e):
    e.orch.handle_command_text("start my examination")
    assert e.orch.state == S.CONFIRM and "consent" in e.tts.last
    e.orch.handle_command_text("yes")
    assert e.orch.exam_active and "Examination started" in e.tts.last


def dictate(e, fixture_id, transcriber_text=None):
    fx = demo_fixture(fixture_id)
    e.rec.wav_path = fx["audio_path"]
    e.flow.next_text = fx["inserted_text"]
    if transcriber_text is not None:
        e.transcriber["t"] = ScriptedTranscriber(transcriber_text)
    e.orch.handle_command_text("start writing")
    assert e.orch.state == S.DICTATING
    e.clock.t += 3
    e.orch.on_heard("hey swar finish writing")
    assert e.orch.state == S.DICTATION_STOPPING
    e.sched.advance(5)
    return fx


def test_consent_required_and_no_session_on_no(env):
    env.orch.handle_command_text("start my examination")
    env.orch.handle_command_text("no")
    assert not env.orch.exam_active
    assert env.store.all("SELECT * FROM sessions") == []


def test_full_workflow_with_scripted_asr(env):
    start_exam(env)
    env.orch.handle_command_text("next question")
    assert env.orch.qidx == 1 and "Question 2 of 4" in env.tts.last
    fx = dictate(env, "demo-q2", transcriber_text=demo_fixture("demo-q2")["spoken_text"].lower().replace(",", "").replace(".", ""))
    assert env.editor.text == fx["inserted_text"]
    assert any("may need review" in s for s in env.tts.spoken)
    reviews = env.store.reviews(sid=env.orch.session_id)
    assert [r["category"] for r in reviews] == ["numeric"] and reviews[0]["written"] == "15"
    # review -> play clip -> correct
    env.orch.handle_command_text("read review")
    assert "Written: 15" in env.tts.last
    env.orch.handle_command_text("play the original")
    assert env.player.played and env.player.played[-1][1] == 16000
    env.orch.handle_command_text("replace that with 10")
    assert env.editor.text.endswith("gives 10.")
    assert "corrected" in env.tts.last
    r = env.store.reviews(sid=env.orch.session_id)[0]
    assert r["status"] == "student_corrected" and r["resolution_revision"]
    # undo restores the previous text, as a new revision (history is append-only)
    env.orch.handle_command_text("undo")
    assert env.editor.text.endswith("gives 15.")
    env.orch.handle_command_text("replace 15 with 10")
    # equation (confirmed) and diagram
    env.orch.handle_command_text("equation a equals F over m")
    env.orch.handle_command_text("yes")
    assert env.orch.blocks[0]["linear"] == "a = F/m"
    env.orch.handle_command_text("go to question 4")
    for c in ("create node input", "create node controller", "connect input to controller"):
        env.orch.handle_command_text(c)
    assert "input -> controller" in env.orch.blocks[0]["adjacency"]
    # graph question
    env.orch.handle_command_text("go to question 3")
    env.orch.handle_command_text("read the graph")
    assert "0, 0; 1, 2; 2, 4; 3, 4; 4, 3; 5, 1" in env.tts.last
    env.orch.handle_command_text("play the graph")
    env.orch.handle_command_text("next point")
    assert "Point 1 of 6" in env.tts.last
    # submit
    env.orch.handle_command_text("submit examination")
    assert "unanswered" in env.tts.last and env.orch.state == S.CONFIRM
    env.orch.handle_command_text("yes")
    assert env.orch.state == S.SUBMITTED
    env.orch.handle_command_text("start writing")
    assert "locked" in env.tts.last
    # examiner side
    sub = env.store.submission(env.orch.session_id)["path"]
    view = open_submission(sub)
    assert view.integrity_ok, view.integrity
    assert "fixture" not in view.manifest["turn_sources"]
    turn_id = view.verification["turns"][0]["id"]
    wav = decrypt_turn_audio(view, turn_id, env.key.read_bytes())
    assert wav[:4] == b"RIFF"
    answers = {a["ordinal"]: a for a in view.answers["answers"]}
    assert answers[2]["text"].endswith("gives 10.")
    assert "answers.docx" in view.files


def test_submission_is_idempotent_and_tamper_evident(env, tmp_path):
    start_exam(env)
    env.editor.type("A typed answer.")
    env.sched.advance(2)
    path = env.orch.submitter(env.orch.session_id, env.orch.exam)
    assert env.orch.submitter(env.orch.session_id, env.orch.exam) == path
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    a = json.loads(files["answers.json"])
    a["answers"][0]["text"] = "changed by someone"
    files["answers.json"] = json.dumps(a).encode()
    bad = tmp_path / "bad.swarsub"
    with zipfile.ZipFile(bad, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    view = open_submission(bad)
    assert not view.integrity_ok
    assert any(name == "file answers.json" and not ok for name, ok, _ in view.integrity)


def test_typed_edits_are_saved_and_labelled(env):
    start_exam(env)
    env.editor.type("Typed by keyboard.")
    env.sched.advance(2)
    revs = env.store.revisions(env.orch.session_id)
    assert revs[-1]["event_type"] == "keyboard_edit" and revs[-1]["payload"]["text"] == "Typed by keyboard."


def test_no_insertion_preserves_audio_and_never_guesses(env):
    start_exam(env)
    fx = demo_fixture("demo-q1")
    env.rec.wav_path = fx["audio_path"]
    env.flow.next_text = ""   # Flow inserts nothing
    env.orch.handle_command_text("start writing")
    env.clock.t += 2
    env.orch.handle_command_text("finish writing")
    env.sched.advance(30)
    assert env.editor.text == ""
    t = env.store.turns(env.orch.session_id)[0]
    assert t["status"] == "no_insertion" and env.store.audio_for_turn(t["id"])
    assert any("No text arrived" in s for s in env.tts.spoken)


def test_flow_not_running_blocks_dictation_without_substitute(env):
    start_exam(env)
    env.flow.fail_start = "Wispr Flow does not appear to be running."
    env.orch.handle_command_text("start writing")
    assert env.orch.turn is None and "does not appear to be running" in env.tts.last
    assert env.editor.text == ""


def test_focus_lost_refuses_to_start(env):
    start_exam(env)
    env.editor.foreground = False
    env.orch.handle_command_text("start writing")
    assert env.orch.turn is None and "front" in env.tts.last
    assert "start" not in env.flow.calls


def test_commands_inside_dictation_are_content(env):
    start_exam(env)
    env.rec.wav_path = demo_fixture("demo-q1")["audio_path"]
    env.orch.handle_command_text("start writing")
    env.orch.on_heard("submit examination")      # no wake phrase -> answer content, not a command
    env.orch.on_heard("delete node a")
    assert env.orch.state == S.DICTATING and env.orch.pending is None


def test_trailing_spoken_command_removed_only_after_confirmation(env):
    start_exam(env)
    env.transcriber["t"] = ScriptedTranscriber("the beam bends hey swar finish writing")
    env.rec.wav_path = demo_fixture("demo-q1")["audio_path"]
    env.flow.next_text = "The beam bends. Hey Swar, finish writing."
    env.orch.handle_command_text("start writing")
    env.clock.t += 2
    env.orch.on_heard("hey swar finish writing")
    env.sched.advance(5)
    assert env.orch.state == S.CONFIRM and "spoken command" in env.tts.spoken[-1] or any(
        "spoken command" in s for s in env.tts.spoken)
    assert env.editor.text.endswith("finish writing.")
    env.orch.handle_command_text("yes")
    assert env.editor.text == "The beam bends."


def test_ambiguous_concurrent_edit_asks(env):
    start_exam(env)
    env.editor.type("Original sentence.")
    env.sched.advance(2)
    env.rec.wav_path = demo_fixture("demo-q1")["audio_path"]
    env.orch.handle_command_text("start writing")
    env.editor.text = "Changed sentence."   # someone edited existing text during the turn
    env.flow.next_text = " New text."
    env.clock.t += 2
    env.orch.handle_command_text("finish writing")
    env.sched.advance(5)
    assert env.orch.pending is not None and "more than one place" in env.orch.pending.description
    env.orch.handle_command_text("no")
    assert env.editor.text == "Original sentence."


def test_wake_without_consent_is_ignored(env):
    env.orch.prefs.wake_enabled = False
    env.orch.on_heard("hey swar start my examination")
    assert env.orch.state == S.PASSIVE_WAKE and not env.tts.spoken


def test_timer_warnings_and_time_up(env):
    start_exam(env)
    env.clock.now = env.orch.deadline.replace() - __import__("datetime").timedelta(seconds=30)
    env.orch.tick()
    assert "One minute" in env.tts.last
    env.clock.now = env.orch.deadline
    env.orch.tick()
    assert "Time is up" in env.tts.last


def test_resume_restores_question(env):
    start_exam(env)
    env.orch.handle_command_text("go to question 3")
    sid = env.orch.session_id
    env.orch.session_id = None
    env.orch.open_package(str(env.pkg), announce=True)
    assert env.orch.session_id == sid and env.orch.qidx == 2 and "Resuming" in env.tts.last


@has_model
def test_real_vosk_verification_on_fixture_audio(env):
    """Real local ASR on the synthetic-speech WAV: the planted '15' must be flagged, nothing else critical."""
    start_exam(env)
    env.orch.handle_command_text("go to question 2")
    dictate(env, "demo-q2")
    reviews = env.store.reviews(sid=env.orch.session_id)
    assert any(r["category"] == "numeric" and r["written"] == "15" for r in reviews)
    al = env.store.all("SELECT spans_json FROM alignments")[0]
    assert "acceleration" in json.loads(al["spans_json"])["hypothesis"]


@has_model
def test_real_vosk_negation_fixture(env):
    start_exam(env)
    env.orch.handle_command_text("go to question 3")
    dictate(env, "demo-q3")
    cats = [r["category"] for r in env.store.reviews(sid=env.orch.session_id)]
    assert "negation" in cats


def test_he_swore_in_dictated_content_does_not_end_turn(env):
    start_exam(env)
    env.rec.wav_path = demo_fixture("demo-q1")["audio_path"]
    env.orch.handle_command_text("start writing")
    env.orch.on_heard("he swore that the experiment had been repeated")
    assert env.orch.state == S.DICTATING
    env.clock.t += 2
    env.orch.on_heard("he swore finish writing")
    assert env.orch.state == S.DICTATION_STOPPING


@pytest.mark.parametrize("policy", ["review_clips", "none"])
def test_selective_disclosure(env, policy):
    from swarlekhak.examination.demo import make_demo_package
    from swarlekhak.export.submission import audio_index, decrypt_audio_entry
    pkg, key = make_demo_package(env.tmp / f"exams_{policy}", exam_id=f"physics-{policy}", audio_disclosure=policy)
    env.orch.open_package(str(pkg))
    env.orch.handle_command_text("yes")
    env.orch.handle_command_text("go to question 2")
    dictate(env, "demo-q2", transcriber_text=demo_fixture("demo-q2")["spoken_text"].lower().replace(",", "").replace(".", ""))
    path = env.orch.submitter(env.orch.session_id, env.orch.exam)
    view = open_submission(path)
    assert view.integrity_ok and view.manifest["audio_disclosure"] == policy
    entries = audio_index(view)
    if policy == "none":
        assert entries == [] and not any(n.startswith("encrypted_audio/") for n in view.files)
        return
    assert entries and all(e["kind"] == "clip" for e in entries)          # no full turn audio leaves the machine
    clip = decrypt_audio_entry(view, entries[0]["id"], key.read_bytes())
    from swarlekhak.audio.recorder import read_wav
    pcm, rate = read_wav(clip)
    assert 0 < len(pcm) / rate <= 6.0


def test_structured_blocks_inline_in_answer_and_export(env):
    start_exam(env)
    env.editor.type("Results are shown below.\n\nThe reaction is exothermic.")
    env.sched.advance(2)
    env.orch.handle_command_text("find shown")                # work in paragraph 1
    env.orch.handle_command_text("create a table with two columns")
    env.orch.handle_command_text("set heading one to time")
    env.orch.handle_command_text("row one column one is 0")
    env.orch.handle_command_text("chemical equation methane")  # rejected: unknown word, nothing inserted
    assert "did not understand" in env.tts.last
    env.orch.handle_command_text("chemical equation c h four plus two oxygen two gives c o two plus two hydrogen two oxygen")
    env.orch.handle_command_text("yes")
    env.orch.handle_command_text("add line repeat until stable")
    kinds = [b["type"] for b in env.orch.blocks]
    assert kinds == ["table", "chemistry", "pseudocode"]
    assert env.orch.blocks[0]["anchor_paragraph"] == 1
    env.orch.handle_command_text("read my answer")
    said = env.tts.last
    assert said.index("Results are shown below") < said.index("Table with 2 columns") < said.index("exothermic")
    assert env.orch.blocks[1]["linear"] == "CH₄ + 2O₂ → CO₂ + 2H₂O"
    path = env.orch.submitter(env.orch.session_id, env.orch.exam)
    import docx, io
    view = open_submission(path)
    d = docx.Document(io.BytesIO(view.files["answers.docx"]))
    assert d.tables and d.tables[0].cell(0, 0).text == "time"
