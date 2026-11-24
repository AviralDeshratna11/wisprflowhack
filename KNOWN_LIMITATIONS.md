# Known limitations (truthful status, 2026-11-25)

## Blocked on this build machine
1. **Live Wispr Flow integration has not been verified (Gate A blocked).** Wispr Flow 1.6.1609 is now installed,
   but in Gate A run 2 it produced no final transcript even with the shortcut pressed by hand (text briefly
   appeared in our editor, then vanished), and the user reports it does not type in Notepad either - Flow's own
   setup must be completed first. The live bridge presses the user-configured hands-free shortcut (default
   Ctrl+Win+Space per docs.wisprflow.ai) via Win32 `SendInput` and observes insertion in our editor. Unverified:
   whether Flow accepts *injected* key events (some apps ignore `LLKHF_INJECTED`), paste timing, and whether
   Windows foreground rules allow our editor to take focus from a background hotkey. If injection is ignored,
   the student must press the Flow shortcut themselves (still works: our editor observes the insertion), and
   the "fully hands-free" goal is not met.
2. **Microphone sharing with Flow is only half verified.** Our capture kept working with Flow running (Gate A
   run 2: 4 turns, 0 dropped blocks, speech captured), but Flow's own transcription while we record is unconfirmed.
3. **Installer not built.** Inno Setup 6 is not installed here. `installer/SwarLekhak.iss` and
   `scripts/build.ps1` are provided; the installer is unbuilt/untested and would be **unsigned**.

## Partially working
- **Gemini TTS voice is implemented but not verified live** (no Gemini key yet). Built from the current AI Studio
  docs (Interactions API); the response parser also accepts the older generateContent `inlineData` shape. Unary
  requests only (no streaming), so a long sentence is spoken only after it is fully synthesised. No Indian-English
  prebuilt voice; regional voices need Google's extended voice library (not integrated).
- **ElevenLabs voice "Abhi" is implemented but not verified live**: no API key was available during development.
  The request format, PCM streaming, cache, consent gate and local fallback are unit-tested with a mocked HTTP
  layer only. Library voices may first need "Add to My Voices" in ElevenLabs (Settings > "Check key and voice"
  reports this). The voice is cloud-only, needs internet and the user's plan quota; first-word latency depends on
  the network.
- **Conversation orb**: original design, driven by measured mic/voice levels; tested offscreen and rendered, not
  yet checked side-by-side with Wispr Flow's own bar or with NVDA.
4. **Wake phrase "Hey Swar" is an approximation.** No trained "Hey Swar" model exists; "swar" is not in the Vosk
   vocabulary. Wake detection runs the general Vosk model and matches the transcript against "hey/he/hay +
   swar-like word" (swore, swear, sour, star, ...). On 20 synthetic SAPI utterances (tuned on the same set) it
   reached 12/12 recall and 0/8 false triggers; real-voice, accent, noise and false-triggers-per-hour are
   **not measured**. Phrases like "he swore" in speech can trigger it. Labelled keyboard activation
   (Ctrl+Alt+Space) is always available.
5. **Verification false positives are above target on held-out data.** Held-out synthetic set: planted recall
   14/14, but 3/14 (21 %, 95 % CI 7.6-47.6 %) clean controls raised a review (target <= 15 %). All three were
   local-ASR mishearings ("half m v" -> "have mv", "in air" -> "and there"). Synthetic TTS speech only; no
   human/Indian-English/noisy speech evaluated.
6. **Timestamps come from the local ASR's word times, not CTC forced alignment.** The blueprint's MMS CTC
   aligner (torchaudio + ~1 GB model) is not integrated. Clip intervals are padded ASR word spans; deletion
   regions are marked "approximate".
7. **"Sounds-alike" suppression** (to cut ASR mishearing flags) could hide a genuine substitution between two
   similar-sounding words that is not on the antonym list.
8. **Equations and diagrams** are stored as structured blocks *after* the answer text (with accessible text
   equivalents), not inline at the caret. Math is the MVP grammar (no integrals/limits, no chemistry, tables or
   pseudocode modes).
9. **Typed edits** are attributed per revision ("keyboard_edit"), not per keystroke; a typed edit made during
   a pending dictation turn is detected by the before/after diff and triggers an explicit "keep or restore" choice.
10. **Graph sonification** plays only the first series; no speed/volume controls yet beyond system volume.

## Not implemented (planned)
- PDF question import (PyMuPDF), selective disclosure, Hindi / code-switching, quiet-speech mode, personalised
  calibration, external Word/UI-Automation editors, macOS.
- Examiner-held signatures/anchors: the local SHA-256 hash chain detects later edits but a privileged user
  could rewrite the whole chain.
- CI pipeline (lint/type-check/Windows build smoke) is described but not configured.

## By design
- The local exam timer is not tamper-resistant; institutional policy governs pauses.
- This is not OS lockdown and not certified for regulated examinations.
- No accessibility claim (WCAG / NVDA) is made: no NVDA walkthrough or testing with visually impaired
  participants has been done yet.
