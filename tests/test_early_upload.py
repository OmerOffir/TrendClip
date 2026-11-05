"""Slot uploads go up an hour early, private, scheduled for the slot (the owner, 2026-11-05)."""
from datetime import datetime, timedelta, timezone

from trendclip import publish


NOW = datetime(2026, 11, 5, 11, 30, tzinfo=timezone.utc)


def test_scheduled_only_while_youtube_can_still_schedule():
    assert publish.EARLY_UPLOAD == timedelta(hours=1)
    assert publish.scheduled(NOW + timedelta(hours=1), NOW) == NOW + timedelta(hours=1)
    assert publish.scheduled(NOW + timedelta(minutes=16), NOW) == NOW + timedelta(minutes=16)
    assert publish.scheduled(NOW + timedelta(minutes=10), NOW) is None  # too close: public now
    assert publish.scheduled(NOW - timedelta(minutes=5), NOW) is None  # a late or retried upload
    assert publish.scheduled(None, NOW) is None


def test_the_after_publish_list(tmp_path):
    at = (NOW + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert publish.later(tmp_path, "a.mp4", {"video_id": "v1", "privacy": "public"}) is False  # not scheduled
    assert publish.later(tmp_path, "a.mp4", {"video_id": "v1", "privacy": "private", "scheduled_for": at})
    assert publish.due_later(tmp_path, NOW) == []
    due = publish.due_later(tmp_path, NOW + timedelta(hours=1))
    assert [(e["key"], e["video_id"]) for e in due] == [("a.mp4", "v1")]
    assert publish.due_later(tmp_path, NOW + timedelta(hours=2)) == []  # taken off the list
    assert publish.again(tmp_path, due[0], NOW + timedelta(hours=1))
    assert publish.due_later(tmp_path, NOW + timedelta(hours=1, minutes=5)) == []
    retry = publish.due_later(tmp_path, NOW + timedelta(hours=1, minutes=10))
    assert [e["key"] for e in retry] == ["a.mp4"]
    assert publish.again(tmp_path, retry[0], NOW + timedelta(hours=8)) is False  # gave up after 6 h


def test_privacy_note():
    assert publish.privacy_note({"privacy": "public"}) == "public"
    assert publish.privacy_note({"privacy": "private", "scheduled_for": "2026-11-05T12:30:00Z"}) == "private, public at 14:30"
