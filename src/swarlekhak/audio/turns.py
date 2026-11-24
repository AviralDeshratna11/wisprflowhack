"""Turn recorders: live microphone evidence vs. labelled demo fixture audio."""
from __future__ import annotations

from pathlib import Path

from swarlekhak.audio.recorder import AudioAsset, EvidenceRecorder, EvidenceVault


class LiveTurnRecorder:
    label = "live"

    def __init__(self, session, recorder: EvidenceRecorder) -> None:
        self.session = session
        self.recorder = recorder

    def start(self, turn_id: str) -> int:
        if not self.session.running:
            raise RuntimeError("the microphone is not active")
        self.recorder.start(turn_id, self.session.native_rate)
        return self.session.attach_recorder(self.recorder)

    def stop(self) -> tuple[AudioAsset | None, int]:
        end = self.session.detach_recorder()
        return self.recorder.stop(), end


class FixtureTurnRecorder:
    """Stores the prerecorded fixture WAV (synthetic/consented speech) as the turn's evidence."""

    label = "fixture"

    def __init__(self, vault: EvidenceVault, bridge) -> None:
        self.vault = vault
        self.bridge = bridge
        self.turn_id: str | None = None

    def start(self, turn_id: str) -> int:
        self.turn_id = turn_id
        return 0

    def stop(self) -> tuple[AudioAsset | None, int]:
        fx = self.bridge.current
        if not fx or not self.turn_id:
            return None, 0
        asset = self.vault.store_wav(self.turn_id, Path(fx["audio"]).read_bytes())
        return asset, asset.num_samples
