from __future__ import annotations

import gzip
import hashlib
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from ai_stock_sentinel import api
from ai_stock_sentinel.active_etf_holdings.provider import (
    ActiveEtfFundDescriptor,
    ActiveEtfFundSnapshot,
    ActiveEtfHoldingRow,
    ActiveEtfProviderError,
    IssuerOfficialActiveEtfProvider,
    MoneyDjActiveEtfProvider,
    parse_moneydj_holdings_html,
    parse_nomura_holdings_payload,
    parse_twse_active_equity_funds,
)
from ai_stock_sentinel.active_etf_holdings.router import (
    get_active_etf_holdings_provider,
    get_active_etf_verification_provider,
)
from ai_stock_sentinel.active_etf_holdings.service import (
    get_active_etf_daily_response,
    refresh_active_etf_holdings,
)
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.db.models import (
    ActiveEtfFund,
    ActiveEtfHolding,
    ActiveEtfHoldingSnapshot,
    ActiveEtfSourceHolding,
    ActiveEtfSourceObservation,
    User,
)
from ai_stock_sentinel.db.session import Base, get_db
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "active_etf_holdings"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture()
def etf_db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            ActiveEtfFund.__table__,
            ActiveEtfHoldingSnapshot.__table__,
            ActiveEtfHolding.__table__,
            ActiveEtfSourceObservation.__table__,
            ActiveEtfSourceHolding.__table__,
        ],
    )
    with Session(engine) as session:
        yield session


def _fund(code: str = "00985A", name: str = "主動野村台灣50") -> ActiveEtfFundDescriptor:
    return ActiveEtfFundDescriptor(
        fund_code=code,
        name=name,
        category="domestic",
        source_url=(
            "https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm"
            f"?etfid={code}.TW&page=1"
        ),
    )


def _snapshot(
    data_date: date,
    rows: list[tuple[str, str, int, str]],
    *,
    fund: ActiveEtfFundDescriptor | None = None,
) -> ActiveEtfFundSnapshot:
    descriptor = fund or _fund()
    normalized = "|".join(f"{symbol}:{shares}:{weight}" for symbol, _, shares, weight in rows)
    return ActiveEtfFundSnapshot(
        fund=descriptor,
        data_date=data_date,
        fetched_at=datetime(data_date.year, data_date.month, data_date.day, 6, tzinfo=timezone.utc),
        holdings=tuple(
            ActiveEtfHoldingRow(
                symbol=symbol,
                name=name,
                shares=shares,
                weight_pct=Decimal(weight),
                position_order=index,
            )
            for index, (symbol, name, shares, weight) in enumerate(rows)
        ),
        skipped_instrument_count=0,
        payload_hash=(normalized + "-raw").ljust(64, "0")[:64],
        normalized_hash=normalized.ljust(64, "0")[:64],
        raw_payload=normalized.encode(),
        source_url=descriptor.source_url,
    )


class FakeProvider:
    source_provider = "moneydj"

    def __init__(
        self,
        snapshots: dict[str, ActiveEtfFundSnapshot],
        *,
        funds: list[ActiveEtfFundDescriptor] | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.funds = funds or [snapshot.fund for snapshot in snapshots.values()]

    def fetch_registry(self) -> list[ActiveEtfFundDescriptor]:
        return self.funds

    def fetch_snapshot(
        self,
        fund: ActiveEtfFundDescriptor,
        *,
        expected_data_date: date | None = None,
    ) -> ActiveEtfFundSnapshot:
        value = self.snapshots[fund.fund_code]
        if isinstance(value, Exception):
            raise value
        return value


class FakeVerificationProvider:
    source_provider = "issuer_official"

    def __init__(self, snapshots: dict[str, ActiveEtfFundSnapshot]) -> None:
        self.snapshots = snapshots

    def supports(self, fund_code: str) -> bool:
        return fund_code in self.snapshots

    def fetch_snapshot(
        self,
        fund: ActiveEtfFundDescriptor,
        *,
        expected_data_date: date | None = None,
    ) -> ActiveEtfFundSnapshot:
        value = self.snapshots[fund.fund_code]
        if isinstance(value, Exception):
            raise value
        return value


def _official_snapshot(snapshot: ActiveEtfFundSnapshot) -> ActiveEtfFundSnapshot:
    return ActiveEtfFundSnapshot(
        fund=snapshot.fund,
        data_date=snapshot.data_date,
        fetched_at=snapshot.fetched_at,
        holdings=tuple(
            ActiveEtfHoldingRow(
                symbol=row.symbol.split(".", 1)[0],
                name=row.name,
                shares=row.shares,
                weight_pct=row.weight_pct,
                position_order=row.position_order,
            )
            for row in snapshot.holdings
        ),
        skipped_instrument_count=0,
        payload_hash=(snapshot.payload_hash[::-1]),
        normalized_hash=(snapshot.normalized_hash[::-1]),
        parser_version="issuer-test-v1",
        raw_payload=b"official:" + snapshot.raw_payload,
        source_provider="issuer_official",
        source_url="https://issuer.example/holdings",
    )


class FakeResponse:
    def __init__(
        self,
        *,
        payload=None,
        text: str = "",
        content: bytes | None = None,
        status_code: int = 200,
    ) -> None:
        self._payload = payload
        self.text = text
        self.content = content if content is not None else (text.encode() if text else b"")
        self.status_code = status_code

    def json(self):
        return self._payload


def test_parse_twse_registry_keeps_only_active_equity_funds() -> None:
    funds = parse_twse_active_equity_funds(
        {
            "status": "ok",
            "fields": ["證券代號", "證券簡稱", "管理方式", "ETF分類"],
            "data": [
                ["00985A", "主動野村台灣50", "主動式交易所交易基金", "domestic"],
                ["00984D", "主動聯博全球非投", "主動式交易所交易基金", "bfIncome"],
            ],
        }
    )

    assert [fund.fund_code for fund in funds] == ["00985A"]
    assert funds[0].category == "domestic"
    assert "etfid=00985A.TW" in funds[0].source_url


def test_parse_moneydj_snapshot_reads_equities_and_skips_futures() -> None:
    html = (FIXTURE_ROOT / "moneydj_00985a.html").read_text()

    snapshot = parse_moneydj_holdings_html(
        html,
        fund=_fund(),
        fetched_at=datetime(2026, 8, 31, 5, tzinfo=timezone.utc),
    )

    assert snapshot.data_date == date(2026, 8, 28)
    assert snapshot.skipped_instrument_count == 1
    assert [(row.symbol, row.shares, row.weight_pct) for row in snapshot.holdings] == [
        ("2330.TW", 588000, Decimal("13.51")),
        ("2454.TW", 26000, Decimal("4.82")),
    ]
    assert len(snapshot.payload_hash) == 64
    assert len(snapshot.normalized_hash) == 64


def test_parse_moneydj_snapshot_reports_explicitly_unpublished_holdings() -> None:
    html = """
    <h1>(00409A.TW)-全部持股</h1>
    <div class="sdate3"></div>
    <table>
      <tr><th>持股名稱</th><th>投資比例(%)</th><th>持有股數</th></tr>
      <tr class="emptyrow"><td colspan="3">查無資料</td></tr>
    </table>
    """

    with pytest.raises(
        ActiveEtfProviderError,
        match="active_etf_holdings_not_published",
    ):
        parse_moneydj_holdings_html(
            html,
            fund=_fund("00409A", "主動復華全球50"),
        )


def test_parse_moneydj_snapshot_ignores_unrelated_empty_table() -> None:
    holdings_html = (FIXTURE_ROOT / "moneydj_00985a.html").read_text()
    html = """
    <table>
      <tr><th>其他資料</th></tr>
      <tr class="emptyrow"><td>查無資料</td></tr>
    </table>
    """ + holdings_html

    snapshot = parse_moneydj_holdings_html(html, fund=_fund())

    assert snapshot.data_date == date(2026, 8, 28)


def test_parse_moneydj_snapshot_fails_closed_on_duplicate_equity() -> None:
    html = (FIXTURE_ROOT / "moneydj_00985a.html").read_text().replace(
        "FICDFN*1.TF&amp;back=00985A.TW\">台積電期貨(FICDFN*1.TF)",
        "2330.TW&amp;back=00985A.TW\">重複台積電(2330.TW)",
    )

    with pytest.raises(ActiveEtfProviderError, match="active_etf_duplicate_holding"):
        parse_moneydj_holdings_html(html, fund=_fund())


def test_parse_moneydj_snapshot_rejects_future_source_date() -> None:
    html = (FIXTURE_ROOT / "moneydj_00985a.html").read_text().replace(
        "資料日期：2026/08/28",
        "資料日期：2026/09/01",
    )

    with pytest.raises(ActiveEtfProviderError, match="active_etf_data_date_in_future"):
        parse_moneydj_holdings_html(
            html,
            fund=_fund(),
            fetched_at=datetime(2026, 8, 31, 5, tzinfo=timezone.utc),
        )


def test_parse_moneydj_snapshot_rejects_wrong_fund_page() -> None:
    html = (FIXTURE_ROOT / "moneydj_00985a.html").read_text()

    with pytest.raises(ActiveEtfProviderError, match="active_etf_fund_identity_mismatch"):
        parse_moneydj_holdings_html(html, fund=_fund("00982A", "主動群益台灣強棒"))


def test_parse_moneydj_snapshot_uses_header_positions_instead_of_fixed_columns() -> None:
    html = (FIXTURE_ROOT / "moneydj_00985a.html").read_text()
    html = html.replace(
        "<th>投資比例(%)</th><th>持有股數</th>",
        "<th>持有股數</th><th>投資比例(%)</th>",
    )
    for weight, shares in [("13.51", "588,000"), ("5.07", "110"), ("4.82", "26,000")]:
        html = html.replace(
            f"<td>{weight}</td>\n          <td>{shares}</td>",
            f"<td>{shares}</td>\n          <td>{weight}</td>",
        )

    snapshot = parse_moneydj_holdings_html(html, fund=_fund())

    assert [(row.symbol, row.shares, row.weight_pct) for row in snapshot.holdings] == [
        ("2330.TW", 588000, Decimal("13.51")),
        ("2454.TW", 26000, Decimal("4.82")),
    ]


def test_parse_moneydj_snapshot_fails_closed_on_incomplete_holding_row() -> None:
    html = (FIXTURE_ROOT / "moneydj_00985a.html").read_text().replace(
        "<td>588,000</td>",
        "",
        1,
    )

    with pytest.raises(ActiveEtfProviderError, match="active_etf_holding_row_invalid"):
        parse_moneydj_holdings_html(html, fund=_fund())


def test_parse_moneydj_snapshot_rejects_shares_above_json_safe_integer() -> None:
    html = (FIXTURE_ROOT / "moneydj_00985a.html").read_text().replace(
        "588,000",
        "9,007,199,254,740,992",
        1,
    )

    with pytest.raises(ActiveEtfProviderError, match="active_etf_holding_shares_invalid"):
        parse_moneydj_holdings_html(html, fund=_fund())


def test_moneydj_provider_uses_twse_registry_and_public_holdings_page() -> None:
    calls: list[str] = []
    html = (FIXTURE_ROOT / "moneydj_00985a.html").read_text()

    def fake_get(url: str, **kwargs):
        calls.append(url)
        if "activeList" in url:
            return FakeResponse(
                payload={
                    "status": "ok",
                    "fields": ["證券代號", "證券簡稱", "管理方式", "ETF分類"],
                    "data": [["00985A", "主動野村台灣50", "主動式交易所交易基金", "domestic"]],
                }
            )
        return FakeResponse(text=html)

    provider = MoneyDjActiveEtfProvider(request_get=fake_get)
    registry = provider.fetch_registry()
    snapshot = provider.fetch_snapshot(registry[0])

    assert snapshot.fund.fund_code == "00985A"
    assert len(calls) == 2
    assert "activeList" in calls[0]
    assert "etfid=00985A.TW" in calls[1]


def test_moneydj_provider_preserves_exact_response_bytes() -> None:
    html = (FIXTURE_ROOT / "moneydj_00985a.html").read_text()
    raw_payload = b"upstream-prefix:" + html.encode("utf-8")
    provider = MoneyDjActiveEtfProvider(
        request_get=lambda *args, **kwargs: FakeResponse(text=html, content=raw_payload)
    )

    snapshot = provider.fetch_snapshot(_fund())

    assert snapshot.raw_payload == raw_payload
    assert snapshot.payload_hash == hashlib.sha256(raw_payload).hexdigest()


def test_parse_nomura_official_snapshot_preserves_exact_share_inventory() -> None:
    payload = {
        "StatusCode": 0,
        "Entries": {
            "CFundId": "00985A",
            "CPcfdate": "2026-08-28T00:00:00",
            "CNavDt": "2026-08-28T00:00:00",
            "Stocks": [
                {
                    "CStockCode": "2330",
                    "CStockName": "台灣積體電路製造",
                    "CQuantity": 588000,
                    "CWeightsPct": 13.51,
                },
                {
                    "CStockCode": "2454",
                    "CStockName": "聯發科技",
                    "CQuantity": 26000,
                    "CWeightsPct": 4.82,
                },
            ],
        },
    }

    snapshot = parse_nomura_holdings_payload(
        payload,
        fund=_fund(),
        raw_payload=b"nomura-json",
        fetched_at=datetime(2026, 8, 31, 5, tzinfo=timezone.utc),
    )

    assert snapshot.source_provider == "issuer_official"
    assert snapshot.data_date == date(2026, 8, 28)
    assert [(row.symbol, row.shares) for row in snapshot.holdings] == [
        ("2330", 588000),
        ("2454", 26000),
    ]


def test_issuer_provider_supports_only_complete_official_adapter() -> None:
    calls: list[dict] = []
    matching_payload = {
        "StatusCode": 0,
        "Entries": {
            "CFundId": "00985A",
            "CPcfdate": "2026-08-31T00:00:00",
            "CNavDt": "2026-08-28T00:00:00",
            "Stocks": [
                {
                    "CStockCode": "2330",
                    "CStockName": "台灣積體電路製造",
                    "CQuantity": 588000,
                    "CWeightsPct": 13.51,
                }
            ],
        },
    }

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        request_date = kwargs["json"]["Date"]
        payload = (
            matching_payload
            if request_date == "2026-08-31"
            else {"StatusCode": 0, "Entries": None}
        )
        response = FakeResponse(payload=payload)
        response.content = b'{"official":true}'
        return response

    provider = IssuerOfficialActiveEtfProvider(request_post=fake_post)
    snapshot = provider.fetch_snapshot(_fund(), expected_data_date=date(2026, 8, 28))

    assert provider.supports("00985A") is True
    assert provider.supports("00982A") is False
    assert snapshot.data_date == date(2026, 8, 28)
    assert calls[0]["json"]["FundNo"] == "00985A"
    assert [call["json"]["Date"] for call in calls] == [
        "2026-08-29",
        "2026-08-30",
        "2026-08-31",
    ]


def test_issuer_provider_rejects_nonmatching_official_snapshot() -> None:
    calls: list[dict] = []
    payload = {
        "StatusCode": 0,
        "Entries": {
            "CFundId": "00985A",
            "CPcfdate": "2026-08-29T00:00:00",
            "CNavDt": "2026-08-29T00:00:00",
            "Stocks": [
                {
                    "CStockCode": "2330",
                    "CStockName": "台灣積體電路製造",
                    "CQuantity": 588000,
                    "CWeightsPct": 13.51,
                }
            ],
        },
    }

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResponse(payload=payload)

    provider = IssuerOfficialActiveEtfProvider(request_post=fake_post)

    with pytest.raises(
        ActiveEtfProviderError,
        match="active_etf_official_snapshot_date_unavailable",
    ):
        provider.fetch_snapshot(_fund(), expected_data_date=date(2026, 8, 28))

    assert [call["json"]["Date"] for call in calls] == ["2026-08-29"]


def test_refresh_is_idempotent_and_daily_response_compares_per_fund_snapshots(
    etf_db_session: Session,
) -> None:
    first = _snapshot(
        date(2026, 8, 27),
        [
            ("2330.TW", "台積電", 100, "50.00"),
            ("2454.TW", "聯發科", 50, "30.00"),
            ("2303.TW", "聯電", 40, "10.00"),
            ("2317.TW", "鴻海", 30, "5.00"),
            ("2881.TW", "富邦金", 20, "3.00"),
            ("2891.TW", "中信金", 10, "2.00"),
        ],
    )
    first_result = refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider({"00985A": first}),
        verification_provider=FakeVerificationProvider(
            {"00985A": _official_snapshot(first)}
        ),
        max_workers=1,
    )
    second = _snapshot(
        date(2026, 8, 28),
        [
            ("2330.TW", "台積電", 120, "51.00"),
            ("2303.TW", "聯電", 48, "10.20"),
            ("2317.TW", "鴻海", 36, "5.10"),
            ("2881.TW", "富邦金", 24, "3.10"),
            ("2891.TW", "中信金", 12, "2.10"),
            ("3711.TW", "日月光投控", 20, "4.00"),
        ],
    )
    second_provider = FakeProvider({"00985A": second})
    second_result = refresh_active_etf_holdings(
        etf_db_session,
        provider=second_provider,
        verification_provider=FakeVerificationProvider(
            {"00985A": _official_snapshot(second)}
        ),
        max_workers=1,
    )
    repeated_result = refresh_active_etf_holdings(
        etf_db_session,
        provider=second_provider,
        verification_provider=FakeVerificationProvider(
            {"00985A": _official_snapshot(second)}
        ),
        max_workers=1,
    )

    response = get_active_etf_daily_response(etf_db_session)

    assert first_result.snapshots_created == 1
    assert second_result.snapshots_created == 1
    assert repeated_result.snapshots_reused == 1
    assert repeated_result.verified_snapshots == 1
    assert etf_db_session.scalar(select(func.count()).select_from(ActiveEtfHoldingSnapshot)) == 2
    observations = list(etf_db_session.scalars(select(ActiveEtfSourceObservation)))
    assert len(observations) == 4
    assert all(gzip.decompress(row.raw_payload_gzip) for row in observations)
    assert response is not None
    assert response.data_date == date(2026, 8, 28)
    assert response.funds[0].source_provider == "moneydj"
    assert "etfid=00985A.TW" in response.funds[0].source_url
    assert response.summary.additions == 1
    assert response.summary.increases == 5
    assert response.summary.removals == 1
    assert response.summary.decreases == 0
    assert response.funds[0].common_scale_ratio == Decimal("1.2000")
    assert all(
        change.likely_fund_scale_change
        for change in response.changes
        if change.action == "increased"
    )
    assert next(change for change in response.changes if change.symbol == "3711.TW").action == "added"
    assert next(change for change in response.changes if change.symbol == "2454.TW").action == "removed"
    assert all(change.source_provider == "moneydj" for change in response.changes)
    assert all(change.verification_status == "verified" for change in response.changes)
    assert all(change.source_count == 2 for change in response.changes)


def test_refresh_preserves_successes_and_reports_failed_funds(
    etf_db_session: Session,
) -> None:
    good_fund = _fund()
    failed_fund = _fund("00982A", "主動群益台灣強棒")

    class PartialProvider(FakeProvider):
        def fetch_snapshot(self, fund: ActiveEtfFundDescriptor) -> ActiveEtfFundSnapshot:
            if fund.fund_code == "00982A":
                raise ActiveEtfProviderError("active_etf_holdings_table_missing")
            return self.snapshots[fund.fund_code]

    result = refresh_active_etf_holdings(
        etf_db_session,
        provider=PartialProvider(
            {"00985A": _snapshot(date(2026, 8, 28), [("2330.TW", "台積電", 100, "10")])},
            funds=[good_fund, failed_fund],
        ),
        max_workers=1,
    )

    assert result.status == "partial"
    assert result.snapshots_created == 1
    assert result.errors[0].fund_code == "00982A"
    assert result.errors[0].code == "active_etf_snapshot_fetch_failed"


def test_refresh_preserves_explicitly_unpublished_error_code(
    etf_db_session: Session,
) -> None:
    good_fund = _fund()
    unpublished_fund = _fund("00409A", "主動復華全球50")

    class PartiallyPublishedProvider(FakeProvider):
        def fetch_snapshot(self, fund: ActiveEtfFundDescriptor) -> ActiveEtfFundSnapshot:
            if fund.fund_code == "00409A":
                raise ActiveEtfProviderError("active_etf_holdings_not_published")
            return self.snapshots[fund.fund_code]

    result = refresh_active_etf_holdings(
        etf_db_session,
        provider=PartiallyPublishedProvider(
            {"00985A": _snapshot(date(2026, 8, 28), [("2330.TW", "台積電", 100, "10")])},
            funds=[good_fund, unpublished_fund],
        ),
        max_workers=1,
    )

    assert result.status == "partial"
    assert result.snapshots_created == 1
    assert result.errors[0].fund_code == "00409A"
    assert result.errors[0].code == "active_etf_holdings_not_published"


def test_single_source_snapshot_is_retained_and_publishes_changes(
    etf_db_session: Session,
) -> None:
    first = _snapshot(
        date(2026, 8, 27),
        [("2330.TW", "台積電", 100, "10")],
    )
    second = _snapshot(
        date(2026, 8, 28),
        [("2330.TW", "台積電", 120, "11")],
    )
    refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider({"00985A": first}),
        max_workers=1,
    )
    result = refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider({"00985A": second}),
        max_workers=1,
    )

    response = get_active_etf_daily_response(etf_db_session)

    assert result.single_source_snapshots == 1
    assert response is not None
    assert response.covered_funds == 1
    assert response.funds[0].status == "ready"
    assert response.funds[0].verification_reason == "official_source_unsupported"
    assert response.funds[0].previous_date == date(2026, 8, 27)
    assert response.changes[0].action == "increased"
    assert response.changes[0].verification_status == "single_source"
    assert response.changes[0].source_count == 1
    assert response.consensus[0].symbol == "2330.TW"
    assert response.consensus[0].fund_count == 1


def test_verified_current_snapshot_uses_single_source_baseline_without_claiming_dual_source(
    etf_db_session: Session,
) -> None:
    first = _snapshot(
        date(2026, 8, 27),
        [("2330.TW", "台積電", 100, "10")],
    )
    second = _snapshot(
        date(2026, 8, 28),
        [("2330.TW", "台積電", 120, "11")],
    )
    refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider({"00985A": first}),
        max_workers=1,
    )
    refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider({"00985A": second}),
        verification_provider=FakeVerificationProvider(
            {"00985A": _official_snapshot(second)}
        ),
        max_workers=1,
    )

    response = get_active_etf_daily_response(etf_db_session)

    assert response is not None
    assert response.funds[0].verification_status == "verified"
    assert response.changes[0].verification_status == "single_source"
    assert response.changes[0].source_count == 1


def test_failed_recheck_preserves_verified_snapshot_when_primary_is_unchanged(
    etf_db_session: Session,
) -> None:
    previous = _snapshot(
        date(2026, 8, 27),
        [("2330.TW", "台積電", 100, "10")],
    )
    current = _snapshot(
        date(2026, 8, 28),
        [("2330.TW", "台積電", 120, "11")],
    )
    for snapshot in (previous, current):
        refresh_active_etf_holdings(
            etf_db_session,
            provider=FakeProvider({"00985A": snapshot}),
            verification_provider=FakeVerificationProvider(
                {"00985A": _official_snapshot(snapshot)}
            ),
            max_workers=1,
        )
    verified_before = etf_db_session.scalar(
        select(ActiveEtfHoldingSnapshot).where(
            ActiveEtfHoldingSnapshot.fund_code == "00985A",
            ActiveEtfHoldingSnapshot.data_date == current.data_date,
        )
    )
    assert verified_before is not None
    verification_details_before = verified_before.verification_details
    refetched = replace(
        current,
        fetched_at=datetime(2026, 8, 28, 7, tzinfo=timezone.utc),
        payload_hash="f" * 64,
        raw_payload=b"refetched-primary-payload",
    )

    result = refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider({"00985A": refetched}),
        verification_provider=FakeVerificationProvider(
            {"00985A": ActiveEtfProviderError("issuer_temporarily_unavailable")}
        ),
        max_workers=1,
    )
    persisted = etf_db_session.scalar(
        select(ActiveEtfHoldingSnapshot).where(
            ActiveEtfHoldingSnapshot.fund_code == "00985A",
            ActiveEtfHoldingSnapshot.data_date == current.data_date,
        )
    )
    response = get_active_etf_daily_response(etf_db_session)

    assert result.status == "partial"
    assert result.snapshots_reused == 1
    assert result.verified_snapshots == 1
    assert result.single_source_snapshots == 0
    assert result.errors[0].code == "active_etf_verification_fetch_failed"
    assert persisted is not None
    assert persisted.verification_status == "verified"
    assert persisted.source_count == 2
    assert persisted.payload_hash == current.payload_hash
    assert persisted.verification_details == verification_details_before
    primary_observation = etf_db_session.scalar(
        select(ActiveEtfSourceObservation).where(
            ActiveEtfSourceObservation.fund_code == "00985A",
            ActiveEtfSourceObservation.data_date == current.data_date,
            ActiveEtfSourceObservation.source_provider == "moneydj",
        )
    )
    assert primary_observation is not None
    assert primary_observation.payload_hash == refetched.payload_hash
    assert response is not None
    assert response.funds[0].status == "ready"
    assert len(response.funds[0].sources) == 2
    assert response.summary.increases == 1


def test_failed_recheck_downgrades_snapshot_when_primary_content_changed(
    etf_db_session: Session,
) -> None:
    original = _snapshot(
        date(2026, 8, 28),
        [("2330.TW", "台積電", 120, "11")],
    )
    changed = _snapshot(
        date(2026, 8, 28),
        [("2330.TW", "台積電", 121, "11")],
    )
    refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider({"00985A": original}),
        verification_provider=FakeVerificationProvider(
            {"00985A": _official_snapshot(original)}
        ),
        max_workers=1,
    )

    result = refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider({"00985A": changed}),
        verification_provider=FakeVerificationProvider(
            {"00985A": ActiveEtfProviderError("issuer_temporarily_unavailable")}
        ),
        max_workers=1,
    )
    persisted = etf_db_session.scalar(
        select(ActiveEtfHoldingSnapshot).where(
            ActiveEtfHoldingSnapshot.fund_code == "00985A",
            ActiveEtfHoldingSnapshot.data_date == changed.data_date,
        )
    )
    response = get_active_etf_daily_response(etf_db_session)

    assert result.status == "partial"
    assert result.snapshots_updated == 1
    assert result.verified_snapshots == 0
    assert result.single_source_snapshots == 1
    assert persisted is not None
    assert persisted.verification_status == "single_source"
    assert etf_db_session.scalar(
        select(func.count()).select_from(ActiveEtfSourceObservation)
    ) == 2
    assert response is not None
    assert response.covered_funds == 1
    assert response.funds[0].status == "no_baseline"
    assert response.funds[0].verification_status == "single_source"
    assert response.changes == []


def test_conflicting_share_inventory_is_fail_closed_with_both_sources_retained(
    etf_db_session: Session,
) -> None:
    primary = _snapshot(
        date(2026, 8, 28),
        [("2330.TW", "台積電", 120, "11")],
    )
    official = _official_snapshot(primary)
    official = ActiveEtfFundSnapshot(
        fund=official.fund,
        data_date=official.data_date,
        fetched_at=official.fetched_at,
        holdings=(
            ActiveEtfHoldingRow(
                symbol="2330",
                name="台灣積體電路製造",
                shares=119,
                weight_pct=Decimal(11),
                position_order=0,
            ),
        ),
        skipped_instrument_count=0,
        payload_hash=official.payload_hash,
        normalized_hash=official.normalized_hash,
        parser_version=official.parser_version,
        raw_payload=official.raw_payload,
        source_provider=official.source_provider,
        source_url=official.source_url,
    )

    result = refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider({"00985A": primary}),
        verification_provider=FakeVerificationProvider({"00985A": official}),
        max_workers=1,
    )
    response = get_active_etf_daily_response(etf_db_session)

    assert result.conflicted_snapshots == 1
    assert response is not None
    assert response.funds[0].status == "source_conflict"
    assert response.funds[0].verification_reason == "holding_mismatch"
    assert response.funds[0].source_count == 2
    assert response.changes == []
    assert etf_db_session.scalar(
        select(func.count()).select_from(ActiveEtfSourceObservation)
    ) == 2


def test_refresh_rejects_explicit_empty_fund_selection(etf_db_session: Session) -> None:
    provider = FakeProvider(
        {"00985A": _snapshot(date(2026, 8, 28), [("2330.TW", "台積電", 100, "10")])}
    )

    with pytest.raises(ActiveEtfProviderError, match="active_etf_requested_funds_empty"):
        refresh_active_etf_holdings(
            etf_db_session,
            provider=provider,
            fund_codes=[],
            max_workers=1,
        )


def test_registry_sync_fails_closed_before_disabling_on_large_coverage_drop(
    etf_db_session: Session,
) -> None:
    funds = [_fund(f"{9800 + index:05d}A", f"測試基金 {index}") for index in range(5)]
    for fund in funds:
        etf_db_session.add(
            ActiveEtfFund(
                fund_code=fund.fund_code,
                name=fund.name,
                market="TW",
                source_provider="moneydj",
                source_url=fund.source_url,
                enabled=True,
            )
        )
    etf_db_session.commit()
    remaining = funds[:3]

    with pytest.raises(ActiveEtfProviderError, match="active_etf_registry_coverage_dropped"):
        refresh_active_etf_holdings(
            etf_db_session,
            provider=FakeProvider(
                {
                    fund.fund_code: _snapshot(
                        date(2026, 8, 28),
                        [("2330.TW", "台積電", 100, "10")],
                        fund=fund,
                    )
                    for fund in remaining
                },
                funds=remaining,
            ),
            max_workers=1,
        )

    assert etf_db_session.scalar(
        select(func.count()).select_from(ActiveEtfFund).where(ActiveEtfFund.enabled.is_(True))
    ) == 5


def test_daily_response_excludes_stale_funds_from_date_and_builds_consensus(
    etf_db_session: Session,
) -> None:
    first_fund = _fund()
    second_fund = _fund("00982A", "主動群益台灣強棒")
    funds = [first_fund, second_fund]
    refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider(
            {
                fund.fund_code: _snapshot(
                    date(2026, 8, 27),
                    [("2330.TW", "台積電", 100, "10")],
                    fund=fund,
                )
                for fund in funds
            },
            funds=funds,
        ),
        verification_provider=FakeVerificationProvider(
            {
                fund.fund_code: _official_snapshot(
                    _snapshot(
                        date(2026, 8, 27),
                        [("2330.TW", "台積電", 100, "10")],
                        fund=fund,
                    )
                )
                for fund in funds
            }
        ),
        max_workers=1,
    )
    refresh_active_etf_holdings(
        etf_db_session,
        provider=FakeProvider(
            {
                first_fund.fund_code: _snapshot(
                    date(2026, 8, 28),
                    [("2330.TW", "台積電", 120, "11")],
                    fund=first_fund,
                ),
                second_fund.fund_code: _snapshot(
                    date(2026, 8, 28),
                    [("2330.TW", "台積電", 110, "10.5")],
                    fund=second_fund,
                ),
            },
            funds=funds,
        ),
        verification_provider=FakeVerificationProvider(
            {
                first_fund.fund_code: _official_snapshot(
                    _snapshot(
                        date(2026, 8, 28),
                        [("2330.TW", "台積電", 120, "11")],
                        fund=first_fund,
                    )
                ),
                second_fund.fund_code: _official_snapshot(
                    _snapshot(
                        date(2026, 8, 28),
                        [("2330.TW", "台積電", 110, "10.5")],
                        fund=second_fund,
                    )
                ),
            }
        ),
        max_workers=1,
    )

    response = get_active_etf_daily_response(etf_db_session)

    assert response is not None
    assert response.covered_funds == 2
    assert response.consensus[0].symbol == "2330.TW"
    assert response.consensus[0].direction == "increase"
    assert response.consensus[0].fund_count == 2

    second_snapshot = etf_db_session.scalar(
        select(ActiveEtfHoldingSnapshot).where(
            ActiveEtfHoldingSnapshot.fund_code == second_fund.fund_code,
            ActiveEtfHoldingSnapshot.data_date == date(2026, 8, 28),
        )
    )
    assert second_snapshot is not None
    etf_db_session.delete(second_snapshot)
    etf_db_session.commit()

    partial_response = get_active_etf_daily_response(etf_db_session)

    assert partial_response is not None
    assert partial_response.covered_funds == 1
    assert partial_response.consensus[0].symbol == "2330.TW"
    assert partial_response.consensus[0].fund_count == 1
    stale_fund = next(fund for fund in partial_response.funds if fund.fund_code == "00982A")
    assert stale_fund.status == "missing"
    assert stale_fund.latest_data_date == date(2026, 8, 27)


@pytest.fixture()
def etf_client(etf_db_session: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    snapshot = _snapshot(
        date(2026, 8, 28),
        [("2330.TW", "台積電", 100, "10.00")],
    )
    provider = FakeProvider({"00985A": snapshot})
    verification_provider = FakeVerificationProvider(
        {"00985A": _official_snapshot(snapshot)}
    )
    api.app.dependency_overrides[get_db] = lambda: etf_db_session
    api.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    api.app.dependency_overrides[require_daily_radar_internal_auth] = lambda: None
    api.app.dependency_overrides[get_active_etf_holdings_provider] = lambda: provider
    api.app.dependency_overrides[get_active_etf_verification_provider] = (
        lambda: verification_provider
    )
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.pop(get_db, None)
        api.app.dependency_overrides.pop(get_current_user, None)
        api.app.dependency_overrides.pop(require_daily_radar_internal_auth, None)
        api.app.dependency_overrides.pop(get_active_etf_holdings_provider, None)
        api.app.dependency_overrides.pop(get_active_etf_verification_provider, None)


def test_active_etf_internal_refresh_and_authenticated_read_endpoint(
    etf_client: TestClient,
) -> None:
    refreshed = etf_client.post("/internal/active-etf-holdings/refresh", json={})
    daily = etf_client.get("/active-etf-holdings/daily")

    assert refreshed.status_code == 200
    assert refreshed.json()["snapshots_created"] == 1
    assert daily.status_code == 200
    assert daily.json()["data_date"] == "2026-08-28"
    assert daily.json()["funds"][0]["status"] == "no_baseline"
    assert daily.json()["funds"][0]["source_provider"] == "moneydj"


def test_active_etf_daily_endpoint_returns_stable_not_found_error(
    etf_client: TestClient,
) -> None:
    response = etf_client.get("/active-etf-holdings/daily")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "active_etf_holdings_not_found"
