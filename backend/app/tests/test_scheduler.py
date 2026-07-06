"""Schedule firing: per-slot (daily + re-fire on time change), timezone-aware."""
from __future__ import annotations

from datetime import datetime, timezone

from app.scheduler.run_scheduler import _due_discovery, slot_of

UTC = timezone.utc


def _at(h: int, m: int, day: int = 31) -> datetime:
    return datetime(2026, 5, day, h, m, tzinfo=UTC)


def test_discovery_fires_then_not_again_for_same_slot() -> None:
    sched = {"enabled": True, "time_utc": "08:00"}
    assert not _due_discovery(sched, _at(7, 59))
    now = _at(8, 0)
    assert _due_discovery(sched, now)
    fired = {**sched, "last_slot": slot_of(now, sched)}
    assert not _due_discovery(fired, _at(9, 0))  # same slot already fired


def test_discovery_refires_same_day_when_time_changes() -> None:
    # Fired at 08:00, then user moves the time -> fires again that day (new slot).
    later = {"enabled": True, "time_utc": "15:00", "last_slot": slot_of(_at(8, 0), {"time_utc": "08:00"})}
    assert _due_discovery(later, _at(15, 0))
    earlier = {"enabled": True, "time_utc": "11:00", "last_slot": slot_of(_at(15, 0), {"time_utc": "15:00"})}
    assert _due_discovery(earlier, _at(16, 0))  # moved to an already-passed time


def test_discovery_runs_every_day() -> None:
    yesterday_slot = slot_of(_at(8, 0, day=30), {"time_utc": "08:00"})
    sched = {"enabled": True, "time_utc": "08:00", "last_slot": yesterday_slot}
    assert _due_discovery(sched, _at(8, 0, day=31))  # next day's slot is new


def test_discovery_timezone_aware() -> None:
    # 08:00 in CST (UTC+8) == 00:00 UTC.
    sched = {"enabled": True, "time_utc": "08:00", "tz": "Asia/Shanghai"}
    assert not _due_discovery(sched, _at(23, 59, day=30))  # 07:59 CST, before
    assert _due_discovery(sched, _at(0, 0, day=31))  # 08:00 CST -> fires
    assert slot_of(_at(0, 0, day=31), sched) == _at(0, 0, day=31).isoformat()


def test_discovery_disabled_or_empty() -> None:
    assert not _due_discovery({"enabled": False, "time_utc": "08:00"}, _at(9, 0))
    assert not _due_discovery({}, _at(9, 0))
