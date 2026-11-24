# 3-minute demo script

**On this build machine, run in LABELLED fixture mode** (`SwarLekhak.exe --fixture` or
`python -m swarlekhak --fixture`). Say clearly on stage: "Wispr Flow is not installed on this laptop, so the
dictated text and audio come from prerecorded synthetic fixtures with planted test errors; the verification,
correction, submission and examiner steps are live." On a machine with Flow, run without `--fixture`.

Preparation: `python -m swarlekhak --demo-setup`; Setup -> consent to wake detection; headphones on.

| Time | Action (voice; keyboard equivalent) | What the audience sees/hears |
|---|---|---|
| 0:00 | "Hey Swar, start my examination" (Ctrl+Alt+Space, then the phrase) -> "yes" | Title, duration, recording policy, Flow disclosure, consent |
| 0:15 | "Go to question two" | Question read aloud |
| 0:25 | "Start writing" ... "Hey Swar, finish writing" (F5 / F6) | Badge "DEMO FIXTURE MODE", RECORDING indicator, text arrives in editor |
| 0:50 | (automatic) "One passage may need review" - **planted test error** | Review queue: numeric, written 15, heard 10 |
| 1:05 | "Read review" -> "play the original" | The clip "...gives ten" plays |
| 1:15 | "Replace that with 10" | Text corrected; review marked corrected; "say undo to revert" |
| 1:40 | "Equation a equals F over m" -> "yes" | Read-back, Equation 1: a = F/m with spoken equivalent |
| 2:00 | "Go to question three" -> "play the graph" -> "next point" | Rising/falling tones; "Point 1 of 6: time 0, velocity 0" |
| 2:10 | "Go to question four" -> "create node input", "create node controller", "connect input to controller" | Diagram + adjacency list |
| 2:20 | "Submit examination" -> "yes" | Summary of unanswered / unreviewed; locked |
| 2:40 | `SwarLekhak.exe --examiner` -> open `.swarsub`, load examiner key | ALL CHECKS PASSED, waveform with highlighted clip, "fixture" turn source shown |

Close with measured numbers from TEST_RESULTS.md (synthetic speech, held-out FP 21 %) - not adoption claims.
