from copy import deepcopy
from types import SimpleNamespace

import pytest

from ai_stock_sentinel.calibration.forward_validation import merge_price_series
from ai_stock_sentinel.calibration.repository import completed_price_rows_from_raw_data


DAY = "2026-06-03"
PRICE = {"date": DAY, "open": 100, "high": 110, "low": 90, "close": 100}


def raw(prices, *, final=True):
    return SimpleNamespace(raw_data_is_final=final, technical={"price_history": prices})


def combine(rows, method):
    if method == "load":
        return completed_price_rows_from_raw_data([raw([row]) for row in rows])
    return merge_price_series({"2330.TW": rows[:1]}, {"2330.TW": rows[1:]})["2330.TW"]


@pytest.mark.parametrize("method", ["load", "merge"])
@pytest.mark.parametrize("field,value", [("open", 101), ("high", 111), ("low", 89), ("close", 101)])
@pytest.mark.parametrize("reverse", [False, True])
def test_valid_same_date_ohlc_differences_keep_conflict_evidence(method, field, value, reverse):
    rows = [PRICE, PRICE | {field: value}]
    result = combine(list(reversed(rows)) if reverse else rows, method)
    assert len(result) == 1
    assert result[0]["ohlc_conflict_fields"] == [field]
    assert result[0][field] == rows[0 if reverse else 1][field]


@pytest.mark.parametrize("method", ["load", "merge"])
def test_identical_numeric_prices_do_not_create_conflicts(method):
    identical = {key: str(value) if key != "date" else value for key, value in PRICE.items()}
    result = combine([PRICE, identical, PRICE], method)
    assert result == [PRICE]


@pytest.mark.parametrize("method", ["load", "merge"])
def test_complementary_fields_and_equal_close_only_rows_are_not_conflicts(method):
    result = combine([
        {"date": DAY, "high": 110, "low": 90, "close": 100},
        {"date": DAY, "open": 100, "close": 100},
        {"date": DAY, "close": 100},
    ], method)
    assert result == [PRICE]


@pytest.mark.parametrize("method", ["load", "merge"])
def test_prior_conflicts_survive_consistent_values_empty_markers_and_further_merging(method):
    incoming = PRICE | {"ohlc_conflict_fields": []}
    rows = [PRICE | {"ohlc_conflict_fields": ["high"]}, incoming,
            PRICE | {"ohlc_conflict_fields": ["low"]}]
    result = combine(rows, method)
    assert result[0]["ohlc_conflict_fields"] == ["high", "low"]
    repeated = merge_price_series({"2330.TW": result}, {"2330.TW": [incoming]})["2330.TW"]
    assert repeated[0]["ohlc_conflict_fields"] == ["high", "low"]


@pytest.mark.parametrize("method", ["load", "merge"])
def test_partial_row_does_not_erase_evidence_needed_for_a_later_open_conflict(method):
    result = combine([PRICE, {"date": DAY, "close": 100}, PRICE | {"open": 101}], method)
    assert result[0]["ohlc_conflict_fields"] == ["open"]


@pytest.mark.parametrize("method", ["load", "merge"])
@pytest.mark.parametrize("field,value", [("open", 101), ("high", 111), ("low", 89)])
def test_invalid_intermediate_field_cannot_erase_earlier_valid_conflict_evidence(method, field, value):
    result = combine([PRICE, PRICE | {field: 0}, PRICE | {field: value}], method)
    assert result[0]["ohlc_conflict_fields"] == [field]


def test_loader_ignores_nonfinal_conflicting_snapshot_and_preserves_input():
    inputs = [raw([deepcopy(PRICE)]), raw([PRICE | {"close": 101}], final=False)]
    assert completed_price_rows_from_raw_data(inputs) == [PRICE]
    assert inputs[0].technical["price_history"] == [PRICE]
