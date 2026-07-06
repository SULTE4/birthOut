from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime

from birthout.models import ChatMessage


class WhatsAppParseError(ValueError):
    pass


_ANDROID = re.compile(
    r"^(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\s+[-–—]\s+(?P<body>.*)$"
)
_IOS = re.compile(
    r"^\[(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\]\s+(?P<body>.*)$"
)
_SENDER = re.compile(r"^(?P<sender>[^:\n]{1,120}):\s?(?P<text>[\s\S]*)$")
_DIRECTION_MARKS = str.maketrans("", "", "\u200e\u200f\u202a\u202b\u202c\u202d\u202e")


class WhatsAppImporter:
    """Parser for common WhatsApp Android and iOS text exports."""

    def parse(self, payload: bytes, *, filename: str, day_order: str = "auto") -> list[ChatMessage]:
        if len(payload) > 100 * 1024 * 1024:
            raise WhatsAppParseError("The import is larger than the 100 MB safety limit.")
        raw = self._extract_text(payload, filename)
        text = self._decode(raw).translate(_DIRECTION_MARKS)
        records = self._split_records(text)
        if not records:
            raise WhatsAppParseError("No WhatsApp messages were recognized in this file.")
        resolved_order = self._detect_day_order(records) if day_order == "auto" else day_order
        if resolved_order not in {"dmy", "mdy", "ymd"}:
            raise WhatsAppParseError("Date order must be auto, dmy, mdy, or ymd.")

        messages: list[ChatMessage] = []
        for index, (date_text, time_text, body) in enumerate(records):
            timestamp = self._parse_timestamp(date_text, time_text, resolved_order)
            sender_match = _SENDER.match(body)
            if sender_match:
                sender = sender_match.group("sender").strip()
                message_text = sender_match.group("text").strip()
                is_system = False
            else:
                sender = None
                message_text = body.strip()
                is_system = True
            messages.append(ChatMessage(index, timestamp, sender, message_text, is_system))
        return messages

    @staticmethod
    def _extract_text(payload: bytes, filename: str) -> bytes:
        if filename.lower().endswith(".zip") or payload.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    text_files = [n for n in archive.namelist() if n.lower().endswith(".txt") and not n.endswith("/")]
                    if not text_files:
                        raise WhatsAppParseError("The ZIP archive does not contain a text export.")
                    preferred = next((n for n in text_files if "chat" in n.lower()), text_files[0])
                    return archive.read(preferred)
            except zipfile.BadZipFile as exc:
                raise WhatsAppParseError("The uploaded ZIP archive is invalid.") from exc
        return payload

    @staticmethod
    def _decode(payload: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-16", "cp1251"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise WhatsAppParseError("The text encoding is not supported.")

    @staticmethod
    def _split_records(text: str) -> list[tuple[str, str, str]]:
        records: list[list[str]] = []
        for line in text.splitlines():
            match = _ANDROID.match(line) or _IOS.match(line)
            if match:
                records.append([match.group("date"), match.group("time"), match.group("body")])
            elif records:
                records[-1][2] += "\n" + line
        return [(date, time, body) for date, time, body in records]

    @staticmethod
    def _detect_day_order(records: list[tuple[str, str, str]]) -> str:
        for date_text, _, _ in records:
            parts = [int(value) for value in re.split(r"[./-]", date_text)]
            if parts[0] > 31:
                return "ymd"
            if parts[0] > 12:
                return "dmy"
            if parts[1] > 12:
                return "mdy"
        # WhatsApp exports follow the phone locale; DMY is the safer default for
        # Kazakhstan and most non-US locales. The UI exposes an explicit override.
        return "dmy"

    @staticmethod
    def _parse_timestamp(date_text: str, time_text: str, order: str) -> datetime:
        parts = [int(value) for value in re.split(r"[./-]", date_text)]
        mapping = dict(zip(order, parts, strict=True))
        year = mapping["y"]
        if year < 100:
            year += 2000 if year < 70 else 1900
        normalized_time = re.sub(r"\s+", " ", time_text.strip().upper())
        formats = ["%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"]
        parsed_time = None
        for fmt in formats:
            try:
                parsed_time = datetime.strptime(normalized_time, fmt).time()
                break
            except ValueError:
                continue
        if parsed_time is None:
            raise WhatsAppParseError(f"Unsupported message time: {time_text}")
        try:
            return datetime(year, mapping["m"], mapping["d"], parsed_time.hour, parsed_time.minute, parsed_time.second)
        except ValueError as exc:
            raise WhatsAppParseError(f"Invalid message timestamp: {date_text} {time_text}") from exc
