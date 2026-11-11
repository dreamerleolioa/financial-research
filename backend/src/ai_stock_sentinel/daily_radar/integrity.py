"""Live source prerequisites; saved step status alone cannot prove coverage."""
from ai_stock_sentinel.daily_radar.background_context import same_day_background_context_is_reusable
from ai_stock_sentinel.daily_radar.repository import get_shared_background_context_rows


def background_gaps(db, prepared):
    rows = get_shared_background_context_rows(db, symbols=prepared.selected_symbols,
        context_types=['lending', 'full_margin'], reference_date=prepared.run_date, point_in_time=True)
    covered = {(row.symbol, row.context_type) for row in rows
               if same_day_background_context_is_reusable(row, run_date=prepared.run_date)}
    return {'refresh-' + stage: [symbol for symbol in prepared.selected_symbols if (symbol, context_type) not in covered]
        for stage, context_type in (('lending', 'lending'), ('full-margin', 'full_margin'))}
