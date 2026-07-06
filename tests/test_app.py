from fastapi.testclient import TestClient

from birthout.app import create_app
from birthout.config import Settings


def test_home_and_import_review_flow(tmp_path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    client = TestClient(create_app(settings))

    home = client.get("/")
    assert home.status_code == 200
    assert "Import WhatsApp chat" in home.text

    chat = "05/07/2025, 12:00 - Aida: My birthday is March 4\n"
    imported = client.post(
        "/import",
        files={"file": ("chat.txt", chat.encode(), "text/plain")},
        data={"day_order": "dmy"},
        follow_redirects=True,
    )
    assert imported.status_code == 200
    assert "Aida" in imported.text
    assert "96% confidence" in imported.text


def test_cross_origin_post_is_rejected(tmp_path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    client = TestClient(create_app(settings))
    response = client.post(
        "/settings/ollama",
        data={"url": "http://127.0.0.1:11434", "model": "qwen3:4b"},
        headers={"origin": "https://example.com"},
    )
    assert response.status_code == 403


def test_merge_review_flow_creates_one_canonical_candidate(tmp_path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    app = create_app(settings)
    client = TestClient(app)
    chat = (
        "05/07/2025, 12:00 - Sam: Aida's birthday is March 4\n"
        "05/07/2025, 12:01 - Dana: Aika's birthday is March 4\n"
    )
    imported = client.post(
        "/import",
        files={"file": ("chat.txt", chat.encode(), "text/plain")},
        data={"day_order": "dmy"},
        follow_redirects=False,
    )
    session_id = imported.headers["location"].split("/")[2]
    current = app.state.sessions.get(session_id)
    candidate_ids = [item.id for item in current.candidates]

    review = client.get(imported.headers["location"])
    assert "Possible nicknames" in review.text

    merged = client.post(
        f"/review/{session_id}/merge",
        data={"candidate_ids": candidate_ids, "canonical_name": "Aida Smith"},
        follow_redirects=True,
    )
    assert merged.status_code == 200
    assert "Aliases in this import" in merged.text
    active = [item for item in current.candidates if item.status == "pending"]
    assert len(active) == 1
    assert active[0].person == "Aida Smith"

    approved = client.post(
        f"/review/{session_id}/{active[0].id}/approve",
        data={"person": "Aida Smith", "month": 3, "day": 4, "year": "", "leap_policy": ""},
        follow_redirects=True,
    )
    assert approved.status_code == 200
    assert len(app.state.storage.list_birthdays()) == 1


def test_name_suggestions_are_editable_and_selected_member_is_saved(tmp_path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    app = create_app(settings)
    client = TestClient(app)
    chat = (
        "05/07/2025, 12:00 - Sam: Sasha's birthday is March 4\n"
        "05/07/2025, 12:01 - Alexander Smith: Hello\n"
    )
    imported = client.post(
        "/import",
        files={"file": ("chat.txt", chat.encode(), "text/plain")},
        data={"day_order": "dmy"},
        follow_redirects=False,
    )
    session_id = imported.headers["location"].split("/")[2]
    current = app.state.sessions.get(session_id)
    candidate = current.candidates[0]
    review = client.get(imported.headers["location"])
    assert f'list="candidate-names-{candidate.id}"' in review.text
    assert 'value="Alexander Smith"' in review.text
    assert "Choose any group member or edit the name directly" in review.text

    client.post(
        f"/review/{session_id}/{candidate.id}/approve",
        data={"person": "Alexander Smith", "month": 3, "day": 4, "year": "", "leap_policy": ""},
        follow_redirects=True,
    )
    assert candidate.person == "Alexander Smith"
    assert candidate.name_source.value == "participant_sender"
    assert app.state.storage.list_birthdays()[0].display_name == "Alexander Smith"


def test_reject_moves_candidate_down_and_can_be_undone(tmp_path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    app = create_app(settings)
    client = TestClient(app)
    chat = (
        "05/07/2025, 12:00 - Aida: My birthday is March 4\n"
        "05/07/2025, 12:01 - Boris: My birthday is May 6\n"
    )
    imported = client.post(
        "/import", files={"file": ("chat.txt", chat.encode(), "text/plain")},
        data={"day_order": "dmy"}, follow_redirects=False,
    )
    session_id = imported.headers["location"].split("/")[2]
    current = app.state.sessions.get(session_id)
    aida = next(item for item in current.candidates if item.person == "Aida")
    client.post(f"/review/{session_id}/{aida.id}/reject", follow_redirects=True)
    assert app.state.sessions.active_candidates(current)[-1] is aida
    review = client.get(f"/review/{session_id}")
    assert "Undo rejection" in review.text

    client.post(f"/review/{session_id}/{aida.id}/undo-review", follow_redirects=True)
    assert aida.status == "pending"


def test_approval_moves_down_and_undo_removes_unsynced_birthday(tmp_path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    app = create_app(settings)
    client = TestClient(app)
    chat = (
        "05/07/2025, 12:00 - Aida: My birthday is March 4\n"
        "05/07/2025, 12:01 - Boris: My birthday is May 6\n"
    )
    imported = client.post(
        "/import", files={"file": ("chat.txt", chat.encode(), "text/plain")},
        data={"day_order": "dmy"}, follow_redirects=False,
    )
    session_id = imported.headers["location"].split("/")[2]
    current = app.state.sessions.get(session_id)
    aida = next(item for item in current.candidates if item.person == "Aida")
    client.post(
        f"/review/{session_id}/{aida.id}/approve",
        data={"person": "Aida", "month": 3, "day": 4, "year": "", "leap_policy": ""},
        follow_redirects=True,
    )
    assert app.state.sessions.active_candidates(current)[-1] is aida
    assert len(app.state.storage.list_birthdays()) == 1
    assert "Undo approval" in client.get(f"/review/{session_id}").text

    client.post(f"/review/{session_id}/{aida.id}/undo-review", follow_redirects=True)
    assert aida.status == "pending"
    assert current.approved_birthday_ids == []
    assert app.state.storage.list_birthdays() == []


def test_undo_approval_restores_existing_birthday(tmp_path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    app = create_app(settings)
    app.state.storage.upsert_birthday(
        display_name="Aida", month=3, day=4, year=1990, leap_policy=None, source_hash="old"
    )
    client = TestClient(app)
    imported = client.post(
        "/import",
        files={"file": ("chat.txt", b"05/07/2025, 12:00 - Aida: My birthday is May 6\n", "text/plain")},
        data={"day_order": "dmy"}, follow_redirects=False,
    )
    session_id = imported.headers["location"].split("/")[2]
    current = app.state.sessions.get(session_id)
    candidate = current.candidates[0]
    client.post(
        f"/review/{session_id}/{candidate.id}/approve",
        data={"person": "Aida", "month": 5, "day": 6, "year": "1991", "leap_policy": ""},
    )
    client.post(f"/review/{session_id}/{candidate.id}/undo-review")
    restored = app.state.storage.list_birthdays()[0]
    assert (restored.month, restored.day, restored.year) == (3, 4, 1990)


def test_review_progress_survives_restart_without_full_chat(tmp_path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    first_app = create_app(settings)
    first_client = TestClient(first_app)
    chat = (
        "05/07/2025, 12:00 - Sam: Aida's birthday is March 4\n"
        "05/07/2025, 12:01 - Dana: Aika's birthday is March 4\n"
        "05/07/2025, 12:02 - Boris: My birthday is May 6\n"
        "05/07/2025, 12:03 - Sam: unrelated private conversation\n"
    )
    imported = first_client.post(
        "/import",
        files={"file": ("chat.txt", chat.encode(), "text/plain")},
        data={"day_order": "dmy"},
        follow_redirects=False,
    )
    session_id = imported.headers["location"].split("/")[2]
    current = first_app.state.sessions.get(session_id)
    aliases = [item for item in current.candidates if item.person in {"Aida", "Aika"}]
    first_client.post(
        f"/review/{session_id}/merge",
        data={"candidate_ids": [item.id for item in aliases], "canonical_name": "Aida Smith"},
    )
    merged = next(item for item in current.candidates if item.person == "Aida Smith")
    boris = next(item for item in current.candidates if item.person == "Boris")
    first_client.post(
        f"/review/{session_id}/{merged.id}/approve",
        data={"person": "Aida Smith", "month": 3, "day": 4, "year": "", "leap_policy": ""},
    )
    first_client.post(f"/review/{session_id}/{boris.id}/reject")

    with first_app.state.storage.connect() as db:
        state_json = db.execute(
            "SELECT state_json FROM review_sessions WHERE id = ?", (session_id,)
        ).fetchone()["state_json"]
    assert "Aida's birthday is March 4" in state_json
    assert "unrelated private conversation" not in state_json

    restarted_app = create_app(settings)
    restarted_client = TestClient(restarted_app)
    restored = restarted_app.state.sessions.get(session_id)
    assert restored is not None
    assert restored.messages == []
    assert restored.message_count == 4
    assert next(item for item in restored.candidates if item.id == merged.id).status == "approved"
    assert next(item for item in restored.candidates if item.id == boris.id).status == "rejected"
    assert "Resume a review" in restarted_client.get("/").text

    restarted_client.post(f"/review/{session_id}/{boris.id}/undo-review")
    restarted_client.post(f"/review/{session_id}/{merged.id}/undo-review")
    assert restarted_app.state.storage.list_birthdays() == []
    assert next(item for item in restored.candidates if item.id == boris.id).status == "pending"
    assert next(item for item in restored.candidates if item.id == merged.id).status == "pending"


def test_google_credentials_can_be_added_from_current_review(tmp_path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    app = create_app(settings)
    client = TestClient(app)
    imported = client.post(
        "/import",
        files={"file": ("chat.txt", b"05/07/2025, 12:00 - Aida: My birthday is March 4\n", "text/plain")},
        data={"day_order": "dmy"},
        follow_redirects=False,
    )
    session_id = imported.headers["location"].split("/")[2]
    credentials = b'{"installed":{"client_id":"id","auth_uri":"https://auth","token_uri":"https://token"}}'
    response = client.post(
        f"/review/{session_id}/google-credentials",
        files={"file": ("client.json", credentials, "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/review/{session_id}?notice=")
    assert settings.google_credentials_path.read_bytes() == credentials
    assert "Google credentials ready" in client.get(f"/review/{session_id}").text
    assert app.state.sessions.get(session_id) is not None


def test_deleting_saved_review_keeps_approved_birthday(tmp_path):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    app = create_app(settings)
    client = TestClient(app)
    imported = client.post(
        "/import",
        files={"file": ("chat.txt", b"05/07/2025, 12:00 - Aida: My birthday is March 4\n", "text/plain")},
        data={"day_order": "dmy"},
        follow_redirects=False,
    )
    session_id = imported.headers["location"].split("/")[2]
    candidate = app.state.sessions.get(session_id).candidates[0]
    client.post(
        f"/review/{session_id}/{candidate.id}/approve",
        data={"person": "Aida", "month": 3, "day": 4, "year": "", "leap_policy": ""},
    )

    response = client.post(f"/review/{session_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert app.state.sessions.get(session_id) is None
    assert [item.display_name for item in app.state.storage.list_birthdays()] == ["Aida"]
    with app.state.storage.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM review_sessions").fetchone()[0] == 0


def test_successful_sync_state_survives_restart(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "db.sqlite3", tmp_path / "google.json")
    settings.google_credentials_path.write_text("{}")
    app = create_app(settings)
    client = TestClient(app)
    imported = client.post(
        "/import",
        files={"file": ("chat.txt", b"05/07/2025, 12:00 - Aida: My birthday is March 4\n", "text/plain")},
        data={"day_order": "dmy"},
        follow_redirects=False,
    )
    session_id = imported.headers["location"].split("/")[2]
    candidate = app.state.sessions.get(session_id).candidates[0]
    client.post(
        f"/review/{session_id}/{candidate.id}/approve",
        data={"person": "Aida", "month": 3, "day": 4, "year": "", "leap_policy": ""},
    )
    birthday_id = app.state.sessions.get(session_id).approval_birthday_ids[candidate.id]
    monkeypatch.setattr("birthout.app.GoogleCalendarSync.sync", lambda self, birthdays: (1, 0))
    assert client.post(f"/review/{session_id}/sync", follow_redirects=False).status_code == 303

    restarted_app = create_app(settings)
    restored = restarted_app.state.sessions.get(session_id)
    assert birthday_id in restored.synced_birthday_ids
    assert "Already synchronized" in TestClient(restarted_app).get(f"/review/{session_id}").text
