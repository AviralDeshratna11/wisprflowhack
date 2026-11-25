"""Native examiner review mode: integrity checks, answers, evidence clips with the
actual waveform, revision history. No cheating scores. Evidence opens are logged."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPushButton, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from swarlekhak.audio.recorder import read_wav
from swarlekhak.config import data_dir
from swarlekhak.export.submission import SubmissionView, audio_index, decrypt_audio_entry, open_submission
from swarlekhak.engines.layout import BLOCK_TITLES, block_visual, interleave
from swarlekhak.ui import theme
from html import escape

log = logging.getLogger(__name__)


class Waveform(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.pcm: np.ndarray | None = None
        self.rate = 16000
        self.clip: tuple[float, float] | None = None
        self.setMinimumHeight(120)
        self.setAccessibleName("Evidence waveform")

    def set_audio(self, pcm, rate, clip=None) -> None:
        self.pcm, self.rate, self.clip = pcm, rate, clip
        dur = len(pcm) / rate if pcm is not None else 0
        desc = f"Recording {dur:.1f} seconds"
        if clip:
            desc += f"; highlighted passage {clip[0]:.2f} to {clip[1]:.2f} seconds"
        self.setAccessibleDescription(desc)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("white"))
        if self.pcm is None or not len(self.pcm):
            p.drawText(self.rect(), Qt.AlignCenter, "Load the examiner key and select a turn to see its waveform")
            return
        w, h = self.width(), self.height()
        dur = len(self.pcm) / self.rate
        if self.clip:
            x0, x1 = self.clip[0] / dur * w, self.clip[1] / dur * w
            p.fillRect(QRectF(x0, 0, max(2, x1 - x0), h), QColor("#FCE9C8"))
        bins = np.array_split(np.abs(self.pcm.astype(np.float32)), max(1, w))
        peak = max(1.0, float(np.abs(self.pcm).max()))
        p.setPen(QPen(QColor(theme.TEAL_DARK), 1))
        for x, b in enumerate(bins):
            v = (b.max() / peak if len(b) else 0) * (h / 2 - 4)
            p.drawLine(x, int(h / 2 - v), x, int(h / 2 + v))
        p.end()


class ExaminerWindow(QMainWindow):
    def __init__(self, player=None) -> None:
        super().__init__()
        self.player = player
        self.view: SubmissionView | None = None
        self.key_pem: bytes | None = None
        self.audio_cache: dict[str, tuple[np.ndarray, int]] = {}
        self.setWindowTitle("SwarLekhak - Examiner review")
        self.setWindowIcon(theme.app_icon())
        self.resize(1150, 760)
        root = QWidget()
        v = QVBoxLayout(root)
        top = QHBoxLayout()
        self.title = QLabel("No submission open")
        self.title.setObjectName("Title")
        top.addWidget(self.title, 1)
        for text, fn in (("Open submission...", self.open_dialog), ("Load examiner key...", self.load_key)):
            b = QPushButton(text)
            b.setAccessibleName(text)
            b.clicked.connect(fn)
            top.addWidget(b)
        v.addLayout(top)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Examiner review sections")
        self.integrity = QTextBrowser()
        self.integrity.setAccessibleName("Integrity status")
        self.answers = QTextBrowser()
        self.answers.setAccessibleName("Answers")
        self.history = QListWidget()
        self.history.setAccessibleName("Revision history")
        ev = QWidget()
        evl = QVBoxLayout(ev)
        split = QSplitter(Qt.Horizontal)
        self.turns = QListWidget()
        self.turns.setAccessibleName("Dictation turns and review markers")
        self.turns.currentItemChanged.connect(self._select)
        self.detail = QTextBrowser()
        self.detail.setAccessibleName("Selected evidence detail")
        split.addWidget(self.turns)
        split.addWidget(self.detail)
        evl.addWidget(split, 1)
        self.wave = Waveform()
        evl.addWidget(self.wave)
        pb = QHBoxLayout()
        for text, fn in (("Play highlighted passage", lambda: self._play(True)),
                         ("Play whole turn", lambda: self._play(False))):
            b = QPushButton(text)
            b.setAccessibleName(text)
            b.clicked.connect(fn)
            pb.addWidget(b)
        evl.addLayout(pb)
        self.tabs.addTab(self.integrity, "&Integrity")
        self.tabs.addTab(self.answers, "&Answers")
        self.tabs.addTab(ev, "&Evidence")
        self.tabs.addTab(self.history, "Revision &history")
        v.addWidget(self.tabs, 1)
        note = QLabel("Review markers are neutral prompts from an automatic comparison. They are not evidence of "
                      "misconduct, and agreement is not proof of identity.")
        note.setWordWrap(True)
        v.addWidget(note)
        self.setCentralWidget(root)

    def _audit(self, kind: str, **detail) -> None:
        line = json.dumps({"utc": datetime.now(timezone.utc).isoformat(), "kind": kind,
                           "submission": self.view.manifest.get("submission_id") if self.view else None, **detail})
        with open(data_dir() / "logs" / "examiner_audit.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open submission", "", "SwarLekhak submission (*.swarsub)")
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> None:
        try:
            self.view = open_submission(path)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot open", str(exc))
            return
        self.audio_cache.clear()
        self._audit("submission_opened", path=Path(path).name)
        m = self.view.manifest
        self.title.setText(f"{m['exam_id']} - submission {m['submission_id']}")
        rows = "".join(f"<tr><td><b>{'PASS' if ok else 'FAIL'}</b></td><td>{n}</td><td>{d}</td></tr>"
                       for n, ok, d in self.view.integrity)
        overall = "ALL CHECKS PASSED" if self.view.integrity_ok else "ONE OR MORE CHECKS FAILED"
        self.integrity.setHtml(
            f"<h3>{overall}</h3><p>Created {m['created_utc']} with SwarLekhak {m['app_version']}. "
            f"Turn sources: {', '.join(m.get('turn_sources', [])) or 'none'}. Audio: {m.get('audio')}.</p>"
            f"<table cellspacing=4>{rows}</table><p><i>{m.get('limitations', '')}</i></p>")
        html = []
        for a in self.view.answers["answers"]:
            html.append(f"<h3>Question {a['ordinal']}</h3><p><i>{a['question_text']}</i></p>")
            html.append(f"<p>Input sources: {', '.join(a.get('input_sources', [])) or 'none'}</p>")
            seq = interleave(a["text"], a.get("blocks", []))
            if not seq:
                html.append("<p>(no answer)</p>")
            for kind, item in seq:
                if kind == "text":
                    html.append(f"<p>{escape(item)}</p>")
                else:
                    title = BLOCK_TITLES.get(item["type"], item["type"])
                    html.append(f"<p><b>{title}:</b><br><span style='font-family:Consolas'>"
                                f"{escape(block_visual(item)).replace(chr(10), '<br>')}</span></p>")
        self.answers.setHtml("".join(html))
        self.history.clear()
        for r in self.view.revisions["revisions"]:
            self.history.addItem(f"#{r['seq']} {r['created_utc']}  {r['event_type']}  hash {r['hash'][:12]}...")
        self.turns.clear()
        reviews = self.view.verification.get("reviews", [])
        for t in self.view.verification.get("turns", []):
            it = QListWidgetItem(f"Turn {t['id'][-6:]} ({t['source']}, {t['status']})")
            it.setData(Qt.UserRole, {"turn": t, "review": None})
            self.turns.addItem(it)
            for r in (r for r in reviews if r["turn_id"] == t["id"]):
                ri = QListWidgetItem(f"    review [{r['status']}] {r['category']}: written '{r['written']}', "
                                     f"heard '{r['heard']}'")
                ri.setData(Qt.UserRole, {"turn": t, "review": r})
                self.turns.addItem(ri)

    def load_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Examiner private key", "", "Key (*.pem)")
        if path:
            self.key_pem = Path(path).read_bytes()
            self._select(self.turns.currentItem(), None)

    def _audio(self, entry_id: str):
        if entry_id in self.audio_cache:
            return self.audio_cache[entry_id]
        if not self.view or not self.key_pem:
            return None
        if f"encrypted_audio/{entry_id}.bin" not in self.view.files:
            return None
        wav = decrypt_audio_entry(self.view, entry_id, self.key_pem)
        self._audit("evidence_decrypted", entry=entry_id)
        self.audio_cache[entry_id] = read_wav(wav)
        return self.audio_cache[entry_id]

    def _entry_for(self, turn: dict, review: dict | None) -> tuple[str | None, tuple[float, float] | None]:
        """Which encrypted audio entry to show, and where its highlight is (selective disclosure aware)."""
        idx = audio_index(self.view) if self.view else []
        if any(e["kind"] == "turn" and e["turn_id"] == turn["id"] for e in idx):
            clip = (review["clip"]["start"], review["clip"]["end"]) if review else None
            return turn["id"], clip
        if review:
            for e in idx:
                if e["kind"] == "clip" and e.get("review_id") == review["id"]:
                    return e["id"], None   # the clip *is* the disclosed passage
        return None, None

    def _select(self, item, _prev) -> None:
        if not item:
            return
        d = item.data(Qt.UserRole)
        t, r = d["turn"], d["review"]
        lines = [f"<p>Turn {t['id']}<br>Source: {t['source']}<br>Status: {t['status']}<br>"
                 f"Revision before: {t['revision_before']}<br>Revision after: {t['revision_after']}</p>"]
        clip = None
        entry, highlight = self._entry_for(t, r)
        if r:
            clip = (r["clip"]["start"], r["clip"]["end"])
            lines.append(f"<p><b>Review ({r['status']})</b>: {r['category']}<br>Written: {r['written'] or '(nothing)'}"
                         f"<br>Local ASR heard: {r['heard'] or '(nothing)'}<br>Clip {clip[0]:.2f}-{clip[1]:.2f} s"
                         f"{' (approximate)' if r['clip'].get('approximate') else ''}<br>Methods: {r['methods']}</p>")
        audio = None
        if entry is None:
            lines.append(f"<p>No audio for this item was disclosed (policy: "
                         f"{self.view.manifest.get('audio_disclosure', 'full')}).</p>")
        else:
            try:
                audio = self._audio(entry)
            except Exception as exc:
                lines.append(f"<p><b>Audio could not be decrypted:</b> {exc}</p>")
            if audio is None and not self.key_pem:
                lines.append("<p>Load the examiner key to open the encrypted evidence audio.</p>")
        self.detail.setHtml("".join(lines))
        if audio:
            self.wave.set_audio(audio[0], audio[1], highlight)
        else:
            self.wave.set_audio(None, 16000)

    def _play(self, clip_only: bool) -> None:
        item = self.turns.currentItem()
        if not item or not self.player:
            return
        d = item.data(Qt.UserRole)
        entry, highlight = self._entry_for(d["turn"], d["review"])
        audio = self._audio(entry) if entry else None
        if not audio:
            return
        pcm, rate = audio
        if clip_only and highlight:
            pcm = pcm[int(highlight[0] * rate):int(highlight[1] * rate)]
        self._audit("evidence_played", turn_id=d["turn"]["id"], clip_only=clip_only)
        self.player.play(pcm, rate)
