from datetime import datetime, timezone

from ai_stock_sentinel.clock import today_taipei


def test_today_taipei_converts_aware_datetime_to_taipei_date() -> None:
    assert today_taipei(datetime(2026, 6, 18, 16, 30, tzinfo=timezone.utc)).isoformat() == "2026-06-19"
