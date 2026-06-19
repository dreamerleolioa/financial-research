from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def today_taipei(now: datetime | None = None) -> date:
    reference = now or datetime.now(TAIPEI_TZ)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=TAIPEI_TZ)
    return reference.astimezone(TAIPEI_TZ).date()


__all__ = ["TAIPEI_TZ", "today_taipei"]
