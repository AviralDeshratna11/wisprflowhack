"""Background verification worker (bounded: one job at a time, off the GUI thread)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from swarlekhak.audio.recorder import read_wav
from swarlekhak.audio.resample import resample_array
from swarlekhak.engines.proof import ProofEngine, VerificationResult
from swarlekhak.contracts import ReviewStatus

log = logging.getLogger(__name__)


class VerificationService:
    def __init__(self, vault, transcriber_factory: Callable[[], object | None],
                 post: Callable[[Callable[[], None]], None], sensitivity: str = "standard") -> None:
        self.vault = vault
        self.transcriber_factory = transcriber_factory
        self.post = post
        self.sensitivity = sensitivity
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="verify")
        self._engine: ProofEngine | None = None

    def _get_engine(self) -> ProofEngine:
        if self._engine is None:
            try:
                tr = self.transcriber_factory()
            except Exception:
                log.exception("ASR model failed to load")
                tr = None
            self._engine = ProofEngine(tr, self.sensitivity)
        return self._engine

    def run_sync(self, audio_path: str, written_text: str) -> VerificationResult:
        engine = self._get_engine()
        try:
            pcm, sr = read_wav(self.vault.load_wav(audio_path))
        except Exception as exc:
            return VerificationResult(ReviewStatus.UNAVAILABLE, methods=engine.methods(),
                                      error=f"evidence audio could not be opened: {exc}")
        pcm16 = pcm if sr == 16000 else resample_array(pcm, sr, 16000)
        return engine.verify(pcm16, written_text)

    def submit(self, job: dict, on_done: Callable[[dict, VerificationResult], None]) -> None:
        def work():
            try:
                res = self.run_sync(job["audio_path"], job["written_text"])
            except Exception as exc:
                log.exception("verification crashed")
                res = VerificationResult(ReviewStatus.UNAVAILABLE, error=str(exc))
            self.post(lambda: on_done(job, res))
        self._pool.submit(work)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
