"""Unit tests for the booking/availability logic in services.google_calendar.

All Google API calls are mocked via conftest.py; these tests exercise the
pure-Python filtering and concurrency logic against synthetic event lists.
"""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from services import google_calendar as gc


TZ = ZoneInfo("Asia/Dubai")


def _iso(hour: int, minute: int = 0, day: str = "2026-05-02") -> str:
    # 2026-05-02 is a Saturday (working day in Oman schedule).
    return datetime(
        int(day[:4]), int(day[5:7]), int(day[8:10]), hour, minute, tzinfo=TZ
    ).isoformat()


def _event(dep: str, start_h: int, end_h: int, doctor: str | None = None) -> dict:
    summary = f"[{dep}] some_service - Some Client"
    if doctor:
        summary += f" ({doctor})"
    return {
        "summary": summary,
        "start": {"dateTime": _iso(start_h)},
        "end": {"dateTime": _iso(end_h)},
    }


class TestAvailability:
    def test_empty_calendar_dentistry_returns_slots_from_opening(self, monkeypatch):
        monkeypatch.setattr(gc, "_get_events_for_day", lambda *_a, **_kw: [])
        slots = gc.get_available_slots("2026-05-02", "dentistry", "checkup")
        # Dentistry checkup is 20 min; opening 10:00. First slot is 10:00.
        assert slots[0] == "10:00"
        # Must NOT include times that would overlap break (14:00–15:00).
        assert "14:00" not in slots

    def test_friday_is_closed(self, monkeypatch):
        monkeypatch.setattr(gc, "_get_events_for_day", lambda *_a, **_kw: [])
        # 2026-05-01 is a Friday.
        assert gc.get_available_slots("2026-05-01", "dentistry", "checkup") == []

    def test_dentistry_capacity_one_blocks_overlapping(self, monkeypatch):
        monkeypatch.setattr(
            gc, "_get_events_for_day",
            lambda *_a, **_kw: [_event("dentistry", 10, 11)],
        )
        slots = gc.get_available_slots("2026-05-02", "dentistry", "checkup")
        # 10:00 is taken (dentistry capacity=1), so it must not appear.
        assert "10:00" not in slots
        # 11:00 onward should be free.
        assert "11:00" in slots

    def test_laser_capacity_four_allows_concurrent(self, monkeypatch):
        # Three concurrent laser appointments at 10:00 → fourth machine is still free.
        monkeypatch.setattr(
            gc, "_get_events_for_day",
            lambda *_a, **_kw: [
                _event("laser_hair_removal", 10, 11),
                _event("laser_hair_removal", 10, 11),
                _event("laser_hair_removal", 10, 11),
            ],
        )
        slots = gc.get_available_slots("2026-05-02", "laser_hair_removal", "bikini")
        assert "10:00" in slots  # 3 < capacity 4

    def test_laser_full_capacity_excludes(self, monkeypatch):
        monkeypatch.setattr(
            gc, "_get_events_for_day",
            lambda *_a, **_kw: [_event("laser_hair_removal", 10, 11)] * 4,
        )
        slots = gc.get_available_slots("2026-05-02", "laser_hair_removal", "bikini")
        assert "10:00" not in slots

    def test_break_time_excluded(self, monkeypatch):
        monkeypatch.setattr(gc, "_get_events_for_day", lambda *_a, **_kw: [])
        slots = gc.get_available_slots("2026-05-02", "dentistry", "checkup")
        # A 20-min slot starting 13:50 would overlap the 14:00 break start.
        assert "13:50" not in slots
        # 15:00 is the earliest post-break slot.
        assert "15:00" in slots

    def test_laser_extended_hours(self, monkeypatch):
        monkeypatch.setattr(gc, "_get_events_for_day", lambda *_a, **_kw: [])
        slots = gc.get_available_slots("2026-05-02", "laser_hair_removal", "bikini")
        # Bikini laser is 15 min; laser runs until 23:00 → 22:45 must be valid.
        assert "22:45" in slots
        # Dentistry cuts off at 20:00 and can't go past it.
        dent_slots = gc.get_available_slots("2026-05-02", "dentistry", "checkup")
        assert all(s < "20:00" for s in dent_slots)


class TestBookingRaceCondition:
    def test_book_raises_when_slot_is_full_under_lock(self, monkeypatch):
        # Simulate: availability check passed in chat, but by the time we
        # actually book, the slot is fully occupied (dentistry capacity 1).
        monkeypatch.setattr(
            gc, "_get_events_for_day",
            lambda *_a, **_kw: [_event("dentistry", 10, 11)],
        )
        with pytest.raises(gc.SlotNoLongerAvailable):
            gc.book_appointment(
                client_name="Test",
                client_phone="123",
                department="dentistry",
                sub_service="checkup",
                date_str="2026-05-02",
                time_str="10:00",
                duration_minutes=20,
            )

    def test_book_succeeds_when_slot_free(self, monkeypatch):
        # No conflicting events; the insert call should be issued.
        monkeypatch.setattr(gc, "_get_events_for_day", lambda *_a, **_kw: [])
        insert_called = {"n": 0}

        class _FakeEvents:
            def insert(self, **_):
                insert_called["n"] += 1
                return self
            def execute(self):
                return {"id": "evt-123"}

        class _FakeService:
            def events(self):
                return _FakeEvents()

        monkeypatch.setattr(gc, "_service", _FakeService())
        result = gc.book_appointment(
            client_name="Test",
            client_phone="123",
            department="dentistry",
            sub_service="checkup",
            date_str="2026-05-02",
            time_str="10:00",
            duration_minutes=20,
        )
        assert result["event_id"] == "evt-123"
        assert insert_called["n"] == 1


class TestWaitlist:
    def test_add_remove_cycle(self, tmp_path, monkeypatch):
        from services import waitlist as wl

        # Point the module at a throwaway file for this test.
        monkeypatch.setattr(wl, "_STATE_FILE", str(tmp_path / "wl.json"))

        wl.add_entry(
            client_phone="123",
            client_name="Ali",
            client_mobile="+968123",
            department="dentistry",
            sub_service="checkup",
            desired_date="2026-05-05",
            desired_time="10:00",
            duration_minutes=20,
            language="en",
        )
        candidates = wl.find_candidates_for_slot("dentistry", "2026-05-05", "10:00")
        assert len(candidates) == 1
        assert candidates[0]["client_name"] == "Ali"

        assert wl.remove_entry("123", "dentistry", "2026-05-05", "10:00")
        assert wl.find_candidates_for_slot("dentistry", "2026-05-05", "10:00") == []

    def test_duplicate_add_dedupes(self, tmp_path, monkeypatch):
        from services import waitlist as wl

        monkeypatch.setattr(wl, "_STATE_FILE", str(tmp_path / "wl.json"))
        for _ in range(3):
            wl.add_entry(
                client_phone="123",
                client_name="Ali",
                client_mobile="+968123",
                department="dentistry",
                sub_service="checkup",
                desired_date="2026-05-05",
                desired_time="10:00",
                duration_minutes=20,
                language="en",
            )
        assert len(wl.list_all()) == 1


class TestRetention:
    def _appt(self, department, sub_service, date_iso, phone="+968111", name="Ali", language="en"):
        return {
            "event_id": f"evt-{phone}-{date_iso}",
            "summary": f"[{department}] {sub_service} - {name}",
            "client_name": name,
            "phone": phone,
            "mobile": phone,
            "channel": "whatsapp",
            "language": language,
            "department": department,
            "sub_service": sub_service,
            "date": date_iso,
            "time": "10:00",
        }

    def test_sends_day_1_aftercare_for_filling(self, tmp_path, monkeypatch):
        from datetime import date
        from services import retention as r

        monkeypatch.setattr(r, "_STATE_FILE", str(tmp_path / "sent.json"))
        # Appointment was yesterday.
        past = [self._appt("dentistry", "filling", "2026-05-01")]
        monkeypatch.setattr(
            r.google_calendar, "get_past_appointments", lambda days_back: past
        )
        sent = []
        monkeypatch.setattr(
            r.whatsapp, "send_message", lambda to, body: sent.append((to, body))
        )

        count = r.run_retention(today=date(2026, 5, 2))
        assert count == 1
        assert sent[0][0] == "+968111"
        assert "Dental Filling" in sent[0][1]

    def test_no_send_on_off_day(self, tmp_path, monkeypatch):
        from datetime import date
        from services import retention as r

        monkeypatch.setattr(r, "_STATE_FILE", str(tmp_path / "sent.json"))
        past = [self._appt("dentistry", "filling", "2026-05-01")]
        monkeypatch.setattr(
            r.google_calendar, "get_past_appointments", lambda days_back: past
        )
        monkeypatch.setattr(r.whatsapp, "send_message", lambda *a: None)
        # Day 3 doesn't match any filling campaign (only day 1).
        assert r.run_retention(today=date(2026, 5, 4)) == 0

    def test_dedup_does_not_resend(self, tmp_path, monkeypatch):
        from datetime import date
        from services import retention as r

        monkeypatch.setattr(r, "_STATE_FILE", str(tmp_path / "sent.json"))
        past = [self._appt("laser_hair_removal", "bikini", "2026-04-04")]
        monkeypatch.setattr(
            r.google_calendar, "get_past_appointments", lambda days_back: past
        )
        sent = []
        monkeypatch.setattr(
            r.whatsapp, "send_message", lambda to, body: sent.append((to, body))
        )

        # First run on day 28 sends. Second run same day does not.
        assert r.run_retention(today=date(2026, 5, 2)) == 1
        assert r.run_retention(today=date(2026, 5, 2)) == 0
        assert len(sent) == 1

    def test_department_any_matches_all_sub_services(self, tmp_path, monkeypatch):
        from datetime import date
        from services import retention as r

        monkeypatch.setattr(r, "_STATE_FILE", str(tmp_path / "sent.json"))
        # Laser has `_any_` schedule; full_body should match too.
        past = [self._appt("laser_hair_removal", "full_body", "2026-04-04")]
        monkeypatch.setattr(
            r.google_calendar, "get_past_appointments", lambda days_back: past
        )
        sent = []
        monkeypatch.setattr(
            r.whatsapp, "send_message", lambda to, body: sent.append((to, body))
        )
        assert r.run_retention(today=date(2026, 5, 2)) == 1

    def test_arabic_template_used_for_ar_language(self, tmp_path, monkeypatch):
        from datetime import date
        from services import retention as r

        monkeypatch.setattr(r, "_STATE_FILE", str(tmp_path / "sent.json"))
        past = [self._appt("dentistry", "filling", "2026-05-01", language="ar", name="علي")]
        monkeypatch.setattr(
            r.google_calendar, "get_past_appointments", lambda days_back: past
        )
        sent = []
        monkeypatch.setattr(
            r.whatsapp, "send_message", lambda to, body: sent.append((to, body))
        )
        r.run_retention(today=date(2026, 5, 2))
        assert "مرحباً" in sent[0][1]


class TestPackages:
    def test_create_and_consume(self, tmp_path, monkeypatch):
        from services import packages as pk

        monkeypatch.setattr(pk, "_STATE_FILE", str(tmp_path / "packages.json"))

        pkg = pk.create_package(
            client_phone="+968111",
            client_name="Ali",
            client_mobile="+968111",
            package_code="laser_bikini_6",
            language="en",
        )
        assert pkg["total_sessions"] == 6
        assert pkg["sessions_used"] == 0
        assert pkg["department"] == "laser_hair_removal"

        # Consume one session.
        updated = pk.consume_session(pkg["id"])
        assert updated is not None
        assert updated["sessions_used"] == 1
        assert pk.sessions_remaining(updated) == 5

    def test_find_usable_matches_exact_and_any(self, tmp_path, monkeypatch):
        from services import packages as pk

        monkeypatch.setattr(pk, "_STATE_FILE", str(tmp_path / "packages.json"))

        # Exact sub_service match.
        pk.create_package("+968111", "Ali", "+968111", "laser_bikini_6", "en")
        match = pk.find_usable_package("+968111", "laser_hair_removal", "bikini")
        assert match is not None

        # Wrong sub_service: no match (bikini package doesn't cover full_body).
        no_match = pk.find_usable_package("+968111", "laser_hair_removal", "full_body")
        assert no_match is None

        # _any_ flex package covers everything in its department.
        pk.create_package("+968222", "Sara", "+968222", "slimming_any_10", "en")
        assert pk.find_usable_package("+968222", "slimming", "schwarzy") is not None
        assert pk.find_usable_package("+968222", "slimming", "onda_plus") is not None

    def test_refund_restores_session(self, tmp_path, monkeypatch):
        from services import packages as pk

        monkeypatch.setattr(pk, "_STATE_FILE", str(tmp_path / "packages.json"))
        pkg = pk.create_package("+968111", "Ali", "+968111", "laser_bikini_6", "en")
        pk.consume_session(pkg["id"])
        pk.consume_session(pkg["id"])
        refunded = pk.refund_session(pkg["id"])
        assert refunded["sessions_used"] == 1

    def test_consume_empty_returns_none(self, tmp_path, monkeypatch):
        from services import packages as pk

        monkeypatch.setattr(pk, "_STATE_FILE", str(tmp_path / "packages.json"))
        pkg = pk.create_package("+968111", "Ali", "+968111", "laser_bikini_6", "en")
        # Drain all 6 sessions.
        for _ in range(6):
            pk.consume_session(pkg["id"])
        # Seventh attempt fails.
        assert pk.consume_session(pkg["id"]) is None

    def test_expired_package_not_returned_by_find(self, tmp_path, monkeypatch):
        from services import packages as pk

        monkeypatch.setattr(pk, "_STATE_FILE", str(tmp_path / "packages.json"))
        pkg = pk.create_package("+968111", "Ali", "+968111", "laser_bikini_6", "en")
        # Manually age the package past its expiry by rewriting state.
        data = pk._load()
        data[0]["expires_at"] = "2020-01-01"
        pk._save(data)
        assert pk.find_usable_package("+968111", "laser_hair_removal", "bikini") is None

    def test_notify_if_exhausted_sends_only_on_zero(self, tmp_path, monkeypatch):
        from services import packages as pk

        monkeypatch.setattr(pk, "_STATE_FILE", str(tmp_path / "packages.json"))
        pkg = pk.create_package("+968111", "Ali", "+968111", "laser_bikini_6", "en")

        sent = []
        # Patch the module the notify function resolves at call time.
        import services.whatsapp as wa
        monkeypatch.setattr(wa, "send_message", lambda to, body: sent.append((to, body)))

        # Use 5 of 6 — should NOT notify.
        for _ in range(5):
            pk.consume_session(pkg["id"])
        partial = pk._load()[0]
        pk.notify_if_exhausted(partial)
        assert sent == []

        # Use the last one — should notify.
        pk.consume_session(pkg["id"])
        final = pk._load()[0]
        pk.notify_if_exhausted(final)
        assert len(sent) == 1
        assert "+968111" == sent[0][0]


class TestAlertsRateLimit:
    def test_dedup_suppresses_repeated_notify(self, monkeypatch):
        from services import alerts

        sent = []
        monkeypatch.setattr(alerts, "_send", lambda text: sent.append(text))
        # Reset dedup state.
        alerts._last_sent.clear()

        for _ in range(5):
            alerts.notify("same error", dedup_key="sig-1")
        assert len(sent) == 1  # only the first gets through the window

        alerts.notify("different error", dedup_key="sig-2")
        assert len(sent) == 2
