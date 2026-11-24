# Test results (actually executed, 2026-11-25)

Machine: Windows 11 Home 10.0.26300, Python 3.11.0, CPU only. Devices: Realtek mic array (WASAPI 48 kHz),
EarPods headset (44.1 kHz, system default during tests). **Wispr Flow not installed.**

## 1. Automated suite - `pytest` -> 116 passed, 0 failed (102 in `docs/test_report.txt` + 14 conversation tests)
- `tests/unit/test_engines.py` - number words, normalization equivalences and meaning-critical differences,
  proof engine categories/clips, math compiler (accept/reject/LaTeX/read-back), diagram ops, cursor, sonification.
- `tests/unit/test_core.py` - intent grammar, refusal of web/shell/academic-help phrasing, confirmation flags,
  dictation-state firewall, wake regex, state machine guards, resampler, encrypted vault, quarantine, X25519
  wrap (wrong key rejected), AES-GCM tamper, DPAPI, package path traversal / hash mismatch / zip bomb /
  undeclared file, hash chain tamper detection, crash-restore, post-submission lock, shortcut parsing.
- `tests/integration/test_workflow.py` - headless student workflow over the real store, vault, verifier and
  submission builder (Flow/TTS/mic are **labelled test doubles**): consent, full workflow, no-insertion,
  Flow-not-running (no ASR substitution), focus refusal, commands-inside-dictation are content, trailing
  command phrase removal only on confirmation, ambiguous concurrent edit, wake without consent ignored, timer,
  resume, tamper-evident + idempotent submission, examiner decryption. Two tests run **real Vosk** on fixture audio.
- `tests/e2e/test_qt_workspace.py` - real Qt widgets/timers, labelled fixture bridge, real Vosk, keyboard-only
  path from exam start to submission and examiner integrity.

## 2. Benchmark - `scripts/benchmark.py` (`docs/benchmark_results.json`)
All speech is **synthetic Windows SAPI** (voices David and Zira). Planted errors were made independently of any
dictation engine. Not human, accented or noisy speech.

| Set | Planted turns | Recall (95% CI) | Clean/formatting-only controls | False-positive rate (95% CI) | Mean latency | RTF |
|---|---|---|---|---|---|---|
| Development (rules tuned on it) | 29 | 1.00 (0.88-1.00) | 23 | 0.043 (0.008-0.21) | 1.28 s | 0.40 |
| **Held-out (never tuned on)** | 14 | **1.00 (0.79-1.00)** | 14 | **0.214 (0.076-0.476)** | 1.44 s | 0.48 |

Category match on planted turns: 100 %. All flagged clips <= 4 s. Proposed gate ">= 90 % recall" met on these
small synthetic sets; proposed gate "<= 15 % FP" **not met** on held-out data (3 of 14; all were local-ASR
mishearings). Before tuning, development FP was 61 % (partly a UTF-8 bug in the benchmark harness itself).

Gate C (wake + command, 20 synthetic utterances, tuned on the same set): wake recall 12/12, false wakes 0/8,
command accuracy 12/12. False triggers per hour on real audio: **not measured**.

## 3. Hardware - `scripts/gate_b_capture.py --turns 10 --seconds 2`
EarPods @ 44.1 kHz: 10/10 turns recorded, encrypted, decrypted and read back; 0 dropped blocks; 0 PortAudio
status flags; worst sample shortfall 2.5 % (first turn, stream warm-up). Realtek @ 48 kHz 3 s probe: 0 flags.
Not tested: concurrent capture with Wispr Flow.

## 4. Self-test and packaging
- `python -m swarlekhak --selftest`: 5/5 checks pass (SQLite, demo package, AES-GCM, DPAPI, Vosk verification on
  fixture flags the planted "15").
- PyInstaller one-folder build: `dist\SwarLekhak\SwarLekhak.exe` (294 MB incl. 40 MB model). Packaged
  `--selftest`: 5/5 pass (first build failed because `libvosk.dll` was not collected - fixed in the spec).
  Packaged GUI launched in fixture mode for 15 s with no errors logged.
- GUI (source) launched on the desktop for 15 s, no errors; workspace, overlay, diagram and examiner windows
  rendered and inspected.
- Installer: **not built** (Inno Setup 6 not installed). Clean-machine install/uninstall: **not tested**.
  Binary is **unsigned**.

## 5. Gate A with real Wispr Flow 1.6.1609 (`scripts/gate_a_flow.py`, human speaker)
- Run 1: discarded - the harness started before the speaker was ready; recordings contained only room noise.
- Run 2 (`docs/gate_a_results_run2.json`): the speaker read all sentences (local ASR transcripts of our
  recordings: "the acceleration is ten meters per second squared", etc.). Manual Flow shortcut: text briefly
  appeared in our Qt editor, then vanished (final: whitespace). Injected shortcut (3 trials): no insertion.
  Esc cancel / quick toggle: no insertion. Our concurrent capture: 0 dropped blocks.
- Conclusion: **not a pass.** Flow does not yet type in Notepad on this PC either, so Flow's setup is the
  blocker; injection acceptance is still unknown. The harness now waits for a Start click, has a mic picker and
  level meter, detects speech against the noise floor, records Flow's visible windows and the full text-change
  timeline.

## 6. Conversation voice and orb (added later on 2026-11-25)
- `tests/unit/test_voice_conversation.py` (14 passed): ElevenLabs request URL/header/body and 16-bit PCM
  streaming (HTTP **mocked**), cache, HTTP-401 fallback to the local voice with a single notice, no cloud call
  without consent/key/provider, DPAPI secret round-trip, follow-up listening/timeout, "thanks" ends the
  conversation, keyboard commands never open the mic, partial words shown only while awake, orb follows real
  levels (still in silence).
- Live, this machine: local Windows voice rendered to memory and played through the new speech output in 2.8 s;
  measured level active in 57 of 139 frames (follows speech, rests in pauses).
- Orb rendered in listening/silent, listening/speech and speaking states and inspected.
- **Live ElevenLabs check (user's key, voice EMxdghWQV7gqV33j4J3F):** key accepted but *scoped* - `GET /v1/user` -> 401 missing `user_read`; synthesis -> 401 missing `text_to_speech`. No audio could be produced; the "Abhi" voice is still unheard. The check now tolerates scoped keys (3 new tests, 119 total).
- App (source) launched 12 s with the new voice engine and orb: no errors. Rebuilt exe: `--selftest` passes.

- Gemini TTS (added after the user's suggestion): 5 tests with **mocked** HTTP - request URL/header/body
  (verbatim text, style annotation, voice), WAV and raw-L16 decoding, error fallback with notice, own consent
  gate, check. **No live Gemini request yet** (no key). Settings dialog rendered and inspected (fixed: invisible
  unticked checkboxes, non-wrapping consent text, horizontal overflow).

## 7. Not executed
Successful live Wispr Flow turns, TTS-echo barge-in test on speakers,
NVDA walkthrough, tests with visually impaired participants, human speech corpus, CI.
