# Architecture

Local-first native Windows application (Python 3.11, PySide6). Not fully offline: Wispr Flow may use the
network. No web server, REST API, browser, Redis or database server.

```
microphone ──► AudioSessionManager (sounddevice/WASAPI shared mode; callback only enqueues)
                 │ worker thread: sample counter, level, dropped-block count
                 ├─► EvidenceRecorder (native rate, only during a dictation turn) ─► EvidenceVault (AES-GCM, DPAPI key)
                 └─► StreamingResampler 16 kHz ─► VoskListener (in-memory only; disabled while muted/speaking)
                                                     │ final utterance (marshalled to GUI thread)
                                                     ▼
 hotkey / tray / buttons / command box ──► Orchestrator (Qt-free, single thread, serialized StateMachine)
                                             │ intents.parse (closed allow-list)  ─► handler ─► say() ─► SapiTTS
                                             │
      start writing ─► FlowBridge.start ─► (Wispr Flow writes into the focused AnswerEditor)
      finish writing ─► FlowBridge.request_stop ─► poll editor until quiet ─► before/after diff
                         ─► Store.commit_revision (hash chain) ─► VerificationService (worker thread)
                              ─► ProofEngine: VoskTranscriber (word times) + normalize + Levenshtein align
                                 + deterministic categories ─► review markers ─► review queue / Audio Cursor
      structured commands ─► math_compiler / Diagram / sonify ─► blocks ─► revision
      submit ─► build_submission (answers.json/.docx, revision chain, verification, diagrams,
                audio encrypted for examiner X25519 key) ─► .swarsub ─► ExaminerWindow
```

## Modules
| Package | Responsibility |
|---|---|
| `assistant/states.py` | 11 states, explicit transition table; dictation pipeline is forward-only; SUBMITTED is terminal for work |
| `assistant/intents.py` | Regex allow-list grammar -> `Command(intent, args, allowed_states, confirmation_required)` |
| `assistant/orchestrator.py` | All workflow logic; ports injected (TTS, Flow, recorder, player, vault, verifier, editor, UI, scheduler, clock, submitter) |
| `assistant/verification.py` | Bounded (1-worker) background verification |
| `audio/` | capture session, resampler, evidence recorder + vault, Vosk listener/transcriber, SAPI TTS, player, turn recorders |
| `integrations/flow_bridge.py` | `WisprFlowBridge` (documented shortcut via SendInput, foreground-validated), `FixtureFlowBridge` (labelled demo) |
| `integrations/keys.py` | `SendInput` chord sender, shortcut parser |
| `engines/` | `normalize`, `proof`, `cursor`, `math_compiler`, `diagram`, `sonify`, `evidence_chain`, `numbers` |
| `examination/` | `.swar` package validation (safe paths, size/ratio limits, hashes, schema) and demo generator |
| `storage/db.py` | SQLite WAL, FK, migrations, transactional hash-chained revisions, jobs, audit, consents, submissions |
| `security/crypto.py` | AES-256-GCM, DPAPI local key, X25519+HKDF key wrap for the examiner |
| `export/submission.py` | `.swarsub` builder (atomic, idempotent) and examiner loader with integrity checks |
| `ui/` | workspace, overlay (non-activating, real level), tray, settings + health, examiner, global hotkey, Qt ports |

## Contracts (versioned, `contracts.py`, CONTRACT_VERSION 1.0)
`Command`, `DictationTurn`, `ReviewEvent`, `TaskResult`, `ReviewStatus`
(pending, strong_correspondence, check_passage, verification_unavailable, student_confirmed, student_corrected),
`TurnSource` (flow_live, fixture, typed).

## Threads
GUI thread: Qt, orchestrator, SQLite connection. PortAudio callback: enqueue only. Audio worker: resample,
recorder writes, Vosk listener. Verification worker: one job at a time. TTS: own COM thread. Cross-thread
results return through `Invoker.post` (queued Qt signal).

## Data locations
`%LOCALAPPDATA%\SwarLekhak\` (override `SWARLEKHAK_DATA_DIR`): `swarlekhak.db`, `local.key` (DPAPI blob),
`audio\*.wav.enc`, `quarantine\`, `exams\*.swar`, `submissions\*.swarsub`, `logs\` (rotating, no speech).
Read-only resources (models, fixtures) live beside the code or in the PyInstaller bundle.

## Round-2 reuse
Exam policy and domain data are isolated in `examination/` and the intent allow-list; the capture, Flow
bridge, verification, cursor, package and evidence services are domain-agnostic.
