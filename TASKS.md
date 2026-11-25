# Tasks and truthful feature status (v0.2.0, 2026-11-25)

Legend: **working** (implemented + tested here), **partial**, **blocked** (needs something unavailable here),
**planned**.

## P0
| Feature | Status | Notes / evidence |
|---|---|---|
| Wake activation | partial | Transcript-match approximation of "Hey Swar"; 0 false wakes in 9.9 min unseen synthetic speech |
| Labelled keyboard activation (Ctrl+Alt+Space, tray, F-keys, command box) | working | RegisterHotKey; e2e keyboard-only test |
| Wispr Flow dictation (live) | blocked | Flow installed but not transcribing on this PC (also not in Notepad); Gate A to re-run |
| Demo fixture dictation (labelled) | working | Qt e2e test |
| Evidence audio capture | working | Gate B 10/10 turns, encrypted; 0 dropped blocks with Flow running |
| Answer persistence + autosave + resume | working | hash-chained revisions; resume test |
| Verification (Acoustic Proof Engine, N-best rescoring, constrained alignment) | working | unseen held-out v3: 16/16 recall, 12.5 % false flags |
| Voice corrections (replace/insert/delete/undo, review keep/skip/correct) | working | integration tests |
| Submission (summary, confirmation, lock, idempotent) | working | tests |
| Native examiner review (integrity, waveform, clips, history, decrypt) | working | e2e test opens a real submission |
| Installer | working (unsigned) | SwarLekhak-Setup.exe: silent install, installed self-test 5/5, silent uninstall verified |

## Conversation
| Feature | Status |
|---|---|
| Gemini TTS voice (default cloud voice) | partial - mocked tests; needs the user's key |
| ElevenLabs "Abhi" voice | partial - mocked tests; user's key lacks text_to_speech |
| Animated conversation orb on wake (real-level driven, live captions) | working |
| Follow-up listening + "thanks" to end | working |

## P1
| Feature | Status |
|---|---|
| Equation compiler (MVP grammar, read-back + confirm) | working |
| Chemistry mode (names, symbols, typed formulas, charges, states, arrows; never balanced) | working |
| Tables (create, headings, set cell, add/delete row, read row/table; real Word tables) | working |
| Pseudocode (add line, indent/outdent, delete line, read; never executed) | working |
| Inline placement of structured blocks (after the paragraph being worked on) | working |
| Voice-to-diagram | working |
| Graph sonification + point stepping + exact data read-back | working (first series only) |
| Approved visual question descriptions | working |
| Selective disclosure (exam policy audio_disclosure: full / review_clips / none) | working |
| PDF question import (examiner tool, text only, figures need approved descriptions) | working |

## P2
Hindi / code-switching, external editors (UI Automation), calibration, quiet speech, macOS: planned.

## Engineering
| Item | Status |
|---|---|
| pytest suite (unit, integration, Qt e2e, accessibility audit, PDF import) | working - see TEST_RESULTS.md |
| Benchmarks: dev + 3 held-out sets + 3 passive false-wake sets | working |
| CI (GitHub Actions: lint, tests, PyInstaller build + packaged self-test) | added - see first run status |
| NVDA walkthrough / user testing | blocked on people (no accessibility claim made) |
| Code signing | not available (needs a certificate) |
