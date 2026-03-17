from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from unittest.mock import MagicMock

from ai_stock_sentinel.data_sources.rss_news_client import RawNewsItem
from ai_stock_sentinel.graph.nodes import clean_node, crawl_node, fetch_institutional_node, fetch_news_node, judge_node, analyze_node, quality_gate_node
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.models import StockSnapshot


def _make_snapshot() -> dict:
    return asdict(StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=100.0,
        previous_close=99.0,
        day_open=99.5,
        day_high=101.0,
        day_low=98.5,
        volume=123456,
        recent_closes=[98.0, 99.0, 100.0],
        fetched_at="2026-03-03T00:00:00+00:00",
    ))


def _base_state(**overrides) -> GraphState:
    state: GraphState = {
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "analysis_detail": None,
        "cleaned_news": None,
        "cleaned_news_quality": None,
        "news_display": None,
        "news_display_items": [],
        "raw_news_items": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
        "technical_context": None,
        "institutional_context": None,
        "institutional_flow": None,
        "strategy_type": None,
        "entry_zone": None,
        "stop_loss": None,
        "holding_period": None,
        "confidence_score": None,
        "cross_validation_note": None,
        "is_final": True,
    }
    state.update(overrides)
    return state


def test_crawl_node_returns_snapshot() -> None:
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=100.0,
        previous_close=99.0,
        day_open=99.5,
        day_high=101.0,
        day_low=98.5,
        volume=123456,
        recent_closes=[98.0, 99.0, 100.0],
        fetched_at="2026-03-03T00:00:00+00:00",
    )

    result = crawl_node(_base_state(), crawler=mock_crawler)

    assert result["snapshot"]["symbol"] == "2330.TW"
    assert result["errors"] == []


def test_judge_node_requires_news_when_none_available() -> None:
    state = _base_state(snapshot=_make_snapshot())

    result = judge_node(state)

    assert result["data_sufficient"] is False
    assert result["requires_news_refresh"] is True


def test_analyze_node_returns_analysis_string() -> None:
    from ai_stock_sentinel.models import AnalysisDetail

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = AnalysisDetail(summary="分析結果")

    state = _base_state(snapshot=_make_snapshot())
    result = analyze_node(state, analyzer=mock_analyzer)

    assert result["analysis"] == "分析結果"
    assert isinstance(result["analysis_detail"], AnalysisDetail)


def test_crawl_node_accumulates_errors_on_failure() -> None:
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.side_effect = RuntimeError("network timeout")

    prior_errors = [{"code": "PRIOR_ERROR", "message": "earlier error"}]
    state = _base_state(errors=prior_errors)
    result = crawl_node(state, crawler=mock_crawler)

    assert result["snapshot"] is None
    assert len(result["errors"]) == 2
    assert result["errors"][0]["code"] == "PRIOR_ERROR"
    assert result["errors"][1]["code"] == "CRAWL_ERROR"
    assert "network timeout" in result["errors"][1]["message"]


def _make_cleaned_news(
    *,
    date_str: str | None = None,
    mentioned_numbers: list[str] | None = None,
) -> dict:
    today = date.today().isoformat()
    return {
        "date": date_str if date_str is not None else today,
        "title": "台積電 2 月營收年增",
        "mentioned_numbers": mentioned_numbers if mentioned_numbers is not None else ["2,600", "18.2%"],
        "sentiment_label": "positive",
    }


def test_judge_node_sufficient_when_snapshot_and_fresh_news() -> None:
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=_make_cleaned_news(),
    )
    result = judge_node(state)
    assert result["data_sufficient"] is True
    assert result["requires_news_refresh"] is False
    assert result["requires_fundamental_update"] is False


def test_judge_node_insufficient_when_snapshot_missing() -> None:
    state = _base_state(snapshot=None)
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_fundamental_update"] is True


def test_judge_node_insufficient_when_news_stale() -> None:
    stale_date = (date.today() - timedelta(days=8)).isoformat()
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=_make_cleaned_news(date_str=stale_date),
    )
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_news_refresh"] is True


def test_judge_node_insufficient_when_no_mentioned_numbers() -> None:
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=_make_cleaned_news(mentioned_numbers=[]),
    )
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_news_refresh"] is True


def test_judge_node_insufficient_when_no_news_provided() -> None:
    """未提供新聞（cleaned_news/news_content 皆無）時，應要求 refresh。"""
    state = _base_state(snapshot=_make_snapshot(), cleaned_news=None)
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_news_refresh"] is True


def test_judge_node_sufficient_when_news_content_provided_without_cleaned_news() -> None:
    """有 news_content 但尚未 cleaned 時，允許進到 clean_node。"""
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=None,
        news_content="2026-03-04 台股焦點新聞",
    )
    result = judge_node(state)
    assert result["data_sufficient"] is True
    assert result["requires_news_refresh"] is False


# ── fetch_news_node ──────────────────────────────────────────────────────────

def _make_raw_news_item(**overrides) -> RawNewsItem:
    defaults = dict(
        source="google-news-rss",
        url="https://example.com/news/1",
        title="台積電 2 月營收年增 20%",
        published_at="Mon, 03 Mar 2026 08:00:00 GMT",
        summary="台積電公佈 2 月營收，年增 20%，優於市場預期。",
    )
    defaults.update(overrides)
    return RawNewsItem(**defaults)


def test_fetch_news_node_returns_raw_news_items() -> None:
    mock_rss = MagicMock()
    mock_rss.fetch_news.return_value = [_make_raw_news_item()]

    state = _base_state()
    result = fetch_news_node(state, rss_client=mock_rss)

    assert result["raw_news_items"] is not None
    assert len(result["raw_news_items"]) == 1
    assert result["raw_news_items"][0]["source"] == "google-news-rss"


def test_fetch_news_node_sets_news_content_from_first_item() -> None:
    mock_rss = MagicMock()
    mock_rss.fetch_news.return_value = [_make_raw_news_item()]

    state = _base_state()
    result = fetch_news_node(state, rss_client=mock_rss)

    assert result["news_content"] is not None
    assert "台積電" in result["news_content"]


def test_fetch_news_node_returns_empty_list_on_no_results() -> None:
    mock_rss = MagicMock()
    mock_rss.fetch_news.return_value = []

    state = _base_state()
    result = fetch_news_node(state, rss_client=mock_rss)

    assert result["raw_news_items"] == []
    assert result.get("news_content") is None


def test_fetch_news_node_accumulates_errors_on_exception() -> None:
    mock_rss = MagicMock()
    mock_rss.fetch_news.side_effect = RuntimeError("connection refused")

    prior_errors = [{"code": "PRIOR", "message": "earlier error"}]
    state = _base_state(errors=prior_errors)
    result = fetch_news_node(state, rss_client=mock_rss)

    assert result["raw_news_items"] == []
    assert len(result["errors"]) == 2
    assert result["errors"][1]["code"] == "RSS_FETCH_ERROR"
    assert "connection refused" in result["errors"][1]["message"]


# ── clean_node ───────────────────────────────────────────────────────────────

def test_clean_node_produces_cleaned_news_from_news_content() -> None:
    mock_cleaner = MagicMock()
    mock_cleaner.clean.return_value = MagicMock(
        model_dump=lambda: {
            "date": "2026-03-03",
            "title": "台積電 2 月營收年增",
            "mentioned_numbers": ["2,600", "18.2%"],
            "sentiment_label": "positive",
        }
    )

    state = _base_state(news_content="2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%")
    result = clean_node(state, news_cleaner=mock_cleaner)

    assert result["cleaned_news"]["sentiment_label"] == "positive"
    assert result["cleaned_news"]["mentioned_numbers"] == ["2,600", "18.2%"]


def test_clean_node_skips_when_no_news_content() -> None:
    mock_cleaner = MagicMock()

    state = _base_state(news_content=None)
    result = clean_node(state, news_cleaner=mock_cleaner)

    mock_cleaner.clean.assert_not_called()
    assert result.get("cleaned_news") is None


def test_clean_node_accumulates_errors_on_exception() -> None:
    mock_cleaner = MagicMock()
    mock_cleaner.clean.side_effect = RuntimeError("LLM timeout")

    state = _base_state(news_content="some news")
    result = clean_node(state, news_cleaner=mock_cleaner)

    assert result.get("cleaned_news") is None
    assert any(e["code"] == "CLEAN_ERROR" for e in result["errors"])


def test_fetch_news_node_news_content_does_not_include_timestamp_as_first_line() -> None:
    """news_content 不應以時間戳作為第一行，以免 LLM 把時間戳當標題。"""
    mock_rss = MagicMock()
    mock_rss.fetch_news.return_value = [_make_raw_news_item()]

    state = _base_state()
    result = fetch_news_node(state, rss_client=mock_rss)

    news_content = result["news_content"]
    assert news_content is not None
    first_line = news_content.splitlines()[0]
    # 第一行不應是純時間戳（RFC 2822 格式）
    assert not first_line.startswith("Mon,")
    assert not first_line.startswith("Tue,")
    assert not first_line.startswith("Wed,")
    assert not first_line.startswith("Thu,")
    assert not first_line.startswith("Fri,")
    assert not first_line.startswith("Sat,")
    assert not first_line.startswith("Sun,")


def test_fetch_news_node_news_content_has_structured_labels() -> None:
    """news_content 應以標記欄位（標題:/摘要:）格式輸出，讓 LLM 明確識別語意。"""
    mock_rss = MagicMock()
    mock_rss.fetch_news.return_value = [_make_raw_news_item()]

    state = _base_state()
    result = fetch_news_node(state, rss_client=mock_rss)

    news_content = result["news_content"]
    assert news_content is not None
    assert "標題:" in news_content
    assert "摘要:" in news_content


def test_fetch_news_node_uses_symbol_prefix_as_query() -> None:
    mock_rss = MagicMock()
    mock_rss.fetch_news.return_value = []

    state = _base_state(symbol="2330.TW")
    fetch_news_node(state, rss_client=mock_rss)

    mock_rss.fetch_news.assert_called_once_with(query="2330")


# ── fetch_institutional_node ─────────────────────────────────────────────────

def test_fetch_institutional_node_writes_flow_to_state() -> None:
    """成功時，institutional_flow 應被寫入 state。"""
    mock_flow_data = {
        "symbol": "2330.TW",
        "foreign_buy": 1000.0,
        "investment_trust_buy": 200.0,
        "dealer_buy": 50.0,
        "margin_delta": None,
        "flow_label": "institutional_accumulation",
        "source_provider": "twse",
    }
    mock_fetcher = MagicMock(return_value=mock_flow_data)

    state = _base_state(snapshot=_make_snapshot())
    result = fetch_institutional_node(state, fetcher=mock_fetcher)

    assert result["institutional_flow"] is not None
    assert result["institutional_flow"]["flow_label"] == "institutional_accumulation"
    mock_fetcher.assert_called_once_with("2330.TW")


def test_fetch_institutional_node_stores_error_dict_on_failure() -> None:
    """fetcher 失敗回傳 error dict 時，仍寫入 institutional_flow，流程不中斷。"""
    mock_flow_data = {
        "symbol": "2330.TW",
        "error": "INSTITUTIONAL_FETCH_ERROR",
        "error_message": "all providers failed",
    }
    mock_fetcher = MagicMock(return_value=mock_flow_data)

    state = _base_state(snapshot=_make_snapshot())
    result = fetch_institutional_node(state, fetcher=mock_fetcher)

    assert result["institutional_flow"]["error"] == "INSTITUTIONAL_FETCH_ERROR"
    assert result.get("errors", []) == []  # 不額外累積 errors，flow 本身帶 error 欄位


# ── quality_gate_node ─────────────────────────────────────────────────────────

def test_quality_gate_node_returns_quality_dict_for_valid_news() -> None:
    """有效的 cleaned_news 應產出 quality_score 與 quality_flags。"""
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": "台積電 2 月營收年增 18.2%",
            "mentioned_numbers": ["2,600", "18.2%"],
            "sentiment_label": "positive",
        }
    )
    result = quality_gate_node(state)

    quality = result["cleaned_news_quality"]
    assert quality is not None
    assert "quality_score" in quality
    assert "quality_flags" in quality
    assert quality["quality_score"] == 100  # 全部欄位都正常，無扣分


def test_quality_gate_node_flags_timestamp_title() -> None:
    """時間戳標題應觸發 TITLE_LOW_QUALITY 並扣分 35。"""
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": "Mon, 03 Mar 2026 08:00:00",
            "mentioned_numbers": ["18.2%"],
            "sentiment_label": "positive",
        }
    )
    result = quality_gate_node(state)

    quality = result["cleaned_news_quality"]
    assert "TITLE_LOW_QUALITY" in quality["quality_flags"]
    assert quality["quality_score"] == 65  # 100 - 35


def test_quality_gate_node_flags_unknown_date() -> None:
    """date=unknown 應觸發 DATE_UNKNOWN 並扣分 15。"""
    state = _base_state(
        cleaned_news={
            "date": "unknown",
            "title": "台積電 2 月營收年增",
            "mentioned_numbers": ["18.2%"],
            "sentiment_label": "neutral",
        }
    )
    result = quality_gate_node(state)

    quality = result["cleaned_news_quality"]
    assert "DATE_UNKNOWN" in quality["quality_flags"]
    assert quality["quality_score"] == 85  # 100 - 15


def test_quality_gate_node_flags_no_financial_numbers() -> None:
    """純日期碎片的 mentioned_numbers 應觸發 NO_FINANCIAL_NUMBERS，但不扣分（deduction=0）。"""
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": "台積電 2 月營收年增",
            "mentioned_numbers": ["2026", "03"],
            "sentiment_label": "neutral",
        }
    )
    result = quality_gate_node(state)

    quality = result["cleaned_news_quality"]
    assert "NO_FINANCIAL_NUMBERS" in quality["quality_flags"]
    assert quality["quality_score"] == 100  # 旗標保留但不扣分


def test_quality_gate_node_returns_none_when_no_cleaned_news() -> None:
    """cleaned_news 為 None 時，cleaned_news_quality 應為 None。"""
    state = _base_state(cleaned_news=None)
    result = quality_gate_node(state)

    assert result["cleaned_news_quality"] is None


def test_quality_gate_node_backfills_title_from_raw_news_when_timestamp() -> None:
    """TITLE_LOW_QUALITY 時，應從 raw_news_items[0].title 回填 cleaned_news.title。"""
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": "Wed, 04 Mar 2026 23:02:08 GMT",
            "mentioned_numbers": ["18.2%"],
            "sentiment_label": "neutral",
        },
        raw_news_items=[
            asdict(_make_raw_news_item(title="台積電 2 月營收年增 20%"))
        ],
    )
    result = quality_gate_node(state)

    assert result["cleaned_news"]["title"] == "台積電 2 月營收年增 20%"


def test_quality_gate_node_does_not_backfill_title_when_quality_ok() -> None:
    """標題正常時，cleaned_news.title 不應被改動。"""
    original_title = "台積電 2 月營收年增 20%"
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": original_title,
            "mentioned_numbers": ["18.2%"],
            "sentiment_label": "positive",
        },
        raw_news_items=[
            asdict(_make_raw_news_item(title="其他標題"))
        ],
    )
    result = quality_gate_node(state)

    # 無 TITLE_LOW_QUALITY，cleaned_news 應維持原標題（或不在回傳中）
    cleaned = result.get("cleaned_news")
    if cleaned is not None:
        assert cleaned["title"] == original_title


def test_quality_gate_node_does_not_backfill_when_no_raw_news() -> None:
    """TITLE_LOW_QUALITY 但無 raw_news_items 時，不應報錯，cleaned_news.title 維持原值。"""
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": "Wed, 04 Mar 2026 23:02:08 GMT",
            "mentioned_numbers": ["18.2%"],
            "sentiment_label": "neutral",
        },
        raw_news_items=None,
    )
    result = quality_gate_node(state)

    # 不應拋出例外，cleaned_news 維持原時間戳標題
    cleaned = result.get("cleaned_news")
    if cleaned is not None:
        assert cleaned["title"] == "Wed, 04 Mar 2026 23:02:08 GMT"


def test_quality_gate_node_produces_news_display() -> None:
    """quality_gate_node 應產出 news_display，含乾淨標題、正規化日期、來源 URL。"""
    state = _base_state(
        cleaned_news={
            "date": "Mon, 03 Mar 2026 08:00:00 GMT",
            "title": "Wed, 04 Mar 2026 23:02:08 GMT",
            "mentioned_numbers": ["18.2%"],
            "sentiment_label": "neutral",
        },
        raw_news_items=[
            asdict(_make_raw_news_item(
                title="台積電 2 月營收年增 20%",
                url="https://example.com/news/1",
                published_at="Mon, 03 Mar 2026 08:00:00 GMT",
            ))
        ],
    )
    result = quality_gate_node(state)

    display = result["news_display"]
    assert display is not None
    assert display["title"] == "台積電 2 月營收年增 20%"
    assert display["date"] == "2026-03-03"   # RFC 2822 → ISO
    assert display["source_url"] == "https://example.com/news/1"


def test_quality_gate_node_news_display_date_none_when_unknown() -> None:
    """cleaned_news.date=unknown 時，news_display.date 應為 None。"""
    state = _base_state(
        cleaned_news={
            "date": "unknown",
            "title": "台積電公告",
            "mentioned_numbers": [],
            "sentiment_label": "neutral",
        },
        raw_news_items=[asdict(_make_raw_news_item())],
    )
    result = quality_gate_node(state)

    assert result["news_display"]["date"] is None


def test_quality_gate_node_news_display_none_when_no_cleaned_news() -> None:
    """cleaned_news 為 None 時，news_display 也應為 None。"""
    state = _base_state(cleaned_news=None)
    result = quality_gate_node(state)

    assert result.get("news_display") is None


def test_quality_gate_node_news_display_none_when_no_raw_news_items() -> None:
    """raw_news_items 為空時，news_display 應為 None（無來源可取）。"""
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": "台積電公告",
            "mentioned_numbers": [],
            "sentiment_label": "neutral",
        },
        raw_news_items=None,
    )
    result = quality_gate_node(state)

    assert result.get("news_display") is None


def test_quality_gate_node_flags_no_financial_numbers_score_100() -> None:
    """NO_FINANCIAL_NUMBERS 旗標不再扣分（deduction=0），quality_score 應為 100。"""
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": "台積電 2 月營收年增",
            "mentioned_numbers": ["2026", "03"],
            "sentiment_label": "neutral",
        }
    )
    result = quality_gate_node(state)

    quality = result["cleaned_news_quality"]
    assert "NO_FINANCIAL_NUMBERS" in quality["quality_flags"]
    assert quality["quality_score"] == 100  # 不再扣分


# ── news_display_items ────────────────────────────────────────────────────────


def test_quality_gate_node_produces_news_display_items_list() -> None:
    """quality_gate_node 應產出 news_display_items 陣列（最多 5 筆）。"""
    raw_items = [
        asdict(_make_raw_news_item(
            title=f"新聞標題 {i}",
            url=f"https://example.com/news/{i}",
            published_at="Mon, 03 Mar 2026 08:00:00 GMT",
        ))
        for i in range(1, 7)  # 6 筆，應截斷為 5
    ]
    state = _base_state(
        cleaned_news={
            "date": "Mon, 03 Mar 2026 08:00:00 GMT",
            "title": "台積電公告",
            "mentioned_numbers": [],
            "sentiment_label": "neutral",
        },
        raw_news_items=raw_items,
    )
    result = quality_gate_node(state)

    items = result["news_display_items"]
    assert isinstance(items, list)
    assert len(items) == 5  # 最多 5 筆
    assert items[0]["title"] == "新聞標題 1"
    assert items[0]["date"] == "2026-03-03"
    assert items[0]["source_url"] == "https://example.com/news/1"


def test_quality_gate_node_news_display_items_empty_when_no_raw() -> None:
    """raw_news_items 為空時，news_display_items 應為空陣列。"""
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": "台積電公告",
            "mentioned_numbers": [],
            "sentiment_label": "neutral",
        },
        raw_news_items=[],
    )
    result = quality_gate_node(state)
    assert result["news_display_items"] == []


# ── preprocess_node rsi14 ─────────────────────────────────────────────────────

def test_preprocess_node_writes_rsi14_float_to_state() -> None:
    """25 筆以上資料 → preprocess_node 應計算 rsi14 並寫入 state。"""
    from ai_stock_sentinel.graph.nodes import preprocess_node
    closes = [float(90 + i) for i in range(25)]  # 25 筆遞增
    snapshot = {"symbol": "2330.TW", "recent_closes": closes}
    state = _base_state(snapshot=snapshot)
    result = preprocess_node(state)
    assert result["rsi14"] is not None
    assert isinstance(result["rsi14"], float)
    assert 0.0 <= result["rsi14"] <= 100.0


def test_preprocess_node_rsi14_is_none_when_insufficient_data() -> None:
    """少於 15 筆資料 → rsi14 應為 None。"""
    from ai_stock_sentinel.graph.nodes import preprocess_node
    closes = [100.0] * 14  # 只有 14 筆，不足 15
    snapshot = {"symbol": "2330.TW", "recent_closes": closes}
    state = _base_state(snapshot=snapshot)
    result = preprocess_node(state)
    assert result["rsi14"] is None


# ── strategy_node action_plan_tag ─────────────────────────────────────────────

def test_strategy_node_returns_action_plan_tag() -> None:
    """strategy_node 應回傳 action_plan_tag 欄位。"""
    from ai_stock_sentinel.graph.nodes import strategy_node
    closes = [float(90 + i) for i in range(25)]
    state = _base_state(
        snapshot={"recent_closes": closes},
        rsi14=25.0,
        confidence_score=80,
        institutional_flow={"flow_label": "institutional_accumulation"},
    )
    result = strategy_node(state)
    assert "action_plan_tag" in result
    assert result["action_plan_tag"] in ("opportunity", "overheated", "neutral")


def test_strategy_node_action_plan_contains_new_fields() -> None:
    """strategy_node 產出的 action_plan 必須包含 evidence-based 新欄位。"""
    from ai_stock_sentinel.graph.nodes import strategy_node
    closes = [float(90 + i) for i in range(25)]
    state = _base_state(
        snapshot={"recent_closes": closes},
        rsi14=55.0,
        confidence_score=75,
        data_confidence=75,
        is_final=True,
        institutional_flow={"flow_label": "institutional_accumulation"},
    )
    result = strategy_node(state)
    action_plan = result.get("action_plan")
    assert action_plan is not None, "action_plan should not be None"
    assert "conviction_level" in action_plan
    assert action_plan["conviction_level"] in ("low", "medium", "high")
    assert "thesis_points" in action_plan
    assert isinstance(action_plan["thesis_points"], list)
    assert "invalidation_conditions" in action_plan
    assert isinstance(action_plan["invalidation_conditions"], list)
    assert "upgrade_triggers" in action_plan
    assert isinstance(action_plan["upgrade_triggers"], list)
    assert "downgrade_triggers" in action_plan
    assert isinstance(action_plan["downgrade_triggers"], list)
    assert "suggested_position_size" in action_plan
    assert isinstance(action_plan["suggested_position_size"], str)


def test_strategy_node_action_plan_conviction_low_when_low_confidence() -> None:
    """confidence_score < 60 時，action_plan.conviction_level 應為 low。"""
    from ai_stock_sentinel.graph.nodes import strategy_node
    closes = [float(90 + i) for i in range(25)]
    state = _base_state(
        snapshot={"recent_closes": closes},
        rsi14=55.0,
        confidence_score=50,  # < 60 → guardrail
        institutional_flow={"flow_label": "institutional_accumulation"},
    )
    result = strategy_node(state)
    action_plan = result.get("action_plan")
    assert action_plan is not None
    assert action_plan["conviction_level"] == "low"


def test_strategy_node_action_plan_position_size_zero_for_defensive_wait() -> None:
    """defensive_wait 策略時，action_plan.suggested_position_size 應為 0%。"""
    from ai_stock_sentinel.graph.nodes import strategy_node
    # 高 bias → defensive_wait
    closes = [100.0] * 25
    closes[-1] = 115.0  # 大幅拉升造成 bias > 10
    state = _base_state(
        snapshot={"recent_closes": closes},
        confidence_score=80,
        institutional_flow={"flow_label": "neutral"},
    )
    result = strategy_node(state)
    action_plan = result.get("action_plan")
    assert action_plan is not None
    if result.get("strategy_type") == "defensive_wait":
        assert action_plan["suggested_position_size"] == "0%"


# -- Position Diagnosis node tests --

from ai_stock_sentinel.analysis.position_scorer import compute_position_metrics  # noqa: E402


def _base_position_state():
    """Minimal GraphState with position fields for testing."""
    return {
        "symbol": "2330.TW",
        "entry_price": 980.0,
        "entry_date": None,
        "quantity": None,
        # snapshot fields required by preprocess_node
        "snapshot": {
            "symbol": "2330.TW",
            "currency": "TWD",
            "current_price": 1050.0,
            "volume": 10000,
            "recent_closes": [1040.0, 1045.0, 1050.0],
            "high_20d": 1060.0,
            "low_20d": 960.0,
            "support_20d": 960.0,
            "resistance_20d": 1060.0,
        },
        "news_content": "",
        "cleaned_news": [],
        "institutional_flow": None,
        "fundamental_data": None,
        "errors": [],
        "is_final": True,
    }


def test_preprocess_node_computes_position_metrics_when_entry_price_set():
    from ai_stock_sentinel.graph.nodes import preprocess_node

    state = _base_position_state()
    result = preprocess_node(state)

    assert "profit_loss_pct" in result
    assert "position_status" in result
    assert "position_narrative" in result
    assert result["position_status"] in ("profitable_safe", "at_risk", "under_water")


def test_preprocess_node_skips_position_metrics_when_no_entry_price():
    from ai_stock_sentinel.graph.nodes import preprocess_node

    state = _base_position_state()
    state["entry_price"] = None
    result = preprocess_node(state)

    assert result.get("profit_loss_pct") is None
    assert result.get("position_status") is None


def test_strategy_node_computes_trailing_stop_when_position_mode():
    from ai_stock_sentinel.graph.nodes import strategy_node

    state = _base_position_state()
    # Add required preprocess outputs
    state.update({
        "profit_loss_pct": 7.14,
        "position_status": "profitable_safe",
        "position_narrative": "獲利安全區",
        "technical_context": "",
        "rsi14": 55.0,
        "support_20d": 960.0,
        "resistance_20d": 1060.0,
        "high_20d": 1060.0,
        "low_20d": 960.0,
        "analysis_detail": {
            "technical_signal": "bullish",
            "institutional_flow": "institutional_accumulation",
            "sentiment_label": "positive",
            "summary": "test",
            "risks": [],
            "tech_insight": "",
            "inst_insight": "",
            "news_insight": "",
            "final_verdict": "",
            "fundamental_insight": "",
        },
        "confidence_score": 70,
    })
    result = strategy_node(state)

    assert "trailing_stop" in result
    assert result["trailing_stop"] is not None
    assert "trailing_stop_reason" in result
    assert "recommended_action" in result
    assert result["recommended_action"] in ("Hold", "Trim", "Exit")


# ── fetch_external_data_node concurrency ──────────────────────────────────────

def test_fetch_external_data_node_fetches_concurrently() -> None:
    """fetch_external_data_node 應用 asyncio.gather 同時抓取籌碼面與基本面，不依序執行。"""
    import time
    call_order: list[str] = []

    def fake_institutional_fetcher(symbol: str) -> dict:
        call_order.append("institutional_start")
        time.sleep(0.05)
        call_order.append("institutional_end")
        return {"symbol": symbol, "flow_label": "neutral"}

    def fake_fundamental_fetcher(symbol: str, current_price: float) -> dict:
        call_order.append("fundamental_start")
        time.sleep(0.05)
        call_order.append("fundamental_end")
        return {"pe_ratio": None, "dividend_yield": None}

    from ai_stock_sentinel.graph.nodes import fetch_external_data_node

    state = _base_state(snapshot=_make_snapshot())
    fetch_external_data_node(
        state,
        institutional_fetcher=fake_institutional_fetcher,
        fundamental_fetcher=fake_fundamental_fetcher,
    )

    # 並發執行時，兩個 start 應都在任一 end 之前出現
    first_end_idx = min(call_order.index("institutional_end"), call_order.index("fundamental_end"))
    assert call_order.index("institutional_start") < first_end_idx
    assert call_order.index("fundamental_start") < first_end_idx


def test_fetch_external_data_node_skips_when_data_already_present():
    """institutional_flow 和 fundamental_data 都已存在時，不應再呼叫外部 fetcher。"""
    inst_calls = []
    fund_calls = []

    def mock_inst(symbol):
        inst_calls.append(symbol)
        return {"flow": "new"}

    def mock_fund(symbol, price):
        fund_calls.append(symbol)
        return {"pe": 99}

    state = _base_state(
        snapshot={"current_price": 100.0, "recent_closes": []},
        institutional_flow={"flow": "existing"},
        fundamental_data={"pe": 20},
    )

    from ai_stock_sentinel.graph.nodes import fetch_external_data_node
    result = fetch_external_data_node(
        state,
        institutional_fetcher=mock_inst,
        fundamental_fetcher=mock_fund,
    )

    assert inst_calls == [], "institutional fetcher should not be called"
    assert fund_calls == [], "fundamental fetcher should not be called"
    assert result == {}


def test_fetch_external_data_node_skips_when_previous_fetch_errored():
    """institutional_flow 含 error key 時も skip する：API key 未設定等の永久的エラーは retry で回復しない。"""
    inst_calls = []

    def mock_inst(symbol):
        inst_calls.append(symbol)
        return {"flow_label": "buy"}

    state = _base_state(
        snapshot={"current_price": 100.0, "recent_closes": []},
        institutional_flow={"error": "API key not configured"},
        fundamental_data={"pe": 20},
    )

    from ai_stock_sentinel.graph.nodes import fetch_external_data_node
    result = fetch_external_data_node(
        state,
        institutional_fetcher=mock_inst,
        fundamental_fetcher=lambda s, p: {},
    )

    assert inst_calls == [], "error response is treated as cached — no retry for permanent failures"
    assert result == {}
