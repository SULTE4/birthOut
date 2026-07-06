from datetime import datetime

from birthout.extractors.rules import RuleBasedBirthdayExtractor
import pytest

from birthout.models import ChatMessage, NameSource


def message(index, sender, text, day=5):
    return ChatMessage(index, datetime(2025, 7, day, 12, index), sender, text)


def test_extracts_explicit_birthdays_in_three_languages():
    messages = [
        message(0, "Aida", "My birthday is March 4"),
        message(1, "Boris", "У Айды день рождения 5 мая"),
        message(2, "Dana", "Аружанның туған күні 12 маусым"),
    ]
    result = RuleBasedBirthdayExtractor().extract(messages)
    values = {(item.person, item.month, item.day) for item in result}
    assert ("Aida", 3, 4) in values
    assert ("Айды", 5, 5) in values
    assert ("Аружан", 6, 12) in values


def test_resolves_relative_date_using_message_date():
    result = RuleBasedBirthdayExtractor().extract([message(0, "Sam", "My birthday is tomorrow", day=31)])
    assert (result[0].month, result[0].day) == (8, 1)


def test_named_congratulations_require_two_distinct_senders():
    extractor = RuleBasedBirthdayExtractor()
    one = extractor.extract([message(0, "Sam", "Happy birthday, Aida!")])
    assert one == []
    two = extractor.extract([
        message(0, "Sam", "Happy birthday, Aida!"),
        message(1, "Dana", "Happy birthday Aida 🎂"),
    ])
    assert len(two) == 1
    assert two[0].person == "Aida"
    assert two[0].confidence < 0.85


def test_generic_congratulations_need_unique_thank_you_sender():
    result = RuleBasedBirthdayExtractor().extract([
        message(0, "Sam", "Happy birthday!"),
        message(1, "Dana", "С днём рождения!"),
        message(2, "Aida", "Спасибо всем!"),
    ])
    assert len(result) == 1
    assert result[0].person == "Aida"
    assert result[0].name_source == NameSource.PARTICIPANT_SENDER
    assert len(result[0].evidence) == 3


def test_thanks_before_second_congratulator_does_not_identify_birthday_person():
    result = RuleBasedBirthdayExtractor().extract([
        message(0, "Sam", "Happy birthday!"),
        message(1, "Aida", "Рахмет!"),
        message(2, "Dana", "С днём рождения!"),
    ])
    assert result == []


def test_multiple_thankers_after_congratulations_are_ambiguous():
    result = RuleBasedBirthdayExtractor().extract([
        message(0, "Sam", "Happy birthday!"),
        message(1, "Dana", "С днём рождения!"),
        message(2, "Aida", "Рахмет!"),
        message(3, "Boris", "Thanks!"),
    ])
    assert result == []


@pytest.mark.parametrize("thanks", ["Thank u all!", "Всем спасибо!", "Үлкен рақмет!", "Алғыс айтамын!"])
def test_supported_thank_you_variations(thanks):
    result = RuleBasedBirthdayExtractor().extract([
        message(0, "Sam", "Happy birthday!"),
        message(1, "Dana", "С днём рождения!"),
        message(2, "Aida Smith", thanks),
    ])
    assert result[0].person == "Aida Smith"


def test_responder_member_name_is_separate_from_congratulation_nickname():
    result = RuleBasedBirthdayExtractor().extract([
        message(0, "Sam", "Happy birthday, Sasha!"),
        message(1, "Dana", "Happy birthday Sasha!"),
        message(2, "Alexander Smith", "Thanks everyone!"),
    ])
    responder = next(item for item in result if item.person == "Alexander Smith")
    nickname = next(item for item in result if item.person == "Sasha")
    assert responder.name_source == NameSource.PARTICIPANT_SENDER
    assert len(responder.evidence) > len(nickname.evidence)


def test_responder_provenance_wins_when_same_name_candidates_merge():
    result = RuleBasedBirthdayExtractor().extract([
        message(0, "Sam", "Happy birthday, Aida!"),
        message(1, "Dana", "Happy birthday Aida!"),
        message(2, "Aida", "Рахмет баршаңызға!"),
    ])
    assert len(result) == 1
    assert result[0].person == "Aida"
    assert result[0].name_source == NameSource.PARTICIPANT_SENDER
    assert len(result[0].evidence) == 3
