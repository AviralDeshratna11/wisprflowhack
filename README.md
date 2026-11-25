# SwarLekhak - voice-native academic accessibility assistant (Windows)

> "A stranger's hand writes the blind student's exam. Give them their own."
> Wispr Flow is the pen; SwarLekhak makes that pen accessible and reviewable.

SwarLekhak is a native Windows desktop assistant (PySide6/Qt, no web stack) for mock examinations and
institution-approved pilots. The student speaks; **Wispr Flow** writes the answer into SwarLekhak's own
accessible editor; SwarLekhak records each dictation turn as encrypted evidence, checks the written text
against an independent **local** recognition of that audio, and lets the student review, replay and correct
by voice. Structured maths, voice-built diagrams and graph sonification are included. An examiner opens the
submission in a native review window with integrity checks, waveform and evidence clips.

It is an accessibility tool, not an answer generator: no web search, no academic help, no shell access.

**Status: working prototype with one critical item unverified - live Wispr Flow integration** (Flow is not
installed on the build machine). See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) and
[TEST_RESULTS.md](TEST_RESULTS.md) for what was actually measured.

## Quick start (PowerShell, repository root)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1     # venv, locked deps, model (SHA-256 checked), fixtures, demo exam
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m swarlekhak                       # live mode (needs Wispr Flow running)
.\.venv\Scripts\python.exe -m swarlekhak --fixture             # LABELLED demo fixture mode (no Flow needed)
.\.venv\Scripts\python.exe -m swarlekhak --examiner            # examiner review window
.\.venv\Scripts\python.exe -m swarlekhak --selftest            # headless self-test (JSON)
```

| Task | Command |
|---|---|
| Tests | `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -m pytest -q` |
| Benchmark (Gates C+D) | `.\.venv\Scripts\python.exe scripts\benchmark.py` -> `docs\benchmark_results.json` |
| Mic hardware check (Gate B) | `.\.venv\Scripts\python.exe scripts\gate_b_capture.py --turns 10` |
| Model download/verify | `powershell -File scripts\download_models.ps1` |
| DB migration | `.\.venv\Scripts\python.exe scripts\migrate.py` |
| Demo exam package | `.\.venv\Scripts\python.exe scripts\make_demo_package.py` |
| Regenerate fixtures | `.\.venv\Scripts\python.exe scripts\make_fixtures.py` |
| Build exe (+ installer if Inno Setup 6 present) | `powershell -File scripts\build.ps1` |

Packaged app: `dist\SwarLekhak\SwarLekhak.exe` (one-folder, unsigned). Installer script: `installer\SwarLekhak.iss`.

## Using it (all actions have voice, keyboard and screen-reader paths)

1. Install Wispr Flow separately; confirm its hands-free shortcut (default **Ctrl+Win+Space**) and enter it in
   SwarLekhak Setup. Consent to passive wake detection if you want "Hey Swar"; otherwise use **Ctrl+Alt+Space**.
2. "Hey Swar, start my examination" -> consent prompt -> "yes".
3. "Read question one" / "next question" / "describe the figure" / "play the graph" / "next point".
4. "Start writing" -> speak your answer (Flow writes it) -> "Hey Swar, finish writing" (or F6 / Ctrl+Alt+Space).
5. "Read review" -> "play the original" -> "keep original" or "replace that with ..." -> "undo".
6. "Equation sigma equals M times y divided by I" -> read-back -> "yes".
   "Create node input", "connect input to controller labelled signal", "read the diagram".
7. "Submit examination" -> summary of unanswered questions / unreviewed passages -> "yes".
8. Examiner: `--examiner`, open the `.swarsub`, load the examiner key, check integrity, play clips.

**Assistant voice and orb.** Default cloud voice: **Gemini TTS** (`gemini-3.8-flash-lite-tts`, voice "Sulafat"; 30 voices to
choose from). Settings -> "Assistant voice and conversation": paste your Gemini API key (AI Studio), tick the Google
consent box, "Check selected voice", "Test speech". Alternatively choose ElevenLabs: paste your ElevenLabs API key
(stored encrypted with DPAPI), tick the consent box, press "Check key and voice", then "Test speech". The default
voice is ElevenLabs "Abhi" (`EMxdghWQV7gqV33j4J3F`) with `eleven_flash_v2_5`; if the check says the voice is not
available, add it to *My Voices* from the ElevenLabs Voice Library. Without key/consent/internet the local Windows
voice is used automatically. Saying "Hey Swar" (or Ctrl+Alt+Space) brings up the animated conversation orb; it
keeps listening a few seconds after each reply - say the next command directly, or "thanks" to close it.

**Structured answers** (inserted after the paragraph you are working on, read back in place, exported to Word):
"chemical equation two hydrogen two plus oxygen two gives two hydrogen two oxygen" (never balanced);
"create a table with three columns", "set heading one to time", "row two column one is 4.5", "read row two";
"add line for each reading", "indent", "read the code" (pseudocode, never executed).

**Examiners:** `python scripts/make_exam_from_pdf.py paper.pdf --exam-id ... --title ...` builds questions from a
PDF (text only; figures must get an approved description). Set `policy.audio_disclosure` to `full`,
`review_clips` (only the clips behind review markers leave the student's machine) or `none`.

**Installer:** `scriptsuild.ps1` -> `dist\installer\SwarLekhak-Setup.exe` (per-user, no admin, unsigned).

Keyboard: F2 read question, F5 start writing, F6 finish, Ctrl+PgUp/PgDn navigate, F7 time left,
F8 mute/unmute, F1 commands; the Command box accepts any spoken command as text.

## Repository map
`src/swarlekhak/{assistant,audio,integrations,examination,engines,storage,security,ui,export}` -
see [ARCHITECTURE.md](ARCHITECTURE.md). Decisions: [DECISIONS.md](DECISIONS.md). Gates:
[docs/FEASIBILITY.md](docs/FEASIBILITY.md). Plan/tasks: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md),
[TASKS.md](TASKS.md). Privacy: [docs/PRIVACY.md](docs/PRIVACY.md). Accessibility:
[docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md). Demo: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).
