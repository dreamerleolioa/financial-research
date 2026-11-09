"""Explicit fake exchange calendars for offline contract tests."""
from datetime import date, timedelta
from calendar import monthrange
from ai_stock_sentinel.daily_radar.trading_calendar import TradingCalendar, months


def calendar(start=date(2026,9,16), end=date(2026,9,24), closed=()):
    days=tuple(start+timedelta(days=i) for i in range((end-start).days+1)
               if (start+timedelta(days=i)).weekday()<5 and start+timedelta(days=i) not in closed)
    def report_dates(month):
        return [str(month+timedelta(days=i)) for i in range(monthrange(month.year,month.month)[1])
                if (month+timedelta(days=i)).weekday()<5 and month+timedelta(days=i) not in closed]
    report_months=list(months(start,end))
    if not any(d >= str(end) for d in report_dates(report_months[-1])):
        report_months.append((end.replace(day=28)+timedelta(days=4)).replace(day=1))
    reports=tuple({"month":m.strftime("%Y-%m"),"source_url":
        "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST?response=json&date="+m.strftime("%Y%m%d"),
        "snapshot_sha256":"a"*64,"captured_at":"2026-11-06T00:00:00+00:00",
        "session_dates":report_dates(m)} for m in report_months)
    return TradingCalendar.parse(TradingCalendar(start,end,days,reports).payload())


class FixtureCalendarProvider:
    def __init__(self,closed_dates=()):
        self.closed_dates=set(closed_dates)

    def fetch(self, *, start_date, end_date):
        return calendar(start_date,end_date,self.closed_dates)
