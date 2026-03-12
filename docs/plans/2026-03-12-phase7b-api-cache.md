# Phase 7b：API 快取層與 n8n 整合 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立 portfolio CRUD 端點、三段式分析快取邏輯（含 `is_final` 時間判斷）、歷史分析查詢端點、n8n 原始數據抓取端點，以及 n8n 自動化工作流配置。

**Architecture:** 所有新 endpoint 使用同步 SQLAlchemy Session（與現有 `api.py` 一致）。`POST /analyze/position` 改為三段式快取：L1 查 `stock_analysis_cache`，L2 查 `stock_raw_data`，L3 爬蟲全流程。`is_final` 由台灣收盤時間（13:30）判斷。n8n 部署於 Zeabur，透過 Cloudflare Tunnel 連線本地 DB。

**Tech Stack:** FastAPI（同步）、SQLAlchemy 2.x（psycopg2）、pytest、n8n（Zeabur）、Cloudflare Tunnel、Telegram Bot API

**前置依賴:** Phase 7a 已完成：
- `db/models.py` 有 `User`、`UserPortfolio`、`DailyAnalysisLog`（含 `is_final`）、`StockRawData`、`StockAnalysisCache`
- `db/session.py` 的同步 `get_db` 可用
- `services/history_loader.py` 已實作

---

## Task 3.5: `POST /portfolio` 新增持倉端點（含上限檢查）

**需求:** 每位使用者最多 5 筆 active 持倉，超過時回傳 HTTP 422。

**Files:**
- Create: `backend/src/ai_stock_sentinel/portfolio/__init__.py`
- Create: `backend/src/ai_stock_sentinel/portfolio/router.py`
- Create: `backend/tests/test_portfolio_router.py`
- Modify: `backend/src/ai_stock_sentinel/api.py`（最後一步才改）

**Step 1: 寫失敗測試**

建立 `backend/tests/test_portfolio_router.py`：

```python
# backend/tests/test_portfolio_router.py
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ai_stock_sentinel import api
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.auth.dependencies import get_current_user


def _make_client(active_count: int) -> TestClient:
    mock_user = MagicMock()
    mock_user.id = 1

    mock_result = MagicMock()
    mock_result.scalar.return_value = active_count

    mock_db = MagicMock()
    mock_db.execute.return_value = mock_result

    app = api.app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def test_add_portfolio_success():
    """active_count < 5 時應成功建立持倉，回傳 201。"""
    client = _make_client(active_count=3)
    resp = client.post("/portfolio", json={
        "symbol": "2330.TW",
        "entry_price": 900.0,
        "entry_date": "2026-01-01",
        "quantity": 100,
    })
    assert resp.status_code == 201


def test_add_portfolio_rejects_when_limit_reached():
    """active_count >= 5 時應回傳 422，且 detail 含 '5'。"""
    client = _make_client(active_count=5)
    resp = client.post("/portfolio", json={
        "symbol": "2454.TW",
        "entry_price": 800.0,
        "entry_date": "2026-01-01",
        "quantity": 50,
    })
    assert resp.status_code == 422
    assert "5" in resp.json()["detail"]
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend && python -m pytest tests/test_portfolio_router.py -v
```

Expected: 失敗（路由不存在）

**Step 3: 建立 portfolio/__init__.py**

建立空檔案 `backend/src/ai_stock_sentinel/portfolio/__init__.py`。

**Step 4: 實作 portfolio/router.py**

建立 `backend/src/ai_stock_sentinel/portfolio/router.py`：

```python
# backend/src/ai_stock_sentinel/portfolio/router.py
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.models import UserPortfolio
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.user_models.user import User

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

PORTFOLIO_LIMIT = 5


class PortfolioCreateRequest(BaseModel):
    symbol: str
    entry_price: float
    entry_date: date
    quantity: int = 0
    notes: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
def add_portfolio(
    payload: PortfolioCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    active_count = db.execute(
        select(func.count()).select_from(UserPortfolio).where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.is_active == True,
        )
    ).scalar()

    if active_count >= PORTFOLIO_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"最多只能追蹤 {PORTFOLIO_LIMIT} 筆持股",
        )

    entry = UserPortfolio(
        user_id=current_user.id,
        symbol=payload.symbol,
        entry_price=payload.entry_price,
        entry_date=payload.entry_date,
        quantity=payload.quantity,
        notes=payload.notes,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "symbol": entry.symbol}
```

**Step 5: 在 api.py 末尾加入 router**

在 `backend/src/ai_stock_sentinel/api.py` 現有 `app.include_router(auth_router)` 之後加入：

```python
from ai_stock_sentinel.portfolio.router import router as portfolio_router
app.include_router(portfolio_router)
```

**Step 6: 執行測試**

```bash
cd backend && python -m pytest tests/test_portfolio_router.py -v
```

Expected: 2 tests PASSED

**Step 7: 確認既有測試無回歸**

```bash
cd backend && python -m pytest tests/ -v -x
```

**Step 8: Commit**

```bash
git add backend/src/ai_stock_sentinel/portfolio/ backend/tests/test_portfolio_router.py backend/src/ai_stock_sentinel/api.py
git commit -m "feat: add POST /portfolio with 5-stock active limit per user"
```

---

## Task 4: `POST /analyze/position` 三段式快取邏輯（含 `is_final` 時間判斷）

**核心決策邏輯：**

```
使用者查詢 symbol X
    │
    ▼
[L1] 查 stock_analysis_cache 有 today 的紀錄？
    ├─ YES，is_final=TRUE → 直接回傳（毫秒級）
    ├─ YES，is_final=FALSE + 現在 < 13:30 → 直接回傳（附免責聲明）
    ├─ YES，is_final=FALSE + 現在 ≥ 13:30 → 作廢，強制走 L2/L3
    └─ NO
         ▼
[L2] 查 stock_raw_data 有 today 的原始數據？
    ├─ YES → 只打 model → is_final=TRUE → 存快取 → 回傳
    └─ NO
         ▼
[L3] 爬蟲抓原始數據 + 打 model → is_final=由時間決定 → 存快取 → 回傳
```

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Create: `backend/tests/test_analysis_cache.py`

**Step 1: 寫失敗測試**

建立 `backend/tests/test_analysis_cache.py`：

```python
# backend/tests/test_analysis_cache.py
from __future__ import annotations

from datetime import time
from unittest.mock import MagicMock, patch

import pytest

from ai_stock_sentinel import api


def _make_mock_cache(is_final: bool, action_tag: str = "Hold", confidence: float = 72.5):
    c = MagicMock()
    c.is_final = is_final
    c.symbol = "2330.TW"
    c.action_tag = action_tag
    c.signal_confidence = confidence
    c.recommended_action = "觀望"
    c.final_verdict = "中性"
    return c


def test_final_cache_hit_returns_without_disclaimer():
    """is_final=TRUE 的快取命中時，intraday_disclaimer 為 None。"""
    mock_cache = _make_mock_cache(is_final=True)

    with patch("ai_stock_sentinel.api.get_analysis_cache", return_value=mock_cache):
        db = MagicMock()
        result = api._handle_cache_hit(mock_cache, now_time=time(10, 0))

    assert result.is_final is True
    assert result.intraday_disclaimer is None


def test_intraday_cache_returns_with_disclaimer():
    """is_final=FALSE + 盤中時間，回傳含免責聲明。"""
    mock_cache = _make_mock_cache(is_final=False)

    result = api._handle_cache_hit(mock_cache, now_time=time(10, 30))

    assert result.is_final is False
    assert result.intraday_disclaimer is not None


def test_stale_cache_is_none_after_market_close():
    """is_final=FALSE + 收盤後（≥13:30），_handle_cache_hit 回傳 None（強制重新分析）。"""
    mock_cache = _make_mock_cache(is_final=False)

    result = api._handle_cache_hit(mock_cache, now_time=time(14, 0))

    assert result is None


def test_build_response_intraday_has_disclaimer():
    """is_final=False 時 _build_analysis_response 含 intraday_disclaimer。"""
    result = api._build_analysis_response(
        symbol="2330.TW",
        action_tag="Hold",
        signal_confidence=65.0,
        recommended_action="觀望",
        final_verdict="中性",
        is_final=False,
    )
    assert result.is_final is False
    assert result.intraday_disclaimer is not None


def test_build_response_final_no_disclaimer():
    """is_final=True 時 _build_analysis_response 無 intraday_disclaimer。"""
    result = api._build_analysis_response(
        symbol="2330.TW",
        action_tag="Hold",
        signal_confidence=65.0,
        recommended_action="觀望",
        final_verdict="中性",
        is_final=True,
    )
    assert result.is_final is True
    assert result.intraday_disclaimer is None
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend && python -m pytest tests/test_analysis_cache.py -v
```

Expected: AttributeError 或 ImportError（函式不存在）

**Step 3: 在 api.py 加入快取輔助層**

在 `backend/src/ai_stock_sentinel/api.py` 加入以下內容（在 `app = FastAPI(...)` 之前）：

```python
# 在現有 import 區塊末尾加入
from datetime import date as _date, datetime, time as _time
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.db.models import StockAnalysisCache, StockRawData, UserPortfolio
```

在 `_build_response` 函式之前加入：

```python
# ─── 快取常數 ───────────────────────────────────────────────
MARKET_CLOSE = _time(13, 30)
INTRADAY_DISCLAIMER = (
    "⚠️ 注意：目前為盤中階段（指標未收定），"
    "以下分析僅供即時參考，不代表當日收盤定論。"
)


# ─── 快取 Response Schema ────────────────────────────────────
class CachedAnalyzeResponse(BaseModel):
    symbol: str
    signal_confidence: float | None
    action_tag: str | None
    recommended_action: str | None
    final_verdict: str | None
    is_final: bool
    intraday_disclaimer: Optional[str] = None


# ─── 快取輔助函式 ─────────────────────────────────────────────

def get_analysis_cache(db: Session, symbol: str) -> StockAnalysisCache | None:
    """查詢今日的分析結果快取（L1）。"""
    return db.execute(
        select(StockAnalysisCache).where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == _date.today(),
        )
    ).scalar_one_or_none()


def get_raw_data(db: Session, symbol: str) -> StockRawData | None:
    """查詢今日的原始數據快取（L2）。"""
    return db.execute(
        select(StockRawData).where(
            StockRawData.symbol == symbol,
            StockRawData.record_date == _date.today(),
        )
    ).scalar_one_or_none()


def _handle_cache_hit(
    cache: StockAnalysisCache,
    now_time: _time,
) -> CachedAnalyzeResponse | None:
    """處理 L1 快取命中邏輯。

    - is_final=TRUE：直接回傳
    - is_final=FALSE + 盤中：回傳含免責聲明
    - is_final=FALSE + 收盤後：回傳 None（強制重新分析）
    """
    if cache.is_final:
        return _build_analysis_response(
            symbol=cache.symbol,
            action_tag=cache.action_tag,
            signal_confidence=float(cache.signal_confidence) if cache.signal_confidence else None,
            recommended_action=cache.recommended_action,
            final_verdict=cache.final_verdict,
            is_final=True,
        )
    if now_time < MARKET_CLOSE:
        return _build_analysis_response(
            symbol=cache.symbol,
            action_tag=cache.action_tag,
            signal_confidence=float(cache.signal_confidence) if cache.signal_confidence else None,
            recommended_action=cache.recommended_action,
            final_verdict=cache.final_verdict,
            is_final=False,
        )
    return None  # 收盤後非定稿快取 → 強制重新分析


def _build_analysis_response(
    *,
    symbol: str,
    action_tag: str | None,
    signal_confidence: float | None,
    recommended_action: str | None,
    final_verdict: str | None,
    is_final: bool,
) -> CachedAnalyzeResponse:
    return CachedAnalyzeResponse(
        symbol=symbol,
        signal_confidence=signal_confidence,
        action_tag=action_tag,
        recommended_action=recommended_action,
        final_verdict=final_verdict,
        is_final=is_final,
        intraday_disclaimer=INTRADAY_DISCLAIMER if not is_final else None,
    )


def upsert_analysis_cache(db: Session, data: dict) -> None:
    """UPSERT 分析結果至 stock_analysis_cache（跨使用者共用）。"""
    import json
    db.execute(
        text("""
            INSERT INTO stock_analysis_cache (
                symbol, record_date, signal_confidence, action_tag,
                recommended_action, indicators, final_verdict,
                prev_action_tag, prev_confidence, is_final, updated_at
            ) VALUES (
                :symbol, CURRENT_DATE, :signal_confidence, :action_tag,
                :recommended_action, :indicators::jsonb, :final_verdict,
                (SELECT action_tag FROM stock_analysis_cache
                 WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1),
                (SELECT signal_confidence FROM stock_analysis_cache
                 WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1),
                :is_final, NOW()
            )
            ON CONFLICT (symbol, record_date) DO UPDATE SET
                signal_confidence  = EXCLUDED.signal_confidence,
                action_tag         = EXCLUDED.action_tag,
                recommended_action = EXCLUDED.recommended_action,
                indicators         = EXCLUDED.indicators,
                final_verdict      = EXCLUDED.final_verdict,
                is_final           = EXCLUDED.is_final,
                updated_at         = NOW()
        """),
        {
            "symbol":             data.get("symbol"),
            "signal_confidence":  data.get("signal_confidence"),
            "action_tag":         data.get("action_tag"),
            "recommended_action": data.get("recommended_action"),
            "indicators":         json.dumps(data.get("indicators") or {}),
            "final_verdict":      data.get("final_verdict"),
            "is_final":           data.get("is_final", False),
        }
    )
    db.commit()


def _extract_indicators(result: dict) -> dict:
    """從 graph result 提取 indicators JSONB 快照。"""
    snapshot = result.get("snapshot") or {}
    inst = result.get("institutional_flow") or {}
    return {
        "ma5":          snapshot.get("ma5"),
        "ma20":         snapshot.get("ma20"),
        "ma60":         snapshot.get("ma60"),
        "rsi_14":       result.get("rsi14"),
        "close_price":  snapshot.get("current_price"),
        "volume_ratio": snapshot.get("volume_ratio"),
        "institutional": {
            "foreign_net": inst.get("foreign_net"),
            "trust_net":   inst.get("trust_net"),
            "dealer_net":  inst.get("dealer_net"),
        } if not inst.get("error") else None,
    }
```

**Step 4: 執行測試**

```bash
cd backend && python -m pytest tests/test_analysis_cache.py -v
```

Expected: 5 tests PASSED

**Step 5: 確認既有測試無回歸**

```bash
cd backend && python -m pytest tests/ -v -x
```

**Step 6: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_analysis_cache.py
git commit -m "feat: add three-stage cache helpers with is_final time-based logic"
```

---

## Task 4.5: `GET /portfolio/{portfolio_id}/history` 歷史分析端點

**需求:** 持倉列表頁展示歷史診斷結果，純 DB 查詢，不觸發 LLM 分析。支援分頁。

**Files:**
- Create: `backend/src/ai_stock_sentinel/portfolio/history_router.py`
- Create: `backend/tests/test_portfolio_history.py`
- Modify: `backend/src/ai_stock_sentinel/api.py`

**Step 1: 寫失敗測試**

建立 `backend/tests/test_portfolio_history.py`：

```python
# backend/tests/test_portfolio_history.py
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ai_stock_sentinel import api
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.auth.dependencies import get_current_user


def _make_client(portfolio=None, records=None, total=0):
    mock_user = MagicMock()
    mock_user.id = 1

    mock_db = MagicMock()

    # mock db.get(UserPortfolio, portfolio_id)
    mock_db.get.return_value = portfolio

    # mock db.execute(...).scalar() for COUNT
    # mock db.execute(...).scalars().all() for records
    def _execute(stmt):
        result = MagicMock()
        result.scalar.return_value = total
        result.scalars.return_value.all.return_value = records or []
        return result

    mock_db.execute.side_effect = _execute

    app = api.app
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


def test_history_returns_404_when_portfolio_not_found():
    """持倉不存在時回傳 404。"""
    client = _make_client(portfolio=None)
    resp = client.get("/portfolio/999/history")
    assert resp.status_code == 404


def test_history_returns_404_when_portfolio_belongs_to_other_user():
    """持倉屬於其他使用者時回傳 404。"""
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 99  # 不是 user.id=1
    mock_portfolio.symbol = "2330.TW"
    client = _make_client(portfolio=mock_portfolio)
    resp = client.get("/portfolio/1/history")
    assert resp.status_code == 404


def test_history_returns_records():
    """應回傳指定持倉的歷史分析紀錄。"""
    from datetime import date
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 1
    mock_portfolio.symbol = "2330.TW"

    mock_record = MagicMock()
    mock_record.record_date = date(2026, 3, 10)
    mock_record.signal_confidence = 72.5
    mock_record.action_tag = "Hold"
    mock_record.recommended_action = "觀望"
    mock_record.indicators = {}
    mock_record.final_verdict = "中性"
    mock_record.prev_action_tag = None
    mock_record.prev_confidence = None

    client = _make_client(portfolio=mock_portfolio, records=[mock_record], total=1)
    resp = client.get("/portfolio/1/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "2330.TW"
    assert data["total"] == 1
    assert len(data["records"]) == 1


def test_history_returns_empty_when_no_records():
    """無紀錄時回傳 total=0、records=[]。"""
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 1
    mock_portfolio.symbol = "2454.TW"
    client = _make_client(portfolio=mock_portfolio, records=[], total=0)
    resp = client.get("/portfolio/1/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["records"] == []


def test_history_supports_pagination():
    """支援 limit/offset 分頁參數（不報錯即可）。"""
    mock_portfolio = MagicMock()
    mock_portfolio.user_id = 1
    mock_portfolio.symbol = "2330.TW"
    client = _make_client(portfolio=mock_portfolio, records=[], total=0)
    resp = client.get("/portfolio/1/history?limit=5&offset=10")
    assert resp.status_code == 200
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend && python -m pytest tests/test_portfolio_history.py -v
```

Expected: 404（路由不存在）

**Step 3: 實作 portfolio/history_router.py**

建立 `backend/src/ai_stock_sentinel/portfolio/history_router.py`：

```python
# backend/src/ai_stock_sentinel/portfolio/history_router.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.models import DailyAnalysisLog, UserPortfolio
from ai_stock_sentinel.db.session import get_db

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/{portfolio_id}/history")
def get_portfolio_history(
    portfolio_id: int,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    portfolio = db.get(UserPortfolio, portfolio_id)
    if not portfolio or portfolio.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="持倉不存在")

    total = db.execute(
        select(func.count())
        .select_from(DailyAnalysisLog)
        .where(
            DailyAnalysisLog.user_id == current_user.id,
            DailyAnalysisLog.symbol == portfolio.symbol,
        )
    ).scalar()

    records = db.execute(
        select(DailyAnalysisLog)
        .where(
            DailyAnalysisLog.user_id == current_user.id,
            DailyAnalysisLog.symbol == portfolio.symbol,
        )
        .order_by(DailyAnalysisLog.record_date.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return {
        "symbol": portfolio.symbol,
        "total": total,
        "records": [
            {
                "record_date":        r.record_date.isoformat(),
                "signal_confidence":  float(r.signal_confidence) if r.signal_confidence else None,
                "action_tag":         r.action_tag,
                "recommended_action": r.recommended_action,
                "indicators":         r.indicators,
                "final_verdict":      r.final_verdict,
                "prev_action_tag":    r.prev_action_tag,
                "prev_confidence":    float(r.prev_confidence) if r.prev_confidence else None,
            }
            for r in records
        ],
    }
```

**Step 4: 在 api.py 加入 history router**

在 `api.py` 現有 `app.include_router(portfolio_router)` 之後加入：

```python
from ai_stock_sentinel.portfolio.history_router import router as history_router
app.include_router(history_router)
```

**Step 5: 執行測試**

```bash
cd backend && python -m pytest tests/test_portfolio_history.py -v
```

Expected: 5 tests PASSED

**Step 6: 確認全部測試無回歸**

```bash
cd backend && python -m pytest tests/ -v -x
```

**Step 7: Commit**

```bash
git add backend/src/ai_stock_sentinel/portfolio/history_router.py backend/tests/test_portfolio_history.py backend/src/ai_stock_sentinel/api.py
git commit -m "feat: add GET /portfolio/{id}/history for historical analysis records"
```

---

## Task 4.6: `POST /internal/fetch-raw-data` 端點

**需求:** n8n cron 每日收盤後呼叫此端點，批次抓取指定股票的原始數據並存入 `stock_raw_data`。使用 API Key 保護（不需要使用者 JWT）。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Create: `backend/tests/test_fetch_raw_data.py`

**Step 1: 寫失敗測試**

建立 `backend/tests/test_fetch_raw_data.py`：

```python
# backend/tests/test_fetch_raw_data.py
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_stock_sentinel import api


def test_fetch_raw_data_requires_api_key():
    """未提供 API Key 時應回傳 401。"""
    client = TestClient(api.app)
    resp = client.post("/internal/fetch-raw-data", json={"symbol": "2330.TW"})
    assert resp.status_code == 401


def test_fetch_raw_data_rejects_wrong_key():
    """提供錯誤 API Key 時應回傳 401。"""
    client = TestClient(api.app)
    resp = client.post(
        "/internal/fetch-raw-data",
        json={"symbol": "2330.TW"},
        headers={"X-Internal-Api-Key": "wrong-key"},
    )
    assert resp.status_code == 401


def test_fetch_raw_data_success():
    """提供正確 API Key 時應回傳 200 含 status=ok。"""
    with patch.dict("os.environ", {"INTERNAL_API_KEY": "test-key"}):
        with patch("ai_stock_sentinel.api.fetch_and_store_raw_data", return_value=None):
            # 重建 app 的 INTERNAL_API_KEY（已在 module load 時讀取，需 patch 模組變數）
            import ai_stock_sentinel.api as api_module
            original_key = api_module.INTERNAL_API_KEY
            api_module.INTERNAL_API_KEY = "test-key"
            try:
                client = TestClient(api.app)
                resp = client.post(
                    "/internal/fetch-raw-data",
                    json={"symbol": "2330.TW"},
                    headers={"X-Internal-Api-Key": "test-key"},
                )
                assert resp.status_code == 200
                assert resp.json()["status"] == "ok"
                assert resp.json()["symbol"] == "2330.TW"
            finally:
                api_module.INTERNAL_API_KEY = original_key
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend && python -m pytest tests/test_fetch_raw_data.py -v
```

Expected: 404 或 AttributeError（端點不存在）

**Step 3: 在 api.py 加入端點**

在 `api.py` 的 `MARKET_CLOSE` 常數附近加入：

```python
import os

INTERNAL_API_KEY: str = os.environ.get("INTERNAL_API_KEY", "")


def verify_internal_api_key(x_internal_api_key: str = Header(default=None)):
    if not INTERNAL_API_KEY or x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


class FetchRawDataRequest(BaseModel):
    symbol: str
    date: str = "today"
```

在 `api.py` 的路由區加入：

```python
@app.post("/internal/fetch-raw-data")
def fetch_raw_data_endpoint(
    payload: FetchRawDataRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_internal_api_key),
):
    """n8n cron 呼叫的內部端點，抓取原始數據並存入 stock_raw_data。"""
    from datetime import date as _date_type
    record_date = _date_type.today() if payload.date == "today" else _date_type.fromisoformat(payload.date)
    fetch_and_store_raw_data(db, payload.symbol, record_date)
    return {"status": "ok", "symbol": payload.symbol, "record_date": record_date.isoformat()}


def fetch_and_store_raw_data(db: Session, symbol: str, record_date) -> None:
    """抓取技術面、籌碼面、基本面原始數據並 UPSERT 至 stock_raw_data。

    TODO: 接入現有爬蟲（yfinance_client、institutional_flow、fundamental）。
    目前為 stub，不執行任何操作。
    """
    pass
```

同時在 `api.py` import 區塊確保有 `Header`：

```python
from fastapi import Depends, FastAPI, Header, HTTPException
```

**Step 4: 執行測試**

```bash
cd backend && python -m pytest tests/test_fetch_raw_data.py -v
```

Expected: 3 tests PASSED

**Step 5: 確認全部測試無回歸**

```bash
cd backend && python -m pytest tests/ -v -x
```

**Step 6: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_fetch_raw_data.py
git commit -m "feat: add POST /internal/fetch-raw-data endpoint for n8n cron raw data ingestion"
```

---

## Task 5–7: n8n 工作流配置（手動）

> 這三個 task 為基礎設施手動配置，無程式碼，Claude 無法代勞。
> 請參考原計劃 `docs/plans/2026-03-12-phase7-infrastructure.md` Tasks 5–7 的步驟操作。

**Task 5 摘要（Workflow A）：**
- Zeabur 部署 n8n
- Cloudflare Tunnel 連線本地 PostgreSQL
- n8n 建立 Cron（18:30 週一–五）→ 查 watchlist → HTTP POST `/internal/fetch-raw-data`

**Task 6 摘要（Workflow B）：**
- 建立 Telegram Bot（@BotFather）
- n8n 查 `stock_analysis_cache` action_tag = Exit/Trim → 組訊息 → Telegram 發送

**Task 7 摘要（Workflow C）：**
- Cron（週日 08:00）→ 查 `daily_analysis_log` 週報統計 → Telegram 發送

---

## 完成檢查清單

- [ ] `POST /portfolio` 成功建立持倉，active_count >= 5 時回傳 422
- [ ] `test_portfolio_router.py` 2 tests PASSED
- [ ] `_handle_cache_hit` 三種情境正確（final/intraday/stale）
- [ ] `test_analysis_cache.py` 5 tests PASSED
- [ ] `GET /portfolio/{id}/history` 回傳 symbol、total、records
- [ ] `test_portfolio_history.py` 5 tests PASSED
- [ ] `POST /internal/fetch-raw-data` 無 key 回傳 401，正確 key 回傳 200
- [ ] `test_fetch_raw_data.py` 3 tests PASSED
- [ ] 全部既有 `tests/test_api.py` 通過（無回歸）
- [ ] n8n Workflow A/B/C 手動配置完成（參考原計劃 Tasks 5–7）

---

*文件版本：v1.0 | 建立：2026-03-12 | 對應原計劃：`docs/plans/2026-03-12-phase7-infrastructure.md` Tasks 3.5–7*
