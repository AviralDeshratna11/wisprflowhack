# Tasks and truthful feature status

Legend: **working** (implemented + tested here), **partial**, **blocked** (needs something unavailable here),
**planned**.

## P0
| Feature | Status | Notes / evidence |
|---|---|---|
| Wake activation | partial | Transcript-match approximation of "Hey Swar"; synthetic-voice test only |
| Labelled keyboard activation (Ctrl+Alt+Space, tray, F-keys, command box) | working | RegisterHotKey; e2e test drives the UI by keyboard |
| Wispr Flow dictation (live) | blocked | Flow not installed; bridge implemented per docs |
| Demo fixture dictation (labelled) | working | Qt e2e test |
| Evidence audio capture | working | Gate B 10/10 turns, encrypted |
| Answer persistence + autosave + resume | working | hash-chained revisions; resume test |
| Verification (Acoustic Proof Engine) | working (with FP caveat) | real Vosk on fixture audio; held-out FP 21 % |
| Voice corrections (replace/insert/delete/undo, review keep/skip/correct) | working | integration tests |
| Submission (+ unanswered / unresolved summary, confirmation, lock, idempotent) | working | tests |
| Native examiner review (integrity, waveform, clips, history, decrypt) | working | rendered + tests |
| Installer | partial | exe built with PyInstaller; Inno Setup script written, not compiled (tool missing) |

## Conversation (added 2026-11-25)
| Feature | Status |
|---|---|
| ElevenLabs "Abhi" voice (streaming PCM, consent, DPAPI key, cache, SAPI fallback) | partial - mocked tests pass; live call needs the user's key |
| Gemini TTS voice (Interactions API, 30 voices, consent, DPAPI key, cache, fallback) | partial - mocked tests pass; live call needs the user's Gemini key |
| Animated conversation orb on wake (real-level driven, live captions of what you said + reply) | working (rendered, unit-tested) |
| Follow-up listening + "thanks" to end | working (tests) |

## P1
| Feature | Status |
|---|---|
| Equation compiler (MVP grammar, read-back + confirm, linear/LaTeX/spoken) | working |
| Voice-to-diagram (create/rename/delete/connect/disconnect/list/read/undo, DOT export) | working |
| Graph sonification + point stepping + exact data read-back | working (first series only) |
| Approved visual question descriptions | working |
| Selective disclosure | planned |
| PDF question import | planned |
| Chemistry / tables / pseudocode modes | planned |

## P2
Hindi / code-switching, external editors (UI Automation), calibration, quiet speech, macOS: planned.

## Engineering
| Item | Status |
|---|---|
| pytest suite (unit, integration, Qt e2e) | working - see TEST_RESULTS.md |
| Benchmark script (dev + held-out) | working |
| CI (lint, type check, Windows build) | planned |
| NVDA walkthrough | planned (not done - no accessibility claims made) |
| Code signing | not available |
