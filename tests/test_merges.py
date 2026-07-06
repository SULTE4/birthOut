from datetime import datetime

import pytest

from birthout.models import BirthdayCandidate, ChatMessage, Evidence, ImportSession, NameSource
from birthout.sessions import CandidateMergeError, SessionStore


def candidate(person, month=3, day=4, year=None, confidence=0.8, index=0):
    return BirthdayCandidate(
        person=person,
        month=month,
        day=day,
        year=year,
        confidence=confidence,
        reason=f"Evidence for {person}",
        evidence=[Evidence(index, datetime(2025, 3, 4, 12, index), "Sender", f"{person} birthday")],
    )


def session(*candidates):
    return ImportSession("session", "chat.txt", "hash", [], list(candidates))


def test_same_day_names_are_suggested_but_not_merged():
    aida = candidate("Aida", confidence=0.7)
    nickname = candidate("Aika", confidence=0.9, index=1)
    current = session(aida, nickname)

    suggestions = SessionStore.merge_suggestions(current)

    assert len(suggestions) == 1
    assert suggestions[0].default_name == "Aika"
    assert all(item.status == "pending" for item in current.candidates)


def test_different_dates_and_conflicting_years_are_not_suggested():
    assert SessionStore.merge_suggestions(session(candidate("Aida"), candidate("Aika", day=5))) == []
    conflicting = session(candidate("Aida", year=1990), candidate("Aika", year=1991, index=1))
    assert SessionStore.merge_suggestions(conflicting) == []
    with pytest.raises(CandidateMergeError, match="conflicting"):
        SessionStore.merge_candidates(conflicting, [item.id for item in conflicting.candidates], "Aida")


def test_merge_combines_aliases_evidence_and_can_be_undone():
    first = candidate("Alexander", year=1990, confidence=0.7)
    second = candidate("Sasha", confidence=0.9, index=1)
    current = session(first, second)

    composite = SessionStore.merge_candidates(current, [first.id, second.id], "Alexander Smith")

    assert composite.person == "Alexander Smith"
    assert composite.aliases == ["Alexander", "Sasha"]
    assert composite.year == 1990
    assert composite.confidence == 0.9
    assert len(composite.evidence) == 2
    assert first.status == second.status == "merged"
    assert SessionStore.merge_suggestions(current) == []

    SessionStore.undo_merge(current, composite.id)
    assert composite not in current.candidates
    assert first.status == second.status == "pending"
    assert first.merged_into is second.merged_into is None


def test_merge_requires_same_day_and_two_pending_candidates():
    first = candidate("Aida")
    second = candidate("Aika", day=5, index=1)
    current = session(first, second)
    with pytest.raises(CandidateMergeError, match="same month and day"):
        SessionStore.merge_candidates(current, [first.id, second.id], "Aida")
    with pytest.raises(CandidateMergeError, match="at least two"):
        SessionStore.merge_candidates(current, [first.id], "Aida")


def test_candidates_sort_by_evidence_then_confidence():
    low = candidate("Low", confidence=0.5, index=0)
    high_confidence = candidate("High confidence", confidence=0.9, index=1)
    more_evidence = candidate("More evidence", confidence=0.6, index=2)
    more_evidence.evidence.append(Evidence(3, datetime(2025, 3, 4, 12, 3), "Other", "More"))
    ordered = SessionStore.active_candidates(session(low, high_confidence, more_evidence))
    assert [item.person for item in ordered] == ["More evidence", "High confidence", "Low"]


def test_approved_and_rejected_candidates_sort_below_pending():
    approved = candidate("Approved", confidence=0.99, index=0)
    approved.evidence.append(Evidence(4, datetime(2025, 3, 4, 14), "Other", "Extra"))
    approved.status = "approved"
    rejected = candidate("Rejected", confidence=0.95, index=1)
    rejected.status = "rejected"
    pending = candidate("Pending", confidence=0.4, index=2)
    ordered = SessionStore.active_candidates(session(approved, rejected, pending))
    assert ordered[0] is pending
    assert {item.status for item in ordered[1:]} == {"approved", "rejected"}


def test_name_options_include_every_group_member_and_remain_editable_values():
    messages = [
        ChatMessage(0, datetime(2025, 3, 4, 10), "Alexander Smith", "Hello"),
        ChatMessage(1, datetime(2025, 3, 4, 11), "Dana", "Hello"),
    ]
    current = SessionStore().create("chat.txt", "hash", messages, [candidate("Sasha")])
    options = SessionStore.candidate_name_options(current)[current.candidates[0].id]
    assert [(item.value, item.label) for item in options] == [
        ("Sasha", "Detected name"),
        ("Alexander Smith", "Group member"),
        ("Dana", "Group member"),
    ]


def test_merge_default_prefers_participant_sender_name():
    nickname = candidate("Sasha", confidence=0.99)
    nickname.evidence.append(Evidence(4, datetime(2025, 3, 4, 13), "Sam", "More evidence"))
    responder = candidate("Alexander Smith", confidence=0.8, index=1)
    responder.name_source = NameSource.PARTICIPANT_SENDER
    current = session(nickname, responder)
    suggestion = SessionStore.merge_suggestions(current)[0]
    assert suggestion.default_name == "Alexander Smith"
