"""Provider 層單元測試：Router + Provider 介面 + fetch_institutional_flow 工具。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ai_stock_sentinel.data_sources.institutional_flow.interface import (
    InstitutionalFlowData,
    InstitutionalFlowError,
)
from ai_stock_sentinel.data_sources.institutional_flow.router import (
    InstitutionalFlowRouter,
    _detect_market,
)
from ai_stock_sentinel.data_sources.institutional_flow.tools import fetch_institutional_flow


# ─── 工具：建立假 Provider ──────────────────────────────────────────────────


def _make_provider(name: str, data: InstitutionalFlowData | None = None, error: InstitutionalFlowError | None = None, twse_only: bool = False):
    """建立一個 mock Provider，成功時回傳 data，失敗時拋出 error。"""
    provider = MagicMock()
    provider.name = name
    provider.twse_only = twse_only
    if error:
        provider.fetch_daily_flow.side_effect = error
    else:
        provider.fetch_daily_flow.return_value = data
    return provider


def _make_data(symbol: str = "2330.TW", source: str = "MockProvider", flow_label: str = "neutral") -> InstitutionalFlowData:
    return InstitutionalFlowData(
        symbol=symbol,
        period_days=5,
        foreign_buy=12000.0,
        investment_trust_buy=3000.0,
        dealer_buy=500.0,
        foreign_net_cumulative=10000.0,
        trust_net_cumulative=2500.0,
        dealer_net_cumulative=300.0,
        three_party_net=12800.0,
        consecutive_buy_days=3,
        margin_delta=100.0,
        margin_balance_delta_pct=1.5,
        flow_label=flow_label,
        source_provider=source,
        warnings=[],
    )


# ─── _detect_market ─────────────────────────────────────────────────────────


class TestDetectMarket:
    def test_tw_suffix(self):
        assert _detect_market("2330.TW") == "twse"

    def test_two_suffix(self):
        assert _detect_market("6488.TWO") == "tpex"

    def test_case_insensitive(self):
        assert _detect_market("2330.tw") == "twse"
        assert _detect_market("6488.two") == "tpex"

    def test_unknown_suffix(self):
        assert _detect_market("2330") == "unknown"


# ─── InstitutionalFlowRouter ────────────────────────────────────────────────


class TestInstitutionalFlowRouter:
    def test_primary_success(self):
        """Primary 成功時直接回傳，不呼叫 Fallback。"""
        data = _make_data(source="FinMind")
        primary = _make_provider("FinMind", data=data)
        fallback = _make_provider("TwseOpenApi")
        router = InstitutionalFlowRouter([primary, fallback])

        result = router.fetch_institutional_flow("2330.TW", days=5)

        assert result.source_provider == "FinMind"
        fallback.fetch_daily_flow.assert_not_called()

    def test_primary_fail_fallback_success(self):
        """Primary 失敗時自動切換 Fallback。"""
        error = InstitutionalFlowError("FINMIND_REQUEST_ERROR", "連線失敗", provider="FinMind")
        primary = _make_provider("FinMind", error=error)
        data = _make_data(source="TwseOpenApi")
        fallback = _make_provider("TwseOpenApi", data=data)

        router = InstitutionalFlowRouter([primary, fallback])
        result = router.fetch_institutional_flow("2330.TW", days=5)

        assert result.source_provider == "TwseOpenApi"

    def test_all_providers_fail(self):
        """全部失敗時拋出 INSTITUTIONAL_FETCH_ERROR。"""
        error = InstitutionalFlowError("FINMIND_NO_DATA", "無資料", provider="FinMind")
        p1 = _make_provider("FinMind", error=error)
        p2 = _make_provider("TwseOpenApi", error=InstitutionalFlowError("TWSE_NO_DATA", "無資料", provider="TwseOpenApi"))
        p3 = _make_provider("Tpex", error=InstitutionalFlowError("TPEX_NO_DATA", "無資料", provider="Tpex"))

        router = InstitutionalFlowRouter([p1, p2, p3])

        with pytest.raises(InstitutionalFlowError) as exc_info:
            router.fetch_institutional_flow("2330.TW", days=5)

        assert exc_info.value.code == "INSTITUTIONAL_FETCH_ERROR"

    def test_tpex_symbol_skips_twse_only_provider(self):
        """上櫃標的（.TWO）應跳過 twse_only=True 的 Provider。"""
        twse_provider = _make_provider("TwseOpenApi", twse_only=True)
        data = _make_data(symbol="6488.TWO", source="Tpex")
        tpex_provider = _make_provider("Tpex", data=data)

        router = InstitutionalFlowRouter([twse_provider, tpex_provider])
        result = router.fetch_institutional_flow("6488.TWO", days=5)

        assert result.source_provider == "Tpex"
        twse_provider.fetch_daily_flow.assert_not_called()

    def test_tw_symbol_tries_all_providers(self):
        """上市標的（.TW）不跳過任何 Provider。"""
        data = _make_data(source="FinMind")
        p1 = _make_provider("FinMind", data=data, twse_only=False)
        p2 = _make_provider("TwseOpenApi", twse_only=True)
        router = InstitutionalFlowRouter([p1, p2])

        result = router.fetch_institutional_flow("2330.TW", days=5)
        assert result.source_provider == "FinMind"

    def test_empty_providers_raises(self):
        with pytest.raises(ValueError):
            InstitutionalFlowRouter([])

    def test_fixed_priority_order(self):
        """確認 Router 依序嘗試 Provider，Primary 失敗才走 Fallback。"""
        call_order: list[str] = []

        def make_recording_provider(name: str, should_fail: bool, twse_only: bool = False):
            p = MagicMock()
            p.name = name
            p.twse_only = twse_only
            if should_fail:
                def side_effect(symbol, days):
                    call_order.append(name)
                    raise InstitutionalFlowError(f"{name.upper()}_ERROR", "fail", provider=name)
                p.fetch_daily_flow.side_effect = side_effect
            else:
                def side_effect(symbol, days):
                    call_order.append(name)
                    return _make_data(source=name)
                p.fetch_daily_flow.side_effect = side_effect
            return p

        p1 = make_recording_provider("FinMind", should_fail=True)
        p2 = make_recording_provider("TwseOpenApi", should_fail=True, twse_only=True)
        p3 = make_recording_provider("Tpex", should_fail=False)

        router = InstitutionalFlowRouter([p1, p2, p3])
        # 上市標的：三個都試
        router.fetch_institutional_flow("2330.TW", days=5)
        assert call_order == ["FinMind", "TwseOpenApi", "Tpex"]


# ─── fetch_institutional_flow 工具函式 ──────────────────────────────────────


class TestFetchInstitutionalFlowTool:
    def test_success_returns_dict(self):
        """成功時回傳 dict，含 source_provider 與 flow_label。"""
        data = _make_data(source="FinMind", flow_label="institutional_accumulation")
        provider = _make_provider("FinMind", data=data)
        router = InstitutionalFlowRouter([provider])

        result = fetch_institutional_flow("2330.TW", days=5, router=router)

        assert isinstance(result, dict)
        assert result["source_provider"] == "FinMind"
        assert result["flow_label"] == "institutional_accumulation"
        assert result.get("error") is None

    def test_failure_returns_error_dict(self):
        """全部 Provider 失敗時，回傳帶 error 鍵的 dict，不拋例外。"""
        error = InstitutionalFlowError("INSTITUTIONAL_FETCH_ERROR", "所有 Provider 均失敗")
        provider = _make_provider("FinMind", error=error)
        router = InstitutionalFlowRouter([provider])

        result = fetch_institutional_flow("2330.TW", days=5, router=router)

        assert "error" in result
        assert result["error"] == "INSTITUTIONAL_FETCH_ERROR"
        assert result["symbol"] == "2330.TW"

    def test_core_fields_present(self):
        """確認核心欄位（foreign_buy、investment_trust_buy、dealer_buy、margin_delta）皆存在。"""
        data = _make_data()
        provider = _make_provider("FinMind", data=data)
        router = InstitutionalFlowRouter([provider])

        result = fetch_institutional_flow("2330.TW", days=5, router=router)

        for field in ["foreign_buy", "investment_trust_buy", "dealer_buy", "margin_delta"]:
            assert field in result, f"缺少欄位：{field}"

    def test_6488_two_uses_tpex_path(self):
        """上櫃標的應使用 TPEX 路徑。"""
        data = _make_data(symbol="6488.TWO", source="Tpex")
        twse_provider = _make_provider("TwseOpenApi", twse_only=True)
        tpex_provider = _make_provider("Tpex", data=data)
        router = InstitutionalFlowRouter([twse_provider, tpex_provider])

        result = fetch_institutional_flow("6488.TWO", days=5, router=router)

        assert result["source_provider"] == "Tpex"
        twse_provider.fetch_daily_flow.assert_not_called()

    def test_default_days_is_10_when_not_provided(self):
        """未傳 days 時，工具應使用預設 10 日視窗。"""

        provider = MagicMock()
        provider.name = "FinMind"
        provider.twse_only = False

        def _side_effect(symbol, days):
            return InstitutionalFlowData(
                symbol=symbol,
                period_days=days,
                source_provider="FinMind",
                flow_label="neutral",
            )

        provider.fetch_daily_flow.side_effect = _side_effect
        router = InstitutionalFlowRouter([provider])

        result = fetch_institutional_flow("2330.TW", router=router)

        provider.fetch_daily_flow.assert_called_once_with(symbol="2330.TW", days=10)
        assert result["period_days"] == 10


# ─── InstitutionalFlowData schema ───────────────────────────────────────────


class TestInstitutionalFlowDataSchema:
    def test_default_flow_label_is_neutral(self):
        data = InstitutionalFlowData(symbol="2330.TW", period_days=5)
        assert data.flow_label == "neutral"

    def test_warnings_default_empty_list(self):
        data = InstitutionalFlowData(symbol="2330.TW", period_days=5)
        assert data.warnings == []

    def test_all_numeric_fields_nullable(self):
        """核心數值欄位允許 None（欄位缺漏時不炸裂）。"""
        data = InstitutionalFlowData(symbol="2330.TW", period_days=5)
        assert data.foreign_buy is None
        assert data.investment_trust_buy is None
        assert data.dealer_buy is None
        assert data.margin_delta is None
