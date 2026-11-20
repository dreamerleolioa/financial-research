from copy import deepcopy
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from ai_stock_sentinel.calibration import official_corporate_actions as actions

DAY = date(2026, 11, 5)
STAMP = int(datetime(2026, 11, 5, 9, tzinfo=ZoneInfo("Asia/Taipei")).timestamp())


def chart():
    split = {"date": STAMP, "numerator": 542.04913, "denominator": 1000, "splitRatio": "542.04913:1000"}
    return {"chart": {"error": None, "result": [{"meta": {"symbol": "3714.TW",
            "exchangeTimezoneName": "Asia/Taipei"}, "timestamp": [STAMP],
            "events": {"splits": {str(STAMP): split},
                       "dividends": {str(STAMP): {"date": STAMP, "amount": 4.579508}}}}]}}


def test_reduction_requires_exact_raw_vendor_representation():
    proof = actions.reduction_precision(chart(), "3714.TW", DAY, "542.04915120", .54204913, 4.579508)
    assert proof["numerator"] == 542.04913 and proof["denominator"] == 1000


@pytest.mark.parametrize("change", ["other_symbol", "foreign_time", "missing_quote", "different_day", "wrong_bin",
                                   "same_bin_wrong_text", "different_units", "wrong_cash", "duplicate"])
def test_reduction_rejects_conflicting_raw_evidence(change):
    payload = chart()
    record = payload["chart"]["result"][0]
    splits = record["events"]["splits"]
    split = next(iter(splits.values()))
    if change == "other_symbol": record["meta"]["symbol"] = "1234.TW"
    if change == "foreign_time": record["meta"]["exchangeTimezoneName"] = "America/New_York"
    if change == "missing_quote": record["timestamp"] = []
    if change == "different_day": split["date"] += 86400
    if change == "wrong_bin": split.update(numerator=542.04919, splitRatio="542.04919:1000")
    if change == "same_bin_wrong_text": split["splitRatio"] = "542.04914:1000"
    if change == "different_units": split.update(numerator=54.204913, denominator=100, splitRatio="54.204913:100")
    if change == "wrong_cash": next(iter(record["events"]["dividends"].values()))["amount"] = 4.58
    if change == "duplicate": splits["extra"] = deepcopy(split)
    assert actions.reduction_precision(payload, "3714.TW", DAY, "542.04915120", .54204913, 4.579508) is None


@pytest.mark.parametrize("issue", ["extra_day", "extra_action", "wrong_event_key"])
def test_precision_requires_one_day_and_only_the_proven_split_and_refund(issue):
    payload = chart()
    record = payload["chart"]["result"][0]
    if issue == "extra_day":
        record["timestamp"].append(STAMP + 86400)
    if issue == "extra_action":
        record["events"]["capitalGains"] = {str(STAMP): {"date": STAMP, "amount": 1.}}
    if issue == "wrong_event_key":
        record["events"]["splits"]["invalid"] = record["events"]["splits"].pop(str(STAMP))
    assert actions.reduction_precision(payload, "3714.TW", DAY, "542.04915120", .54204913, 4.579508) is None


@pytest.mark.parametrize("damage", [None, "removed", "hash", "query_date", "terms", "version"])
def test_stored_precision_receipt_survives_independent_readback(damage):
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
    raw = chart()
    precision = actions.reduction_precision(raw,"3714.TW",DAY,"542.04915120",.54204913,4.579508)
    receipt = {"source_url": "https://query1.finance.yahoo.com/v8/finance/chart/3714.TW"
               "?period1=1793808000&period2=1793894400&interval=1d&events=div%2Csplits",
               "captured_at":"2026-11-19T01:00:00+00:00","snapshot_sha256":fingerprint(raw)}
    proof = {"evidence_version": actions.REDUCTION_PRECISION_VERSION,
             "reduction_precision":precision|{"chart":raw, "source":receipt}}
    event = {"symbol":"3714.TW","share_ratio":.5420491512,"cash_per_share":4.579508,
             "halt_start":str(DAY),"effective_date":"2026-11-16"}
    if damage=="removed": proof.pop("reduction_precision")
    if damage=="hash": receipt["snapshot_sha256"]="0"*64
    if damage=="query_date": receipt["source_url"]=receipt["source_url"].replace("1793808000","1793808001")
    if damage=="terms": event["share_ratio"]+=.001
    if damage=="version": proof["evidence_version"]=actions.EVIDENCE_VERSION
    assert actions.valid_precision_version(proof,event,actions.CASH_REDUCTION_HALT)==(damage is None)

    if damage is None:
        from ai_stock_sentinel.daily_radar.next_open_history_repair import _check_legacy_split
        from ai_stock_sentinel.daily_radar.next_open_official_repair import KEY
        row = {'date':str(DAY), 'stock_splits':.54204913, 'dividends':event['cash_per_share']}
        before = {'skip_reason':'stock_split_in_window', 'outcome':{}}
        after = {'skip_reason':actions.CASH_REDUCTION_HALT, 'outcome':{
            actions.PROVENANCE_KEY:proof | {'event':event | {'type':'cash_reduction'}},
            KEY:{'event_replay':{'kind':'corporate','yahoo':[row]}}}}
        _check_legacy_split(before, after)
        row['stock_splits'] = .54205
        with pytest.raises(ValueError, match='next_open_official_split_unverified'):
            _check_legacy_split(before, after)
