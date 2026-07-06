from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import timedelta

from birthout.models import BirthdayCandidate, ChatMessage, Evidence, NameSource


_MONTHS: dict[str, int] = {}
for number, names in {
    1: ("january", "jan", "январь", "января", "қаңтар", "қаңтарда"),
    2: ("february", "feb", "февраль", "февраля", "ақпан", "ақпанда"),
    3: ("march", "mar", "март", "марта", "наурыз", "наурызда"),
    4: ("april", "apr", "апрель", "апреля", "сәуір", "сәуірде"),
    5: ("may", "май", "мая", "мамыр", "мамырда"),
    6: ("june", "jun", "июнь", "июня", "маусым", "маусымда"),
    7: ("july", "jul", "июль", "июля", "шілде", "шілдеде"),
    8: ("august", "aug", "август", "августа", "тамыз", "тамызда"),
    9: ("september", "sep", "sept", "сентябрь", "сентября", "қыркүйек", "қыркүйекте"),
    10: ("october", "oct", "октябрь", "октября", "қазан", "қазанда"),
    11: ("november", "nov", "ноябрь", "ноября", "қараша", "қарашада"),
    12: ("december", "dec", "декабрь", "декабря", "желтоқсан", "желтоқсанда"),
}.items():
    for name in names:
        _MONTHS[name] = number

_MONTH_PATTERN = "|".join(sorted(map(re.escape, _MONTHS), key=len, reverse=True))
_BIRTHDAY = re.compile(r"\b(?:birthday|bday|день\s+рождения|д[.]?р[.]?|туған\s+күн(?:і|ім|ің)?)\b", re.I)
_CONGRATS = re.compile(
    r"(?:happy\s+(?:birthday|bday)|с\s+дн[её]м\s+рождения|туған\s+күніңмен|туған\s+күнің\s+құтты\s+болсын)",
    re.I,
)
_THANKS = re.compile(
    r"\b(?:thank\s+(?:you|u)|thanks|thx|спасибо|благодарю|спс|рақмет|рахмет|алғыс)\b",
    re.I,
)
_SELF = re.compile(
    r"(?:\bmy\s+(?:birthday|bday)\b|\bмой\s+день\s+рождения\b|\bу\s+меня\s+(?:день\s+рождения|д[.]?р[.]?)\b|менің\s+туған\s+күн(?:ім|і))",
    re.I,
)
_RELATIVE = {
    "today": 0,
    "сегодня": 0,
    "бүгін": 0,
    "tomorrow": 1,
    "завтра": 1,
    "ертең": 1,
    "yesterday": -1,
    "вчера": -1,
    "кеше": -1,
}
_NAME_CHARS = r"A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі0-9@+._'’ -"


def normalize_person(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


class RuleBasedBirthdayExtractor:
    def extract(self, messages: list[ChatMessage]) -> list[BirthdayCandidate]:
        candidates: list[BirthdayCandidate] = []
        congratulations: dict[tuple[str, object], list[ChatMessage]] = defaultdict(list)
        daily_congratulations: dict[object, list[ChatMessage]] = defaultdict(list)

        for message in messages:
            if message.is_system or not message.sender:
                continue
            if _BIRTHDAY.search(message.text):
                date = self._extract_date(message)
                if date:
                    person = self._extract_person(message)
                    if person:
                        month, day, year, relative = date
                        candidates.append(
                            BirthdayCandidate(
                                person=person,
                                month=month,
                                day=day,
                                year=year,
                                confidence=0.90 if relative else 0.96,
                                reason="Explicit birthday statement" + (" with a relative date" if relative else ""),
                                evidence=[self._evidence(message)],
                                name_source=(
                                    NameSource.PARTICIPANT_SENDER
                                    if normalize_person(person) == normalize_person(message.sender) and _SELF.search(message.text)
                                    else NameSource.MESSAGE_TEXT
                                ),
                            )
                        )

            if _CONGRATS.search(message.text):
                target = self._congratulated_person(message.text)
                day_key = message.timestamp.date()
                daily_congratulations[day_key].append(message)
                if target:
                    congratulations[(normalize_person(target), day_key)].append(message)

        candidates.extend(self._congratulation_candidates(congratulations))
        candidates.extend(self._thanks_candidates(daily_congratulations, messages))
        return self._merge(candidates)

    def _extract_date(self, message: ChatMessage) -> tuple[int, int, int | None, bool] | None:
        text = message.text.casefold().replace("ё", "е")
        numeric = re.search(r"(?<!\d)(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?(?!\d)", text)
        if numeric:
            day, month = int(numeric.group(1)), int(numeric.group(2))
            year = self._year(numeric.group(3))
            if self._valid(month, day, year):
                return month, day, year, False

        day_first = re.search(
            rf"(?<!\d)(\d{{1,2}})(?:-?(?:st|nd|rd|th|і|ы|ші|шы))?\s+(?:of\s+)?({_MONTH_PATTERN})\b(?:[,]?\s+(\d{{4}}))?",
            text,
            re.I,
        )
        month_first = re.search(
            rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th|і|ы|ші|шы)?\b(?:[,]?\s+(\d{{4}}))?",
            text,
            re.I,
        )
        if day_first:
            day, month, year = int(day_first.group(1)), _MONTHS[day_first.group(2).casefold()], self._year(day_first.group(3))
            if self._valid(month, day, year):
                return month, day, year, False
        if month_first:
            month, day, year = _MONTHS[month_first.group(1).casefold()], int(month_first.group(2)), self._year(month_first.group(3))
            if self._valid(month, day, year):
                return month, day, year, False

        for word, offset in _RELATIVE.items():
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text):
                resolved = message.timestamp.date() + timedelta(days=offset)
                return resolved.month, resolved.day, None, True
        return None

    @staticmethod
    def _year(value: str | None) -> int | None:
        if value is None:
            return None
        year = int(value)
        if year < 100:
            year += 2000 if year < 30 else 1900
        return year

    @staticmethod
    def _valid(month: int, day: int, year: int | None) -> bool:
        from datetime import date

        try:
            date(year or 2000, month, day)
            return year is None or 1900 <= year <= date.today().year
        except ValueError:
            return False

    def _extract_person(self, message: ChatMessage) -> str | None:
        text = message.text.strip()
        if _SELF.search(text):
            return message.sender

        patterns = [
            rf"(?P<name>[{_NAME_CHARS}]{{2,60}}?)[’']s\s+(?:birthday|bday)\b",
            rf"(?:birthday|bday)\s+(?:of|for)\s+(?P<name>[{_NAME_CHARS}]{{2,60}})",
            rf"\bу\s+(?P<name>[{_NAME_CHARS}]{{2,60}}?)\s+(?:день\s+рождения|д[.]?р[.]?)\b",
            rf"(?:день\s+рождения|д[.]?р[.]?)\s+(?:у\s+)?(?P<name>[{_NAME_CHARS}]{{2,60}})",
            rf"(?P<name>[{_NAME_CHARS}]{{2,60}}?)\s*(?:ның|нің|дың|дің|тың|тің)?\s+туған\s+күн(?:і|і ме|і екен)?\b",
            rf"(?P<name>[{_NAME_CHARS}]{{2,60}}?)\s+(?:birthday|bday)\s+(?:is|was)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                name = self._clean_name(match.group("name"))
                if name:
                    return name
        mention = re.search(r"@([\w.+-]{2,40})", text, re.UNICODE)
        return mention.group(1) if mention else None

    def _congratulated_person(self, text: str) -> str | None:
        mention = re.search(r"@([\w.+-]{2,40})", text, re.UNICODE)
        if mention:
            return mention.group(1)
        phrase = _CONGRATS.search(text)
        if not phrase:
            return None
        before = self._clean_name(text[max(0, phrase.start() - 45) : phrase.start()])
        after = self._clean_name(text[phrase.end() : phrase.end() + 45])
        # A name before the phrase is normally vocative: "Aida, happy birthday".
        if before and len(before.split()) <= 4:
            return before
        if after and len(after.split()) <= 4:
            return after
        return None

    @staticmethod
    def _clean_name(value: str) -> str | None:
        value = value.replace("\n", " ").strip(" ,.!?:;—–-()[]{}🎂🥳🎉❤️❤")
        value = re.sub(r"^(?:to|dear|дорогая|дорогой)\s+", "", value, flags=re.I)
        value = re.split(r"[,!?.;:\n]|\s+(?:is|was|on|today|tomorrow|сегодня|завтра)\b", value, maxsplit=1, flags=re.I)[0]
        value = " ".join(value.split()).strip()
        if not value or len(value) > 60 or value.casefold() in {"to", "тебя", "вас", "you"}:
            return None
        return value.lstrip("@")

    def _congratulation_candidates(self, groups: dict[tuple[str, object], list[ChatMessage]]) -> list[BirthdayCandidate]:
        result: list[BirthdayCandidate] = []
        for (_, day), messages in groups.items():
            senders = {normalize_person(message.sender or "") for message in messages}
            if len(senders) < 2:
                continue
            person = self._congratulated_person(messages[0].text)
            if not person:
                continue
            result.append(
                BirthdayCandidate(
                    person=person,
                    month=day.month,
                    day=day.day,
                    year=None,
                    confidence=0.78,
                    reason=f"Named birthday congratulations from {len(senders)} people",
                    evidence=[self._evidence(message) for message in messages[:5]],
                )
            )
        return result

    def _thanks_candidates(self, groups: dict[object, list[ChatMessage]], all_messages: list[ChatMessage]) -> list[BirthdayCandidate]:
        result: list[BirthdayCandidate] = []
        by_day: dict[object, list[ChatMessage]] = defaultdict(list)
        for message in all_messages:
            if message.sender and not message.is_system:
                by_day[message.timestamp.date()].append(message)
        for day, congrats in groups.items():
            ordered_congrats = sorted(congrats, key=lambda item: item.index)
            all_congratulators = {normalize_person(message.sender or "") for message in ordered_congrats}
            congratulators = set()
            threshold_index = None
            for message in ordered_congrats:
                congratulators.add(normalize_person(message.sender or ""))
                if len(congratulators) >= 2:
                    threshold_index = message.index
                    break
            if threshold_index is None:
                continue

            messages_after_threshold = [
                message for message in by_day[day]
                if message.index > threshold_index
                and normalize_person(message.sender or "") not in all_congratulators
                and _THANKS.search(message.text)
            ]
            thankers: dict[str, str] = {}
            for message in messages_after_threshold:
                thankers.setdefault(normalize_person(message.sender or ""), message.sender or "")
            if len(thankers) != 1:
                continue
            normalized_person, person = next(iter(thankers.items()))
            thank_you_messages = [
                message for message in messages_after_threshold
                if normalize_person(message.sender or "") == normalized_person
            ]
            first_thanks_index = thank_you_messages[0].index
            preceding_congrats = [message for message in ordered_congrats if message.index < first_thanks_index]
            preceding_senders = {normalize_person(message.sender or "") for message in preceding_congrats}
            if len(preceding_senders) < 2:
                continue
            evidence_messages = preceding_congrats + thank_you_messages[:3]
            result.append(
                BirthdayCandidate(
                    person=person,
                    month=day.month,
                    day=day.day,
                    year=None,
                    confidence=0.86,
                    reason=f"Group member replied with thanks after congratulations from {len(preceding_senders)} people",
                    evidence=[self._evidence(message) for message in evidence_messages],
                    name_source=NameSource.PARTICIPANT_SENDER,
                )
            )
        return result

    @staticmethod
    def _evidence(message: ChatMessage) -> Evidence:
        excerpt = message.text if len(message.text) <= 240 else message.text[:237] + "..."
        return Evidence(message.index, message.timestamp, message.sender, excerpt)

    @staticmethod
    def _merge(candidates: list[BirthdayCandidate]) -> list[BirthdayCandidate]:
        merged: dict[tuple[str, int, int], BirthdayCandidate] = {}
        for candidate in candidates:
            key = (normalize_person(candidate.person), candidate.month, candidate.day)
            existing = merged.get(key)
            if not existing:
                merged[key] = candidate
                continue
            existing.confidence = max(existing.confidence, candidate.confidence)
            existing.year = existing.year or candidate.year
            existing.evidence.extend(e for e in candidate.evidence if e.message_index not in {x.message_index for x in existing.evidence})
            existing.evidence.sort(key=lambda item: item.message_index)
            if candidate.name_source == NameSource.PARTICIPANT_SENDER:
                existing.person = candidate.person
                existing.name_source = NameSource.PARTICIPANT_SENDER
            if candidate.reason not in existing.reason:
                existing.reason += "; " + candidate.reason
        return sorted(merged.values(), key=lambda item: (item.month, item.day, item.person.casefold()))
