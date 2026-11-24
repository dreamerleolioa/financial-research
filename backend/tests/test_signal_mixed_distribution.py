from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from ai_stock_sentinel.calibration import twse_rights_recovery as twse
from ai_stock_sentinel.daily_radar import signal_action_evidence
from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceCollector
from ai_stock_sentinel.daily_radar.readiness import classify_window
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
from ai_stock_sentinel.daily_radar.validation_execution import _collect_locked_signal_prices
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
from test_daily_radar_validation_read import add_candidate, add_run
from test_radar_jobs import db
from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider

SIGNAL, EX_DATE = date(2026, 9, 8), date(2026, 9, 9)
DAYS = [SIGNAL, EX_DATE]
SYMBOL = "3706.TW"
CANDIDATE = {"symbol": SYMBOL, "record_date": str(SIGNAL), "data_dates": {"ohlcv": str(SIGNAL)},
             "input_snapshot": {"ohlcv": {"close": 91.3}}}
CASH = "(每股配發現金股利)除息"
FREE = "A. 按普通股股東持股比例每千股無償配股"
BONUS = "B. 員工紅利轉增資"
ISSUE = "C. (有償) 現金增資"


def receipt(path, params, raw):
    return {"source_url": f"{twse.TWSE}{path}?{urlencode(params)}", "snapshot_sha256": fingerprint(raw),
            "captured_at": "2026-11-23T08:00:00+00:00"}


def collect(notice="權息", detail_changes=None, *, offline=False, basis="signal_close", reference="80.27",
            combined="11.027273", price_outage=None, provider=None):
    def capture(path, params):
        if path in {"/exchangeReport/STOCK_DAY", "/indicesReport/MI_5MINS_HIST"}:
            if path == price_outage:
                from ai_stock_sentinel.calibration.research_prices import ResearchSourceError
                raise ResearchSourceError("monthly_report_unavailable", retryable=True, http_status=503)
            index = "stockNo" not in params
            field = "指數" if index else "價"
            prices = [22000, 22050] if index else [91.3, 80.3]
            raw = {"stat": "OK", "date": "20260901", "title": "發行量加權股價指數歷史資料" if index else "115年09月 3706 神達 各日成交資訊",
                   "fields": ["日期", *[k+field for k in ("開盤", "最高", "最低", "收盤")], "成交股數", "成交金額"],
                   "data": [[f"115/09/{day.day:02d}", *([price]*4), 100, 1000] for day, price in zip(DAYS, prices)]}
        elif path == twse.RIGHTS_DETAIL:
            if offline:
                raise TimeoutError("exchange unavailable")
            row = {"股票代號": "3706", CASH: "3 元／股", FREE: "100 股", BONUS: "0 股", ISSUE: "0 股",
                   "a. 公開承銷": "0 股", "b. 員工認購": "0 股", " c. 原股東認購": "0 股",
                   "每股認購金額": "0 元／股", "按股東持股比例每千股認購": "0 股"} | (detail_changes or {})
            raw = {"stat": "ok", "fields": list(row), "data": [list(row.values())]}
        else:
            raw = {"stat": "OK", "strDate": params["startDate"], "endDate": params["endDate"],
                   "fields": ["恢復買賣日期", "股票代號"], "data": []}
            if path == twse.EX_RIGHTS:
                raw.update(fields=["資料日期", "股票代號", "除權息前收盤價", "權/息", "詳細資料", "除權息參考價", "權值+息值"],
                           data=[["115年09月09日", "3706", "91.30", notice, "3706,20260909", reference, combined]])
        return raw, receipt(path, params, raw)

    provider = provider or RecoveringResearchPriceProvider()
    provider.capture_twse_report = capture
    return OfficialPriceCollector(provider, return_basis=basis).collect(SYMBOL, SIGNAL, EX_DATE)


def evaluate(candidate, evidence, calendar):
    row, = _collect_locked_signal_prices(
        [candidate], None, calendar=calendar, request=SimpleNamespace(windows=[1], benchmark_symbol="TAIEX"),
        collect_official=lambda *args: evidence, fetch_prices=lambda *args, **kwargs: {},
    )
    return row


def test_mixed_distribution_round_trips_without_return(db):
    saved = add_candidate(db, add_run(db, SIGNAL), SYMBOL)
    saved.input_snapshot = saved.input_snapshot | CANDIDATE["input_snapshot"]
    db.flush()
    candidate = CANDIDATE | {"candidate_id": saved.id}
    calendar = SimpleNamespace(sessions=DAYS, through=EX_DATE)
    row = evaluate(candidate, collect(), calendar)
    assert (row["status"], row["skip_reason"]) == ("skipped", "stock_distribution_in_window")
    proof = row["outcome"][signal_action_evidence.KEY]
    assert proof["action"]["cash_per_share"] == 3
    assert proof["action"]["share_ratio"] == 1.1
    assert "forward_return_pct" not in row["outcome"]
    assert upsert_forward_validation_results(db, [row], calendar=calendar)["terminal_skipped_count"] == 1
    persisted = db.scalar(select(DailyRadarForwardValidationResult))
    assert classify_window(candidate, row | {"outcome": persisted.outcome}, basis="signal_close",
                           window=1, as_of_date=EX_DATE, calendar=calendar)[0] == "terminal"
    assert upsert_forward_validation_results(db, [row], calendar=calendar)["records_written"] == 0

    assert signal_action_evidence.matches(proof, row["skip_reason"], SYMBOL, list(map(str, DAYS)), published_close=91.3)
    assert not signal_action_evidence.matches(proof, row["skip_reason"], SYMBOL, list(map(str, DAYS)), published_close=90)
    for key, replacement in [("stock_closes", proof["stock_closes"] | {str(EX_DATE): 1000}), ("action", proof["action"] | {"cash_per_share": 2}),
                             ("version", signal_action_evidence.VERSION), ("action_reports", {})]:
        bad = deepcopy(proof)
        bad[key] = replacement
        bad["sha256"] = fingerprint({k: v for k, v in bad.items() if k != "sha256"})
        assert not signal_action_evidence.matches(bad, row["skip_reason"], SYMBOL, list(map(str, DAYS)), published_close=91.3)


@pytest.mark.parametrize(("notice", "changes"), [
    ("權", {CASH: "3 元／股"}), ("權息", {CASH: "0 元／股"}), ("權息", {FREE: "0 股"}),
    ("權息", {BONUS: "1 股"}), ("權息", {ISSUE: "1 股"}), ("權息", {"每股認購金額": "1 元／股"}),
    ("權息", {"按股東持股比例每千股認購": "1 股"}), ("權息", {CASH: "3 股"}),
    ("權息", {CASH: "4 元／股"}), ("權息", {"股票代號": "1234"}), ("權息", {"a. 公開承銷": "1 股"}),
    ("權息", {"b. 員工認購": ""}), ("權息", {" c. 原股東認購": "1 股"}),
])
def test_contradictory_mixed_terms_remain_unresolved(notice, changes):
    assert signal_action_evidence.classify_action(CANDIDATE, DAYS, collect(notice, changes)) is None


def test_unavailable_mixed_detail_is_retryable():
    evidence = collect(offline=True)
    assert str(EX_DATE) in evidence.unavailable_terms
    row = evaluate(CANDIDATE | {"candidate_id": 1}, evidence, SimpleNamespace(sessions=DAYS, through=EX_DATE))
    assert row["skip_reason"] == "validation_price_source_unavailable"


def test_next_open_does_not_request_the_signal_close_detail():
    evidence = collect(offline=True, basis="next_open")
    assert not evidence.unavailable_terms
    assert not evidence.rights_terms


def test_pure_stock_distribution_keeps_its_replay_contract():
    reason, proof = signal_action_evidence.classify_action(CANDIDATE, DAYS, collect("權", {CASH: "0 元／股"}))
    assert reason == "stock_distribution_in_window"
    assert proof["version"] == signal_action_evidence.VERSION
    assert proof["action"]["cash_per_share"] == 0
    assert signal_action_evidence.matches(proof, reason, SYMBOL, list(map(str, DAYS)))

def test_replay_rejects_an_additional_notice_in_another_market_report():
    reason, proof = signal_action_evidence.classify_action(CANDIDATE, DAYS, collect())
    # Simulate a valid later capture of a conflicting face-value notice. Update
    # both hashes to show that scope reconciliation itself rejects the evidence.
    url, raw = next((url, raw) for url, raw in proof["action_reports"].items() if twse.FACE_VALUE in url)
    raw["data"].append(["115/09/09", "3706"])
    next(src for src in proof["sources"] if src["source_url"] == url)["snapshot_sha256"] = fingerprint(raw)
    proof["sha256"] = fingerprint({k:v for k,v in proof.items() if k != "sha256"})
    assert not signal_action_evidence.matches(proof, reason, SYMBOL, list(map(str, DAYS)), published_close=91.3)


def test_cached_mixed_detail_outage_retains_retryability(monkeypatch):
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider, ResearchSourceError

    provider = RecoveringResearchPriceProvider()
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(1)
        raise ResearchSourceError("research_source_http_error", retryable=True, http_status=503)
    monkeypatch.setattr(provider, "_request", fail)
    for _ in range(2):
        with pytest.raises(ResearchSourceError) as exc:
            provider.capture_twse_report(twse.RIGHTS_DETAIL, {"response":"json", "STK_NO":"3706", "T1":"20260909"})
        assert exc.value.retryable and exc.value.http_status == 503
    assert attempts == [1]

def test_monthly_audit_includes_weekends_and_requires_each_market_report():
    from ai_stock_sentinel.daily_radar.mixed_distribution_evidence import MARKET_PATHS, _action_reports
    _, proof = signal_action_evidence.classify_action(CANDIDATE, DAYS, collect())
    dates = ["2026-08-28", *map(str, DAYS)]
    params = {"response":"json", "startDate":"20260828", "endDate":"20260907"}
    for path, date_field in MARKET_PATHS.items():
        raw = {"stat":"OK", "strDate":params["startDate"], "endDate":params["endDate"],
               "fields":["股票代號", date_field], "data":[]}
        proof["action_reports"][receipt(path, params, raw)["source_url"]] = raw
    assert _action_reports(proof, "3706", dates)
    url = receipt(twse.EX_RIGHTS, params, {})["source_url"]
    proof["action_reports"][url]["data"] = [["3706", "115/08/29"]]
    assert _action_reports(proof, "3706", dates) is None
    del proof["action_reports"][url]
    assert _action_reports(proof, "3706", dates) is None

@pytest.mark.parametrize(("reference", "eligible"), [("75.01", True), ("75.00", False)])
def test_reference_at_half_cent_uses_exact_published_terms(reference, eligible):
    terms = {CASH: "5.7943 元／股", FREE: "140 股"}
    evidence = collect(detail_changes=terms, reference=reference, combined="16.295000")
    assert bool(signal_action_evidence.classify_action(CANDIDATE, DAYS, evidence)) == eligible


def test_overlapping_captures_must_agree_on_the_whole_intersection():
    from ai_stock_sentinel.daily_radar.mixed_distribution_evidence import _action_reports
    _, proof = signal_action_evidence.classify_action(CANDIDATE, DAYS, collect())
    original = next(raw for url, raw in proof["action_reports"].items() if twse.EX_RIGHTS + "?" in url)
    params = {"response":"json", "startDate":"20260907", "endDate":"20260909"}
    overlapping = deepcopy(original) | {"strDate":"20260907"}
    proof["action_reports"][receipt(twse.EX_RIGHTS, params, overlapping)["source_url"]] = overlapping
    assert _action_reports(proof, "3706", list(map(str, DAYS)))
    overlapping["data"] = []
    assert _action_reports(proof, "3706", list(map(str, DAYS))) is None


@pytest.mark.parametrize("price_path", ["/exchangeReport/STOCK_DAY", "/indicesReport/MI_5MINS_HIST"])
def test_mixed_monthly_price_outage_remains_retryable_on_reuse(price_path):
    provider = RecoveringResearchPriceProvider()
    for attempt in range(2):
        evidence = collect(price_outage=price_path, provider=provider)
        row = evaluate(CANDIDATE | {"candidate_id": 1}, evidence, SimpleNamespace(sessions=DAYS, through=EX_DATE))
        assert row["skip_reason"] == "validation_price_source_unavailable", attempt
