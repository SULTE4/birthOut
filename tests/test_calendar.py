from datetime import date

from birthout.calendar import GoogleCalendarSync
from birthout.models import ApprovedBirthday, LeapPolicy


def birthday(month, day, policy=None):
    return ApprovedBirthday("id", "Aida", month, day, 1990, policy)


def test_event_is_all_day_recurring_with_one_day_reminder():
    body = GoogleCalendarSync._event_body(birthday(3, 4))
    assert body["summary"] == "🎂 Aida"
    assert body["start"].keys() == {"date"}
    assert body["recurrence"] == ["RRULE:FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=4"]
    assert body["reminders"]["overrides"][0]["minutes"] == 1440


def test_february_29_policies():
    _, last_february = GoogleCalendarSync._recurrence(birthday(2, 29, LeapPolicy.FEBRUARY_28))
    _, march = GoogleCalendarSync._recurrence(birthday(2, 29, LeapPolicy.MARCH_1))
    _, leap_only = GoogleCalendarSync._recurrence(birthday(2, 29, LeapPolicy.LEAP_ONLY))
    assert last_february.endswith("BYMONTH=2;BYMONTHDAY=-1")
    assert march.endswith("BYMONTH=3;BYMONTHDAY=1")
    assert leap_only.endswith("BYMONTH=2;BYMONTHDAY=29")
