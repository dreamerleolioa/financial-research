from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast

from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_RISK_LABELS
from ai_stock_sentinel.daily_radar.types import DailyRadarRiskLabel


PrefilterStatus = Literal["accepted", "rejected", "stale_data"]


@dataclass(frozen=True)
class PrefilterConfig:
    min_avg_turnover_value_million: float = 300.0
    min_price: float = 20.0
    max_missing_trading_days_60: int = 3
    max_core_data_lag_days: int = 2
    max_rsi14: float = 80.0
    max_bias20: float = 20.0
    max_mfi14: float = 85.0
    max_volume_ratio: float = 2.5
    max_margin_delta_pct: float = 10.0
    max_margin_to_volume: float = 4.0


REASON_TEXT: dict[str, str] = {
    "low_liquidity": "20日平均成交金額低於觀察門檻",
    "min_price": "收盤價低於最低觀察門檻",
    "data_gap": "近60個交易日資料缺漏過多",
    "stale_core_data": "核心資料日期過舊",
    "overextended": "短期技術指標過熱",
    "weak_structure": "長期結構偏弱",
    "margin_crowding": "融資籌碼過度擁擠",
}


def prefilter_record(
    record: Mapping[str, Any],
    *,
    config: PrefilterConfig | None = None,
) -> dict[str, Any]:
    active_config = config or PrefilterConfig()
    ohlcv = _mapping(record.get("ohlcv"))
    indicators = _mapping(record.get("indicators"))
    institutional_flow = _mapping(record.get("institutional_flow"))
    margin = _mapping(record.get("margin"))
    data_dates = {key: str(value) for key, value in _mapping(record.get("data_dates")).items()}

    close = _float(ohlcv.get("close"))
    avg_volume_20 = _float(ohlcv.get("avg_volume_20"))
    avg_turnover_value_million = close * avg_volume_20 / 1_000_000
    missing_trading_days_60 = _int(indicators.get("missing_trading_days_60"))
    record_date = _parse_date(str(record.get("record_date")))

    debug = _build_debug(
        config=active_config,
        close=close,
        avg_volume_20=avg_volume_20,
        avg_turnover_value_million=avg_turnover_value_million,
        missing_trading_days_60=missing_trading_days_60,
        indicators=indicators,
        margin=margin,
        data_dates=data_dates,
        record_date=record_date,
    )

    reasons: list[dict[str, Any]] = []
    if avg_turnover_value_million < active_config.min_avg_turnover_value_million:
        reasons.append(
            _reason(
                "low_liquidity",
                value=round(avg_turnover_value_million, 3),
                threshold=active_config.min_avg_turnover_value_million,
            )
        )

    if close < active_config.min_price:
        reasons.append(_reason("min_price", value=close, threshold=active_config.min_price))

    if missing_trading_days_60 > active_config.max_missing_trading_days_60 or _has_risk_flag(
        institutional_flow,
        margin,
        flag="data_gap",
    ):
        reasons.append(
            _reason(
                "data_gap",
                value=missing_trading_days_60,
                threshold=active_config.max_missing_trading_days_60,
            )
        )

    stale_fields = _stale_core_fields(record_date, data_dates, active_config.max_core_data_lag_days)
    if stale_fields or _has_risk_flag(institutional_flow, margin, flag="stale_data"):
        reasons.append(
            _reason(
                "stale_core_data",
                stale_fields=stale_fields,
                max_lag_days=active_config.max_core_data_lag_days,
            )
        )

    overextended_metrics = _overextended_metrics(indicators, active_config)
    if overextended_metrics or _has_risk_flag(institutional_flow, margin, flag="overextended"):
        reasons.append(_reason("overextended", metrics=overextended_metrics))

    weak_structure = _weak_structure(close, indicators)
    if (
        weak_structure["close_below_ma60"]
        and weak_structure["ma_stack_weak"]
        and weak_structure["participation_weak"]
    ):
        reasons.append(_reason("weak_structure", **weak_structure))

    margin_metrics = _margin_crowding_metrics(margin, active_config)
    if margin_metrics or _has_risk_flag(institutional_flow, margin, flag="margin_crowding"):
        reasons.append(_reason("margin_crowding", metrics=margin_metrics))

    status: PrefilterStatus = "accepted"
    if reasons:
        status = "stale_data" if any(reason["code"] == "stale_core_data" for reason in reasons) else "rejected"

    return {
        "symbol": record.get("symbol"),
        "name": record.get("name"),
        "record_date": record.get("record_date"),
        "fixture_case": record.get("fixture_case"),
        "expected_bucket_seed": record.get("expected_bucket_seed"),
        "prefilter_status": status,
        "prefilter_reasons": reasons,
        "risk_labels": _risk_labels_from_reasons(reasons),
        "data_dates": data_dates,
        "debug": debug,
        "source_record": {
            "ohlcv": dict(ohlcv),
            "indicators": dict(indicators),
            "institutional_flow": dict(institutional_flow),
            "margin": dict(margin),
        },
    }


def run_stage1_prefilter(
    records: Iterable[Mapping[str, Any]],
    *,
    limit: int = 100,
    include_rejected: bool = True,
    config: PrefilterConfig | None = None,
) -> list[dict[str, Any]]:
    results = [prefilter_record(record, config=config) for record in records]
    accepted = sorted(
        (result for result in results if result["prefilter_status"] == "accepted"),
        key=lambda result: (
            -float(result["debug"]["liquidity"]["avg_turnover_value_million"]),
            str(result["symbol"]),
        ),
    )[:limit]

    if not include_rejected:
        return accepted

    non_accepted = [result for result in results if result["prefilter_status"] != "accepted"]
    return accepted + non_accepted


def _build_debug(
    *,
    config: PrefilterConfig,
    close: float,
    avg_volume_20: float,
    avg_turnover_value_million: float,
    missing_trading_days_60: int,
    indicators: Mapping[str, Any],
    margin: Mapping[str, Any],
    data_dates: Mapping[str, str],
    record_date: date | None,
) -> dict[str, Any]:
    return {
        "thresholds": {
            "min_avg_turnover_value_million": config.min_avg_turnover_value_million,
            "min_price": config.min_price,
            "max_missing_trading_days_60": config.max_missing_trading_days_60,
            "max_core_data_lag_days": config.max_core_data_lag_days,
            "max_rsi14": config.max_rsi14,
            "max_bias20": config.max_bias20,
            "max_mfi14": config.max_mfi14,
            "max_volume_ratio": config.max_volume_ratio,
            "max_margin_delta_pct": config.max_margin_delta_pct,
            "max_margin_to_volume": config.max_margin_to_volume,
        },
        "liquidity": {
            "close": close,
            "avg_volume_20": avg_volume_20,
            "avg_turnover_value_million": round(avg_turnover_value_million, 3),
        },
        "price": {"close": close},
        "data_quality": {"missing_trading_days_60": missing_trading_days_60},
        "freshness": {
            "record_date": record_date.isoformat() if record_date else None,
            "data_dates": dict(data_dates),
        },
        "overextension": {
            "rsi14": _float(indicators.get("rsi14")),
            "bias20": _float(indicators.get("bias20")),
            "mfi14": _float(indicators.get("mfi14")),
            "volume_ratio": _float(indicators.get("volume_ratio")),
        },
        "structure": _weak_structure(close, indicators),
        "margin": {
            "margin_delta_pct": _float(margin.get("margin_delta_pct")),
            "margin_to_volume": _float(margin.get("margin_to_volume")),
        },
    }


def _reason(code: str, **details: Any) -> dict[str, Any]:
    return {
        "code": code,
        "text": REASON_TEXT[code],
        "details": details,
    }


def _overextended_metrics(indicators: Mapping[str, Any], config: PrefilterConfig) -> dict[str, float]:
    metrics: dict[str, float] = {}
    checks = {
        "rsi14": config.max_rsi14,
        "bias20": config.max_bias20,
        "mfi14": config.max_mfi14,
        "volume_ratio": config.max_volume_ratio,
    }
    for key, threshold in checks.items():
        value = _float(indicators.get(key))
        if value >= threshold:
            metrics[key] = value
    return metrics


def _weak_structure(close: float, indicators: Mapping[str, Any]) -> dict[str, Any]:
    ma5 = _float(indicators.get("ma5"))
    ma20 = _float(indicators.get("ma20"))
    ma60 = _float(indicators.get("ma60"))
    obv_trend = str(indicators.get("obv_trend") or "")
    volume_ratio = _float(indicators.get("volume_ratio"))
    return {
        "close": close,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "obv_trend": obv_trend,
        "volume_ratio": volume_ratio,
        "close_below_ma60": close < ma60,
        "ma_stack_weak": ma5 < ma20 < ma60,
        "participation_weak": obv_trend in {"falling", "weak"} or volume_ratio < 0.95,
    }


def _margin_crowding_metrics(margin: Mapping[str, Any], config: PrefilterConfig) -> dict[str, float]:
    metrics: dict[str, float] = {}
    margin_delta_pct = _float(margin.get("margin_delta_pct"))
    margin_to_volume = _float(margin.get("margin_to_volume"))
    if margin_delta_pct >= config.max_margin_delta_pct:
        metrics["margin_delta_pct"] = margin_delta_pct
    if margin_to_volume >= config.max_margin_to_volume:
        metrics["margin_to_volume"] = margin_to_volume
    return metrics


def _stale_core_fields(
    record_date: date | None,
    data_dates: Mapping[str, str],
    max_lag_days: int,
) -> dict[str, int]:
    if record_date is None:
        return {}

    stale_fields: dict[str, int] = {}
    for key in ("ohlcv", "technical_indicators", "institutional_flow", "margin"):
        data_date = _parse_date(data_dates.get(key))
        if data_date is None:
            stale_fields[key] = max_lag_days + 1
            continue
        lag_days = (record_date - data_date).days
        if lag_days > max_lag_days:
            stale_fields[key] = lag_days
    return stale_fields


def _has_risk_flag(*payloads: Mapping[str, Any], flag: str) -> bool:
    for payload in payloads:
        risk_flags = payload.get("risk_flags")
        if isinstance(risk_flags, list) and flag in risk_flags:
            return True
    return False


def _risk_labels_from_reasons(reasons: list[dict[str, Any]]) -> list[DailyRadarRiskLabel]:
    labels: list[DailyRadarRiskLabel] = []
    for reason in reasons:
        code = reason["code"]
        if code in DAILY_RADAR_RISK_LABELS and code not in labels:
            labels.append(cast(DailyRadarRiskLabel, code))
    return labels


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    return float(value)


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    return int(value)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = [
    "PrefilterConfig",
    "PrefilterStatus",
    "prefilter_record",
    "run_stage1_prefilter",
]
