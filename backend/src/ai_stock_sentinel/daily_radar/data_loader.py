from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


DAILY_RADAR_FIXTURE_FILES = (
    "ohlcv.json",
    "institutional_flow.json",
    "margin.json",
)

FLAT_INSTITUTIONAL_FLOW_FIELDS = (
    "flow_label",
    "foreign_net_cumulative",
    "trust_net_cumulative",
    "investment_trust_buy",
    "foreign_buy",
    "three_party_net",
    "consecutive_buy_days",
    "consecutive_sell_days",
    "dominant_buyer",
    "flow_strength",
    "source_provider",
    "universe_primary_track",
    "institutional_universe_tracks",
    "universe_track_metrics",
    "same_day_rank",
    "recent_accumulation_rank",
    "scores",
    "warnings",
)


@dataclass(frozen=True)
class DailyRadarJoinedRecord:
    symbol: str
    name: str
    record_date: str
    ohlcv: dict[str, Any]
    indicators: dict[str, Any]
    technical_profile: dict[str, Any]
    price_history: list[dict[str, Any]]
    institutional_flow: dict[str, Any]
    margin: dict[str, Any]
    data_dates: dict[str, str]
    fixture_case: str | None = None
    expected_bucket_seed: str | None = None
    source: str = "fixture"
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "record_date": self.record_date,
            "ohlcv": dict(self.ohlcv),
            "indicators": dict(self.indicators),
            "technical_profile": dict(self.technical_profile),
            "price_history": list(self.price_history),
            "institutional_flow": dict(self.institutional_flow),
            "margin": dict(self.margin),
            "data_dates": dict(self.data_dates),
            "fixture_case": self.fixture_case,
            "expected_bucket_seed": self.expected_bucket_seed,
        }


def load_daily_radar_fixture_records(fixture_dir: str | Path) -> list[dict[str, Any]]:
    fixture_path = Path(fixture_dir)
    ohlcv_records = _fixture_records_by_symbol(fixture_path / "ohlcv.json")
    flow_records = _fixture_records_by_symbol(fixture_path / "institutional_flow.json")
    margin_records = _fixture_records_by_symbol(fixture_path / "margin.json")

    records: list[dict[str, Any]] = []
    for symbol in ohlcv_records:
        ohlcv_record = ohlcv_records[symbol]
        flow_record = flow_records[symbol]
        margin_record = margin_records[symbol]
        records.append(
            DailyRadarJoinedRecord(
                symbol=symbol,
                name=str(ohlcv_record["name"]),
                record_date=str(ohlcv_record["record_date"]),
                ohlcv=dict(ohlcv_record["ohlcv"]),
                indicators=dict(ohlcv_record["indicators"]),
                technical_profile=dict(_as_mapping(ohlcv_record.get("technical_profile"))),
                price_history=list(_as_list(ohlcv_record.get("price_history"))),
                institutional_flow=dict(flow_record["institutional_flow"]),
                margin=dict(margin_record["margin"]),
                data_dates=_merge_data_dates(ohlcv_record, flow_record, margin_record),
                fixture_case=ohlcv_record.get("fixture_case"),
                expected_bucket_seed=ohlcv_record.get("expected_bucket_seed"),
            ).to_dict()
        )

    return records


def load_daily_radar_cache_records(rows: Iterable[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        technical = _as_mapping(_read_field(row, "technical"))
        institutional_payload = _as_mapping(_read_field(row, "institutional"))
        fundamental = _as_mapping(_read_field(row, "fundamental"))

        records.append(
            DailyRadarJoinedRecord(
                symbol=str(_read_field(row, "symbol")),
                name=str(technical.get("name") or _read_field(row, "symbol")),
                record_date=_date_to_string(_read_field(row, "record_date")),
                ohlcv=dict(_as_mapping(technical.get("ohlcv"))),
                indicators=dict(_as_mapping(technical.get("indicators"))),
                technical_profile=dict(_as_mapping(technical.get("technical_profile"))),
                price_history=list(_as_list(technical.get("price_history"))),
                institutional_flow=_normalize_institutional_flow(institutional_payload),
                margin=dict(_as_mapping(fundamental.get("margin"))),
                data_dates=_merge_mapping_data_dates(technical, institutional_payload, fundamental),
                fixture_case=_coalesce_fixture_case(technical, institutional_payload, fundamental),
                expected_bucket_seed=None,
                source="cache",
            ).to_dict()
        )
    return records


def _fixture_records_by_symbol(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {record["symbol"]: record for record in payload["records"]}


def _merge_data_dates(*records: Mapping[str, Any]) -> dict[str, str]:
    data_dates: dict[str, str] = {}
    for record in records:
        data_dates.update({key: str(value) for key, value in _as_mapping(record.get("data_dates")).items()})
    return data_dates


def _merge_mapping_data_dates(*payloads: Mapping[str, Any]) -> dict[str, str]:
    data_dates: dict[str, str] = {}
    for payload in payloads:
        dates = _as_mapping(payload.get("data_dates"))
        data_dates.update({key: _date_to_string(value) for key, value in dates.items()})
    return data_dates


def _coalesce_fixture_case(*payloads: Mapping[str, Any]) -> str | None:
    for payload in payloads:
        fixture_case = payload.get("fixture_case")
        if fixture_case is not None:
            return str(fixture_case)
    return None


def _normalize_institutional_flow(institutional_payload: Mapping[str, Any]) -> dict[str, Any]:
    nested_flow = dict(_as_mapping(institutional_payload.get("institutional_flow")))
    if nested_flow:
        return nested_flow

    flat_flow = {
        key: institutional_payload[key]
        for key in FLAT_INSTITUTIONAL_FLOW_FIELDS
        if key in institutional_payload and institutional_payload[key] is not None
    }
    _copy_alias(flat_flow, "foreign_net_shares", institutional_payload, "foreign_net_cumulative")
    _copy_alias(flat_flow, "investment_trust_net_shares", institutional_payload, "trust_net_cumulative")
    _copy_alias(flat_flow, "three_party_net_shares", institutional_payload, "three_party_net")
    _copy_alias(flat_flow, "consecutive_positive_days", institutional_payload, "consecutive_buy_days")
    _copy_alias(flat_flow, "consecutive_negative_days", institutional_payload, "consecutive_sell_days")
    _copy_alias(flat_flow, "flow_state", institutional_payload, "flow_label")
    return flat_flow


def _copy_alias(target: dict[str, Any], target_key: str, source: Mapping[str, Any], source_key: str) -> None:
    value = source.get(source_key)
    if value is not None:
        target[target_key] = value


def _read_field(row: Any, field_name: str) -> Any:
    if isinstance(row, Mapping):
        return row[field_name]
    return getattr(row, field_name)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _date_to_string(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


__all__ = [
    "DAILY_RADAR_FIXTURE_FILES",
    "DailyRadarJoinedRecord",
    "load_daily_radar_cache_records",
    "load_daily_radar_fixture_records",
]
