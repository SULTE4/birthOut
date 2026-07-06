from birthout.models import LeapPolicy
from birthout.storage import Storage


def test_upsert_is_idempotent_by_normalized_name(tmp_path):
    storage = Storage(tmp_path / "birthout.sqlite3")
    first = storage.upsert_birthday(
        display_name="Aida", month=3, day=4, year=None, leap_policy=None, source_hash="one"
    )
    second = storage.upsert_birthday(
        display_name="  AIDA ", month=3, day=5, year=1995, leap_policy=None, source_hash="two"
    )
    assert first.id == second.id
    saved = storage.list_birthdays()
    assert len(saved) == 1
    assert (saved[0].day, saved[0].year) == (5, 1995)


def test_database_does_not_have_raw_message_columns(tmp_path):
    storage = Storage(tmp_path / "birthout.sqlite3")
    with storage.connect() as db:
        tables = db.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall()
    schema = " ".join(row[0] for row in tables).casefold()
    assert "message_text" not in schema
    assert "excerpt" not in schema


def test_restore_removes_new_approval(tmp_path):
    storage = Storage(tmp_path / "birthout.sqlite3")
    snapshot = storage.snapshot_birthday("Aida")
    created = storage.upsert_birthday(
        display_name="Aida", month=3, day=4, year=None, leap_policy=None, source_hash="new"
    )
    storage.restore_birthday(created.id, snapshot)
    assert storage.list_birthdays() == []


def test_restore_reinstates_previous_birthday(tmp_path):
    storage = Storage(tmp_path / "birthout.sqlite3")
    original = storage.upsert_birthday(
        display_name="Aida", month=3, day=4, year=1990, leap_policy=None, source_hash="old"
    )
    snapshot = storage.snapshot_birthday("Aida")
    updated = storage.upsert_birthday(
        display_name="Aida", month=5, day=6, year=1991, leap_policy=None, source_hash="new"
    )
    storage.restore_birthday(updated.id, snapshot)
    restored = storage.get_birthday(original.id)
    assert (restored.month, restored.day, restored.year) == (3, 4, 1990)


def test_corrupt_saved_review_does_not_prevent_loading_other_data(tmp_path):
    storage = Storage(tmp_path / "birthout.sqlite3")
    with storage.connect() as db:
        db.execute(
            """
            INSERT INTO review_sessions(id, filename, created_at, updated_at, state_version, state_json)
            VALUES ('broken', 'chat.txt', '2025-01-01', '2025-01-01', 1, '{broken')
            """
        )
    assert storage.load_review_sessions() == []
