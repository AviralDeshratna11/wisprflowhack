"""Gate C + Gate D benchmark on LABELLED synthetic SAPI fixtures.

Gate C: wake phrase + command recognition (Vosk small en-us, local) on synthetic
        utterances, including non-wake distractors.
Gate D: Acoustic Proof Engine planted-discrepancy recall, false-positive rate on
        formatting-only/clean controls, clip localisation, latency.

Results are measured on synthetic TTS speech - NOT human, accented or noisy
speech - and are reported as such. Writes docs/benchmark_results.json.
"""
from __future__ import annotations

import json
import math
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swarlekhak.assistant import intents  # noqa: E402
from swarlekhak.assistant.orchestrator import build_wake_regex, wake_decision  # noqa: E402
from swarlekhak.audio.asr import VoskTranscriber  # noqa: E402
from swarlekhak.audio.recorder import read_wav  # noqa: E402
from swarlekhak.config import VOSK_MODEL_NAME  # noqa: E402
from swarlekhak.engines.proof import ProofEngine, word_error_rate  # noqa: E402
from swarlekhak.engines.normalize import normalize_text  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "audio"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - r) / d, 3), round((c + r) / d, 3))


def gate_d(engine, ev: list[dict]) -> dict:
    planted_total = planted_hit = cat_correct = extra_flags = 0
    control_total = control_flagged = 0
    latencies, rtf, wers, clip_ok, clip_n = [], [], [], 0, 0
    rows = []
    for f in ev:
        pcm, sr = read_wav((FIX / f["audio"]).read_bytes())
        t0 = time.perf_counter()
        res = engine.verify(pcm, f["inserted_text"])
        dt = time.perf_counter() - t0
        latencies.append(dt)
        rtf.append(dt / max(0.01, len(pcm) / sr))
        ref = [t.text for t in normalize_text(f["spoken_text"])]
        hyp = [t.text for t in normalize_text(res.hypothesis)]
        wers.append(word_error_rate(ref, hyp))
        cats = [d.category for d in res.discrepancies]
        if f["planted"]:
            planted_total += 1
            planted_hit += int(bool(res.discrepancies))
            cat_correct += int(f["planted"][0] in cats)
            extra_flags += max(0, len(cats) - len(f["planted"]))
            for d in res.discrepancies:
                clip_n += 1
                clip_ok += int(d.clip[1] - d.clip[0] <= 4.0)
        else:
            control_total += 1
            control_flagged += int(bool(res.discrepancies))
        rows.append({"id": f["id"], "planted": f["planted"], "flags": [(d.category, d.written, d.heard, d.clip,
                     d.approximate) for d in res.discrepancies], "status": res.status.value,
                     "asr": res.hypothesis, "latency_s": round(dt, 3)})
    return {
        "n_turns": len(ev), "planted_turns": planted_total, "control_turns": control_total,
        "planted_recall": round(planted_hit / max(1, planted_total), 3),
        "planted_recall_95ci": wilson(planted_hit, planted_total),
        "category_match_rate": round(cat_correct / max(1, planted_total), 3),
        "control_false_positive_rate": round(control_flagged / max(1, control_total), 3),
        "control_fp_95ci": wilson(control_flagged, control_total),
        "extra_flags_on_planted_turns": extra_flags,
        "clips_at_most_4s": f"{clip_ok}/{clip_n}",
        "asr_wer_vs_spoken_mean": round(sum(wers) / len(wers), 3),
        "latency_mean_s": round(sum(latencies) / len(latencies), 3),
        "latency_max_s": round(max(latencies), 3),
        "real_time_factor_mean": round(sum(rtf) / len(rtf), 3),
        "rows": rows,
    }


def passive_false_wakes(model_path: Path, wake_rx, pattern: str = "passive-*.wav") -> dict:
    """Continuous free-form recognition (as the passive listener runs) over long non-wake speech that contains
    many near-miss phrases ("hey", "swore", "sewer", "star", "sour"...). Synthetic speech only."""
    import json as _json
    from vosk import KaldiRecognizer
    from swarlekhak.audio.asr import load_model
    model = load_model(model_path)
    long_dir = FIX.parent / "audio_long"
    files = sorted(long_dir.glob(pattern))
    if not files:
        return {"error": "run scripts/make_fixtures.py --only passive"}
    secs, triggers, utterances = 0.0, [], 0
    for f in files:
        pcm, sr = read_wav(f.read_bytes())
        secs += len(pcm) / sr
        rec = KaldiRecognizer(model, 16000)
        data = pcm.tobytes()
        for i in range(0, len(data), 3200):   # 100 ms blocks, like the live listener
            if rec.AcceptWaveform(data[i:i + 3200]):
                t = _json.loads(rec.Result()).get("text", "")
                if t:
                    utterances += 1
                    if wake_decision(intents.clean(t), wake_rx)[0]:
                        triggers.append({"file": f.name, "heard": t})
        t = _json.loads(rec.FinalResult()).get("text", "")
        if t and wake_decision(intents.clean(t), wake_rx)[0]:
            triggers.append({"file": f.name, "heard": t})
    hours = secs / 3600
    return {"audio_minutes": round(secs / 60, 1), "utterances": utterances, "false_wakes": len(triggers),
            "false_wakes_per_hour": round(len(triggers) / hours, 1) if hours else None, "examples": triggers[:10]}


def main() -> int:
    model = ROOT / "models" / VOSK_MODEL_NAME
    tr = VoskTranscriber(model)
    engine = ProofEngine(tr)
    wake_rx = build_wake_regex("hey swar")
    out: dict = {"environment": {"os": platform.platform(), "python": sys.version.split()[0],
                                 "cpu": platform.processor(), "asr": tr.version,
                                 "speech": "synthetic Windows SAPI voices (David, Zira); not human speech"}}

    # ---------------- Gate C
    wake = json.loads((FIX / "wake_fixtures.json").read_text(encoding="utf-8"))
    rows = []
    tp = fn = fp = tn = cmd_ok = cmd_total = 0
    for f in wake:
        pcm, sr = read_wav((FIX / f["audio"]).read_bytes())
        hyp = tr.transcribe(pcm)["text"]
        t = intents.clean(hyp)
        woke, _rest = wake_decision(t, wake_rx)
        m = wake_rx.search(t)
        expect_wake = f["expect"] is not None
        if expect_wake and woke:
            tp += 1
        elif expect_wake:
            fn += 1
        elif woke:
            fp += 1
        else:
            tn += 1
        got = None
        if woke:
            rest = t[m.end():].strip()
            c = intents.parse(rest) if rest else None
            got = c.intent if c else ("wake_only" if not rest else None)
        if expect_wake:
            cmd_total += 1
            cmd_ok += int(got == f["expect"])
        rows.append({"id": f["id"], "said": f["text"], "heard": hyp, "woke": woke, "intent": got, "expected": f["expect"]})
    out["gate_c"] = {"wake_true_positive": tp, "wake_false_negative": fn, "wake_false_positive": fp,
                     "wake_true_negative": tn, "wake_recall": round(tp / max(1, tp + fn), 3),
                     "wake_recall_95ci": wilson(tp, tp + fn),
                     "command_accuracy_given_expected_wake": round(cmd_ok / max(1, cmd_total), 3),
                     "n_utterances": len(wake), "rows": rows}

    # ---------------- Gate D (development set = tuned on; held-out = never tuned on)
    dev = json.loads((FIX / "eval_fixtures.json").read_text(encoding="utf-8")) + [
        {**d, "planted": [p["category"] for p in d["planted"]]}
        for d in json.loads((FIX / "demo_fixtures.json").read_text(encoding="utf-8"))]
    held = json.loads((FIX / "heldout_fixtures.json").read_text(encoding="utf-8"))
    out["gate_d_development"] = gate_d(engine, dev)
    out["gate_d_heldout"] = gate_d(engine, held)
    h2 = FIX / "heldout2_fixtures.json"
    if h2.exists():
        out["gate_d_heldout2"] = gate_d(engine, json.loads(h2.read_text(encoding="utf-8")))
    out["gate_c_passive"] = passive_false_wakes(model, wake_rx)          # used to tune the wake rules
    out["gate_c_passive_v2"] = passive_false_wakes(model, wake_rx, "passive2-*.wav")   # used for the weak-wake rule
    out["gate_c_passive_v3"] = passive_false_wakes(model, wake_rx, "passive3-*.wav")   # never tuned on
    h3 = FIX / "heldout3_fixtures.json"
    if h3.exists():
        out["gate_d_heldout3"] = gate_d(engine, json.loads(h3.read_text(encoding="utf-8")))
    dest = ROOT / "docs" / (sys.argv[1] if len(sys.argv) > 1 else "benchmark_results.json")
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {name: {k: v for k, v in out[name].items() if k != "rows"}
               for name in ("gate_c", "gate_c_passive", "gate_c_passive_v2", "gate_c_passive_v3",
                            "gate_d_development", "gate_d_heldout",
                            "gate_d_heldout2", "gate_d_heldout3")
               if name in out}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
