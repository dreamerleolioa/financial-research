from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")


def resolve_run_date(
    *,
    event_name: str,
    schedule: str = "",
    run_created_at: str = "",
    input_run_date: str = "",
    now: datetime | None = None,
) -> date:
    if event_name == "schedule":
        if not schedule or not run_created_at:
            raise ValueError("scheduled runs require schedule and run_created_at")
        return _scheduled_slot_date(schedule, _parse_utc_datetime(run_created_at))
    if input_run_date:
        return date.fromisoformat(input_run_date)
    if run_created_at:
        return _parse_utc_datetime(run_created_at).astimezone(TAIPEI).date()
    current = now or datetime.now(tz=TAIPEI)
    return current.astimezone(TAIPEI).date()


def _scheduled_slot_date(schedule: str, created_at: datetime) -> date:
    minute_text, hour_text, day_of_month, month, weekday_text = schedule.split()
    if day_of_month != "*" or month != "*":
        raise ValueError("Daily Radar schedules must use wildcard day-of-month and month")
    minute = int(minute_text)
    hour = int(hour_text)
    weekdays = _parse_cron_weekdays(weekday_text)
    created_utc = created_at.astimezone(timezone.utc)
    for days_back in range(8):
        candidate_date = created_utc.date() - timedelta(days=days_back)
        cron_weekday = (candidate_date.weekday() + 1) % 7
        if cron_weekday not in weekdays:
            continue
        candidate = datetime.combine(candidate_date, time(hour, minute), tzinfo=timezone.utc)
        if candidate <= created_utc:
            return candidate.date()
    raise ValueError("could not resolve a cron slot within the previous week")


def _parse_cron_weekdays(value: str) -> set[int]:
    if value == "*":
        return set(range(7))
    weekdays: set[int] = set()
    for part in value.split(","):
        if "-" in part:
            start_text, end_text = part.split("-", maxsplit=1)
            start = int(start_text) % 7
            end = int(end_text) % 7
            if start > end:
                raise ValueError("wrapped cron weekday ranges are unsupported")
            weekdays.update(range(start, end + 1))
        else:
            weekdays.add(int(part) % 7)
    return weekdays


def _parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("run_created_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--schedule", default="")
    parser.add_argument("--run-created-at", default="")
    parser.add_argument("--input-run-date", default="")
    args = parser.parse_args()
    resolved = resolve_run_date(
        event_name=args.event_name,
        schedule=args.schedule,
        run_created_at=args.run_created_at,
        input_run_date=args.input_run_date,
    )
    print(resolved.isoformat())


if __name__ == "__main__":
    main()
