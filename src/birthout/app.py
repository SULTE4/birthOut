from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from birthout.calendar import GoogleCalendarSync
from birthout.config import Settings
from birthout.extractors.ollama import OllamaError, OllamaExtractor
from birthout.extractors.rules import RuleBasedBirthdayExtractor, normalize_person
from birthout.importers import WhatsAppImporter, WhatsAppParseError
from birthout.models import LeapPolicy, NameSource
from birthout.sessions import CandidateMergeError, SessionStore
from birthout.storage import Storage


PACKAGE_DIR = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    app = FastAPI(title="BirthOut", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    app.state.settings = settings
    app.state.storage = Storage(settings.database_path)
    app.state.sessions = SessionStore(app.state.storage.load_review_sessions())
    app.state.importer = WhatsAppImporter()
    app.state.extractor = RuleBasedBirthdayExtractor()
    app.state.templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @app.middleware("http")
    async def local_origin_only(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            allowed = origin and (origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost"))
            if origin and not allowed:
                return HTMLResponse("Cross-origin requests are not allowed.", status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, notice: str | None = None, error: str | None = None):
        storage: Storage = app.state.storage
        review_sessions = []
        for session in app.state.sessions.all():
            active = app.state.sessions.active_candidates(session)
            review_sessions.append(
                {
                    "id": session.id,
                    "filename": session.filename,
                    "updated_at": session.updated_at,
                    "pending": sum(item.status == "pending" for item in active),
                    "approved": sum(item.status == "approved" for item in active),
                    "rejected": sum(item.status == "rejected" for item in active),
                }
            )
        return app.state.templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "birthdays": storage.list_birthdays(),
                "review_sessions": review_sessions,
                "credentials_ready": settings.google_credentials_path.exists(),
                "ollama_url": storage.get_setting("ollama_url") or "http://127.0.0.1:11434",
                "ollama_model": storage.get_setting("ollama_model") or "qwen3:4b",
                "notice": notice,
                "error": error,
            },
        )

    @app.post("/import")
    async def import_chat(
        file: UploadFile = File(...),
        day_order: str = Form("auto"),
        use_ai: str | None = Form(None),
    ):
        payload = await file.read(100 * 1024 * 1024 + 1)
        filename = file.filename or "chat.txt"
        try:
            messages = app.state.importer.parse(payload, filename=filename, day_order=day_order)
            candidates = app.state.extractor.extract(messages)
        except WhatsAppParseError as exc:
            return _redirect_error(str(exc))

        warning = None
        if use_ai:
            storage: Storage = app.state.storage
            ollama = OllamaExtractor(
                storage.get_setting("ollama_url") or "http://127.0.0.1:11434",
                storage.get_setting("ollama_model") or "qwen3:4b",
            )
            for candidate in [item for item in candidates if item.confidence < 0.85]:
                try:
                    await ollama.enrich(candidate)
                except OllamaError as exc:
                    warning = str(exc)
                    break

        content_hash = hashlib.sha256(payload).hexdigest()
        app.state.storage.record_import(content_hash, filename)
        session = app.state.sessions.create(filename, content_hash, messages, candidates)
        app.state.storage.save_review_session(session)
        target = f"/review/{session.id}" + (f"?error={quote(warning)}" if warning else "")
        return RedirectResponse(target, status_code=303)

    @app.get("/review/{session_id}", response_class=HTMLResponse)
    async def review(request: Request, session_id: str, notice: str | None = None, error: str | None = None):
        session = app.state.sessions.get(session_id)
        if not session:
            raise HTTPException(404, "This review session expired. Import the chat again.")
        review_candidates = app.state.sessions.active_candidates(session)
        undoable_candidate_ids = {
            candidate.id
            for candidate in review_candidates
            if candidate.status == "rejected"
            or (
                candidate.status == "approved"
                and session.approval_birthday_ids.get(candidate.id) not in session.synced_birthday_ids
            )
        }
        return app.state.templates.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "session": session,
                "review_candidates": review_candidates,
                "candidate_name_options": app.state.sessions.candidate_name_options(session),
                "merge_suggestions": app.state.sessions.merge_suggestions(session),
                "undoable_candidate_ids": undoable_candidate_ids,
                "credentials_ready": settings.google_credentials_path.exists(),
                "notice": notice,
                "error": error,
            },
        )

    @app.post("/review/{session_id}/merge")
    async def merge_candidates(
        session_id: str,
        candidate_ids: list[str] = Form(...),
        canonical_name: str = Form(...),
    ):
        session = app.state.sessions.get(session_id)
        if not session:
            raise HTTPException(404, "Review session expired")
        try:
            composite = app.state.sessions.merge_candidates(session, candidate_ids, canonical_name)
        except CandidateMergeError as exc:
            return _review_error(session_id, str(exc))
        app.state.storage.save_review_session(session)
        aliases = ", ".join(composite.aliases)
        message = f"Merged aliases into {composite.person}" + (f": {aliases}" if aliases else "")
        return RedirectResponse(f"/review/{session_id}?notice={quote(message)}", status_code=303)

    @app.post("/review/{session_id}/merge/{candidate_id}/undo")
    async def undo_merge(session_id: str, candidate_id: str):
        session = app.state.sessions.get(session_id)
        if not session:
            raise HTTPException(404, "Review session expired")
        try:
            app.state.sessions.undo_merge(session, candidate_id)
        except CandidateMergeError as exc:
            return _review_error(session_id, str(exc))
        app.state.storage.save_review_session(session)
        return RedirectResponse(f"/review/{session_id}?notice=Merge%20undone", status_code=303)

    @app.post("/review/{session_id}/{candidate_id}/approve")
    async def approve(
        session_id: str,
        candidate_id: str,
        person: str = Form(...),
        month: int = Form(...),
        day: int = Form(...),
        year: str = Form(""),
        leap_policy: str = Form(""),
    ):
        session = app.state.sessions.get(session_id)
        if not session:
            raise HTTPException(404, "Review session expired")
        candidate = next((item for item in session.candidates if item.id == candidate_id), None)
        if not candidate:
            raise HTTPException(404, "Candidate not found")
        if candidate.status != "pending" or candidate.merged_into is not None:
            return _review_error(session_id, "Only a pending, active candidate can be approved.")
        person = person.strip()
        if not person:
            return _review_error(session_id, "Person name cannot be empty.")
        try:
            parsed_year = int(year) if year.strip() else None
            date(parsed_year or 2000, month, day)
        except ValueError:
            return _review_error(session_id, "Enter a valid birthday date.")
        if parsed_year and not 1900 <= parsed_year <= date.today().year:
            return _review_error(session_id, "Birth year must be between 1900 and the current year.")
        normalized_person = normalize_person(person)
        already_approved = {
            normalize_person(item.person)
            for item in session.candidates
            if item.status == "approved" and item.id != candidate.id
        }
        if normalized_person in already_approved:
            return _review_error(session_id, "This person is already approved in the current review. Merge or reject the duplicate.")
        candidate.person = person
        participant_keys = {name.casefold(): name for name in session.participant_names}
        if person.casefold() in participant_keys:
            candidate.name_source = NameSource.PARTICIPANT_SENDER
        elif not candidate.merged_source_ids:
            candidate.name_source = NameSource.MESSAGE_TEXT
        policy = None
        if month == 2 and day == 29:
            try:
                policy = LeapPolicy(leap_policy)
            except ValueError:
                return _review_error(session_id, "Choose how the February 29 birthday should be observed.")
        snapshot = app.state.storage.snapshot_birthday(person)
        birthday = app.state.storage.upsert_birthday(
            display_name=person,
            month=month,
            day=day,
            year=parsed_year,
            leap_policy=policy,
            source_hash=session.import_hash,
        )
        candidate.month = month
        candidate.day = day
        candidate.year = parsed_year
        candidate.leap_policy = policy
        candidate.status = "approved"
        session.approval_snapshots[candidate.id] = snapshot
        session.approval_birthday_ids[candidate.id] = birthday.id
        if birthday.id not in session.approved_birthday_ids:
            session.approved_birthday_ids.append(birthday.id)
        app.state.storage.save_review_session(session)
        return RedirectResponse(f"/review/{session_id}?notice={quote(person + ' approved')}", status_code=303)

    @app.post("/review/{session_id}/{candidate_id}/reject")
    async def reject(session_id: str, candidate_id: str):
        session = app.state.sessions.get(session_id)
        if not session:
            raise HTTPException(404, "Review session expired")
        candidate = next((item for item in session.candidates if item.id == candidate_id), None)
        if not candidate:
            raise HTTPException(404, "Candidate not found")
        if candidate.status != "pending" or candidate.merged_into is not None:
            return _review_error(session_id, "Only a pending, active candidate can be rejected.")
        candidate.status = "rejected"
        app.state.storage.save_review_session(session)
        return RedirectResponse(f"/review/{session_id}", status_code=303)

    @app.post("/review/{session_id}/{candidate_id}/undo-review")
    async def undo_review(session_id: str, candidate_id: str):
        session = app.state.sessions.get(session_id)
        if not session:
            raise HTTPException(404, "Review session expired")
        candidate = next((item for item in session.candidates if item.id == candidate_id), None)
        if not candidate:
            raise HTTPException(404, "Candidate not found")
        previous_status = candidate.status
        if previous_status == "rejected":
            candidate.status = "pending"
        elif previous_status == "approved":
            birthday_id = session.approval_birthday_ids.get(candidate.id)
            if not birthday_id:
                return _review_error(session_id, "Approval rollback information is unavailable.")
            if birthday_id in session.synced_birthday_ids:
                return _review_error(session_id, "This birthday was already synchronized and cannot be undone here.")
            try:
                app.state.storage.restore_birthday(
                    birthday_id, session.approval_snapshots.get(candidate.id)
                )
            except ValueError as exc:
                return _review_error(session_id, str(exc))
            session.approval_snapshots.pop(candidate.id, None)
            session.approval_birthday_ids.pop(candidate.id, None)
            if birthday_id not in session.approval_birthday_ids.values():
                session.approved_birthday_ids = [
                    item for item in session.approved_birthday_ids if item != birthday_id
                ]
            candidate.status = "pending"
        else:
            return _review_error(session_id, "Only approved or rejected candidates can be undone.")
        app.state.storage.save_review_session(session)
        return RedirectResponse(
            f"/review/{session_id}?notice={quote(previous_status.capitalize() + ' decision undone')}",
            status_code=303,
        )

    @app.post("/review/{session_id}/sync")
    async def sync_session(session_id: str):
        session = app.state.sessions.get(session_id)
        if not session:
            raise HTTPException(404, "Review session expired")
        birthdays = [app.state.storage.get_birthday(item) for item in session.approved_birthday_ids]
        birthdays = [item for item in birthdays if item]
        if not birthdays:
            return _review_error(session_id, "Approve at least one birthday before syncing.")
        if not settings.google_credentials_path.exists():
            return _review_error(session_id, "Set up Google Calendar credentials below before syncing.")
        try:
            created, updated = GoogleCalendarSync(app.state.storage, settings.google_credentials_path).sync(birthdays)
        except Exception as exc:
            return _review_error(session_id, f"Calendar sync failed: {exc}")
        session.synced_birthday_ids.update(item.id for item in birthdays)
        app.state.storage.save_review_session(session)
        message = f"Calendar synchronized: {created} created, {updated} updated"
        return RedirectResponse(f"/review/{session_id}?notice={quote(message)}", status_code=303)

    @app.post("/settings/google-credentials")
    async def google_credentials(file: UploadFile = File(...)):
        payload = await file.read(1024 * 1024)
        error = _store_google_credentials(payload, settings.google_credentials_path)
        if error:
            return _redirect_error(error)
        return RedirectResponse("/?notice=Google%20credentials%20saved", status_code=303)

    @app.post("/review/{session_id}/google-credentials")
    async def review_google_credentials(session_id: str, file: UploadFile = File(...)):
        if not app.state.sessions.get(session_id):
            raise HTTPException(404, "Review session not found")
        payload = await file.read(1024 * 1024)
        error = _store_google_credentials(payload, settings.google_credentials_path)
        if error:
            return _review_error(session_id, error)
        return RedirectResponse(
            f"/review/{session_id}?notice=Google%20credentials%20saved.%20You%20can%20sync%20now.",
            status_code=303,
        )

    @app.post("/review/{session_id}/delete")
    async def delete_review(session_id: str):
        if not app.state.sessions.get(session_id):
            raise HTTPException(404, "Review session not found")
        app.state.storage.delete_review_session(session_id)
        app.state.sessions.discard(session_id)
        return RedirectResponse("/?notice=Saved%20review%20deleted", status_code=303)

    @app.post("/settings/ollama")
    async def ollama_settings(url: str = Form(...), model: str = Form(...)):
        if not url.startswith(("http://", "https://")):
            return _redirect_error("Ollama URL must start with http:// or https://.")
        app.state.storage.set_setting("ollama_url", url.rstrip("/"))
        app.state.storage.set_setting("ollama_model", model.strip() or "qwen3:4b")
        return RedirectResponse("/?notice=Ollama%20settings%20saved", status_code=303)

    return app


def _redirect_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"/?error={quote(message)}", status_code=303)


def _review_error(session_id: str, message: str) -> RedirectResponse:
    return RedirectResponse(f"/review/{session_id}?error={quote(message)}", status_code=303)


def _store_google_credentials(payload: bytes, path: Path) -> str | None:
    try:
        parsed = json.loads(payload)
        installed = parsed["installed"]
        if not installed.get("client_id") or not installed.get("auth_uri") or not installed.get("token_uri"):
            raise ValueError
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return "This is not a valid Google Desktop OAuth credentials JSON file."
    path.write_bytes(payload)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return None
