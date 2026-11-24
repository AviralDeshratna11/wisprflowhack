# Phase Zero feasibility matrix (build machine, 2026-11-25)

Machine: Windows 11 Home 10.0.26300, Python 3.11.0, Realtek mic array (WASAPI) + EarPods headset,
SAPI voices David and Zira. Wispr Flow: **not installed**.

| Gate | Test | Result | Evidence | Fallback / consequence |
|---|---|---|---|---|
| A. Flow insertion | `scripts/gate_a_flow.py` with Wispr Flow 1.6.1609 installed (2 runs, human speaker) | **BLOCKED - Flow not yet transcribing on this PC** | Run 1 (`docs/gate_a_results_run1_no_speech.json`): harness started before the speaker was ready, no speech, no insertion - discarded. Run 2 (`docs/gate_a_results_run2.json`): speech present in every trial (local ASR transcribed all 4 recordings); T0 manual shortcut: user saw text appear then vanish, editor ended with whitespace only (2 change events at ~35.7 s); T1-T3 injected Ctrl+Win+Space: no insertion, no new Flow window observed; Esc cancel and quick double-toggle inserted nothing (uninformative). User reports Flow does not type in Notepad either. | Root cause is Flow setup (sign-in/onboarding/microphone), not yet SwarLekhak. Whether Flow accepts injected shortcuts remains **unknown**. Re-run after Flow works in Notepad. Fixture mode for demos meanwhile. |
| B. Shared capture | 10 turns via `scripts/gate_b_capture.py`; plus 4 turns inside Gate A run 2 with Flow running | **PASS** (our side) | EarPods 44.1 kHz: 10/10 turns encrypted + decrypted, 0 dropped blocks, 0 status flags, first-turn warm-up shortfall 2.5 %. Gate A run 2: 4 turns, 0 dropped blocks, speech captured while Flow was running and (in T0) producing text | Flow's own capture quality while we record is not yet confirmed (Flow produced no final transcript). |
| C. Wake + commands | 20 synthetic utterances (12 wake, 8 distractors) | **PARTIAL** | Recall 12/12 (after adding "he" variant; tuned on this set), 0/8 false wakes, command accuracy 12/12 | "Hey Swar" model not trained; transcript approximation; Ctrl+Alt+Space labelled activation. TTS cannot trigger commands: listener disabled in SPEAKING (unit/integration tested), real echo test not run. |
| D. Alignment / review | 52 dev + 28 held-out synthetic turns, planted substitution / negation deletion / unit / operator / term + formatting-only controls | **PASS recall, FAIL held-out FP target** | Held-out: recall 14/14, FP 3/14 (21 %); dev: 29/29, 1/23. Clip <= 4 s for all flags. Mean latency ~1.25 s, RTF ~0.41 on CPU | Timestamps from ASR words (not CTC). Neutral statuses only. |
| Install | `pip install -r requirements.lock` on Windows; model SHA-256 | **PASS** | `pip check` clean; model hash 30F26242...98 | - |

Go/no-go: integrated **live** demo is **no-go** on this machine (Gate A). The demo uses the labelled fixture
mode, in which real recorded (synthetic-speech) audio is verified by the real engine; it is never presented as
live Flow.
