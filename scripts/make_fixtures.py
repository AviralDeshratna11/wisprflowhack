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

# 4) HELD-OUT v2 (written 2026-11-25 BEFORE the N-best / constrained-alignment changes; never used for tuning).
#    Includes a deliberately hard sound-alike substitution (refraction -> reflection).
HELDOUT2 = [
    ("The wavelength is six hundred nanometres.", "The wavelength is 600 nanometres.", []),
    ("Ohm's law relates voltage, current and resistance.", "Ohm's law relates voltage, current and resistance.", []),
    ("The pendulum completes one oscillation every two seconds.", "The pendulum completes 1 oscillation every 2 seconds.", []),
    ("Catalysts lower the activation energy of a reaction.", "Catalysts lower the activation energy of a reaction.", []),
    ("The beam carries a load of five kilonewtons.", "The beam carries a load of 5 kilonewtons.", []),
    ("Water boils at one hundred degrees Celsius at sea level.", "Water boils at 100 degrees Celsius at sea level.", []),
    ("The capacitor stores energy in its electric field.", "The capacitor stores energy in its electric field.", []),
    ("Refraction occurs when light changes speed.", "Refraction occurs when light changes speed.", []),
    ("The wavelength is six hundred nanometres.", "The wavelength is 60 nanometres.", ["numeric"]),
    ("Catalysts lower the activation energy of a reaction.", "Catalysts raise the activation energy of a reaction.", ["term"]),
    ("The capacitor does not conduct direct current.", "The capacitor does conduct direct current.", ["negation"]),
    ("The beam carries a load of five kilonewtons.", "The beam carries a load of 5 newtons.", ["unit"]),
    ("Momentum equals mass times velocity.", "Momentum equals mass divided by velocity.", ["operator"]),
    ("Water boils at one hundred degrees Celsius at sea level.", "Water freezes at 100 degrees Celsius at sea level.", ["term"]),
    ("Refraction occurs when light changes speed.", "Reflection occurs when light changes speed.", ["term"]),
    ("The resistance is twelve ohms.", "The resistance is 20 ohms.", ["numeric"]),
]

# 6) HELD-OUT v3 + passive v2: written 2026-11-25 AFTER v2 was used to find the SI-prefix gap and BEFORE that
#    fix and the final wake rules were implemented. Final reported numbers come from these.
HELDOUT3 = [
    ("The specific heat capacity of water is about four thousand two hundred joules per kilogram kelvin.",
     "The specific heat capacity of water is about 4200 joules per kilogram kelvin.", []),
    ("A transformer cannot change the frequency of the supply.", "A transformer cannot change the frequency of the supply.", []),
    ("The projectile reaches its maximum height after three seconds.", "The projectile reaches its maximum height after 3 seconds.", []),
    ("Isotopes have the same number of protons but different numbers of neutrons.",
     "Isotopes have the same number of protons but different numbers of neutrons.", []),
    ("The lens has a focal length of twenty five centimetres.", "The lens has a focal length of 25 cm.", []),
    ("Diffusion is faster at higher temperatures.", "Diffusion is faster at higher temperatures.", []),
    ("The total resistance of two resistors in parallel is less than either resistor.",
     "The total resistance of two resistors in parallel is less than either resistor.", []),
    ("Kinetic energy is not conserved in an inelastic collision.", "Kinetic energy is not conserved in an inelastic collision.", []),
    ("The projectile reaches its maximum height after three seconds.", "The projectile reaches its maximum height after 13 seconds.", ["numeric"]),
    ("A transformer cannot change the frequency of the supply.", "A transformer can change the frequency of the supply.", ["negation"]),
    ("The lens has a focal length of twenty five centimetres.", "The lens has a focal length of 25 metres.", ["unit"]),
    ("Pressure equals force divided by area.", "Pressure equals force times area.", ["operator"]),
    ("Diffusion is faster at higher temperatures.", "Diffusion is slower at higher temperatures.", ["term"]),
    ("Isotopes have the same number of protons but different numbers of neutrons.",
     "Isotopes have the same number of electrons but different numbers of neutrons.", ["term"]),
    ("Kinetic energy is not conserved in an inelastic collision.", "Kinetic energy is conserved in an inelastic collision.", ["negation"]),
    ("The total resistance of two resistors in parallel is less than either resistor.",
     "The total resistance of two resistors in parallel is more than either resistor.", ["term"]),
]
PASSIVE_TEXT_2 = [
    "Hey, did anyone see where I left my lab coat this morning?",
    "The soldier swore an oath before the ceremony began.",
    "Hay fever is common in spring when pollen counts are high.",
    "The water in the sewer system is treated before it reaches the river.",
    "He swiftly solved the second problem on the board.",
    "A swan glided across the lake behind the college.",
    "Hey team, the results of the mock test will be posted on Friday.",
    "The answer sheet must be signed by the invigilator.",
    "Hi everyone, please switch off your phones before the exam.",
    "The bird soared above the hills and disappeared into the clouds.",
    "Sour milk contains lactic acid produced by bacteria.",
    "He swore he would finish writing the report by tonight.",
]

# 7) passive v3: written 2026-11-25 BEFORE the weak-wake rule; the final false-wake number comes from this set.
PASSIVE_TEXT_3 = [
    "He swore that the experiment had been repeated three times.",
    "Hey, is the library open after the exam today?",
    "The heavy suitcase was left near the station entrance.",
    "He sorted the samples by mass before starting the titration.",
    "Hey, start the timer when the ball leaves the ramp.",
    "Swarms of locusts can destroy crops within hours.",
    "He swore at the broken printer and then fixed the paper jam.",
    "The answer for question four should include a labelled diagram.",
    "Hey, next time bring a ruler and a protractor.",
    "Hay is stored in the barn to keep it dry during the monsoon.",
    "He swears by drinking water before every long exam.",
    "The swing completes twenty oscillations in forty seconds.",
]

# 5) long passive (non-wake) speech for false-wake-per-hour measurement (not committed: large)
PASSIVE_TEXT = [
    "The examination will begin shortly. Please read each question carefully before you answer.",
    "Newton's second law states that force equals mass times acceleration.",
    "He swept the floor and then he sorted the papers on the desk.",
    "Hey everyone, the star of the show tonight is a young singer from Hyderabad.",
    "The sewer pipes under the old city were replaced last summer.",
    "She swore she had submitted the assignment before the deadline.",
    "Heat flows from a hotter body to a colder body until equilibrium is reached.",
    "A sour taste usually indicates the presence of an acid.",
    "Hey, can you pass me the calculator and the graph paper please?",
    "The swirling water drained slowly from the large steel tank.",
    "In a series circuit the same current flows through every component.",
    "Hey there, how was the physics practical this morning?",
    "The soaring eagle circled above the valley for nearly an hour.",
    "The answer depends on whether the surface is frictionless or rough.",
    "Hey Sam, we should start revising the chapter on thermodynamics.",
    "A swarm of bees settled on the branch outside the classroom window.",
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
    ap.add_argument("--only", choices=["all", "heldout2", "heldout3", "passive", "passive2", "passive3"],
                    default="all")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.only in ("all", "heldout2"):
        h2 = []
        for i, (spoken, inserted, planted) in enumerate(HELDOUT2):
            for voice, rate in (("David", 0), ("Zira", 1)):
                wav = out / f"heldout2-{i:02d}-{voice.lower()}.wav"
                synth(spoken, voice, rate, wav)
                h2.append({"id": wav.stem, "audio": wav.name, "spoken_text": spoken, "inserted_text": inserted,
                           "planted": planted, "voice": voice, "rate": rate})
        (out / "heldout2_fixtures.json").write_text(json.dumps(h2, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {len(h2)} held-out v2 fixtures")
    if args.only in ("all", "passive"):
        long_dir = out.parent / "audio_long"
        long_dir.mkdir(exist_ok=True)
        n = 0
        for rep_i in range(6):
            for voice, rate in (("David", rep_i - 2), ("Zira", 2 - rep_i)):
                text = " ".join(PASSIVE_TEXT[(rep_i * 3) % len(PASSIVE_TEXT):] + PASSIVE_TEXT[:(rep_i * 3) % len(PASSIVE_TEXT)])
                synth(text, voice, rate, long_dir / f"passive-{rep_i}-{voice.lower()}.wav")
                n += 1
        print(f"wrote {n} long passive files to {long_dir}")
    if args.only in ("all", "heldout3"):
        h3 = []
        for i, (spoken, inserted, planted) in enumerate(HELDOUT3):
            for voice, rate in (("David", 0), ("Zira", 1)):
                wav = out / f"heldout3-{i:02d}-{voice.lower()}.wav"
                synth(spoken, voice, rate, wav)
                h3.append({"id": wav.stem, "audio": wav.name, "spoken_text": spoken, "inserted_text": inserted,
                           "planted": planted, "voice": voice, "rate": rate})
        (out / "heldout3_fixtures.json").write_text(json.dumps(h3, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {len(h3)} held-out v3 fixtures")
    if args.only in ("all", "passive2"):
        long_dir = out.parent / "audio_long"
        long_dir.mkdir(exist_ok=True)
        for rep_i in range(6):
            for voice, rate in (("David", 1 - rep_i), ("Zira", rep_i - 3)):
                k = (rep_i * 5) % len(PASSIVE_TEXT_2)
                synth(" ".join(PASSIVE_TEXT_2[k:] + PASSIVE_TEXT_2[:k]), voice, rate,
                      long_dir / f"passive2-{rep_i}-{voice.lower()}.wav")
        print("wrote 12 long passive v2 files")
    if args.only in ("all", "passive3"):
        long_dir = out.parent / "audio_long"
        long_dir.mkdir(exist_ok=True)
        for rep_i in range(6):
            for voice, rate in (("David", rep_i - 3), ("Zira", 2 - rep_i)):
                k = (rep_i * 7) % len(PASSIVE_TEXT_3)
                synth(" ".join(PASSIVE_TEXT_3[k:] + PASSIVE_TEXT_3[:k]), voice, rate,
                      long_dir / f"passive3-{rep_i}-{voice.lower()}.wav")
        print("wrote 12 long passive v3 files")
    if args.only != "all":
        return 0
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
