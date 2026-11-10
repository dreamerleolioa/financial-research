"""An evaluated window can be collected without accepting a database mutation."""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text


@dataclass
class ValidationPreparation:
    candidates: list[dict]
    outcomes: list[dict]
    calendar: object
    as_of_date: date
    return_basis: str
    benchmark_prices: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)


def reserve_validation_commit(db):
    """Same relation -> result/candidate -> prepared order as reviewed repair."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        db.execute(text("LOCK TABLE daily_radar_runs, daily_radar_candidates IN ROW EXCLUSIVE MODE"))
    elif db.get_bind().dialect.name == "sqlite":
        db.execute(text("UPDATE daily_radar_runs SET id = id WHERE 0"))


def accept_validation_results(db, outcomes, *, calendar, **upsert_options):
    """Accept a legacy endpoint batch without committing the caller's transaction.

    Durable work accepts its own calendar ledger and domain effects atomically;
    its prepare-only collectors do not use this prepared-run checkpoint owner.
    """
    from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
    from ai_stock_sentinel.daily_radar.trading_calendar import save_calendar
    reserve_validation_commit(db)
    # Preserve relation -> result/candidate -> prepared order. Even if the
    # caller catches an error and commits, this batch cannot leak flushed rows.
    with db.begin_nested():
        summary = upsert_forward_validation_results(db, outcomes, **upsert_options)
        if calendar is not None and not save_calendar(db, calendar):
            raise ValueError('calendar_checkpoint_unavailable')
    return summary
