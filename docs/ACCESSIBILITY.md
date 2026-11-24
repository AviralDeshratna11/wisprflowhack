# Accessibility checklist

Automated checks cannot prove accessibility. Nothing below has yet been validated with NVDA or with
visually impaired participants; items are "implemented", not "verified".

| Requirement | Implementation | Verified? |
|---|---|---|
| Main path needs no sight after setup | Every action has a spoken command; TTS reads questions, answers, reviews, graphs, diagrams | Keyboard-only e2e test (automated); no blind-user test |
| Keyboard alternative for every voice action | Command box accepts any command; F1/F2/F5/F6/F7/F8, Ctrl+PgUp/PgDn, Ctrl+O; global Ctrl+Alt+Space | automated |
| Accessible names / buddies | `setAccessibleName` on all controls, `&` mnemonics with `setBuddy`, logical tab order | not with NVDA |
| Visible focus | 2 px amber focus border in stylesheet | visual check of screenshots |
| Non-colour status | State, mode, recording and review status always shown as text (e.g. "RECORDING evidence", "[CHECK]") | visual check |
| Caption / announcements | Caption label updates accessible name + NameChanged event | not with NVDA |
| Interruptible speech, adjustable rate/voice | "stop", "slower", "faster"; Settings voice/rate | manual |
| Dialogs accessible | Native Qt dialogs; settings controls labelled | not with NVDA |
| Errors spoken once, actionable | Orchestrator `say()` with recovery instructions | tests check messages |
| Reduced motion | Static overlay option | manual |
| Overlay does not steal focus | `WindowDoesNotAcceptFocus`, `WA_ShowWithoutActivating`, `Qt.Tool` | not measured with Flow |
| No audio-only critical control | Submission and destructive edits need explicit yes (voice or typed) | tests |

Known gaps: NVDA may double-speak (SAPI + screen reader) - a "screen reader speaks captions" mode is planned.
Equations are read in linear spoken form; no MathML/MathCAT output yet.
