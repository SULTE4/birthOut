import io
import zipfile

import pytest

from birthout.importers import WhatsAppImporter, WhatsAppParseError


def test_parses_android_export_and_multiline_message():
    payload = (
        "31/12/2025, 22:14 - Aida: My birthday is 4 March\n"
        "This continues on another line\n"
        "31/12/2025, 22:15 - Messages are end-to-end encrypted\n"
    ).encode()
    messages = WhatsAppImporter().parse(payload, filename="chat.txt")
    assert len(messages) == 2
    assert messages[0].sender == "Aida"
    assert messages[0].timestamp.month == 12
    assert "continues" in messages[0].text
    assert messages[1].is_system


def test_parses_ios_export_with_mdy_override():
    payload = b"[12/31/25, 10:14:02 PM] Sam: Birthday on 03/04\n"
    messages = WhatsAppImporter().parse(payload, filename="chat.txt", day_order="mdy")
    assert messages[0].timestamp.year == 2025
    assert messages[0].timestamp.hour == 22


def test_reads_text_from_zip():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("_chat.txt", "01.02.2025, 09:00 - Alex: hello")
    messages = WhatsAppImporter().parse(stream.getvalue(), filename="chat.zip")
    assert messages[0].sender == "Alex"


def test_rejects_unrecognized_text():
    with pytest.raises(WhatsAppParseError):
        WhatsAppImporter().parse(b"not a chat", filename="notes.txt")
