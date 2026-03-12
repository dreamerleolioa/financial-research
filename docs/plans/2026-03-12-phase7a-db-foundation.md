# Phase 7a：DB 基礎層建置 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立 PostgreSQL Docker Compose、補齊 SQLAlchemy ORM models（`is_final`、`StockRawData`、`StockAnalysisCache`、`User`），並實作 `history_loader` 服務，讓系統具備歷史記憶基礎。

**Architecture:** 現有 `db/session.py` 使用同步 SQLAlchemy + psycopg2，維持同步架構（不升級 async，避免影響現有 api.py）。Alembic 管理 schema migration。`history_loader` 以同步 Session 查詢昨日分析結果，供 LangGraph 注入上下文。

**Tech Stack:** PostgreSQL 17、Docker Compose、SQLAlchemy 2.x（psycopg2 driver，同步）、Alembic、pytest

**前置依賴:**
- `backend/pyproject.toml` 已含 `sqlalchemy>=2.0`、`psycopg2-binary`、`alembic`
- `db/session.py` 已有同步 `Base`、`get_db`（維持不動）
- `db/models.py` 已有 `UserPortfolio`、`DailyAnalysisLog`（需補欄位）

---

## Task 1: Docker Compose 部署 PostgreSQL

**Files:**
- Create: `docker-compose.yml`（專案根目錄）
- Create: `db/init/01_schema.sql`（僅供參考，不執行）

**Step 1: 確認 docker-compose.yml 不存在**

```bash
ls docker-compose.yml 2>/dev/null && echo "EXISTS" || echo "NOT FOUND"
```

Expected: `NOT FOUND`

**Step 2: 建立 docker-compose.yml**

```yaml
# docker-compose.yml
services:
  db:
    image: postgres:17
    restart: unless-stopped
    environment:
      POSTGRES_USER: sentinel
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: ai_stock_sentinel
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"

volumes:
  postgres_data:
```

**Step 3: 建立 db/init/ 目錄與參考 SQL**

```bash
mkdir -p db/init
```

在 `db/init/01_schema.sql` 寫入（僅供參考，schema 由 Alembic 管理）：

```sql
-- db/init/01_schema.sql
-- ⚠️ 僅供參考。實際 schema 由 Alembic 管理。
-- 部署新環境請執行：alembic upgrade head

-- 此檔案不會被 Docker 自動執行（未掛載至 entrypoint-initdb.d）
```

**Step 4: Commit**

```bash
git add docker-compose.yml db/init/01_schema.sql
git commit -m "feat: add PostgreSQL docker-compose and reference schema"
```

---

## Task 2: 補齊 SQLAlchemy ORM Models

**背景:** 現有 `db/models.py` 缺少：
1. `User` model（`users` 表）
2. `DailyAnalysisLog.is_final` 欄位
3. `StockRawData` model
4. `StockAnalysisCache` model
5. `DailyAnalysisLog` 的 unique index 應為 `(user_id, symbol, record_date)`（現為 `(symbol, record_date)`）

現有 `Base` 定義在 `db/session.py`，models 從 `db.session` import，維持此架構。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/db/models.py`
- Create: `backend/tests/test_db_models.py`

**Step 1: 寫失敗測試**

建立 `backend/tests/test_db_models.py`：

```python
# backend/tests/test_db_models.py
from ai_stock_sentinel.db.models import (
    DailyAnalysisLog,
    StockAnalysisCache,
    StockRawData,
    UserPortfolio,
)
from ai_stock_sentinel.db.session import Base


def test_user_table_exists_in_base():
    """users 表應在 Base.metadata 中。"""
    assert "users" in Base.metadata.tables


def test_user_portfolio_model_columns():
    cols = {c.name for c in UserPortfolio.__table__.columns}
    assert {"id", "user_id", "symbol", "entry_price", "quantity", "entry_date", "is_active"} <= cols


def test_daily_analysis_log_has_is_final():
    """DailyAnalysisLog 必須有 is_final 欄位。"""
    cols = {c.name for c in DailyAnalysisLog.__table__.columns}
    assert "is_final" in cols


def test_daily_analysis_log_model_columns():
    cols = {c.name for c in DailyAnalysisLog.__table__.columns}
    assert {
        "id", "user_id", "symbol", "record_date", "signal_confidence",
        "action_tag", "indicators", "final_verdict",
        "prev_action_tag", "prev_confidence", "is_final",
    } <= cols


def test_stock_raw_data_model_columns():
    cols = {c.name for c in StockRawData.__table__.columns}
    assert {"id", "symbol", "record_date", "technical", "institutional", "fundamental", "fetched_at"} <= cols


def test_stock_analysis_cache_model_columns():
    cols = {c.name for c in StockAnalysisCache.__table__.columns}
    assert {
        "id", "symbol", "record_date", "signal_confidence",
        "action_tag", "indicators", "final_verdict",
        "prev_action_tag", "prev_confidence", "is_final", "created_at", "updated_at",
    } <= cols
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend && python -m pytest tests/test_db_models.py -v
```

Expected: ImportError 或 AssertionError（缺少 model）

**Step 3: 更新 db/models.py**

完整替換 `backend/src/ai_stock_sentinel/db/models.py`：

```python
# backend/src/ai_stock_sentinel/db/models.py
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from ai_stock_sentinel.db.session import Base


class User(Base):
    __tablename__ = "users"

    id:         Mapped[int]            = mapped_column(Integer, primary_key=True)
    google_sub: Mapped[str]            = mapped_column(String(255), unique=True, nullable=False)
    email:      Mapped[str]            = mapped_column(String(255), unique=True, nullable=False)
    name:       Mapped[str | None]     = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None]     = mapped_column(Text, nullable=True)
    is_active:  Mapped[bool]           = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class UserPortfolio(Base):
    __tablename__ = "user_portfolio"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_portfolio_user_symbol"),
    )

    id:          Mapped[int]        = mapped_column(Integer, primary_key=True)
    user_id:     Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    symbol:      Mapped[str]        = mapped_column(String(20), nullable=False)
    entry_price: Mapped[float]      = mapped_column(Numeric(10, 2), nullable=False)
    quantity:    Mapped[int]        = mapped_column(Integer, nullable=False, default=0)
    entry_date:  Mapped[date]       = mapped_column(Date, nullable=False)
    is_active:   Mapped[bool]       = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notes:       Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:  Mapped[datetime]   = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at:  Mapped[datetime]   = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DailyAnalysisLog(Base):
    __tablename__ = "daily_analysis_log"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "record_date", name="uq_log_user_symbol_date"),
        Index("idx_log_indicators_gin", "indicators", postgresql_using="gin"),
    )

    id:                 Mapped[int]          = mapped_column(Integer, primary_key=True)
    user_id:            Mapped[int | None]   = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    symbol:             Mapped[str]          = mapped_column(String(20), nullable=False)
    record_date:        Mapped[date]         = mapped_column(Date, nullable=False)
    signal_confidence:  Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    action_tag:         Mapped[str | None]   = mapped_column(String(20), nullable=True)
    recommended_action: Mapped[str | None]   = mapped_column(Text, nullable=True)
    indicators:         Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    final_verdict:      Mapped[str | None]   = mapped_column(Text, nullable=True)
    prev_action_tag:    Mapped[str | None]   = mapped_column(String(20), nullable=True)
    prev_confidence:    Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_final:           Mapped[bool]         = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at:         Mapped[datetime]     = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockRawData(Base):
    __tablename__ = "stock_raw_data"
    __table_args__ = (
        UniqueConstraint("symbol", "record_date", name="uq_raw_symbol_date"),
        Index("idx_raw_technical_gin", "technical", postgresql_using="gin"),
        Index("idx_raw_institutional_gin", "institutional", postgresql_using="gin"),
    )

    id:            Mapped[int]          = mapped_column(Integer, primary_key=True)
    symbol:        Mapped[str]          = mapped_column(String(20), nullable=False)
    record_date:   Mapped[date]         = mapped_column(Date, nullable=False)
    technical:     Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    institutional: Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    fundamental:   Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    fetched_at:    Mapped[datetime]     = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockAnalysisCache(Base):
    __tablename__ = "stock_analysis_cache"
    __table_args__ = (
        UniqueConstraint("symbol", "record_date", name="uq_cache_symbol_date"),
        Index("idx_cache_indicators_gin", "indicators", postgresql_using="gin"),
    )

    id:                 Mapped[int]          = mapped_column(Integer, primary_key=True)
    symbol:             Mapped[str]          = mapped_column(String(20), nullable=False)
    record_date:        Mapped[date]         = mapped_column(Date, nullable=False)
    signal_confidence:  Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    action_tag:         Mapped[str | None]   = mapped_column(String(20), nullable=True)
    recommended_action: Mapped[str | None]   = mapped_column(Text, nullable=True)
    indicators:         Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
    final_verdict:      Mapped[str | None]   = mapped_column(Text, nullable=True)
    prev_action_tag:    Mapped[str | None]   = mapped_column(String(20), nullable=True)
    prev_confidence:    Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_final:           Mapped[bool]         = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at:         Mapped[datetime]     = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at:         Mapped[datetime]     = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
```

**Step 4: 執行測試，確認通過**

```bash
cd backend && python -m pytest tests/test_db_models.py -v
```

Expected: 6 tests PASSED

**Step 5: 確認既有測試不破壞**

```bash
cd backend && python -m pytest tests/ -v --ignore=tests/test_db_models.py -x
```

Expected: 全部通過（或與修改前相同數量通過）

**Step 6: Commit**

```bash
git add backend/src/ai_stock_sentinel/db/models.py backend/tests/test_db_models.py
git commit -m "feat: add User, StockRawData, StockAnalysisCache models; add is_final to DailyAnalysisLog"
```

---

## Task 3: `history_loader` 服務

**背景:** `history_loader` 從 DB 讀取昨日 `DailyAnalysisLog` 結果，回傳 dict 供 LangGraph 注入上下文。使用同步 SQLAlchemy Session（與現有 `get_db` 一致）。

**Files:**
- Create: `backend/src/ai_stock_sentinel/services/__init__.py`
- Create: `backend/src/ai_stock_sentinel/services/history_loader.py`
- Create: `backend/tests/test_history_loader.py`

**Step 1: 寫失敗測試**

建立 `backend/tests/test_history_loader.py`：

```python
# backend/tests/test_history_loader.py
from __future__ import annotations

from unittest.mock import MagicMock

from ai_stock_sentinel.services.history_loader import load_yesterday_context


def _make_db(row):
    """建立模擬的同步 SQLAlchemy Session。"""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute.return_value = mock_result
    return db


def test_returns_none_when_no_record():
    """昨日無紀錄時回傳 None。"""
    db = _make_db(None)
    result = load_yesterday_context("2330.TW", db)
    assert result is None


def test_returns_context_when_record_exists():
    """有昨日紀錄時回傳對應欄位。"""
    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = 61.5
    mock_row.indicators = {"rsi_14": 65.2, "ma5": 975.0, "ma20": 960.0, "ma60": 940.0}
    db = _make_db(mock_row)

    result = load_yesterday_context("2330.TW", db)

    assert result is not None
    assert result["prev_action_tag"] == "Hold"
    assert result["prev_confidence"] == 61.5
    assert result["prev_rsi"] == 65.2


def test_ma_alignment_bullish():
    """ma5 > ma20 > ma60 時 ma_alignment 為 bullish。"""
    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = 61.5
    mock_row.indicators = {"rsi_14": 65.2, "ma5": 975.0, "ma20": 960.0, "ma60": 940.0}
    db = _make_db(mock_row)

    result = load_yesterday_context("2330.TW", db)

    assert result["prev_ma_alignment"] == "bullish"


def test_ma_alignment_bearish():
    """ma5 < ma20 < ma60 時 ma_alignment 為 bearish。"""
    mock_row = MagicMock()
    mock_row.action_tag = "Exit"
    mock_row.signal_confidence = 78.0
    mock_row.indicators = {"rsi_14": 72.0, "ma5": 920.0, "ma20": 940.0, "ma60": 960.0}
    db = _make_db(mock_row)

    result = load_yesterday_context("2330.TW", db)

    assert result["prev_ma_alignment"] == "bearish"


def test_ma_alignment_neutral_when_missing():
    """indicators 缺少 ma 值時 ma_alignment 為 neutral。"""
    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = 55.0
    mock_row.indicators = {"rsi_14": 50.0}
    db = _make_db(mock_row)

    result = load_yesterday_context("2330.TW", db)

    assert result["prev_ma_alignment"] == "neutral"


def test_prev_confidence_none_when_null():
    """signal_confidence 為 None 時 prev_confidence 回傳 None。"""
    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = None
    mock_row.indicators = {}
    db = _make_db(mock_row)

    result = load_yesterday_context("2330.TW", db)

    assert result["prev_confidence"] is None
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend && python -m pytest tests/test_history_loader.py -v
```

Expected: `ImportError: cannot import name 'load_yesterday_context'`

**Step 3: 建立 services/__init__.py**

建立空檔案 `backend/src/ai_stock_sentinel/services/__init__.py`（空白即可）。

**Step 4: 實作 history_loader.py**

建立 `backend/src/ai_stock_sentinel/services/history_loader.py`：

```python
# backend/src/ai_stock_sentinel/services/history_loader.py
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import DailyAnalysisLog


def _derive_ma_alignment(indicators: dict) -> str:
    """從技術指標判斷均線排列方向。"""
    ma5  = indicators.get("ma5")
    ma20 = indicators.get("ma20")
    ma60 = indicators.get("ma60")
    if ma5 is not None and ma20 is not None and ma60 is not None:
        if ma5 > ma20 > ma60:
            return "bullish"
        if ma5 < ma20 < ma60:
            return "bearish"
    return "neutral"


def load_yesterday_context(symbol: str, db: Session) -> dict | None:
    """從 DB 讀取昨日分析結果，作為 LLM 的歷史上下文。

    回傳的數值必須從 DB 讀取，嚴禁由呼叫方或 LLM 推斷。
    """
    yesterday = date.today() - timedelta(days=1)
    result = db.execute(
        select(DailyAnalysisLog).where(
            DailyAnalysisLog.symbol == symbol,
            DailyAnalysisLog.record_date == yesterday,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None

    indicators = row.indicators or {}
    return {
        "prev_action_tag":   row.action_tag,
        "prev_confidence":   float(row.signal_confidence) if row.signal_confidence is not None else None,
        "prev_rsi":          indicators.get("rsi_14"),
        "prev_ma_alignment": _derive_ma_alignment(indicators),
    }
```

**Step 5: 執行測試，確認通過**

```bash
cd backend && python -m pytest tests/test_history_loader.py -v
```

Expected: 6 tests PASSED

**Step 6: 執行全部測試，確認無回歸**

```bash
cd backend && python -m pytest tests/ -v -x
```

Expected: 全部通過

**Step 7: Commit**

```bash
git add backend/src/ai_stock_sentinel/services/ backend/tests/test_history_loader.py
git commit -m "feat: add history_loader service for yesterday context injection"
```

---

## 完成檢查清單

- [ ] `docker-compose.yml` 建立（services.db、postgres:17）
- [ ] `db/init/01_schema.sql` 建立（僅參考，標註不執行）
- [ ] `db/models.py` 有 `User`、`UserPortfolio`、`DailyAnalysisLog`（含 `is_final`）、`StockRawData`、`StockAnalysisCache`
- [ ] `test_db_models.py` 6 tests PASSED
- [ ] `services/history_loader.py` 實作完成
- [ ] `test_history_loader.py` 6 tests PASSED
- [ ] 全部既有測試無回歸

---

*文件版本：v1.0 | 建立：2026-03-12 | 對應原計劃：`docs/plans/2026-03-12-phase7-infrastructure.md` Tasks 1–3*
