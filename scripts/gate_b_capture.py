"""Gate B hardware check: real microphone -> AudioSessionManager -> (Vosk listener + evidence recorder)
-> encrypted vault -> read back. Repeats N turns and reports frames, drops and timing.
Usage: python scripts/gate_b_capture.py [--turns 10] [--seconds 3] [--device INDEX]"""
import argparse, json, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from swarlekhak.audio.asr import VoskListener
from swarlekhak.audio.recorder import EvidenceRecorder, EvidenceVault, read_wav
from swarlekhak.audio.session import AudioSessionManager
from swarlekhak.audio.turns import LiveTurnRecorder
from swarlekhak.config import VOSK_MODEL_NAME

ap = argparse.ArgumentParser()
ap.add_argument("--turns", type=int, default=10)
ap.add_argument("--seconds", type=float, default=3.0)
ap.add_argument("--device", default="")
a = ap.parse_args()
tmp = Path(tempfile.mkdtemp(prefix="swar_gateb_"))
(tmp / "audio").mkdir(); (tmp / "q").mkdir()
vault = EvidenceVault(tmp / "audio", tmp / "q", lambda: b"g" * 32)
heard = []
cap = AudioSessionManager(a.device or None)
lis = VoskListener(ROOT / "models" / VOSK_MODEL_NAME, on_final=heard.append)
cap.add_consumer(lis)
cap.start(); lis.set_enabled(True)
rec = LiveTurnRecorder(cap, EvidenceRecorder(vault))
rows = []
for i in range(a.turns):
    s0 = rec.start(f"turn_{i:02d}"); t0 = time.monotonic()
    time.sleep(a.seconds)
    asset, s1 = rec.stop(); dt = time.monotonic() - t0
    pcm, sr = read_wav(vault.load_wav(asset.path))
    rows.append({"turn": i, "start_sample": s0, "end_sample": s1, "samples_in_wav": len(pcm),
                 "expected": int(sr * dt), "rate": sr, "wall_s": round(dt, 3)})
cap.stop()
loss = [1 - r["samples_in_wav"] / max(1, r["expected"]) for r in rows]
print(json.dumps({"device": cap.device_name, "native_rate": cap.native_rate, "turns": len(rows),
                  "dropped_blocks": cap.dropped_blocks, "status_flags": cap.status_flags,
                  "max_sample_shortfall_pct": round(100 * max(loss), 2),
                  "all_turns_saved_and_decrypted": all(r["samples_in_wav"] > 0 for r in rows),
                  "listener_utterances": len(heard), "rows": rows[:3]}, indent=2))
