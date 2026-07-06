from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class LeapPolicy(StrEnum):
    LEAP_ONLY = "leap_only"
    FEBRUARY_28 = "february_28"
    MARCH_1 = "march_1"


class NameSource(StrEnum):
    PARTICIPANT_SENDER = "participant_sender"
    MESSAGE_TEXT = "message_text"
    MERGED = "merged"


@dataclass(slots=True, frozen=True)
class ChatMessage:
    index: int
    timestamp: datetime
    sender: str | None
    text: str
    is_system: bool = False


@dataclass(slots=True, frozen=True)
class Evidence:
    message_index: int
    timestamp: datetime
    sender: str | None
    excerpt: str


@dataclass(slots=True)
class BirthdayCandidate:
    person: str
    month: int
    day: int
    year: int | None
    confidence: float
    reason: str
    evidence: list[Evidence] = field(default_factory=list)
    leap_policy: LeapPolicy | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "pending"
    aliases: list[str] = field(default_factory=list)
    merged_source_ids: list[str] = field(default_factory=list)
    merged_into: str | None = None
    name_source: NameSource = NameSource.MESSAGE_TEXT


@dataclass(slots=True, frozen=True)
class NameOption:
    value: str
    label: str


@dataclass(slots=True, frozen=True)
class MergeSuggestion:
    month: int
    day: int
    candidates: list[BirthdayCandidate]
    default_name: str
    name_options: list[NameOption] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class ApprovedBirthday:
    id: str
    display_name: str
    month: int
    day: int
    year: int | None
    leap_policy: LeapPolicy | None


@dataclass(slots=True)
class ImportSession:
    id: str
    filename: str
    import_hash: str
    messages: list[ChatMessage]
    candidates: list[BirthdayCandidate]
    message_count: int = 0
    approved_birthday_ids: list[str] = field(default_factory=list)
    participant_names: list[str] = field(default_factory=list)
    approval_snapshots: dict[str, dict | None] = field(default_factory=dict)
    approval_birthday_ids: dict[str, str] = field(default_factory=dict)
    synced_birthday_ids: set[str] = field(default_factory=set)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
