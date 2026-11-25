# Decisions

| # | Decision | Why | Alternative kept |
|---|---|---|---|
| D1 | Python 3.11 + PySide6 6.12, pinned in `requirements.lock` | Native accessible widgets, tray, overlay; 3.11 present on the machine | - |
| D2 | Flow driven only by the user-configured hands-free shortcut via `SendInput`, after verifying our editor is foreground | docs.wisprflow.ai documents shortcuts but no desktop API; nothing undocumented | Student presses the Flow shortcut manually; insertion is still observed |
| D3 | Insertion detected by before/after diff of our own editor + quiet window | Flow pastes whole transcripts after stop; no provenance API | Ambiguous diffs ask "keep or restore" |
| D4 | Vosk small en-us (Apache-2.0, 40 MB) for wake approximation, commands and the verification hypothesis | Fits CPU/latency budget, word timestamps, offline, redistributable | faster-whisper / MMS CTC aligner planned |
| D5 | Wake phrase via transcript match of "hey/he + swar-variant" | No trained "Hey Swar" model; "swar" out of vocabulary | Labelled Ctrl+Alt+Space activation always available |
| D6 | Word timestamps from ASR, not CTC forced alignment | torchaudio MMS aligner (~1 GB) not justified inside the time box | Clips padded, "approximate" when localisation is weak |
| D7 | Deterministic categories + antonym list + sounds-alike suppression | Spec forbids correctness checks; cut ASR-mishearing false positives (dev FP 61% -> 4%) | `review_sensitivity=high` keeps more flags |
| D8 | Held-out fixture split written before tuning and never tuned on | Honest generalisation estimate | - |
| D9 | Hand-written recursive-descent math parser instead of Lark | Small grammar, clearer spoken error messages, one less dependency | - |
| D10 | Equations/diagrams as structured blocks with text equivalents (not inline rich text) | Accessible, exportable, evidence-linked; avoids fragile rich-text editing | Inline placement planned |
| D11 | AES-256-GCM evidence at rest; key protected by DPAPI; submission audio wrapped for examiner X25519 key | DPAPI keys are not portable; examiner must decrypt elsewhere | No audio exported when the exam lacks an examiner key (stated in manifest) |
| D12 | SQLite revisions hash-chained per session; `.swarsub` lists file hashes + chain head | Tamper-evidence for later edits | Signatures/anchors are an institutional extension |
| D13 | `.part` WAV streamed during a turn, encrypted at stop; leftovers quarantined (encrypted) on start | Mic failure / crash must not lose evidence | - |
| D14 | Fixture mode as an explicit, labelled bridge + recorder pair | Lets the real verification pipeline run on real audio when Flow is unavailable, without pretending to be live | Labelled in UI, overlay, DB (`source=fixture`) and submission manifest |
| D15 | Synthetic SAPI speech for fixtures | No personal data; reproducible; licensed with Windows | Human consented corpus is future work |
| D16 | PyInstaller one-folder + Inno Setup per-user install (`PrivilegesRequired=lowest`) | Native DLLs (Vosk, PortAudio, Qt) are more reliable one-folder | - |
| D18 | Conversation voice = ElevenLabs "Abhi" (voice EMxdghWQV7gqV33j4J3F, model eleven_flash_v2_5, raw PCM 22.05 kHz stream) behind explicit consent; Windows SAPI rendered to memory as fallback | User request; PCM lets the orb use the real output level; spoken text includes exam content, so consent + DPAPI key + automatic local fallback | `voice_provider=sapi` keeps everything local |
| D19 | Wake opens an animated "conversation orb" (Siri-like workflow, original artwork); deformation only from measured levels; click-through, non-activating | User request + blueprint rule "no fake listening indicators, no Siri/Bixby clone" | Reduced-motion static ring; can be disabled |
| D20 | Follow-up listening (default 6 s) after a reply in a voice/hotkey conversation; "thanks" ends it; never after keyboard-only commands or during dictation | Conversational feel without repeating the wake phrase; window is short and commands remain allow-listed | Toggle in Settings |
| D21 | Gemini TTS (`gemini-3.8-flash-lite-tts`, Interactions API, prebuilt voice "Sulafat") added as the default cloud voice; ElevenLabs kept as an option; separate consent per provider | User request; docs say Gemini TTS reads text "strictly as a verbatim transcript", which approved question text requires | Gemini Live rejected: it generates its own spoken replies (exam mode forbids answer generation and paraphrase) |
| D22 | N-best rescoring: a flag survives only if the recogniser's closest alternative reading still disagrees with the text | Cut false flags on unseen data (held-out v1 21 % -> 7 %) with no tunable threshold | Costs ~0.6 s per flagged turn; can miss sound-alike substitutions (documented) |
| D23 | Kaldi grammar-constrained alignment (Vosk) for spans the ASR did not hear, instead of MMS CTC | Precise clips for deletions without a 1 GB torch model in the installer | CTC remains possible later |
| D24 | Wake phrase must open an utterance; weak "he swore ..." matches need a following command or a pause; low-precision variants dropped | Measured 50 and 40 false wakes/hour on two passive sets; 0 on a third unseen set after the change | Ctrl+Alt+Space always available |
| D25 | Every fix found on a held-out set triggers a new held-out set written before the fix | Keeps reported numbers honest | - |
| D26 | Selective disclosure as exam policy `audio_disclosure` (full / review_clips / none) | Lets institutions limit which recordings leave the student's machine | Default full |
| D27 | Structured blocks anchored after a paragraph; chemistry/tables/pseudocode as constrained grammars | Inline reading order that survives edits; explicit content only (no balancing, no execution) | - |
| D28 | PDF import is an examiner-side tool, text only, refuses to package figures without approved descriptions | Blueprint: PDFs for text only, no automatic interpretation that could reveal a solution | - |
| D17 | `os._exit` after orderly shutdown | Native worker threads can block interpreter teardown; all data already committed | - |
