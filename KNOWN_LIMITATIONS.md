# Known limitations (truthful status, 2026-11-25, v0.2.0)

## Blocked - needs the user / real people
1. **Live Wispr Flow integration has not been verified (Gate A not passed).** Wispr Flow 1.6.1609 is installed,
   but in Gate A run 2 it produced no final transcript even with the shortcut pressed by hand (text briefly
   appeared in our editor, then vanished), and Flow did not type in Notepad either - Flow's own setup must be
   completed first. The live bridge presses the user-configured hands-free shortcut (default Ctrl+Win+Space per
   docs.wisprflow.ai) via Win32 `SendInput` and observes insertion in our editor. Unverified: whether Flow accepts
   *injected* key events, paste timing, and focus from a background hotkey. If injection is ignored, the student
   presses the Flow shortcut themselves (our editor still observes the insertion) and "fully hands-free" is unmet.
   Re-run: `scripts/gate_a_flow.py`.
2. **Microphone sharing with Flow is only half verified**: our capture kept working with Flow running (4 turns,
   0 dropped blocks) but Flow's own transcription while we record is unconfirmed.
3. **Cloud voices (Gemini TTS, ElevenLabs "Abhi") are untested live.** No working key: the ElevenLabs key provided
   lacks the `text_to_speech` permission; no Gemini key yet. Both are covered by mocked-HTTP tests only; the
   local Windows voice is the automatic fallback. Gemini requests are unary (a sentence starts after it is fully
   synthesised); there is no Indian-English prebuilt Gemini voice.
4. **No human-speech evaluation and no NVDA / user testing.** All accuracy numbers use synthetic Windows SAPI
   speech. No accent, noise, speech-impairment or fatigue data. No NVDA walkthrough, no sessions with visually
   impaired students: **no accessibility claim (WCAG/NVDA) is made**. An automated Qt accessibility audit
   (names, tab order, text status) passes - it cannot prove accessibility.
5. **Installer is unsigned.** `SwarLekhak-Setup.exe` builds and passed a silent install / self-test / uninstall
   test on this machine (per-user, no admin), but it is not code-signed (SmartScreen will warn) and has not been
   tried on a clean virtual machine.

## Partially working (measured limits)
6. **Wake phrase "Hey Swar" is an approximation** of a general model's transcript ("hey swar", "hey swear",
   and "he swore ..." only when followed by a command or a pause). Synthetic speech: 12/12 wake + command,
   0 false wakes in 9.9 min of unseen passive speech (95 % upper bound about 18/hour). Real voices, accents and
   noise: not measured. Ctrl+Alt+Space activation always works.
7. **Verification (final, unseen held-out v3, synthetic):** 16/16 planted errors caught; 2/16 (12.5 %, 95 % CI
   3.5-36 %) clean answers flagged - both local-ASR mishearings of "joules". Sample sizes are small.
8. **Sound-alike substitutions can be missed**: "reflection" written for spoken "refraction" was not flagged
   (held-out v2), because the recogniser's alternative readings and the sounds-alike rule treat it as a possible
   mishearing. Meaning-reversing pairs on the antonym list (increase/decrease, ...) and all numeric, negation,
   unit and operator changes are always checked.
9. **Clip timing uses ASR word times plus Kaldi text-constrained (grammar) alignment for unheard spans**, not
   the blueprint's MMS CTC aligner (torchaudio + ~1 GB model; not adopted to keep the installer at 95 MB).
10. **Verification takes about 2 s per turn on CPU** (second N-best pass when a discrepancy is found).
11. **Structured blocks are anchored after a paragraph**, not at a character position inside a sentence.
    Chemistry is never balanced, pseudocode never executed (by design). Math has no integrals/limits yet.
12. **Graph sonification** plays only the first series; no speed/volume controls beyond system volume.
13. **PDF import is text-only**: figures are never interpreted; the examiner must write approved descriptions.

## Not implemented (P2 in the blueprint)
- Hindi / code-switching, quiet-speech mode, personalised calibration, external Word/UI-Automation editors, macOS.
- Examiner-held signatures/anchors: the local SHA-256 hash chain detects later edits, but a privileged user could
  rewrite the whole chain.

## By design
- The local exam timer is not tamper-resistant; institutional policy governs pauses.
- Not OS lockdown; not certified for regulated examinations.
