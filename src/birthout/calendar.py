from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from birthout.models import ApprovedBirthday, LeapPolicy
from birthout.storage import Storage


SCOPES = ["https://www.googleapis.com/auth/calendar.app.created"]
KEYRING_SERVICE = "birthout-google-calendar"
KEYRING_USER = "oauth-token"


class CalendarConfigurationError(RuntimeError):
    pass


class GoogleCalendarSync:
    def __init__(self, storage: Storage, credentials_path: Path):
        self.storage = storage
        self.credentials_path = credentials_path

    def authenticate(self):
        if not self.credentials_path.exists():
            raise CalendarConfigurationError("Upload a Google Desktop OAuth credentials JSON file first.")
        try:
            import keyring
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise CalendarConfigurationError("Install BirthOut with its Google and keyring dependencies.") from exc

        token_json = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        credentials = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES) if token_json else None
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
            credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True)
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, credentials.to_json())
        except Exception as exc:
            raise CalendarConfigurationError(
                "The OAuth token could not be stored in the operating-system keyring. Configure a keyring backend and retry."
            ) from exc
        return credentials

    def sync(self, birthdays: list[ApprovedBirthday]) -> tuple[int, int]:
        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
        except ImportError as exc:
            raise CalendarConfigurationError("Google Calendar dependencies are not installed.") from exc
        service = build("calendar", "v3", credentials=self.authenticate(), cache_discovery=False)
        calendar_id = self._calendar_id(service)
        created = updated = 0
        for birthday in birthdays:
            body = self._event_body(birthday)
            link = self.storage.get_event_link(birthday.id)
            if link and link[0] == calendar_id:
                try:
                    event = service.events().update(calendarId=calendar_id, eventId=link[1], body=body).execute()
                    updated += 1
                except HttpError as exc:
                    if getattr(exc.resp, "status", None) != 404:
                        raise
                    event = service.events().insert(calendarId=calendar_id, body=body).execute()
                    created += 1
            else:
                event = service.events().insert(calendarId=calendar_id, body=body).execute()
                created += 1
            self.storage.set_event_link(birthday.id, calendar_id, event["id"])
        return created, updated

    def _calendar_id(self, service) -> str:
        calendar_id = self.storage.get_setting("google_calendar_id")
        if calendar_id:
            try:
                service.calendars().get(calendarId=calendar_id).execute()
                return calendar_id
            except Exception:
                pass
        calendar = service.calendars().insert(
            body={"summary": "BirthOut Birthdays", "description": "Birthday events reviewed and managed by BirthOut."}
        ).execute()
        self.storage.set_setting("google_calendar_id", calendar["id"])
        return calendar["id"]

    @classmethod
    def _event_body(cls, birthday: ApprovedBirthday) -> dict:
        start, recurrence = cls._recurrence(birthday)
        description = "Managed by BirthOut."
        if birthday.year:
            description += f" Born {birthday.year}."
        return {
            "summary": f"🎂 {birthday.display_name}",
            "description": description,
            "start": {"date": start.isoformat()},
            "end": {"date": (start + timedelta(days=1)).isoformat()},
            "recurrence": [recurrence],
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 1440}]},
            "extendedProperties": {"private": {"birthout_id": birthday.id}},
        }

    @staticmethod
    def _recurrence(birthday: ApprovedBirthday) -> tuple[date, str]:
        today = date.today()
        if birthday.month == 2 and birthday.day == 29:
            if birthday.leap_policy == LeapPolicy.FEBRUARY_28:
                candidate = date(today.year, 2, 28)
                if candidate < today:
                    candidate = date(today.year + 1, 2, 28)
                return candidate, "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=-1"
            if birthday.leap_policy == LeapPolicy.MARCH_1:
                candidate = date(today.year, 3, 1)
                if candidate < today:
                    candidate = date(today.year + 1, 3, 1)
                return candidate, "RRULE:FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=1"
            year = today.year
            while True:
                try:
                    candidate = date(year, 2, 29)
                    if candidate >= today:
                        return candidate, "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29"
                except ValueError:
                    pass
                year += 1
        candidate = date(today.year, birthday.month, birthday.day)
        if candidate < today:
            candidate = date(today.year + 1, birthday.month, birthday.day)
        return candidate, f"RRULE:FREQ=YEARLY;BYMONTH={birthday.month};BYMONTHDAY={birthday.day}"
