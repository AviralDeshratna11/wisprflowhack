"""Generate LABELLED synthetic speech fixtures with Windows SAPI (no human speech, no personal data).

Each fixture = a WAV of the *spoken* text + the *inserted* text a dictation
engine might have produced. Planted discrepancies are created here,
independently of Wispr Flow, and are listed explicitly so evaluation can score
recall / false positives. Usage:

    python scripts/make_fixtures.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 1) demo fixtures used by "fixture mode" in the app (one per question)
DEMO = [
    {"id": "demo-q1", "question_ordinal": 1, "voice": "David",
     "spoken": "Bending stress sigma equals M times y divided by I. So when the moment of inertia increases, "
               "the bending stress decreases.",
     "inserted": "Bending stress σ equals M times y divided by I. So when the moment of inertia increases, "
                 "the bending stress increases.",
     "planted": [{"category": "term", "written": "increases", "spoken": "decreases"}]},
    {"id": "demo-q2", "question_ordinal": 2, "voice": "Zira",
     "spoken": "The acceleration is ten metres per second squared, because twenty newtons divided by two "
               "kilograms gives ten.",
     "inserted": "The acceleration is 10 m/s², because 20 N divided by 2 kg gives 15.",
     "planted": [{"category": "numeric", "written": "15", "spoken": "ten"}]},
    {"id": "demo-q3", "question_ordinal": 3, "voice": "David",
     "spoken": "The velocity does not stay constant. It rises from zero to four metres per second, "
               "and then falls to one.",
     "inserted": "The velocity does stay constant. It rises from 0 to 4 m/s, and then falls to 1.",
     "planted": [{"category": "negation", "written": "", "spoken": "not"}]},
    {"id": "demo-q4", "question_ordinal": 4, "voice": "Zira",
     "spoken": "The sensor measures the output of the plant and feeds it back to the controller.",
     "inserted": "The sensor measures the output of the plant and feeds it back to the controller.",
     "planted": []},
]

# 2) evaluation corpus: (spoken, inserted, planted categories) - formatting-only controls have planted=[]
EVAL = [
    ("Twenty newtons is the net force on the block.", "20 N is the net force on the block.", []),
    ("The current is three point five amperes.", "The current is 3.5 A.", []),
    ("The speed is fifteen metres per second.", "The speed is 50 m/s.", ["numeric"]),
    ("The resistance is forty ohms.", "The resistance is 14 ohms.", ["numeric"]),
    ("The reaction is not reversible at room temperature.", "The reaction is reversible at room temperature.", ["negation"]),
    ("Friction never acts in the direction of motion here.", "Friction acts in the direction of motion here.", ["negation"]),
    ("The mass is two kilograms.", "The mass is 2 kg.", []),
    ("The mass is two kilograms.", "The mass is 2 g.", ["unit"]),
    ("The length is five centimetres.", "The length is 5 metres.", ["unit"]),
    ("Force equals mass times acceleration.", "Force equals mass plus acceleration.", ["operator"]),
    ("Voltage equals current times resistance.", "Voltage equals current divided by resistance.", ["operator"]),
    ("Pressure increases when the volume decreases.", "Pressure increases when the volume increases.", ["term"]),
    ("The wave is transverse, not longitudinal.", "The wave is longitudinal, not transverse.", ["term"]),
    ("Um, the energy is conserved in this collision.", "The energy is conserved in this collision.", []),
    ("The temperature rose by twelve degrees.", "The temperature rose by 12°.", []),
    ("The beam deflects by two millimetres.", "The beam deflects by two millimetres.", []),
    ("Entropy of an isolated system does not decrease.", "Entropy of an isolated system does not decrease.", []),
    ("The frequency is fifty hertz.", "The frequency is 15 Hz.", ["numeric"]),
    ("Heat flows from the hot body to the cold body.", "Heat flows from the cold body to the hot body.", ["term"]),
    ("The sum of the angles is one hundred and eighty degrees.", "The sum of the angles is 180 degrees.", []),
    ("The efficiency is sixty percent.", "The efficiency is 60%.", []),
    ("Acceleration is the rate of change of velocity.", "Acceleration is the rate of change of velocity.", []),
    ("The gas is ideal, so the internal energy depends only on temperature.",
     "The gas is ideal, so the internal energy depends only on temperature.", []),
    ("The block does not slide down the incline.", "The block does slide down the incline.", ["negation"]),
]

# 3) HELD-OUT test set. Written before any rule tuning; never used to tune thresholds or rules.
HELDOUT = [
    ("The car travels thirty kilometres in half an hour.", "The car travels 30 km in half an hour.", []),
    ("The car travels thirty kilometres in half an hour.", "The car travels 13 km in half an hour.", ["numeric"]),
    ("Kinetic energy is half m v squared.", "Kinetic energy is half m v squared.", []),
    ("The current does not depend on the length of the wire.", "The current does depend on the length of the wire.", ["negation"]),
    ("Power equals work divided by time.", "Power equals work times time.", ["operator"]),
    ("The density of water is one thousand kilograms per cubic metre.", "The density of water is 1000 kg per cubic metre.", []),
    ("The magnetic field is strongest at the poles.", "The magnetic field is weakest at the poles.", ["term"]),
    ("The output voltage is twelve volts.", "The output voltage is 12 V.", []),
    ("The output voltage is twelve volts.", "The output voltage is 12 A.", ["unit"]),
    ("Light travels faster in air than in glass.", "Light travels faster in air than in glass.", []),
    ("The resistance doubles when the length doubles.", "The resistance halves when the length doubles.", ["term"]),
    ("Sound cannot travel through a vacuum.", "Sound can travel through a vacuum.", ["negation"]),
    ("The spring constant is two hundred newtons per metre.", "The spring constant is 200 N/m.", []),
    ("Momentum is conserved in all collisions.", "Momentum is conserved in all collisions.", []),
]


def synth(text: str, voice: str, rate: int, out: Path) -> None:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    v = win32com.client.Dispatch("SAPI.SpVoice")
    for tok in v.GetVoices():
        if voice.lower() in tok.GetDescription().lower():
            v.Voice = tok
    v.Rate = rate
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18  # SAFT16kHz16BitMono
    stream.Format = fmt
    stream.Open(str(out), 3, False)  # SSFMCreateForWrite
    v.AudioOutputStream = stream
    v.Speak(text)
    stream.Close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "audio"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    demo = []
    for f in DEMO:
        wav = out / f"{f['id']}.wav"
        synth(f["spoken"], f["voice"], 0, wav)
        demo.append({"id": f["id"], "question_ordinal": f["question_ordinal"], "audio": wav.name,
                     "spoken_text": f["spoken"], "inserted_text": f["inserted"], "planted": f["planted"],
                     "label": "DEMO FIXTURE - synthetic SAPI speech; inserted text contains planted test errors"})
    (out / "demo_fixtures.json").write_text(json.dumps(demo, indent=2, ensure_ascii=False), encoding="utf-8")
    ev = []
    for i, (spoken, inserted, planted) in enumerate(EVAL):
        for voice, rate in (("David", 0), ("Zira", 1)):
            wav = out / f"eval-{i:02d}-{voice.lower()}.wav"
            synth(spoken, voice, rate, wav)
            ev.append({"id": f"eval-{i:02d}-{voice.lower()}", "audio": wav.name, "spoken_text": spoken,
                       "inserted_text": inserted, "planted": planted, "voice": voice, "rate": rate})
    (out / "eval_fixtures.json").write_text(json.dumps(ev, indent=2, ensure_ascii=False), encoding="utf-8")
    ho = []
    for i, (spoken, inserted, planted) in enumerate(HELDOUT):
        for voice, rate in (("David", 0), ("Zira", 1)):
            wav = out / f"heldout-{i:02d}-{voice.lower()}.wav"
            synth(spoken, voice, rate, wav)
            ho.append({"id": wav.stem, "audio": wav.name, "spoken_text": spoken, "inserted_text": inserted,
                       "planted": planted, "voice": voice, "rate": rate})
    (out / "heldout_fixtures.json").write_text(json.dumps(ho, indent=2, ensure_ascii=False), encoding="utf-8")
    # wake-phrase / command fixtures (Gate C)
    wake = []
    for i, (text, expect) in enumerate([
        ("Hey Swar, start my examination.", "start_exam"),
        ("Hey Swar, read question one.", "read_question"),
        ("Hey Swar, finish writing.", "finish_writing"),
        ("Hey Swar.", "wake_only"),
        ("Hey Swar, next question.", "next_question"),
        ("Hey Swar, play the original.", "play_original"),
        ("The stress in the beam is proportional to the moment.", None),
        ("Hey, start the engine and check the oil.", None),
        ("Say hello to the class before we begin.", None),
        ("The star is very bright tonight.", None),
    ]):
        for voice in ("David", "Zira"):
            wav = out / f"wake-{i:02d}-{voice.lower()}.wav"
            synth(text, voice, 0, wav)
            wake.append({"id": wav.stem, "audio": wav.name, "text": text, "expect": expect, "voice": voice})
    (out / "wake_fixtures.json").write_text(json.dumps(wake, indent=2), encoding="utf-8")
    print(f"wrote {len(demo)} demo, {len(ev)} eval, {len(ho)} held-out and {len(wake)} wake fixtures to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
