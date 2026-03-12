# Phase 7 基礎設施建置 Implementation Plan

> **⚠️ 已拆分：此計劃已拆分為兩份較小的計劃，請改用以下文件：**
> - **Phase 7a**（Tasks 1–3）：`docs/plans/2026-03-12-phase7a-db-foundation.md`
> - **Phase 7b**（Tasks 3.5–7）：`docs/plans/2026-03-12-phase7b-api-cache.md`
>
> 此檔案保留作為原始參考，**不應直接執行**。

> **For Claude:** 請勿使用此計劃執行任務，改用上方兩份拆分計劃。

**Goal:** 建立 PostgreSQL 持久化層、SQLAlchemy ORM、history_loader 服務，以及 n8n 自動化工作流，將系統從無記憶的 Stateless 分析工具升級為具備歷史記憶與自動排程的閉環平台。

**Architecture:** 以 PostgreSQL 作為資料持久化中心，SQLAlchemy AsyncSession 接入 FastAPI，`history_loader.py` 在每次分析前從 DB 讀取昨日上下文注入 LangGraph。n8n 部署於雲端，透過 Cloudflare Tunnel 安全連線至本地 DB，每日自動批次診斷並觸發 Telegram 預警。

**Tech Stack:** PostgreSQL 17、Docker Compose、SQLAlchemy 2.x（asyncpg driver）、python-jose（JWT）、google-auth（id_token 驗證）、n8n（Zeabur）、Cloudflare Tunnel、Telegram Bot API

**前置依賴:** Phase 7 前置任務（使用者系統）已完成，`users` 表已存在，FastAPI 已有 `get_current_user` Depends。

---

> ⚠️ Schema 管理已改由 **Alembic** 負責（見 `docs/plans/2026-03-11-alembic-migration.md`）。
> `db/init/01_schema.sql` 僅保留作為參考，不再執行。
> 部署新環境時，請執行 `alembic upgrade head` 而非手動 SQL。

## Task 1: Docker Compose 部署 PostgreSQL

**Files:**
- Create: `docker-compose.yml`（專案根目錄）
- Create: `db/init/01_schema.sql`

**Step 1: 建立 docker-compose.yml**

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
      - ./db/init:/docker-entrypoint-initdb.d
    ports:
      - "127.0.0.1:5432:5432"   # 只綁定 localhost，不開放公網

volumes:
  postgres_data:
```

**Step 2: 建立 schema 初始化 SQL**

```sql
-- db/init/01_schema.sql

-- users 表（Phase 7 前置任務已設計，此處一併建立確保順序正確）
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    google_sub  VARCHAR(255) NOT NULL UNIQUE,
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(255),
    avatar_url  TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    deleted_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_google_sub ON users (google_sub);
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

-- user_portfolio 表
CREATE TABLE IF NOT EXISTS user_portfolio (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    symbol      VARCHAR(20)    NOT NULL,
    entry_price NUMERIC(10, 2) NOT NULL,
    quantity    INTEGER        NOT NULL DEFAULT 0,
    entry_date  DATE           NOT NULL,
    is_active   BOOLEAN        NOT NULL DEFAULT TRUE,
    notes       TEXT,
    created_at  TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_active_symbol
    ON user_portfolio (user_id, symbol)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_portfolio_symbol  ON user_portfolio (symbol);
CREATE INDEX IF NOT EXISTS idx_portfolio_active  ON user_portfolio (is_active);
CREATE INDEX IF NOT EXISTS idx_portfolio_user_id ON user_portfolio (user_id);

-- daily_analysis_log 表
CREATE TABLE IF NOT EXISTS daily_analysis_log (
    id                 SERIAL PRIMARY KEY,
    user_id            INTEGER REFERENCES users(id) ON DELETE SET NULL,
    symbol             VARCHAR(20)    NOT NULL,
    record_date        DATE           NOT NULL,
    signal_confidence  NUMERIC(5, 2),
    action_tag         VARCHAR(20),
    recommended_action TEXT,
    indicators         JSONB,
    final_verdict      TEXT,
    prev_action_tag    VARCHAR(20),
    prev_confidence    NUMERIC(5, 2),
    is_final           BOOLEAN        NOT NULL DEFAULT FALSE,  -- FALSE=盤中非定稿；TRUE=收盤定稿
    created_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_log_user_symbol_date
    ON daily_analysis_log (user_id, symbol, record_date);

CREATE INDEX IF NOT EXISTS idx_log_symbol       ON daily_analysis_log (symbol);
CREATE INDEX IF NOT EXISTS idx_log_record_date  ON daily_analysis_log (record_date);
CREATE INDEX IF NOT EXISTS idx_log_action_tag   ON daily_analysis_log (action_tag);
CREATE INDEX IF NOT EXISTS idx_log_user_id      ON daily_analysis_log (user_id);

CREATE INDEX IF NOT EXISTS idx_log_indicators_gin
    ON daily_analysis_log USING GIN (indicators);

-- 表達式索引：加速範圍比較查詢（GIN 對 > / < 效能受限）
CREATE INDEX IF NOT EXISTS idx_log_rsi_value
    ON daily_analysis_log (((indicators->>'rsi_14')::NUMERIC));
CREATE INDEX IF NOT EXISTS idx_log_bias_value
    ON daily_analysis_log (((indicators->>'bias_20')::NUMERIC));

-- stock_raw_data 表（原始數據快取，每股每日一筆，不含 LLM 推理）
CREATE TABLE IF NOT EXISTS stock_raw_data (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20)     NOT NULL,
    record_date     DATE            NOT NULL,
    technical       JSONB,
    institutional   JSONB,
    fundamental     JSONB,
    fetched_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_symbol_date
    ON stock_raw_data (symbol, record_date);

CREATE INDEX IF NOT EXISTS idx_raw_symbol      ON stock_raw_data (symbol);
CREATE INDEX IF NOT EXISTS idx_raw_record_date ON stock_raw_data (record_date);
CREATE INDEX IF NOT EXISTS idx_raw_technical_gin
    ON stock_raw_data USING GIN (technical);
CREATE INDEX IF NOT EXISTS idx_raw_institutional_gin
    ON stock_raw_data USING GIN (institutional);

-- stock_analysis_cache 表（分析結果快取，跨使用者共用，不含 user_id）
CREATE TABLE IF NOT EXISTS stock_analysis_cache (
    id                  SERIAL PRIMARY KEY,
    symbol              VARCHAR(20)     NOT NULL,
    record_date         DATE            NOT NULL,
    signal_confidence   NUMERIC(5, 2),
    action_tag          VARCHAR(20),
    recommended_action  TEXT,
    indicators          JSONB,
    final_verdict       TEXT,
    prev_action_tag     VARCHAR(20),
    prev_confidence     NUMERIC(5, 2),
    is_final            BOOLEAN         NOT NULL DEFAULT FALSE,  -- FALSE=盤中非定稿；TRUE=收盤定稿
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cache_symbol_date
    ON stock_analysis_cache (symbol, record_date);

CREATE INDEX IF NOT EXISTS idx_cache_symbol      ON stock_analysis_cache (symbol);
CREATE INDEX IF NOT EXISTS idx_cache_record_date ON stock_analysis_cache (record_date);
CREATE INDEX IF NOT EXISTS idx_cache_action_tag  ON stock_analysis_cache (action_tag);
CREATE INDEX IF NOT EXISTS idx_cache_indicators_gin
    ON stock_analysis_cache USING GIN (indicators);
```

**Step 3: 在 .env 加入 DB 設定**

```bash
# .env（新增以下欄位）
DB_PASSWORD=your_secure_password_here
DATABASE_URL=postgresql+asyncpg://sentinel:your_secure_password_here@localhost:5432/ai_stock_sentinel
```

**Step 4: 啟動 PostgreSQL**

```bash
docker compose up -d db
```

Expected: `db` container 狀態為 `healthy`

**Step 5: 驗證 schema 建立成功**

```bash
docker compose exec db psql -U sentinel -d ai_stock_sentinel -c "\dt"
```

Expected: 列出 `users`、`user_portfolio`、`daily_analysis_log`、`stock_raw_data`、`stock_analysis_cache` 五張表

**Step 6: Commit**

```bash
git add docker-compose.yml db/init/01_schema.sql
git commit -m "feat: add PostgreSQL docker compose and schema DDL"
```

---

## Task 2: SQLAlchemy ORM Models

**Files:**
- Create: `backend/src/ai_stock_sentinel/db/base.py`
- Create: `backend/src/ai_stock_sentinel/db/models.py`
- Create: `backend/src/ai_stock_sentinel/db/session.py`
- Modify: `backend/requirements.txt`

**Step 1: 新增依賴套件**

在 `backend/requirements.txt` 末尾加入：

```
sqlalchemy>=2.0.0
asyncpg>=0.30.0
```

安裝：

```bash
cd backend && pip install sqlalchemy asyncpg
```

**Step 2: 建立 db/base.py**

```python
# backend/src/ai_stock_sentinel/db/base.py
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

**Step 3: 建立 db/models.py**

```python
# backend/src/ai_stock_sentinel/db/models.py
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, Date, ForeignKey, Index, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMPTZ
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base


class User(Base):
    __tablename__ = "users"

    id:         Mapped[int]            = mapped_column(primary_key=True)
    google_sub: Mapped[str]            = mapped_column(String(255), unique=True, nullable=False)
    email:      Mapped[str]            = mapped_column(String(255), unique=True, nullable=False)
    name:       Mapped[str | None]     = mapped_column(String(255))
    avatar_url: Mapped[str | None]     = mapped_column(Text)
    is_active:  Mapped[bool]           = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime]       = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())

    portfolios: Mapped[list[UserPortfolio]]  = relationship(back_populates="user")
    logs:       Mapped[list[DailyAnalysisLog]] = relationship(back_populates="user")


class UserPortfolio(Base):
    __tablename__ = "user_portfolio"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_portfolio_active_symbol"),
    )

    id:          Mapped[int]           = mapped_column(primary_key=True)
    user_id:     Mapped[int | None]    = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    symbol:      Mapped[str]           = mapped_column(String(20), nullable=False)
    entry_price: Mapped[float]         = mapped_column(Numeric(10, 2), nullable=False)
    quantity:    Mapped[int]           = mapped_column(nullable=False, default=0)
    entry_date:  Mapped[date]          = mapped_column(Date, nullable=False)
    is_active:   Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)
    notes:       Mapped[str | None]    = mapped_column(Text)
    created_at:  Mapped[datetime]      = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    updated_at:  Mapped[datetime]      = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())

    user: Mapped[User | None] = relationship(back_populates="portfolios")


class DailyAnalysisLog(Base):
    __tablename__ = "daily_analysis_log"

    id:                 Mapped[int]          = mapped_column(primary_key=True)
    user_id:            Mapped[int | None]   = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    symbol:             Mapped[str]          = mapped_column(String(20), nullable=False)
    record_date:        Mapped[date]         = mapped_column(Date, nullable=False)
    signal_confidence:  Mapped[float | None] = mapped_column(Numeric(5, 2))
    action_tag:         Mapped[str | None]   = mapped_column(String(20))
    recommended_action: Mapped[str | None]   = mapped_column(Text)
    indicators:         Mapped[dict | None]  = mapped_column(JSONB)
    final_verdict:      Mapped[str | None]   = mapped_column(Text)
    prev_action_tag:    Mapped[str | None]   = mapped_column(String(20))
    prev_confidence:    Mapped[float | None] = mapped_column(Numeric(5, 2))
    is_final:           Mapped[bool]         = mapped_column(Boolean, nullable=False, default=False)
    created_at:         Mapped[datetime]     = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())

    user: Mapped[User | None] = relationship(back_populates="logs")

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "record_date", name="uq_log_user_symbol_date"),
        Index("idx_log_indicators_gin", "indicators", postgresql_using="gin"),
    )


class StockRawData(Base):
    __tablename__ = "stock_raw_data"

    id:            Mapped[int]          = mapped_column(primary_key=True)
    symbol:        Mapped[str]          = mapped_column(String(20), nullable=False)
    record_date:   Mapped[date]         = mapped_column(Date, nullable=False)
    technical:     Mapped[dict | None]  = mapped_column(JSONB)
    institutional: Mapped[dict | None]  = mapped_column(JSONB)
    fundamental:   Mapped[dict | None]  = mapped_column(JSONB)
    fetched_at:    Mapped[datetime]     = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("symbol", "record_date", name="uq_raw_symbol_date"),
        Index("idx_raw_technical_gin", "technical", postgresql_using="gin"),
        Index("idx_raw_institutional_gin", "institutional", postgresql_using="gin"),
    )


class StockAnalysisCache(Base):
    __tablename__ = "stock_analysis_cache"

    id:                 Mapped[int]          = mapped_column(primary_key=True)
    symbol:             Mapped[str]          = mapped_column(String(20), nullable=False)
    record_date:        Mapped[date]         = mapped_column(Date, nullable=False)
    signal_confidence:  Mapped[float | None] = mapped_column(Numeric(5, 2))
    action_tag:         Mapped[str | None]   = mapped_column(String(20))
    recommended_action: Mapped[str | None]   = mapped_column(Text)
    indicators:         Mapped[dict | None]  = mapped_column(JSONB)
    final_verdict:      Mapped[str | None]   = mapped_column(Text)
    prev_action_tag:    Mapped[str | None]   = mapped_column(String(20))
    prev_confidence:    Mapped[float | None] = mapped_column(Numeric(5, 2))
    is_final:           Mapped[bool]         = mapped_column(Boolean, nullable=False, default=False)
    created_at:         Mapped[datetime]     = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    updated_at:         Mapped[datetime]     = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("symbol", "record_date", name="uq_cache_symbol_date"),
        Index("idx_cache_indicators_gin", "indicators", postgresql_using="gin"),
    )
```

**Step 4: 建立 db/session.py**

```python
# backend/src/ai_stock_sentinel/db/session.py
from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_async_engine(_DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

**Step 5: 建立 db/__init__.py**

```python
# backend/src/ai_stock_sentinel/db/__init__.py
from .models import Base, DailyAnalysisLog, StockAnalysisCache, StockRawData, User, UserPortfolio
from .session import AsyncSessionLocal, get_db

__all__ = [
    "Base", "User", "UserPortfolio", "DailyAnalysisLog",
    "StockRawData", "StockAnalysisCache",
    "AsyncSessionLocal", "get_db",
]
```

**Step 6: 寫單元測試確認 ORM model 欄位**

```python
# backend/tests/test_db_models.py
from ai_stock_sentinel.db.models import (
    DailyAnalysisLog, StockAnalysisCache, StockRawData, User, UserPortfolio,
)


def test_user_model_columns():
    cols = {c.name for c in User.__table__.columns}
    assert {"id", "google_sub", "email", "name", "avatar_url",
            "is_active", "deleted_at", "created_at"} <= cols


def test_user_portfolio_model_columns():
    cols = {c.name for c in UserPortfolio.__table__.columns}
    assert {"id", "user_id", "symbol", "entry_price",
            "quantity", "entry_date", "is_active"} <= cols


def test_daily_analysis_log_model_columns():
    cols = {c.name for c in DailyAnalysisLog.__table__.columns}
    assert {"id", "user_id", "symbol", "record_date", "signal_confidence",
            "action_tag", "indicators", "final_verdict",
            "prev_action_tag", "prev_confidence"} <= cols


def test_stock_raw_data_model_columns():
    cols = {c.name for c in StockRawData.__table__.columns}
    assert {"id", "symbol", "record_date", "technical",
            "institutional", "fundamental", "fetched_at"} <= cols


def test_stock_analysis_cache_model_columns():
    cols = {c.name for c in StockAnalysisCache.__table__.columns}
    assert {"id", "symbol", "record_date", "signal_confidence",
            "action_tag", "indicators", "final_verdict",
            "prev_action_tag", "prev_confidence",
            "is_final", "created_at", "updated_at"} <= cols


def test_daily_analysis_log_has_is_final():
    cols = {c.name for c in DailyAnalysisLog.__table__.columns}
    assert "is_final" in cols
```

**Step 7: 執行測試**

```bash
cd backend && pytest tests/test_db_models.py -v
```

Expected: 5 tests PASSED

**Step 8: Commit**

```bash
git add backend/src/ai_stock_sentinel/db/ backend/tests/test_db_models.py backend/requirements.txt
git commit -m "feat: add SQLAlchemy ORM models for users, portfolio, analysis log, raw data, and analysis cache"
```

---

## Task 3: `history_loader.py` 服務

**Files:**
- Create: `backend/src/ai_stock_sentinel/services/history_loader.py`
- Create: `backend/tests/test_history_loader.py`

**Step 1: 寫失敗測試**

```python
# backend/tests/test_history_loader.py
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_stock_sentinel.services.history_loader import load_yesterday_context


@pytest.mark.asyncio
async def test_returns_none_when_no_record():
    """昨日無紀錄時回傳 None。"""
    db = AsyncMock()
    db.execute.return_value.scalar_one_or_none.return_value = None

    result = await load_yesterday_context("2330.TW", db)

    assert result is None


@pytest.mark.asyncio
async def test_returns_context_when_record_exists():
    """有昨日紀錄時回傳對應欄位。"""
    db = AsyncMock()

    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = 61.5
    mock_row.indicators = {"rsi_14": 65.2, "ma5": 975.0, "ma20": 960.0, "ma60": 940.0}
    db.execute.return_value.scalar_one_or_none.return_value = mock_row

    result = await load_yesterday_context("2330.TW", db)

    assert result is not None
    assert result["prev_action_tag"] == "Hold"
    assert result["prev_confidence"] == 61.5
    assert result["prev_rsi"] == 65.2


@pytest.mark.asyncio
async def test_ma_alignment_bullish():
    """ma5 > ma20 > ma60 時 ma_alignment 為 bullish。"""
    db = AsyncMock()

    mock_row = MagicMock()
    mock_row.action_tag = "Hold"
    mock_row.signal_confidence = 61.5
    mock_row.indicators = {"rsi_14": 65.2, "ma5": 975.0, "ma20": 960.0, "ma60": 940.0}
    db.execute.return_value.scalar_one_or_none.return_value = mock_row

    result = await load_yesterday_context("2330.TW", db)

    assert result["prev_ma_alignment"] == "bullish"


@pytest.mark.asyncio
async def test_ma_alignment_bearish():
    """ma5 < ma20 < ma60 時 ma_alignment 為 bearish。"""
    db = AsyncMock()

    mock_row = MagicMock()
    mock_row.action_tag = "Exit"
    mock_row.signal_confidence = 78.0
    mock_row.indicators = {"rsi_14": 72.0, "ma5": 920.0, "ma20": 940.0, "ma60": 960.0}
    db.execute.return_value.scalar_one_or_none.return_value = mock_row

    result = await load_yesterday_context("2330.TW", db)

    assert result["prev_ma_alignment"] == "bearish"
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend && pytest tests/test_history_loader.py -v
```

Expected: ImportError（模組不存在）

**Step 3: 實作 history_loader.py**

```python
# backend/src/ai_stock_sentinel/services/history_loader.py
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def load_yesterday_context(symbol: str, db: AsyncSession) -> dict | None:
    """從 DB 讀取昨日分析結果，作為 LLM 的歷史上下文。

    回傳的數值必須從 DB 讀取，嚴禁由呼叫方或 LLM 推斷。
    """
    yesterday = date.today() - timedelta(days=1)
    result = await db.execute(
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
        "prev_confidence":   float(row.signal_confidence) if row.signal_confidence else None,
        "prev_rsi":          indicators.get("rsi_14"),
        "prev_ma_alignment": _derive_ma_alignment(indicators),
    }
```

**Step 4: 建立 services/__init__.py**

```python
# backend/src/ai_stock_sentinel/services/__init__.py
```

**Step 5: 執行測試，確認通過**

```bash
cd backend && pytest tests/test_history_loader.py -v
```

Expected: 4 tests PASSED

**Step 6: Commit**

```bash
git add backend/src/ai_stock_sentinel/services/ backend/tests/test_history_loader.py
git commit -m "feat: add history_loader service for yesterday context injection"
```

---

## Task 3.5: `POST /portfolio` 新增持倉端點（含上限檢查）

**Files:**
- Create: `backend/src/ai_stock_sentinel/portfolio/router.py`
- Create: `backend/tests/test_portfolio_router.py`

**需求：** 每位使用者最多 5 筆 active 持倉，超過時回傳 `HTTP 422`。

**Step 1: 寫失敗測試**

```python
# backend/tests/test_portfolio_router.py
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from ai_stock_sentinel import api

def _make_client(active_count: int):
    mock_user = MagicMock(id=1)
    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.count.return_value = active_count

    app = api.app
    app.dependency_overrides[api.get_current_user] = lambda: mock_user
    app.dependency_overrides[api.get_db] = lambda: mock_db
    return TestClient(app)

def test_add_portfolio_success():
    client = _make_client(active_count=3)
    resp = client.post("/portfolio", json={
        "symbol": "2330.TW", "entry_price": 900.0,
        "entry_date": "2026-01-01", "quantity": 100
    })
    assert resp.status_code == 201

def test_add_portfolio_rejects_when_limit_reached():
    client = _make_client(active_count=5)
    resp = client.post("/portfolio", json={
        "symbol": "2454.TW", "entry_price": 800.0,
        "entry_date": "2026-01-01", "quantity": 50
    })
    assert resp.status_code == 422
    assert "5" in resp.json()["detail"]
```

**Step 2: 實作 portfolio/router.py**

```python
# backend/src/ai_stock_sentinel/portfolio/router.py
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
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
    active_count = (
        db.query(UserPortfolio)
        .filter_by(user_id=current_user.id, is_active=True)
        .count()
    )
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

**Step 3: 在 api.py 註冊 router**

```python
from ai_stock_sentinel.portfolio.router import router as portfolio_router
app.include_router(portfolio_router)
```

**Step 4: 執行測試**

```bash
cd backend && pytest tests/test_portfolio_router.py -v
```

Expected: 2 tests PASSED

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/portfolio/ backend/tests/test_portfolio_router.py
git commit -m "feat: add POST /portfolio with 5-stock active limit per user"
```

---

## Task 4: `POST /analyze/position` 三段式快取邏輯（含 `is_final` 時間判斷）

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Create: `backend/tests/test_analysis_cache.py`

**核心決策邏輯（實作前必讀）**

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
[L3] 爬蟲抓原始數據 + 打 model → is_final=FALSE → 存快取 → 回傳
```

**`MARKET_CLOSE = time(13, 30)`**（台灣收盤時間，盤中/定稿分界點）

**Step 1: 寫失敗測試**

```python
# backend/tests/test_analysis_cache.py
from __future__ import annotations

from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_stock_sentinel import api


@pytest.mark.asyncio
async def test_final_cache_hit_skips_model():
    """is_final=TRUE 的快取命中時，不觸發 LLM。"""
    db = AsyncMock()
    mock_cache = MagicMock()
    mock_cache.is_final = True
    mock_cache.symbol = "2330.TW"
    mock_cache.action_tag = "Hold"
    mock_cache.signal_confidence = 72.5

    with patch("ai_stock_sentinel.api.get_analysis_cache", return_value=mock_cache):
        graph = AsyncMock()
        result = await api.analyze_position.__wrapped__(
            payload=MagicMock(symbol="2330.TW"),
            graph=graph, db=db, current_user=MagicMock(id=1),
        )
        graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_intraday_cache_hit_skips_model():
    """is_final=FALSE + 盤中時間，直接回傳（附免責聲明），不觸發 LLM。"""
    db = AsyncMock()
    mock_cache = MagicMock()
    mock_cache.is_final = False
    mock_cache.symbol = "2330.TW"
    mock_cache.action_tag = "Hold"
    mock_cache.signal_confidence = 68.5

    with patch("ai_stock_sentinel.api.get_analysis_cache", return_value=mock_cache):
        with patch("ai_stock_sentinel.api.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = time(10, 30)  # 盤中
            graph = AsyncMock()
            result = await api.analyze_position.__wrapped__(
                payload=MagicMock(symbol="2330.TW"),
                graph=graph, db=db, current_user=MagicMock(id=1),
            )
            graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_stale_intraday_cache_triggers_reanalysis():
    """is_final=FALSE + 收盤後（≥13:30），強制重新分析（L2/L3）。"""
    db = AsyncMock()
    mock_cache = MagicMock()
    mock_cache.is_final = False

    with patch("ai_stock_sentinel.api.get_analysis_cache", return_value=mock_cache):
        with patch("ai_stock_sentinel.api.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = time(14, 0)  # 收盤後
            with patch("ai_stock_sentinel.api.get_raw_data", return_value=None):
                graph = AsyncMock()
                graph.ainvoke.return_value = {}
                await api.analyze_position.__wrapped__(
                    payload=MagicMock(symbol="2330.TW"),
                    graph=graph, db=db, current_user=MagicMock(id=1),
                )
                graph.ainvoke.assert_called_once()  # 強制重新分析


@pytest.mark.asyncio
async def test_raw_data_hit_skips_crawler():
    """原始數據快取命中時，應只打 model，不呼叫爬蟲。"""
    db = AsyncMock()
    mock_raw = MagicMock()

    with patch("ai_stock_sentinel.api.get_analysis_cache", return_value=None):
        with patch("ai_stock_sentinel.api.get_raw_data", return_value=mock_raw):
            with patch("ai_stock_sentinel.api.run_model_only", return_value={}) as mock_model:
                graph = AsyncMock()
                await api.analyze_position.__wrapped__(
                    payload=MagicMock(symbol="2330.TW"),
                    graph=graph, db=db, current_user=MagicMock(id=1),
                )
                mock_model.assert_called_once()
                graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_response_includes_is_final_and_disclaimer():
    """盤中分析（is_final=False）回應應包含 intraday_disclaimer。"""
    db = AsyncMock()
    with patch("ai_stock_sentinel.api.get_analysis_cache", return_value=None):
        with patch("ai_stock_sentinel.api.get_raw_data", return_value=None):
            with patch("ai_stock_sentinel.api.datetime") as mock_dt:
                mock_dt.now.return_value.time.return_value = time(10, 30)
                graph = AsyncMock()
                graph.ainvoke.return_value = {"signal_confidence": 65.0, "action_plan_tag": "Hold"}
                result = await api.analyze_position.__wrapped__(
                    payload=MagicMock(symbol="2330.TW"),
                    graph=graph, db=db, current_user=MagicMock(id=1),
                )
                assert result.is_final is False
                assert result.intraday_disclaimer is not None


@pytest.mark.asyncio
async def test_analysis_log_written_only_when_portfolio_exists():
    """有持倉時寫入 daily_analysis_log，無持倉時不寫入。"""
    db = AsyncMock()

    with patch("ai_stock_sentinel.api.has_active_portfolio", return_value=False):
        with patch("ai_stock_sentinel.api.upsert_analysis_log") as mock_log:
            await api._maybe_write_analysis_log(db, user_id=1, symbol="2330.TW", result={}, is_final=False)
            mock_log.assert_not_called()
```

**Step 2: 新增 Response Schema（含 `is_final` 與 `intraday_disclaimer`）**

在 `api.py` 更新 `AnalyzeResponse` Pydantic Model：

```python
from typing import Optional

class AnalyzeResponse(BaseModel):
    symbol:             str
    signal_confidence:  float | None
    action_tag:         str | None
    recommended_action: str | None
    final_verdict:      str | None
    is_final:           bool
    intraday_disclaimer: Optional[str] = None  # 僅 is_final=False 時存在

INTRADAY_DISCLAIMER = "⚠️ 注意：目前為盤中階段（指標未收定），以下分析僅供即時參考，不代表當日收盤定論。"
MARKET_CLOSE = time(13, 30)
```

**Step 3: 實作三段式快取邏輯並掛入 api.py**

在 `backend/src/ai_stock_sentinel/api.py` 新增輔助函式與更新路由：

```python
# api.py 新增 import
from datetime import datetime, time as time_type
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_stock_sentinel.db import get_db, StockRawData, StockAnalysisCache

MARKET_CLOSE = time_type(13, 30)

# 查詢分析快取（L1）
async def get_analysis_cache(db: AsyncSession, symbol: str) -> StockAnalysisCache | None:
    """查詢今日的分析結果快取。"""
    from sqlalchemy import select
    from datetime import date
    result = await db.execute(
        select(StockAnalysisCache).where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == date.today(),
        )
    )
    return result.scalar_one_or_none()


# 查詢原始數據快取（L2）
async def get_raw_data(db: AsyncSession, symbol: str) -> StockRawData | None:
    """查詢今日的原始數據快取。"""
    from sqlalchemy import select
    from datetime import date
    result = await db.execute(
        select(StockRawData).where(
            StockRawData.symbol == symbol,
            StockRawData.record_date == date.today(),
        )
    )
    return result.scalar_one_or_none()


# UPSERT 分析快取（含 is_final）
async def upsert_analysis_cache(db: AsyncSession, data: dict) -> None:
    """UPSERT 分析結果至 stock_analysis_cache（跨使用者共用）。"""
    import json
    await db.execute(
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
    await db.commit()


# UPSERT 持倉分析紀錄（含 is_final）
async def upsert_analysis_log(db: AsyncSession, data: dict) -> None:
    """UPSERT 分析結果至 daily_analysis_log（含 user_id，限有持倉時呼叫）。"""
    import json
    await db.execute(
        text("""
            INSERT INTO daily_analysis_log (
                user_id, symbol, record_date, signal_confidence, action_tag,
                recommended_action, indicators, final_verdict,
                prev_action_tag, prev_confidence, is_final
            ) VALUES (
                :user_id, :symbol, CURRENT_DATE, :signal_confidence, :action_tag,
                :recommended_action, :indicators::jsonb, :final_verdict,
                (SELECT action_tag FROM daily_analysis_log
                 WHERE user_id = :user_id AND symbol = :symbol AND record_date = CURRENT_DATE - 1),
                (SELECT signal_confidence FROM daily_analysis_log
                 WHERE user_id = :user_id AND symbol = :symbol AND record_date = CURRENT_DATE - 1),
                :is_final
            )
            ON CONFLICT (user_id, symbol, record_date) DO UPDATE SET
                signal_confidence  = EXCLUDED.signal_confidence,
                action_tag         = EXCLUDED.action_tag,
                recommended_action = EXCLUDED.recommended_action,
                indicators         = EXCLUDED.indicators,
                final_verdict      = EXCLUDED.final_verdict,
                is_final           = EXCLUDED.is_final
        """),
        {
            "user_id":            data.get("user_id"),
            "symbol":             data.get("symbol"),
            "signal_confidence":  data.get("signal_confidence"),
            "action_tag":         data.get("action_tag"),
            "recommended_action": data.get("recommended_action"),
            "indicators":         json.dumps(data.get("indicators") or {}),
            "final_verdict":      data.get("final_verdict"),
            "is_final":           data.get("is_final", False),
        }
    )
    await db.commit()


# 持倉檢查輔助
async def has_active_portfolio(db: AsyncSession, user_id: int, symbol: str) -> bool:
    from sqlalchemy import select
    from ai_stock_sentinel.db.models import UserPortfolio
    result = await db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == user_id,
            UserPortfolio.symbol == symbol,
            UserPortfolio.is_active == True,
        )
    )
    return result.scalar_one_or_none() is not None


async def _maybe_write_analysis_log(db, user_id, symbol, result, *, is_final: bool):
    if await has_active_portfolio(db, user_id, symbol):
        await upsert_analysis_log(db, {
            "user_id":  user_id,
            "symbol":   symbol,
            "is_final": is_final,
            **result,
        })
```

將 `analyze_position` 路由改為三段式快取邏輯（含 `is_final` 時間判斷）：

```python
@app.post("/analyze/position", response_model=AnalyzeResponse)
async def analyze_position(
    payload: PositionAnalyzeRequest,
    graph=Depends(get_graph),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AnalyzeResponse:
    now = datetime.now().time()
    is_post_market = now >= MARKET_CLOSE

    # L1: 檢查分析快取
    cache = await get_analysis_cache(db, payload.symbol)
    if cache:
        if cache.is_final:
            return _build_response_from_cache(cache)  # 定稿，直接回傳
        if not is_post_market:
            return _build_response_from_cache(cache)  # 盤中非定稿，直接回傳（含免責聲明）
        # 收盤後發現非定稿快取 → 強制重新分析，fall through to L2/L3

    # L2: 檢查原始數據
    raw_data = await get_raw_data(db, payload.symbol)
    if raw_data:
        # 收盤後有原始數據：只打 model，is_final=TRUE
        result = await run_model_only(graph, payload, raw_data)
        is_final = True
    else:
        # 盤中首次查詢：爬蟲 + model，is_final=FALSE
        result = await graph.ainvoke(...)
        is_final = is_post_market  # 收盤後爬蟲抓到的也算定稿

    # 寫入分析快取（跨使用者共用）
    await upsert_analysis_cache(db, {
        "symbol":             payload.symbol,
        "signal_confidence":  result.get("signal_confidence"),
        "action_tag":         result.get("action_plan_tag"),
        "recommended_action": result.get("recommended_action"),
        "indicators":         _extract_indicators(result),
        "final_verdict":      result.get("analysis"),
        "is_final":           is_final,
    })

    # 若有持倉，寫入 daily_analysis_log（含 user_id）
    await _maybe_write_analysis_log(db, current_user.id, payload.symbol, {
        "signal_confidence":  result.get("signal_confidence"),
        "action_tag":         result.get("action_plan_tag"),
        "recommended_action": result.get("recommended_action"),
        "indicators":         _extract_indicators(result),
        "final_verdict":      result.get("analysis"),
    }, is_final=is_final)

    return _build_response(result, is_final=is_final)


def _build_response_from_cache(cache) -> AnalyzeResponse:
    """從快取物件建構 AnalyzeResponse，is_final=False 時自動附加免責聲明。"""
    return AnalyzeResponse(
        symbol=cache.symbol,
        signal_confidence=float(cache.signal_confidence) if cache.signal_confidence else None,
        action_tag=cache.action_tag,
        recommended_action=cache.recommended_action,
        final_verdict=cache.final_verdict,
        is_final=cache.is_final,
        intraday_disclaimer=INTRADAY_DISCLAIMER if not cache.is_final else None,
    )


def _build_response(result: dict, *, is_final: bool) -> AnalyzeResponse:
    """從 graph result 建構 AnalyzeResponse。"""
    return AnalyzeResponse(
        symbol=result.get("symbol", ""),
        signal_confidence=result.get("signal_confidence"),
        action_tag=result.get("action_plan_tag"),
        recommended_action=result.get("recommended_action"),
        final_verdict=result.get("analysis"),
        is_final=is_final,
        intraday_disclaimer=INTRADAY_DISCLAIMER if not is_final else None,
    )
```

新增 `_extract_indicators` 輔助函式：

```python
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

**Step 4: 執行新測試與既有 API 測試確認不破壞**

```bash
cd backend && pytest tests/test_analysis_cache.py tests/test_api.py -v
```

Expected: 全部 PASSED

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_analysis_cache.py
git commit -m "feat: implement three-stage cache with is_final time-based logic for POST /analyze/position"
```

---

## Task 4.5: `GET /portfolio/{portfolio_id}/history` 歷史分析端點

**Files:**
- Create: `backend/src/ai_stock_sentinel/portfolio/history_router.py`
- Create: `backend/tests/test_portfolio_history.py`

**需求：** 持倉列表頁展示歷史診斷結果，純 DB 查詢，不觸發 LLM 分析。支援分頁。

**Step 1: 寫失敗測試**

```python
# backend/tests/test_portfolio_history.py
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from ai_stock_sentinel import api


def test_history_returns_records():
    """應回傳指定持倉的歷史分析紀錄。"""
    # ... mock db query, assert response structure
    pass


def test_history_returns_empty_when_no_records():
    """無紀錄時回傳空列表。"""
    pass


def test_history_supports_pagination():
    """支援 limit/offset 分頁參數。"""
    pass
```

**Step 2: 實作 history_router.py**

```python
# backend/src/ai_stock_sentinel/portfolio/history_router.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.models import DailyAnalysisLog, UserPortfolio
from ai_stock_sentinel.db.session import get_db

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/{portfolio_id}/history")
async def get_portfolio_history(
    portfolio_id: int,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # 驗證持倉屬於當前使用者
    portfolio = await db.get(UserPortfolio, portfolio_id)
    if not portfolio or portfolio.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="持倉不存在")

    # 查詢總筆數
    count_result = await db.execute(
        select(func.count())
        .select_from(DailyAnalysisLog)
        .where(
            DailyAnalysisLog.user_id == current_user.id,
            DailyAnalysisLog.symbol == portfolio.symbol,
        )
    )
    total = count_result.scalar()

    # 查詢分頁資料
    result = await db.execute(
        select(DailyAnalysisLog)
        .where(
            DailyAnalysisLog.user_id == current_user.id,
            DailyAnalysisLog.symbol == portfolio.symbol,
        )
        .order_by(DailyAnalysisLog.record_date.desc())
        .limit(limit)
        .offset(offset)
    )
    records = result.scalars().all()

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

**Step 3: 在 api.py 註冊 router**

```python
from ai_stock_sentinel.portfolio.history_router import router as history_router
app.include_router(history_router)
```

**Step 4: 執行測試**

```bash
cd backend && pytest tests/test_portfolio_history.py -v
```

Expected: 全部 PASSED

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/portfolio/history_router.py backend/tests/test_portfolio_history.py
git commit -m "feat: add GET /portfolio/{id}/history for historical analysis records"
```

---

## Task 4.6: `POST /internal/fetch-raw-data` 端點

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Create: `backend/tests/test_fetch_raw_data.py`

**需求：** n8n cron 每日收盤後呼叫此端點，批次抓取指定股票的原始數據並存入 `stock_raw_data`。使用 API Key 保護（不需要使用者 JWT）。

**Step 1: 寫失敗測試**

```python
# backend/tests/test_fetch_raw_data.py
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ai_stock_sentinel import api


def test_fetch_raw_data_requires_api_key():
    """未提供 API Key 時應回傳 401。"""
    client = TestClient(api.app)
    resp = client.post("/internal/fetch-raw-data", json={"symbol": "2330.TW", "date": "today"})
    assert resp.status_code == 401


def test_fetch_raw_data_success():
    """提供正確 API Key 時應成功抓取並寫入 stock_raw_data。"""
    with patch("ai_stock_sentinel.api.fetch_and_store_raw_data", return_value=None):
        client = TestClient(api.app)
        resp = client.post(
            "/internal/fetch-raw-data",
            json={"symbol": "2330.TW", "date": "today"},
            headers={"X-Internal-API-Key": "test-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["symbol"] == "2330.TW"
```

**Step 2: 實作端點與 API Key 驗證**

```python
# api.py 新增

import os
from fastapi import Header, HTTPException

INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")


async def verify_internal_api_key(x_internal_api_key: str = Header(...)):
    if not INTERNAL_API_KEY or x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


class FetchRawDataRequest(BaseModel):
    symbol: str
    date: str = "today"  # "today" 或 ISO date string


@app.post("/internal/fetch-raw-data")
async def fetch_raw_data_endpoint(
    payload: FetchRawDataRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_api_key),
):
    """n8n cron 呼叫的內部端點，抓取原始數據並存入 stock_raw_data。"""
    from datetime import date as date_type
    record_date = date_type.today() if payload.date == "today" else date_type.fromisoformat(payload.date)
    await fetch_and_store_raw_data(db, payload.symbol, record_date)
    return {"status": "ok", "symbol": payload.symbol, "record_date": record_date.isoformat()}


async def fetch_and_store_raw_data(db: AsyncSession, symbol: str, record_date) -> None:
    """抓取技術面、籌碼面、基本面原始數據並 UPSERT 至 stock_raw_data。"""
    # 實作細節：呼叫現有爬蟲取得 technical / institutional / fundamental
    # 寫入 stock_raw_data（ON CONFLICT (symbol, record_date) DO UPDATE SET ...）
    pass
```

**.env 新增**

```bash
INTERNAL_API_KEY=your_secure_internal_key_here
```

**Step 3: 執行測試**

```bash
cd backend && pytest tests/test_fetch_raw_data.py -v
```

Expected: 2 tests PASSED

**Step 4: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_fetch_raw_data.py
git commit -m "feat: add POST /internal/fetch-raw-data endpoint for n8n cron raw data ingestion"
```

---

## Task 5: n8n 每日數據更新流（Workflow A）

> 此任務為基礎設施配置，無程式碼測試。依步驟操作並截圖記錄。

**Step 1: 在 Zeabur 部署 n8n**

1. 登入 Zeabur → 新建 Project → 搜尋 `n8n` → 部署
2. 設定環境變數：
   - `N8N_BASIC_AUTH_ACTIVE=true`
   - `N8N_BASIC_AUTH_USER=admin`
   - `N8N_BASIC_AUTH_PASSWORD=<your_password>`
3. 記錄 n8n 的公開 URL（格式：`https://xxx.zeabur.app`）

**Step 2: 設定 Cloudflare Tunnel（本地 DB 安全連線）**

在本地伺服器執行：

```bash
# 安裝 cloudflared
brew install cloudflared   # macOS

# 登入 Cloudflare
cloudflared tunnel login

# 建立 Named Tunnel
cloudflared tunnel create sentinel-db

# 建立 config.yml
cat > ~/.cloudflared/config.yml << EOF
tunnel: <tunnel-id>
credentials-file: ~/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: db.your-domain.com
    service: tcp://localhost:5432
  - service: http_status:404
EOF

# 啟動 Tunnel（建議設為 systemd service）
cloudflared tunnel run sentinel-db
```

**Step 3: 在 n8n 建立 PostgreSQL Credential**

n8n → Settings → Credentials → New → PostgreSQL：
- Host: `db.your-domain.com`（Cloudflare Tunnel hostname）
- Port: `5432`
- Database: `ai_stock_sentinel`
- User: `sentinel`
- Password: `<DB_PASSWORD>`

**Step 4: 建立 Workflow A（每日數據更新流）**

節點設定（此流程不打 model，只抓原始數據）：

```
[Cron Trigger]
  Schedule: 30 18 * * 1-5（台灣時間週一至週五 18:30）

[Postgres Node] - 收集 watchlist（持倉 ∪ 近 30 天被查過的股票）
  Query:
    SELECT DISTINCT symbol FROM user_portfolio WHERE is_active = TRUE
    UNION
    SELECT DISTINCT symbol FROM stock_analysis_cache
    WHERE record_date >= CURRENT_DATE - 30

[Split In Batches]
  Batch Size: 1  ← HTTP Request 速率控制用；DB 寫入見 Step 4b

[HTTP Request Node] - 呼叫原始數據抓取端點
  Method: POST
  URL: https://<your-api-domain>/internal/fetch-raw-data
  Headers: { "X-Internal-API-Key": "{{ $env.INTERNAL_API_KEY }}" }
  Body: {
    "symbol": "{{ $json.symbol }}",
    "date":   "today"
  }

[Wait Node]
  Duration: 1 second（避免外部 API 速率限制）

[Webhook Node] - 觸發風險預警流
  HTTP Method: POST
  URL: <Workflow B 的 Webhook URL>
```

> **⚠️ 批次寫入規範（Cloudflare Tunnel 傳輸效率）**
>
> n8n（Zeabur 雲端）透過 Cloudflare Tunnel 連接本地 PostgreSQL，**嚴禁逐筆 INSERT**：
>
> ❌ 禁止：在迴圈中對 `stock_raw_data` 逐筆 INSERT（100 筆 = 100 次 TCP 握手，易觸發 Tunnel 超時）
>
> ✅ 正確：後端 `fetch_and_store_raw_data` 已負責 UPSERT，n8n 只需呼叫 HTTP Endpoint；
> 若有直接寫 DB 的場景（如 Workflow B 的 Postgres Node），需改用 **Execute Query 節點**，
> 將多筆合併為單一 `INSERT ... VALUES (...)... ON CONFLICT DO UPDATE` 語句（每批 50–100 筆）。
>
> | 設定項 | 建議值 |
> |--------|--------|
> | Split In Batches Batch Size（DB 直寫路徑） | 50–100 筆 |
> | 節點類型 | Execute Query（非 Insert Row） |
> | Wait Node 間隔（每批後） | 1 秒 |

**Step 5: 設定 n8n 環境變數**

n8n → Settings → Variables：
- `INTERNAL_API_KEY`: FastAPI `/internal/*` 端點的 API Key（對應後端 `INTERNAL_API_KEY` 環境變數）

**Step 6: 手動觸發測試**

點擊 "Execute Workflow" 測試一次，確認：
- Postgres Node 成功讀取 watchlist
- HTTP Request 成功呼叫 `/internal/fetch-raw-data` 並收到 200 回應
- DB 的 `stock_raw_data` 有新紀錄寫入

---

## Task 6: n8n 風險預警流（Workflow B）+ Telegram Bot

> 此任務為基礎設施配置。

**Step 1: 建立 Telegram Bot**

1. 在 Telegram 找 `@BotFather` → `/newbot` → 記錄 Bot Token
2. 將 Bot 加入你的群組或頻道，取得 Chat ID：
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

**Step 2: 在 n8n 設定 Telegram Credential**

n8n → Settings → Credentials → New → Telegram：
- Access Token: `<Bot Token>`

**Step 3: 建立 Workflow B（風險預警流）**

```
[Webhook Trigger]
  Path: /risk-alert（由 Workflow A 觸發）

[Postgres Node] - 查詢今日持倉中需預警的分析快取
  Query: SELECT symbol, action_tag, signal_confidence, recommended_action,
                prev_action_tag, prev_confidence
         FROM stock_analysis_cache
         WHERE record_date = CURRENT_DATE
           AND action_tag IN ('Exit', 'Trim')
           AND symbol IN (
               SELECT symbol FROM user_portfolio WHERE is_active = TRUE
           )

[IF Node]
  Condition: {{ $json.length > 0 }}

  YES branch:
    [Function Node] - 組裝訊息
      ```js
      const items = $input.all();
      let msg = `🚨 AI Stock Sentinel 風險預警 (${new Date().toISOString().slice(0,10)})\n\n`;
      for (const item of items) {
        const d = item.json;
        msg += `📌 ${d.symbol}\n`;
        msg += `   建議：${d.action_tag}\n`;
        msg += `   信心分數：${d.signal_confidence}\n`;
        msg += `   摘要：${d.recommended_action}\n`;
        if (d.prev_action_tag && d.prev_action_tag !== d.action_tag) {
          const delta = (d.signal_confidence - d.prev_confidence).toFixed(1);
          msg += `   🔄 訊號轉向：${d.prev_action_tag} → ${d.action_tag}（${delta > 0 ? '+' : ''}${delta} 分）\n`;
        }
        msg += '\n';
      }
      msg += '⚠️ 以上建議僅供參考，請結合個人判斷操作。';
      return [{ json: { text: msg } }];
      ```

    [Telegram Node]
      Chat ID: <your_chat_id>
      Text: {{ $json.text }}

  NO branch:
    [NoOp]
```

**Step 4: 測試預警流**

手動在 DB 插入一筆 `action_tag = 'Exit'` 的測試紀錄，觸發 Webhook，確認 Telegram 收到訊息：

```sql
INSERT INTO stock_analysis_cache (symbol, record_date, action_tag, signal_confidence, recommended_action)
VALUES ('TEST.TW', CURRENT_DATE, 'Exit', 79.5, '測試預警訊息');
```

確認收到後刪除測試資料：

```sql
DELETE FROM stock_analysis_cache WHERE symbol = 'TEST.TW';
```

---

## Task 7: n8n 優化回測流（Workflow C）

> P3 優先序，基礎設施配置。

**Step 1: 建立 Workflow C（每週日回測報告）**

```
[Cron Trigger]
  Schedule: 0 8 * * 0（每週日 08:00）

[Postgres Node] - 信心分數統計
  Query:
    SELECT
        action_tag,
        COUNT(*)                              AS total_signals,
        ROUND(AVG(signal_confidence), 1)      AS avg_confidence,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP
              (ORDER BY signal_confidence)::NUMERIC, 1) AS median_confidence,
        MIN(signal_confidence)                AS min_confidence,
        MAX(signal_confidence)                AS max_confidence
    FROM daily_analysis_log
    WHERE record_date >= CURRENT_DATE - 7
    GROUP BY action_tag
    ORDER BY avg_confidence DESC

[Function Node] - 組裝週報文字
  ```js
  const rows = $input.all();
  const start = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
  const end   = new Date().toISOString().slice(0, 10);
  let report = `📊 AI Stock Sentinel 週報 (${start} ~ ${end})\n\n`;
  report += `action_tag | 訊號數 | 平均信心 | 中位信心\n`;
  report += `-----------|--------|----------|----------\n`;
  for (const r of rows) {
    const d = r.json;
    report += `${d.action_tag.padEnd(10)} | ${String(d.total_signals).padStart(6)} | ${String(d.avg_confidence).padStart(8)} | ${String(d.median_confidence).padStart(8)}\n`;
  }
  return [{ json: { text: report } }];
  ```

[Telegram Node]
  Chat ID: <your_chat_id>
  Text: {{ $json.text }}
```

---

## 完成檢查清單

- [ ] PostgreSQL 容器正常運行，五張表建立成功（含 `stock_raw_data`、`stock_analysis_cache`）
- [ ] `stock_raw_data` 表建立成功，`uq_raw_symbol_date` unique index 存在
- [ ] `stock_analysis_cache` 表建立成功，`uq_cache_symbol_date` unique index 存在，`is_final` 欄位存在
- [ ] `daily_analysis_log` 有 `is_final` 欄位，`idx_log_rsi_value` / `idx_log_bias_value` 表達式索引存在
- [ ] SQLAlchemy ORM model 測試全部通過（含 `test_daily_analysis_log_has_is_final`）
- [ ] `history_loader` 測試全部通過
- [ ] 三段式快取 `is_final` 時間判斷測試通過（`test_final_cache_hit_skips_model`、`test_stale_intraday_cache_triggers_reanalysis`）
- [ ] 盤中分析 Response 包含 `is_final=false` 與 `intraday_disclaimer`（`test_response_includes_is_final_and_disclaimer`）
- [ ] `POST /analyze/position` 呼叫後 `stock_analysis_cache` 有寫入紀錄（含 `is_final`，跨使用者共用）
- [ ] `POST /analyze/position` 有持倉時 `daily_analysis_log` 有寫入紀錄（含 `user_id` 和 `is_final`）
- [ ] `GET /portfolio/{id}/history` 回傳正確的歷史分析紀錄（含分頁）
- [ ] 既有 `tests/test_api.py` 全部通過（無回歸）
- [ ] n8n Workflow A 呼叫 `/internal/fetch-raw-data` 成功寫入 `stock_raw_data`（確認 Execute Query 非 Insert Row）
- [ ] n8n Workflow B Telegram 預警訊息收到（查 `stock_analysis_cache`）
- [ ] n8n Workflow C 週報格式正確

---

*文件版本：v1.2 | 最後更新：2026-03-12 | 對應需求：`docs/ai-stock-sentinel-automation-review-spec.md` Phase 7*
