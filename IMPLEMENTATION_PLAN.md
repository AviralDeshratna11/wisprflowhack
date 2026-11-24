# Implementation plan

Vertical-slice order, P0 before P1 before P2. Status in [TASKS.md](TASKS.md).

1. **Phase Zero (gates A-D)** - hardware/Flow matrix -> [docs/FEASIBILITY.md](docs/FEASIBILITY.md). Done; A blocked.
2. **Core contracts and state machine** - states, intents allow-list, typed contracts. Done.
3. **Thin slice** - activation (hotkey + wake approximation) -> read question -> Flow (or labelled fixture)
   insertion + evidence capture -> save revision -> real verification -> spoken review -> correction -> export ->
   native examiner. Done in fixture mode; live Flow unverified.
4. **Persistence and evidence** - SQLite WAL/migrations, hash chain, encrypted vault, quarantine, idempotent
   submission, examiner integrity. Done.
5. **Audio Cursor and command firewall** - sentence/paragraph/word navigation, find/replace/insert/delete,
   undo, confirmation for destructive edits, state guards during dictation. Done.
6. **P1 structured expression** - math MVP, voice-to-diagram, sonification + point stepping, approved visual
   descriptions. Done (MVP scope).
7. **Native UI** - workspace, overlay, tray, settings/health, diagnostics, examiner window. Done.
8. **Packaging** - PyInstaller one-folder, Inno Setup script, self-test. Exe built; installer pending Inno Setup.
9. **Evaluation** - fixture corpus, dev/held-out benchmark, Gate B script, pytest suite. Done (synthetic speech).

Next, in priority order:
1. Run Gate A on a machine with Wispr Flow: injected shortcut accepted? paste timing? focus? concurrent mic?
2. NVDA keyboard/screen-reader walkthrough; then consented sessions with visually impaired students.
3. Consented human speech corpus (Indian English, STEM terms, noise); re-measure FP with a fresh held-out split.
4. CTC forced alignment (MMS) or faster-whisper word timestamps as an alternative hypothesis.
5. Build installer with Inno Setup 6; clean-VM install/uninstall test; code signing.
