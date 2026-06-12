from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import yfinance as yf
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.metrics import calc_rsi, ma
from ai_stock_sentinel.db.models import StockRawData, UserPortfolio


MAX_DETECTED_EVENTS = 8
TRADE_REVIEW_LOOKBACK_DAYS = 120
TRADE_REVIEW_REQUIRED_LOOKBACK_ROWS = 60


def ensure_trade_review_market_data(db: Session, portfolio: UserPortfolio, fetcher=None) -> None:
    if portfolio.exit_date is None:
        return

    rows = db.execute(
        select(StockRawData)
        .where(
            StockRawData.symbol == portfolio.symbol,
            StockRawData.record_date <= portfolio.exit_date,
        )
        .order_by(StockRawData.record_date.asc())
    ).scalars().all()
    if not _needs_trade_review_backfill(rows, portfolio):
        return

    start_date = portfolio.entry_date - timedelta(days=TRADE_REVIEW_LOOKBACK_DAYS)
    end_date = portfolio.exit_date + timedelta(days=1)
    history = fetcher(portfolio.symbol, start_date, end_date) if fetcher else _download_trade_review_history(
        portfolio.symbol,
        start_date,
        end_date,
    )
    _store_trade_review_ohlcv_rows(db, portfolio, start_date, history)
    db.flush()


def build_trade_review_payload(db: Session, portfolio: UserPortfolio) -> tuple[dict, dict]:
    rows = db.execute(
        select(StockRawData)
        .where(
            StockRawData.symbol == portfolio.symbol,
            StockRawData.record_date <= portfolio.exit_date,
        )
        .order_by(StockRawData.record_date.asc())
    ).scalars().all()

    data_quality = _build_data_quality(portfolio, rows)
    path_metrics = _build_path_metrics(portfolio, rows, data_quality)
    entry_indicators = _build_point_in_time_indicators(
        rows,
        portfolio.entry_date,
        _number(portfolio.entry_price),
        "entry",
        data_quality,
    )
    exit_indicators = _build_point_in_time_indicators(
        rows,
        portfolio.exit_date,
        _number(portfolio.exit_price),
        "exit",
        data_quality,
    )
    trade_result = {
        **path_metrics,
        "entry_indicators": entry_indicators,
        "exit_indicators": exit_indicators,
    }
    entry_market_regime = _classify_market_regime(rows, portfolio.entry_date)
    exit_market_regime = _classify_market_regime(rows, portfolio.exit_date)
    entry_indicators["market_regime"] = entry_market_regime
    exit_indicators["market_regime"] = exit_market_regime
    detected_events = _detect_holding_events(portfolio, rows)
    entry_review = _build_entry_review(portfolio, rows, entry_indicators, entry_market_regime)
    holding_review = _build_holding_review(portfolio, path_metrics, detected_events, exit_market_regime)
    exit_review = _build_exit_review(
        portfolio,
        rows,
        exit_indicators,
        path_metrics,
        detected_events,
        exit_market_regime,
    )
    operation_review = _build_operation_review(portfolio, entry_review, holding_review, exit_review, exit_market_regime)
    user_readable_conclusion = _build_user_readable_conclusion(
        data_quality,
        path_metrics,
        entry_review,
        exit_review,
        exit_indicators,
        detected_events,
    )

    review_result = {
        "data_quality": data_quality,
        "trade_result": trade_result,
        "entry_review": entry_review,
        "holding_review": holding_review,
        "exit_review": exit_review,
        "operation_review": operation_review,
        "user_readable_conclusion": user_readable_conclusion,
    }
    evidence_payload = {
        "trade": _serialize_trade(portfolio),
        "position_group_id": portfolio.position_group_id,
        "path_metrics": path_metrics,
        "entry_indicators": entry_indicators,
        "exit_indicators": exit_indicators,
        "detected_events": detected_events,
        "data_quality": data_quality,
        "source_data": {
            "symbol": portfolio.symbol,
            "rows_up_to_exit": len(rows),
            "holding_rows": len(_holding_rows(portfolio, rows)),
            "first_record_date": _date_to_iso(rows[0].record_date) if rows else None,
            "last_record_date": _date_to_iso(rows[-1].record_date) if rows else None,
        },
    }
    return review_result, evidence_payload


def _build_entry_review(
    portfolio: UserPortfolio,
    rows: list[StockRawData],
    entry_indicators: dict[str, Any],
    market_regime: str,
) -> dict[str, Any]:
    point_rows = _rows_up_to(rows, portfolio.entry_date)
    point_values = _point_in_time_values(rows, portfolio.entry_date)
    valid_closes = point_values["closes"]
    entry_close = valid_closes[-1] if valid_closes else _last_close(point_rows)
    entry_price = _number(portfolio.entry_price)
    review_price = entry_close if entry_close is not None else entry_price
    supporting: list[str] = []
    conflicting: list[str] = []
    caveats: list[str] = []
    classification = "range_entry"

    if market_regime == "insufficient_data" or review_price is None or len(valid_closes) < 20:
        caveats.append("進場日前可用價格資料少於 20 筆，進場分類信心偏低。")
        return _review_section("insufficient_data", "low", market_regime, supporting, conflicting, caveats)

    entry_vs_ma20 = _number(entry_indicators.get("entry_vs_ma20_pct"))
    entry_vs_ma60 = _number(entry_indicators.get("entry_vs_ma60_pct"))
    rsi14 = _number(entry_indicators.get("rsi14"))
    volume_ratio = _number(entry_indicators.get("volume_ratio"))
    previous_closes = valid_closes[:-1] if point_values["from_snapshot"] else [
        close for row in point_rows if row.record_date < portfolio.entry_date for close in [_close(row)] if close is not None
    ]
    recent_high = max(previous_closes[-20:]) if previous_closes else None

    if recent_high is not None and entry_close is not None and entry_close >= recent_high * 1.01:
        supporting.append("進場當日收盤價突破近 20 筆資料高點。")
        if volume_ratio is not None and volume_ratio >= 1.15:
            supporting.append("進場量能高於近 20 筆平均量。")
            classification = "breakout_entry"
        else:
            conflicting.append("突破時量能確認不足。")
            classification = "range_entry"

    if entry_vs_ma20 is not None and entry_vs_ma20 >= 8 and rsi14 is not None and rsi14 >= 70:
        supporting.append("進場價明顯高於 MA20，且 RSI 偏高。")
        if classification == "breakout_entry" and volume_ratio is not None and volume_ratio >= 1.5:
            conflicting.append("動能已有延伸，突破品質需要保守看待。")
        else:
            classification = "chase_entry"

    if market_regime == "downtrend" or _is_negative(entry_vs_ma20, -3) and _is_negative(entry_vs_ma60, -3):
        supporting.append("進場價低於關鍵均線，或當時處於偏弱趨勢。")
        classification = "weak_entry"

    if classification == "range_entry" and market_regime == "range_bound":
        supporting.append("進場時行情偏區間整理。")

    if classification == "range_entry" and (_near_zero(entry_vs_ma20, 3) or _near_zero(entry_vs_ma60, 3)):
        supporting.append("進場位置接近 MA20 或 MA60，較像拉回後進場。")
        classification = "pullback_entry"

    if classification == "range_entry" and market_regime == "uptrend" and not supporting:
        supporting.append("進場方向順著上升趨勢，但缺少明確突破或拉回訊號。")

    if market_regime == "strong_momentum" and classification == "chase_entry":
        caveats.append("強動能行情可能維持高檔，單純超買訊號不一定代表立即轉弱。")
    if market_regime == "high_volatility":
        caveats.append("當時波動偏高，單純用均線判斷進場品質的信心會下降。")
    confidence = _confidence_for(supporting, conflicting, caveats)
    return _review_section(classification, confidence, market_regime, supporting, conflicting, caveats)


def _build_holding_review(
    portfolio: UserPortfolio,
    path_metrics: dict[str, Any],
    detected_events: list[dict[str, Any]],
    market_regime: str,
) -> dict[str, Any]:
    caveats: list[str] = []
    holding_days = portfolio.holding_days
    if holding_days is None and portfolio.exit_date is not None:
        holding_days = (portfolio.exit_date - portfolio.entry_date).days
    if holding_days is not None and holding_days < 3:
        caveats.append("持有期間很短，趨勢事件檢討的參考價值有限。")
    if path_metrics.get("max_profit_pct") is None:
        caveats.append("持有期間缺少足夠可用價格，無法完整檢討持有過程。")
    risk_events = [event for event in detected_events if event["type"] != "new_high_continuation"]
    supporting = [f"{event['date']} 偵測到「{_event_label(event['type'])}」。" for event in detected_events[:3]]
    confidence = "low" if caveats else "medium"
    return {
        "market_regime": market_regime,
        "confidence": confidence,
        "detected_events": detected_events,
        "event_count": len(detected_events),
        "risk_event_count": len(risk_events),
        "supporting_signals": supporting,
        "conflicting_signals": [],
        "caveats": caveats,
        "summary": "持有期間檢討會依時間順序列出有限數量的技術事件，避免把短期雜訊過度解讀。",
    }


def _build_exit_review(
    portfolio: UserPortfolio,
    rows: list[StockRawData],
    exit_indicators: dict[str, Any],
    path_metrics: dict[str, Any],
    detected_events: list[dict[str, Any]],
    market_regime: str,
) -> dict[str, Any]:
    valid_closes = _point_in_time_values(rows, portfolio.exit_date)["closes"]
    exit_price = _number(portfolio.exit_price)
    realized_return_pct = _number(portfolio.realized_return_pct)
    if realized_return_pct is None:
        realized_return_pct = _number(path_metrics.get("realized_return_pct"))
    supporting: list[str] = []
    conflicting: list[str] = []
    caveats: list[str] = []
    classification = "technical_break_exit"

    if market_regime == "insufficient_data" or exit_price is None or len(valid_closes) < 20:
        caveats.append("出場日前可用價格資料少於 20 筆，出場分類信心偏低。")
        return _review_section("insufficient_data", "low", market_regime, supporting, conflicting, caveats)

    exit_vs_ma20 = _number(exit_indicators.get("exit_vs_ma20_pct"))
    exit_vs_ma60 = _number(exit_indicators.get("exit_vs_ma60_pct"))
    profit_giveback_pct = _number(path_metrics.get("profit_giveback_pct"))
    max_drawdown_pct = _number(path_metrics.get("max_drawdown_pct"))
    event_types = {event["type"] for event in detected_events}
    has_break_event = bool(event_types & {"ma20_break", "ma60_break", "support_break"})

    if realized_return_pct is not None and realized_return_pct <= -8 and max_drawdown_pct is not None and max_drawdown_pct <= -12:
        supporting.append("出場發生在明顯虧損與持有期間深度回撤之後。")
        classification = "late_stop_exit"
    elif realized_return_pct is not None and realized_return_pct <= -3 and (has_break_event or _is_negative(exit_vs_ma20, -2)):
        supporting.append("技術轉弱後出場，有助於限制虧損擴大。")
        classification = "stop_loss_exit"
    elif realized_return_pct is not None and realized_return_pct >= 5 and (
        has_break_event or _is_negative(exit_vs_ma20, -1) or (profit_giveback_pct is not None and profit_giveback_pct >= 3)
    ):
        supporting.append("動能降溫或獲利回吐後出場，有保護既有獲利的效果。")
        classification = "profit_protection_exit"
    elif realized_return_pct is not None and 0 < realized_return_pct < 5 and not has_break_event and market_regime in {"uptrend", "strong_momentum"}:
        supporting.append("趨勢證據仍偏正向時先小幅獲利了結。")
        classification = "early_profit_exit"
    elif realized_return_pct is not None and realized_return_pct < 0 and _near_holding_low(exit_price, path_metrics):
        supporting.append("虧損後在接近持有期間低點的位置出場。")
        classification = "panic_exit"
    else:
        supporting.append("出場時已可看到均線或支撐轉弱跡象。")
        classification = "technical_break_exit"

    if has_break_event:
        supporting.append("持有期間檢討曾偵測到均線或支撐破位。")
    if _is_positive(exit_vs_ma20, 2) and classification in {"stop_loss_exit", "technical_break_exit", "panic_exit"}:
        conflicting.append("出場價仍高於 MA20，技術破位判斷需要保守看待。")
    if _is_positive(exit_vs_ma60, 2) and classification in {"technical_break_exit", "panic_exit"}:
        conflicting.append("出場價仍高於 MA60，較長期趨勢尚未確認轉弱。")
    if market_regime == "range_bound" and classification == "technical_break_exit":
        caveats.append("區間盤整時，單一均線跌破較容易產生雜訊。")
    if market_regime == "high_volatility":
        caveats.append("當時波動偏高，單純用跌破訊號判斷出場品質的信心會下降。")
    confidence = _confidence_for(supporting, conflicting, caveats)
    return _review_section(classification, confidence, market_regime, supporting, conflicting, caveats)


def _build_operation_review(
    portfolio: UserPortfolio,
    entry_review: dict[str, Any],
    holding_review: dict[str, Any],
    exit_review: dict[str, Any],
    market_regime: str,
) -> dict[str, Any]:
    caveats: list[str] = []
    supporting: list[str] = ["本次檢討範圍僅限目前這一筆已結案出場批次。"]
    conflicting: list[str] = []
    if entry_review["classification"] == "insufficient_data" or exit_review["classification"] == "insufficient_data":
        caveats.append("部分檢討階段的市場資料不足，結論需要保守解讀。")
    if holding_review["risk_event_count"]:
        supporting.append("持有期間檢討在出場前曾偵測到風險事件。")
    if exit_review["classification"] in {"late_stop_exit", "panic_exit"}:
        conflicting.append("出場檢討顯示這次執行可能偏被動或偏晚。")
    confidence = _confidence_for(supporting, conflicting, caveats)
    return {
        "classification": "rule_based_trade_review",
        "confidence": confidence,
        "market_regime": market_regime,
        "supporting_signals": [_risk_language_text(item) for item in supporting],
        "conflicting_signals": [_risk_language_text(item) for item in conflicting],
        "caveats": [_risk_language_text(item) for item in caveats],
        "reviewed_portfolio_id": portfolio.id,
        "position_group_id": portfolio.position_group_id,
        "scope": "current_closed_row_only",
        "summary": _risk_language_text("本次整體檢討只評估這一筆出場批次，不會合併同一部位群組的其他分批交易。"),
    }


def _build_user_readable_conclusion(
    data_quality: dict[str, Any],
    path_metrics: dict[str, Any],
    entry_review: dict[str, Any],
    exit_review: dict[str, Any],
    exit_indicators: dict[str, Any],
    detected_events: list[dict[str, Any]],
) -> dict[str, Any]:
    verdict = _overall_verdict(data_quality, path_metrics, entry_review, exit_review, exit_indicators, detected_events)
    return {
        "overall_verdict": verdict,
        "overall_verdict_label": _risk_language_text(_overall_verdict_label(verdict)),
        "one_sentence_reason": _risk_language_text(_one_sentence_reason(verdict, path_metrics, exit_review, exit_indicators)),
        "evidence": [_risk_language_text(item) for item in _user_readable_evidence(verdict, data_quality, path_metrics, exit_review, exit_indicators, detected_events)],
        "next_time_rules": [_risk_language_text(item) for item in _next_time_rules(verdict)],
    }


def _overall_verdict(
    data_quality: dict[str, Any],
    path_metrics: dict[str, Any],
    entry_review: dict[str, Any],
    exit_review: dict[str, Any],
    exit_indicators: dict[str, Any],
    detected_events: list[dict[str, Any]],
) -> str:
    exit_classification = exit_review.get("classification")
    if data_quality.get("status") == "insufficient":
        return "insufficient"
    if entry_review.get("classification") == "insufficient_data" or exit_classification == "insufficient_data":
        return "insufficient"
    if exit_classification in {"late_stop_exit", "panic_exit"}:
        return "late"

    realized_return_pct = _number(path_metrics.get("realized_return_pct"))
    exit_vs_ma20 = _number(exit_indicators.get("exit_vs_ma20_pct"))
    exit_vs_ma60 = _number(exit_indicators.get("exit_vs_ma60_pct"))
    market_regime = exit_review.get("market_regime")
    event_types = {event["type"] for event in detected_events}
    has_break_event = bool(event_types & {"ma20_break", "ma60_break", "support_break"})

    if (
        realized_return_pct is not None
        and 0 < realized_return_pct < 5
        and _is_positive(exit_vs_ma20, 0)
        and _is_positive(exit_vs_ma60, 0)
        and market_regime in {"uptrend", "strong_momentum", "high_volatility"}
    ):
        return "early"

    supported_technical_break = exit_classification == "technical_break_exit" and (
        has_break_event or _is_negative(exit_vs_ma20, -1) or _is_negative(exit_vs_ma60, -1)
    )
    if exit_classification in {"profit_protection_exit", "stop_loss_exit"} or supported_technical_break:
        return "reasonable"
    return "reasonable"


def _overall_verdict_label(verdict: str) -> str:
    labels = {
        "early": "這次出場偏早",
        "reasonable": "這次出場合理",
        "late": "這次出場偏晚",
        "insufficient": "資料不足",
    }
    return labels[verdict]


def _one_sentence_reason(
    verdict: str,
    path_metrics: dict[str, Any],
    exit_review: dict[str, Any],
    exit_indicators: dict[str, Any],
) -> str:
    realized_return_pct = _number(path_metrics.get("realized_return_pct"))
    return_text = f"本次實現報酬約 {realized_return_pct:.2f}%" if realized_return_pct is not None else "本次實現報酬缺少可用數值"
    if verdict == "early":
        return f"{return_text}，但出場時仍站上 MA20/MA60 且行情未明確轉弱，較像提前小賺離場。"
    if verdict == "reasonable":
        return f"{return_text}，出場與獲利保護、停損或技術轉弱訊號相符。"
    if verdict == "late":
        return f"{return_text}，出場發生在較大回撤或接近低點之後，執行節奏偏晚。"
    if exit_review.get("market_regime") == "insufficient_data":
        return "出場日前資料不足，無法穩定判斷這次出場早晚。"
    exit_vs_ma20 = _number(exit_indicators.get("exit_vs_ma20_pct"))
    if exit_vs_ma20 is None:
        return "關鍵均線資料不足，無法穩定判斷這次出場早晚。"
    return "可用的 point-in-time 資料不足，無法穩定判斷這次出場早晚。"


def _user_readable_evidence(
    verdict: str,
    data_quality: dict[str, Any],
    path_metrics: dict[str, Any],
    exit_review: dict[str, Any],
    exit_indicators: dict[str, Any],
    detected_events: list[dict[str, Any]],
) -> list[str]:
    if verdict == "insufficient":
        notes = data_quality.get("notes")
        if isinstance(notes, list) and notes:
            return [str(note) for note in notes[:3]]
        return ["可用市場資料不足，無法建立穩定的交易檢討結論。"]

    evidence: list[str] = []
    realized_return_pct = _number(path_metrics.get("realized_return_pct"))
    if realized_return_pct is not None:
        evidence.append(f"本次實現報酬約 {realized_return_pct:.2f}%。")

    exit_vs_ma20 = _number(exit_indicators.get("exit_vs_ma20_pct"))
    exit_vs_ma60 = _number(exit_indicators.get("exit_vs_ma60_pct"))
    if _is_positive(exit_vs_ma20, 0) and _is_positive(exit_vs_ma60, 0):
        evidence.append(f"出場價仍高於 MA20 約 {exit_vs_ma20:.2f}%，也高於 MA60 約 {exit_vs_ma60:.2f}%。")
    elif _is_negative(exit_vs_ma20, -1) or _is_negative(exit_vs_ma60, -1):
        ma_notes = []
        if _is_negative(exit_vs_ma20, -1):
            ma_notes.append(f"低於 MA20 約 {abs(exit_vs_ma20):.2f}%")
        if _is_negative(exit_vs_ma60, -1):
            ma_notes.append(f"低於 MA60 約 {abs(exit_vs_ma60):.2f}%")
        evidence.append(f"出場價{'，'.join(ma_notes)}，技術結構已有轉弱跡象。")

    profit_giveback_pct = _number(path_metrics.get("profit_giveback_pct"))
    event_types = {event["type"] for event in detected_events}
    if "profit_giveback" in event_types or (profit_giveback_pct is not None and profit_giveback_pct >= 3):
        if profit_giveback_pct is not None:
            evidence.append(f"持有期間高點到出場約回吐 {profit_giveback_pct:.2f}%，已有保護獲利的理由。")
        else:
            evidence.append("持有期間曾偵測到獲利回吐事件，已有保護獲利的理由。")

    break_events = [_event_label(event["type"]) for event in detected_events if event["type"] in {"ma20_break", "ma60_break", "support_break"}]
    if break_events:
        evidence.append(f"持有期間曾出現{'、'.join(break_events)}，出場前已有技術破位證據。")

    if exit_review.get("market_regime") == "high_volatility":
        evidence.append("出場時屬高波動環境，單一均線或短線訊號的可信度會降低。")

    return evidence or [exit_review.get("summary", "已依規則完成出場檢討。")]


def _next_time_rules(verdict: str) -> list[str]:
    rules = {
        "early": [
            "若價格仍站上 MA20/MA60 且趨勢未轉弱，先保留核心部位，只用分批停利處理小幅獲利。",
            "小賺出場前先檢查是否已跌破 MA20、MA60 或近期支撐，沒有破位就把移動停利放在 MA20 下方。",
            "高波動行情中不要只因短線震盪出場，至少等待收盤跌破關鍵均線或獲利回吐達預設門檻。",
        ],
        "reasonable": [
            "獲利已有明顯回吐時，可以分批落袋並把剩餘部位停利線上移到 MA20 或前低下方。",
            "虧損單跌破 MA20、MA60 或近期支撐時，優先執行原定停損，不用等反彈確認。",
            "出場後記錄觸發訊號，下一次用同一組均線與回吐門檻檢查紀律是否一致。",
        ],
        "late": [
            "進場前先寫好最大可承受虧損，跌破停損價或關鍵支撐時直接執行。",
            "若虧損擴大且價格接近持有期間低點，不要等情緒修復才處理，先降低部位風險。",
            "每次移動停損只往有利方向調整，避免把原本的防守線越放越寬。",
        ],
        "insufficient": [
            "下次建立交易紀錄時，同步保存進場日前至少 60 筆與出場日前至少 60 筆價格資料。",
            "出場後先確認 MA20、MA60、RSI14 與持有期間高低點都有資料，再解讀檢討結論。",
            "資料不足時不要把結論當成操作評分，只把它視為補資料提醒。",
        ],
    }
    return rules[verdict]


def _review_section(
    classification: str,
    confidence: str,
    market_regime: str,
    supporting_signals: list[str],
    conflicting_signals: list[str],
    caveats: list[str],
) -> dict[str, Any]:
    return {
        "classification": classification,
        "confidence": confidence,
        "market_regime": market_regime,
        "supporting_signals": [_risk_language_text(item) for item in supporting_signals],
        "conflicting_signals": [_risk_language_text(item) for item in conflicting_signals],
        "caveats": [_risk_language_text(item) for item in caveats],
        "summary": _risk_language_text(_summary_for(classification)),
    }


def _summary_for(classification: str) -> str:
    summaries = {
        "breakout_entry": "進場偏向突破型，價格與量能都有一定確認。",
        "pullback_entry": "進場偏向拉回型，位置接近均線支撐區。",
        "chase_entry": "依進場當時資料判斷，進場位置已有技術面延伸。",
        "weak_entry": "進場時趨勢或均線結構偏弱。",
        "range_entry": "進場位於區間內，缺少明確突破訊號。",
        "profit_protection_exit": "出場有保護獲利的效果，發生在動能降溫或回吐跡象之後。",
        "stop_loss_exit": "技術轉弱後出場，有助於降低虧損擴大。",
        "late_stop_exit": "出場發生在較大虧損後，可能反應偏慢。",
        "early_profit_exit": "在趨勢尚未明確轉弱前先小幅獲利了結。",
        "panic_exit": "出場接近短期低點，缺少較強確認訊號。",
        "technical_break_exit": "出場與當時可見的技術轉弱訊號一致。",
        "insufficient_data": "可用的 point-in-time 資料不足，無法穩定分類。",
    }
    return summaries.get(classification, "已完成規則化交易檢討。")


def _insufficient_indicator_note(label: str, key: str, count: int) -> str:
    side = "進場" if label == "entry" else "結案"
    indicator_labels = {
        "ma20": "MA20",
        "ma60": "MA60",
        "rsi14": "RSI14",
        "volume_ratio": "量比",
    }
    return f"{side}日前只有 {count} 筆可用資料，無法穩定計算 {indicator_labels.get(key, key)}。"


def _risk_language_text(text: str) -> str:
    replacements = {
        "這次出場合理": "這次結案節奏合理",
        "這次出場偏早": "這次結案節奏偏早",
        "這次出場偏晚": "這次結案節奏偏晚",
        "小賺出場": "小幅獲利結案",
        "小賺離場": "小幅獲利結案",
        "出場日前": "結案日前",
        "出場前": "結案前",
        "出場後": "結案後",
        "出場時": "結案時",
        "出場價": "結案價",
        "出場發生": "結案發生",
        "出場與": "結案與",
        "出場": "結案",
        "停損": "風險控制",
        "停利": "獲利保護",
        "減碼": "降低曝險",
        "分批落袋": "分批保護獲利",
    }
    rewritten = text
    for source, target in replacements.items():
        rewritten = rewritten.replace(source, target)
    return rewritten


def _event_label(event_type: str) -> str:
    labels = {
        "new_high_continuation": "持有期間創高",
        "volume_down_day": "放量下跌",
        "ma20_break": "跌破 MA20",
        "ma60_break": "跌破 MA60",
        "support_break": "跌破近期支撐",
        "rsi_overheated": "RSI 過熱",
        "profit_giveback": "獲利回吐",
    }
    return labels.get(event_type, event_type)


def _serialize_trade(portfolio: UserPortfolio) -> dict[str, Any]:
    realized_return_pct = _number(portfolio.realized_return_pct)
    return {
        "id": portfolio.id,
        "position_group_id": portfolio.position_group_id,
        "symbol": portfolio.symbol,
        "entry_price": _number(portfolio.entry_price),
        "entry_date": _date_to_iso(portfolio.entry_date),
        "quantity": portfolio.quantity,
        "exit_date": _date_to_iso(portfolio.exit_date),
        "exit_price": _number(portfolio.exit_price),
        "exit_quantity": portfolio.exit_quantity,
        "realized_pnl": _number(portfolio.realized_pnl),
        "realized_return_pct": realized_return_pct,
        "return_pct": realized_return_pct,
        "holding_days": portfolio.holding_days,
    }


def _build_data_quality(portfolio: UserPortfolio, rows: list[StockRawData]) -> dict[str, Any]:
    notes: list[str] = []
    insufficient_data: list[str] = []
    if not rows:
        notes.append("出場日前沒有找到此股票的原始市場資料。")
        insufficient_data.extend([
            "holding_path_prices",
            "entry_ma20",
            "entry_ma60",
            "entry_rsi14",
            "entry_volume_ratio",
            "exit_ma20",
            "exit_ma60",
            "exit_rsi14",
            "exit_volume_ratio",
        ])
    elif not _holding_rows(portfolio, rows):
        notes.append("進場日至出場日期間沒有找到可用的持有期間價格資料。")
        insufficient_data.append("holding_path_prices")
    return {
        "status": "insufficient" if insufficient_data else "ok",
        "notes": [_risk_language_text(note) for note in notes],
        "insufficient_data": insufficient_data,
    }


def _build_path_metrics(
    portfolio: UserPortfolio,
    rows: list[StockRawData],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    entry_price = _number(portfolio.entry_price)
    exit_price = _number(portfolio.exit_price)
    holding_days = portfolio.holding_days
    if holding_days is None and portfolio.exit_date is not None:
        holding_days = (portfolio.exit_date - portfolio.entry_date).days

    metrics = {
        "entry_date": _date_to_iso(portfolio.entry_date),
        "exit_date": _date_to_iso(portfolio.exit_date),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "realized_pnl": _number(portfolio.realized_pnl),
        "realized_return_pct": _number(portfolio.realized_return_pct),
        "holding_days": holding_days,
        "max_profit_pct": None,
        "max_drawdown_pct": None,
        "profit_giveback_pct": None,
        "highest_close_during_holding": None,
        "lowest_close_during_holding": None,
    }
    if metrics["realized_return_pct"] is None and entry_price and exit_price is not None:
        metrics["realized_return_pct"] = _round_pct((exit_price - entry_price) / entry_price * 100)

    closes = [_close(row) for row in _holding_rows(portfolio, rows)]
    valid_closes = [close for close in closes if close is not None]
    if not valid_closes:
        _add_insufficient(data_quality, "holding_path_prices", "持有期間沒有可用收盤價。")
        return metrics
    if not entry_price:
        _add_insufficient(data_quality, "entry_price", "進場價格缺失或為 0。")
        return metrics

    highest_close = max(valid_closes)
    lowest_close = min(valid_closes)
    metrics["highest_close_during_holding"] = _round_price(highest_close)
    metrics["lowest_close_during_holding"] = _round_price(lowest_close)
    metrics["max_profit_pct"] = _round_pct((highest_close - entry_price) / entry_price * 100)
    metrics["max_drawdown_pct"] = _round_pct((lowest_close - entry_price) / entry_price * 100)
    if exit_price is not None:
        metrics["profit_giveback_pct"] = _round_pct(max(0.0, (highest_close - exit_price) / entry_price * 100))
    return metrics


def _build_point_in_time_indicators(
    rows: list[StockRawData],
    as_of: date | None,
    trade_price: float | None,
    label: str,
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    if as_of is None:
        _add_insufficient(data_quality, f"{label}_date", f"{label}_date is missing.")
        return {}
    point_values = _point_in_time_values(rows, as_of)
    valid_closes = point_values["closes"]
    valid_volumes = point_values["volumes"]

    result: dict[str, Any] = {"as_of_date": _date_to_iso(as_of)}
    ma20 = ma(valid_closes, 20)
    ma60 = ma(valid_closes, 60)
    rsi14 = calc_rsi(valid_closes, period=14)
    result["ma20"] = _round_price(ma20)
    result["ma60"] = _round_price(ma60)
    result["rsi14"] = _round_pct(rsi14)
    result["entry_vs_ma20_pct" if label == "entry" else "exit_vs_ma20_pct"] = _distance_pct(trade_price, ma20)
    result["entry_vs_ma60_pct" if label == "entry" else "exit_vs_ma60_pct"] = _distance_pct(trade_price, ma60)

    volume_ratio = None
    if len(valid_volumes) >= 20:
        avg_volume_20 = sum(valid_volumes[-20:]) / 20
        if avg_volume_20:
            volume_ratio = valid_volumes[-1] / avg_volume_20
    result["volume_ratio"] = _round_pct(volume_ratio)

    required_lengths = {"ma20": 20, "ma60": 60, "rsi14": 15, "volume_ratio": 20}
    for key, required_length in required_lengths.items():
        if len(valid_closes if key != "volume_ratio" else valid_volumes) < required_length:
            _add_insufficient(
                data_quality,
                f"{label}_{key}",
                _insufficient_indicator_note(label, key, len(valid_closes if key != "volume_ratio" else valid_volumes)),
            )
    return result


def _holding_rows(portfolio: UserPortfolio, rows: list[StockRawData]) -> list[StockRawData]:
    if portfolio.exit_date is None:
        return []
    return [row for row in rows if portfolio.entry_date <= row.record_date <= portfolio.exit_date]


def _rows_up_to(rows: list[StockRawData], as_of: date | None) -> list[StockRawData]:
    if as_of is None:
        return []
    return [row for row in rows if row.record_date <= as_of]


def _classify_market_regime(rows: list[StockRawData], as_of: date | None) -> str:
    point_rows = _rows_up_to(rows, as_of)
    point_values = _point_in_time_values(rows, as_of)
    valid_closes = point_values["closes"]
    if len(valid_closes) < 20:
        return "insufficient_data"

    recent_closes = valid_closes[-20:]
    latest_close = recent_closes[-1]
    ma20 = ma(valid_closes, 20)
    ma60 = ma(valid_closes, 60)
    rsi14 = calc_rsi(valid_closes, period=14)
    recent_return_pct = _distance_pct(latest_close, recent_closes[0])
    recent_range_pct = _distance_pct(max(recent_closes), min(recent_closes))
    avg_daily_range_pct = _average_daily_range_pct_from_values(
        point_values["closes"],
        point_values["highs"],
        point_values["lows"],
    )
    if avg_daily_range_pct is None:
        avg_daily_range_pct = _average_daily_range_pct(point_rows[-20:])

    if avg_daily_range_pct is not None and avg_daily_range_pct >= 6:
        return "high_volatility"
    if ma20 is not None and rsi14 is not None and latest_close > ma20 and rsi14 >= 75:
        if _is_positive(_distance_pct(latest_close, ma20), 6) or _is_positive(recent_return_pct, 12):
            return "strong_momentum"
    if ma20 is not None and latest_close < ma20 and (ma60 is None or ma20 < ma60):
        return "downtrend"
    if ma20 is not None and latest_close > ma20 and (ma60 is None or ma20 > ma60):
        return "uptrend"
    if recent_range_pct is not None and recent_range_pct <= 12:
        return "range_bound"
    return "range_bound"


def _needs_trade_review_backfill(rows: list[StockRawData], portfolio: UserPortfolio) -> bool:
    entry_values = _point_in_time_values(rows, portfolio.entry_date)
    exit_values = _point_in_time_values(rows, portfolio.exit_date)
    holding_has_close = any(_close(row) is not None for row in _holding_rows(portfolio, rows))
    return (
        len(entry_values["closes"]) < TRADE_REVIEW_REQUIRED_LOOKBACK_ROWS
        or len(exit_values["closes"]) < TRADE_REVIEW_REQUIRED_LOOKBACK_ROWS
        or not holding_has_close
    )


def _download_trade_review_history(symbol: str, start_date: date, end_date: date):
    return yf.download(
        symbol,
        start=start_date.isoformat(),
        end=end_date.isoformat(),
        interval="1d",
        progress=False,
        threads=False,
    )


def _store_trade_review_ohlcv_rows(db: Session, portfolio: UserPortfolio, start_date: date, history: Any) -> None:
    if portfolio.exit_date is None:
        return
    bars = [bar for bar in _iter_history_bars(history) if start_date <= bar["date"] <= portfolio.exit_date]
    if not bars:
        return

    existing_rows = db.execute(
        select(StockRawData).where(
            StockRawData.symbol == portfolio.symbol,
            StockRawData.record_date.in_([bar["date"] for bar in bars]),
        )
    ).scalars().all()
    existing_by_date = {row.record_date: row for row in existing_rows}
    previous_close: float | None = None

    for index, bar in enumerate(bars):
        row = existing_by_date.get(bar["date"])
        if row is not None and row.raw_data_is_final and _close(row) is not None:
            previous_close = _close(row)
            continue
        row = row or StockRawData(symbol=portfolio.symbol, record_date=bar["date"])
        if row.id is None:
            db.add(row)
        recent_bars = bars[max(0, index - 59):index + 1]
        volumes = [recent_bar["volume"] for recent_bar in recent_bars if recent_bar["volume"] is not None]
        avg_volume_20 = sum(volumes[-20:]) / len(volumes[-20:]) if volumes else None
        row.technical = {
            "name": portfolio.symbol,
            "ohlcv": {
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "previous_close": previous_close,
                "volume": bar["volume"],
                "avg_volume_20": avg_volume_20,
            },
            "indicators": {},
            "data_dates": {
                "ohlcv": bar["date"].isoformat(),
                "technical_indicators": bar["date"].isoformat(),
            },
        }
        row.raw_data_is_final = True
        previous_close = bar["close"]


def _iter_history_bars(history: Any) -> list[dict[str, Any]]:
    if isinstance(history, list):
        bars = []
        for item in history:
            if not isinstance(item, dict):
                continue
            bar = _normalize_history_bar(item.get("date"), item)
            if bar is not None:
                bars.append(bar)
        return bars
    if not hasattr(history, "iterrows") or getattr(history, "empty", True):
        return []
    bars = []
    for index, row in history.iterrows():
        bar = _normalize_history_bar(index, row)
        if bar is not None:
            bars.append(bar)
    return bars


def _normalize_history_bar(raw_date: Any, row: Any) -> dict[str, Any] | None:
    bar_date = _bar_date(raw_date)
    if bar_date is None:
        return None
    open_price = _finite_number(_row_value(row, "Open", "open"))
    high = _finite_number(_row_value(row, "High", "high"))
    low = _finite_number(_row_value(row, "Low", "low"))
    close = _finite_number(_row_value(row, "Close", "close"))
    volume = _finite_number(_row_value(row, "Volume", "volume"))
    if close is None:
        return None
    return {
        "date": bar_date,
        "open": open_price if open_price is not None else close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": volume,
    }


def _row_value(row: Any, *keys: str) -> Any:
    for key in keys:
        try:
            value = row[key]
        except (KeyError, TypeError, IndexError):
            continue
        if value is not None:
            return value
    return None


def _bar_date(value: Any) -> date | None:
    if hasattr(value, "date"):
        value = value.date()
    return value if isinstance(value, date) else None


def _finite_number(value: Any) -> float | None:
    number = _number(value)
    if number is None or number != number:
        return None
    return number


def _detect_holding_events(portfolio: UserPortfolio, rows: list[StockRawData]) -> list[dict[str, Any]]:
    holding_rows = _holding_rows(portfolio, rows)
    if len(holding_rows) < 2 or not _number(portfolio.entry_price):
        return []

    events: list[dict[str, Any]] = []
    entry_price = _number(portfolio.entry_price)
    running_high = entry_price
    prior_closes: list[float] = []
    prior_volumes: list[float] = []
    previous_close: float | None = None
    previous_ma20: float | None = None
    previous_ma60: float | None = None

    for row in rows:
        close = _close(row)
        volume = _volume(row)
        if close is None:
            continue
        in_holding_window = portfolio.entry_date <= row.record_date <= portfolio.exit_date
        ma20 = ma(prior_closes + [close], 20)
        ma60 = ma(prior_closes + [close], 60)
        rsi14 = calc_rsi(prior_closes + [close], period=14)
        avg_volume_20 = sum(prior_volumes[-19:] + [volume]) / len(prior_volumes[-19:] + [volume]) if volume is not None else None
        recent_support = min(prior_closes[-10:]) if len(prior_closes) >= 10 else None

        if in_holding_window and row.record_date > portfolio.entry_date:
            if running_high is not None and close > running_high and entry_price is not None and close >= entry_price * 1.05:
                _append_event(events, row, "new_high_continuation", close, "收盤價創下持有期間新高，且至少高於進場價 5%。")
            if previous_close is not None and close < previous_close and volume is not None and avg_volume_20 and volume / avg_volume_20 >= 1.5:
                _append_event(events, row, "volume_down_day", close, "下跌日量能至少達近期平均量的 1.5 倍。")
            if ma20 is not None and previous_ma20 is not None and previous_close is not None and previous_close >= previous_ma20 and close < ma20:
                _append_event(events, row, "ma20_break", close, "持有期間收盤價跌破 MA20。")
            if ma60 is not None and previous_ma60 is not None and previous_close is not None and previous_close >= previous_ma60 and close < ma60:
                _append_event(events, row, "ma60_break", close, "持有期間收盤價跌破 MA60。")
            if recent_support is not None and close < recent_support * 0.98:
                _append_event(events, row, "support_break", close, "收盤價跌破近 10 筆資料支撐超過 2%。")
            if rsi14 is not None and rsi14 >= 75:
                _append_event(events, row, "rsi_overheated", close, "持有期間 RSI14 達 75 以上，短線動能偏熱。")
            if running_high is not None and entry_price is not None and running_high > entry_price and close <= running_high * 0.95:
                _append_event(events, row, "profit_giveback", close, "收盤價自持有期間高點回落至少 5%。")
            if len(events) >= MAX_DETECTED_EVENTS:
                return events

        if in_holding_window:
            running_high = max(running_high, close) if running_high is not None else close
        prior_closes.append(close)
        if volume is not None:
            prior_volumes.append(volume)
        previous_close = close
        previous_ma20 = ma20
        previous_ma60 = ma60
    return events


def _append_event(events: list[dict[str, Any]], row: StockRawData, event_type: str, close: float, summary: str) -> None:
    if len(events) >= MAX_DETECTED_EVENTS:
        return
    if any(event["type"] == event_type and event["date"] == _date_to_iso(row.record_date) for event in events):
        return
    events.append({
        "date": _date_to_iso(row.record_date),
        "type": event_type,
        "summary": summary,
        "evidence": {"close": _round_price(close)},
    })


def _last_close(rows: list[StockRawData]) -> float | None:
    for row in reversed(rows):
        close = _close(row)
        if close is not None:
            return close
    return None


def _close(row: StockRawData) -> float | None:
    return _latest_technical_value(row, "close", "recent_closes")


def _high(row: StockRawData) -> float | None:
    return _latest_technical_value(row, "high", "recent_highs")


def _low(row: StockRawData) -> float | None:
    return _latest_technical_value(row, "low", "recent_lows")


def _volume(row: StockRawData) -> float | None:
    return _latest_technical_value(row, "volume", "recent_volumes")


def _latest_technical_value(row: StockRawData, ohlcv_key: str, recent_key: str) -> float | None:
    ohlcv_value = _number(_technical_section(row, "ohlcv").get(ohlcv_key))
    if ohlcv_value is not None:
        return ohlcv_value
    values = _technical_values(row, recent_key)
    return values[-1] if values else None


def _technical_values(row: StockRawData, key: str) -> list[float]:
    if not isinstance(row.technical, dict):
        return []
    raw_values = row.technical.get(key)
    if not isinstance(raw_values, list):
        return []
    values: list[float] = []
    for value in raw_values:
        number = _number(value)
        if number is not None:
            values.append(number)
    return values


def _latest_row_at_or_before(rows: list[StockRawData], as_of: date | None) -> StockRawData | None:
    if as_of is None:
        return None
    for row in reversed(rows):
        if row.record_date <= as_of:
            return row
    return None


def _point_in_time_values(rows: list[StockRawData], as_of: date | None) -> dict[str, Any]:
    latest_row = _latest_row_at_or_before(rows, as_of)
    if latest_row is not None:
        closes = _technical_values(latest_row, "recent_closes")
        if closes:
            return {
                "closes": closes,
                "highs": _technical_values(latest_row, "recent_highs"),
                "lows": _technical_values(latest_row, "recent_lows"),
                "volumes": _technical_values(latest_row, "recent_volumes"),
                "from_snapshot": True,
            }

    point_rows = _rows_up_to(rows, as_of)
    return {
        "closes": [close for row in point_rows for close in [_close(row)] if close is not None],
        "highs": [high for row in point_rows for high in [_high(row)] if high is not None],
        "lows": [low for row in point_rows for low in [_low(row)] if low is not None],
        "volumes": [volume for row in point_rows for volume in [_volume(row)] if volume is not None],
        "from_snapshot": False,
    }


def _technical_section(row: StockRawData, key: str) -> dict[str, Any]:
    if not isinstance(row.technical, dict):
        return {}
    section = row.technical.get(key)
    return section if isinstance(section, dict) else {}


def _distance_pct(value: float | None, baseline: float | None) -> float | None:
    if value is None or not baseline:
        return None
    return _round_pct((value - baseline) / baseline * 100)


def _average_daily_range_pct(rows: list[StockRawData]) -> float | None:
    ranges: list[float] = []
    for row in rows:
        high = _high(row)
        low = _low(row)
        close = _close(row)
        if high is not None and low is not None and close:
            ranges.append((high - low) / close * 100)
    if not ranges:
        return None
    return _round_pct(sum(ranges) / len(ranges))


def _average_daily_range_pct_from_values(closes: list[float], highs: list[float], lows: list[float]) -> float | None:
    aligned_length = min(len(closes), len(highs), len(lows))
    if aligned_length == 0:
        return None
    ranges: list[float] = []
    for close, high, low in zip(closes[-aligned_length:][-20:], highs[-aligned_length:][-20:], lows[-aligned_length:][-20:]):
        if close:
            ranges.append((high - low) / close * 100)
    if not ranges:
        return None
    return _round_pct(sum(ranges) / len(ranges))


def _near_zero(value: float | None, threshold: float) -> bool:
    return value is not None and abs(value) <= threshold


def _is_positive(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _is_negative(value: float | None, threshold: float) -> bool:
    return value is not None and value <= threshold


def _near_holding_low(exit_price: float, path_metrics: dict[str, Any]) -> bool:
    low = _number(path_metrics.get("lowest_close_during_holding"))
    if low is None:
        return False
    return exit_price <= low * 1.02


def _confidence_for(supporting: list[str], conflicting: list[str], caveats: list[str]) -> str:
    if len(supporting) >= 2 and not conflicting and not caveats:
        return "high"
    if supporting and len(conflicting) <= 1:
        return "medium"
    return "low"


def _add_insufficient(data_quality: dict[str, Any], key: str, note: str) -> None:
    insufficient = data_quality.setdefault("insufficient_data", [])
    notes = data_quality.setdefault("notes", [])
    if key not in insufficient:
        insufficient.append(key)
    if note not in notes:
        notes.append(note)
    data_quality["status"] = "insufficient"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_pct(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _round_price(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _date_to_iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value
