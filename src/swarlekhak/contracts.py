"""Versioned typed contracts shared between services (no REST, no Qt)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

CONTRACT_VERSION = "1.0"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    STRONG = "strong_correspondence"
    CHECK = "check_passage"
    UNAVAILABLE = "verification_unavailable"
    CONFIRMED = "student_confirmed"
    CORRECTED = "student_corrected"

    @property
    def unresolved(self) -> bool:
        return self in (ReviewStatus.PENDING, ReviewStatus.CHECK)


class TurnSource(str, Enum):
    FLOW_LIVE = "flow_live"          # real Wispr Flow insertion observed in our editor
    FIXTURE = "fixture"              # labelled demo fixture (prerecorded audio + scripted text)
    TYPED = "typed"                  # keyboard input


@dataclass(frozen=True)
class Command:
    intent: str
    args: dict[str, Any] = field(default_factory=dict)
    context_id: str | None = None
    allowed_states: frozenset[str] = frozenset()
    confirmation_required: bool = False
    source: str = "voice"            # voice | keyboard
    raw_text: str = ""
    version: str = CONTRACT_VERSION


@dataclass
class DictationTurn:
    id: str
    source: TurnSource
    answer_id: str
    start_mono: float
    end_mono: float | None = None
    start_sample: int = 0
    end_sample: int | None = None
    revision_before: str | None = None
    revision_after: str | None = None
    text_before: str = ""
    version: str = CONTRACT_VERSION


@dataclass
class ReviewEvent:
    id: str
    revision_id: str
    turn_id: str
    category: str                    # numeric | negation | unit | operator | term
    text_span: tuple[int, int]       # char offsets in the answer text at revision_id
    written: str
    heard: str
    audio_interval: tuple[float, float]
    approximate: bool
    methods: dict[str, str]
    scores: dict[str, float]
    status: ReviewStatus = ReviewStatus.CHECK
    version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class TaskResult:
    ok: bool
    code: str
    spoken_message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    version: str = CONTRACT_VERSION
