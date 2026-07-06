from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Iterable
from uuid import uuid4

from birthout.extractors.rules import normalize_person
from birthout.models import (
    BirthdayCandidate,
    Evidence,
    ImportSession,
    LeapPolicy,
    MergeSuggestion,
    NameOption,
    NameSource,
)


SESSION_STATE_VERSION = 1


class CandidateMergeError(ValueError):
    pass


class SessionStore:
    """Process-memory storage; raw chat data never reaches SQLite."""

    def __init__(self, sessions: Iterable[ImportSession] = ()):
        self._sessions: dict[str, ImportSession] = {session.id: session for session in sessions}

    def create(self, filename: str, import_hash: str, messages, candidates) -> ImportSession:
        participant_names = sorted(
            {message.sender.strip() for message in messages if message.sender and not message.is_system},
            key=str.casefold,
        )
        session = ImportSession(
            id=uuid4().hex,
            filename=filename,
            import_hash=import_hash,
            messages=messages,
            candidates=candidates,
            message_count=len(messages),
            participant_names=participant_names,
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> ImportSession | None:
        return self._sessions.get(session_id)

    def discard(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def all(self) -> list[ImportSession]:
        return sorted(self._sessions.values(), key=lambda item: item.updated_at, reverse=True)

    @staticmethod
    def merge_suggestions(session: ImportSession) -> list[MergeSuggestion]:
        by_date: dict[tuple[int, int], list[BirthdayCandidate]] = defaultdict(list)
        for candidate in session.candidates:
            if candidate.status == "pending" and candidate.merged_into is None:
                by_date[(candidate.month, candidate.day)].append(candidate)

        suggestions: list[MergeSuggestion] = []
        for (month, day), candidates in sorted(by_date.items()):
            names = {normalize_person(item.person) for item in candidates}
            explicit_years = {item.year for item in candidates if item.year is not None}
            if len(candidates) < 2 or len(names) < 2 or len(explicit_years) > 1:
                continue
            candidates.sort(key=SessionStore._candidate_sort_key)
            participant_candidates = [
                item for item in candidates if item.name_source == NameSource.PARTICIPANT_SENDER
            ]
            default = (participant_candidates or candidates)[0]
            options = SessionStore._name_options(session, candidates)
            suggestions.append(MergeSuggestion(month, day, candidates, default.person, options))
        return suggestions

    @staticmethod
    def active_candidates(session: ImportSession) -> list[BirthdayCandidate]:
        return sorted(
            (item for item in session.candidates if item.status != "merged"),
            key=SessionStore._candidate_sort_key,
        )

    @staticmethod
    def candidate_name_options(session: ImportSession) -> dict[str, list[NameOption]]:
        return {
            candidate.id: SessionStore._name_options(session, [candidate])
            for candidate in SessionStore.active_candidates(session)
        }

    @staticmethod
    def _candidate_sort_key(candidate: BirthdayCandidate) -> tuple:
        reviewed_rank = 0 if candidate.status == "pending" else 1
        return (reviewed_rank, -len(candidate.evidence), -candidate.confidence, candidate.person.casefold())

    @staticmethod
    def _name_options(session: ImportSession, candidates: list[BirthdayCandidate]) -> list[NameOption]:
        options: list[NameOption] = []
        seen = set()

        def add(value: str, label: str) -> None:
            key = normalize_person(value)
            if key and key not in seen:
                options.append(NameOption(value, label))
                seen.add(key)

        for candidate in candidates:
            source_label = (
                "Group member responder"
                if candidate.name_source == NameSource.PARTICIPANT_SENDER
                else ("Merged canonical name" if candidate.name_source == NameSource.MERGED else "Detected name")
            )
            add(candidate.person, source_label)
        for candidate in candidates:
            for alias in candidate.aliases:
                add(alias, "Detected alias")
        for participant in session.participant_names:
            add(participant, "Group member")
        return options

    @staticmethod
    def merge_candidates(
        session: ImportSession, candidate_ids: list[str], canonical_name: str
    ) -> BirthdayCandidate:
        canonical_name = canonical_name.strip()
        unique_ids = list(dict.fromkeys(candidate_ids))
        if not canonical_name:
            raise CandidateMergeError("Enter a canonical calendar name.")
        if len(unique_ids) < 2:
            raise CandidateMergeError("Select at least two candidates to merge.")

        selected = [item for item in session.candidates if item.id in unique_ids]
        if len(selected) != len(unique_ids):
            raise CandidateMergeError("One or more selected candidates no longer exist.")
        if any(item.status != "pending" or item.merged_into is not None for item in selected):
            raise CandidateMergeError("Only pending, active candidates can be merged.")
        dates = {(item.month, item.day) for item in selected}
        if len(dates) != 1:
            raise CandidateMergeError("Only candidates with the same month and day can be merged.")
        years = {item.year for item in selected if item.year is not None}
        if len(years) > 1:
            raise CandidateMergeError("These candidates have conflicting explicit birth years and cannot be merged.")

        evidence = []
        evidence_indices = set()
        reasons = []
        aliases = []
        alias_keys = set()
        canonical_key = normalize_person(canonical_name)
        for candidate in selected:
            for alias in [candidate.person, *candidate.aliases]:
                key = normalize_person(alias)
                if key and key != canonical_key and key not in alias_keys:
                    aliases.append(alias)
                    alias_keys.add(key)
            if candidate.reason not in reasons:
                reasons.append(candidate.reason)
            for item in candidate.evidence:
                if item.message_index not in evidence_indices:
                    evidence.append(item)
                    evidence_indices.add(item.message_index)

        month, day = next(iter(dates))
        composite = BirthdayCandidate(
            person=canonical_name,
            month=month,
            day=day,
            year=next(iter(years), None),
            confidence=max(item.confidence for item in selected),
            reason="Merged aliases: " + ", ".join(item.person for item in selected) + "; " + "; ".join(reasons),
            evidence=sorted(evidence, key=lambda item: item.message_index),
            aliases=aliases,
            merged_source_ids=[item.id for item in selected],
            name_source=(
                NameSource.PARTICIPANT_SENDER
                if canonical_key in {normalize_person(name) for name in session.participant_names}
                else NameSource.MERGED
            ),
        )
        for candidate in selected:
            candidate.status = "merged"
            candidate.merged_into = composite.id
        session.candidates.append(composite)
        return composite

    @staticmethod
    def undo_merge(session: ImportSession, composite_id: str) -> None:
        composite = next((item for item in session.candidates if item.id == composite_id), None)
        if not composite or not composite.merged_source_ids:
            raise CandidateMergeError("Merged candidate not found.")
        if composite.status != "pending":
            raise CandidateMergeError("Only a pending merge can be undone.")
        sources = [item for item in session.candidates if item.id in composite.merged_source_ids]
        if len(sources) != len(composite.merged_source_ids):
            raise CandidateMergeError("The original candidates are no longer available.")
        for source in sources:
            source.status = "pending"
            source.merged_into = None
        session.candidates.remove(composite)


def serialize_session(session: ImportSession) -> str:
    """Serialize review state without persisting the imported raw chat."""
    payload = {
        "version": SESSION_STATE_VERSION,
        "id": session.id,
        "filename": session.filename,
        "import_hash": session.import_hash,
        "message_count": session.message_count or len(session.messages),
        "participant_names": session.participant_names,
        "approved_birthday_ids": session.approved_birthday_ids,
        "approval_snapshots": session.approval_snapshots,
        "approval_birthday_ids": session.approval_birthday_ids,
        "synced_birthday_ids": sorted(session.synced_birthday_ids),
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "candidates": [_serialize_candidate(candidate) for candidate in session.candidates],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def deserialize_session(state_json: str) -> ImportSession:
    try:
        payload = json.loads(state_json)
        if payload.get("version") != SESSION_STATE_VERSION:
            raise ValueError("Unsupported review session version")
        candidates = [_deserialize_candidate(item) for item in payload["candidates"]]
        return ImportSession(
            id=str(payload["id"]),
            filename=str(payload["filename"]),
            import_hash=str(payload["import_hash"]),
            messages=[],
            candidates=candidates,
            message_count=int(payload["message_count"]),
            approved_birthday_ids=[str(item) for item in payload.get("approved_birthday_ids", [])],
            participant_names=[str(item) for item in payload.get("participant_names", [])],
            approval_snapshots=dict(payload.get("approval_snapshots", {})),
            approval_birthday_ids={
                str(key): str(value) for key, value in payload.get("approval_birthday_ids", {}).items()
            },
            synced_birthday_ids={str(item) for item in payload.get("synced_birthday_ids", [])},
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid saved review session") from exc


def _serialize_candidate(candidate: BirthdayCandidate) -> dict:
    return {
        "id": candidate.id,
        "person": candidate.person,
        "month": candidate.month,
        "day": candidate.day,
        "year": candidate.year,
        "confidence": candidate.confidence,
        "reason": candidate.reason,
        "evidence": [
            {
                "message_index": item.message_index,
                "timestamp": item.timestamp.isoformat(),
                "sender": item.sender,
                "excerpt": item.excerpt,
            }
            for item in candidate.evidence
        ],
        "leap_policy": candidate.leap_policy.value if candidate.leap_policy else None,
        "status": candidate.status,
        "aliases": candidate.aliases,
        "merged_source_ids": candidate.merged_source_ids,
        "merged_into": candidate.merged_into,
        "name_source": candidate.name_source.value,
    }


def _deserialize_candidate(payload: dict) -> BirthdayCandidate:
    status = str(payload.get("status", "pending"))
    if status not in {"pending", "approved", "rejected", "merged"}:
        raise ValueError("Invalid candidate status")
    return BirthdayCandidate(
        id=str(payload["id"]),
        person=str(payload["person"]),
        month=int(payload["month"]),
        day=int(payload["day"]),
        year=int(payload["year"]) if payload.get("year") is not None else None,
        confidence=float(payload["confidence"]),
        reason=str(payload["reason"]),
        evidence=[
            Evidence(
                message_index=int(item["message_index"]),
                timestamp=datetime.fromisoformat(item["timestamp"]),
                sender=str(item["sender"]) if item.get("sender") is not None else None,
                excerpt=str(item["excerpt"]),
            )
            for item in payload.get("evidence", [])
        ],
        leap_policy=LeapPolicy(payload["leap_policy"]) if payload.get("leap_policy") else None,
        status=status,
        aliases=[str(item) for item in payload.get("aliases", [])],
        merged_source_ids=[str(item) for item in payload.get("merged_source_ids", [])],
        merged_into=str(payload["merged_into"]) if payload.get("merged_into") else None,
        name_source=NameSource(payload.get("name_source", NameSource.MESSAGE_TEXT.value)),
    )
