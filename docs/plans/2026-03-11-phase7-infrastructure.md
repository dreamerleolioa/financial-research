# Phase 7 基礎設施建置 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立 PostgreSQL 持久化層、SQLAlchemy ORM、history_loader 服務，以及 n8n 自動化工作流，將系統從無記憶的 Stateless 分析工具升級為具備歷史記憶與自動排程的閉環平台。

**Architecture:** 以 PostgreSQL 作為資料持久化中心，SQLAlchemy AsyncSession 接入 FastAPI，`history_loader.py` 在每次分析前從 DB 讀取昨日上下文注入 LangGraph。n8n 部署於雲端，透過 Cloudflare Tunnel 安全連線至本地 DB，每日自動批次診斷並觸發 Telegram 預警。

**Tech Stack:** PostgreSQL 17、Docker Compose、SQLAlchemy 2.x（asyncpg driver）、python-jose（JWT）、google-auth（id_token 驗證）、n8n（Zeabur）、Cloudflare Tunnel、Telegram Bot API

**前置依賴:** Phase 7 前置任務（使用者系統）已完成，`users` 表已存在，FastAPI 已有 `get_current_user` Depends。

---

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
    created_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_log_symbol_date
    ON daily_analysis_log (symbol, record_date);

CREATE INDEX IF NOT EXISTS idx_log_symbol       ON daily_analysis_log (symbol);
CREATE INDEX IF NOT EXISTS idx_log_record_date  ON daily_analysis_log (record_date);
CREATE INDEX IF NOT EXISTS idx_log_action_tag   ON daily_analysis_log (action_tag);
CREATE INDEX IF NOT EXISTS idx_log_user_id      ON daily_analysis_log (user_id);

CREATE INDEX IF NOT EXISTS idx_log_indicators_gin
    ON daily_analysis_log USING GIN (indicators);
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

Expected: 列出 `users`、`user_portfolio`、`daily_analysis_log` 三張表

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
    created_at:         Mapped[datetime]     = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())

    user: Mapped[User | None] = relationship(back_populates="logs")

    __table_args__ = (
        UniqueConstraint("symbol", "record_date", name="uq_log_symbol_date"),
        Index("idx_log_indicators_gin", "indicators", postgresql_using="gin"),
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
from .models import Base, DailyAnalysisLog, User, UserPortfolio
from .session import AsyncSessionLocal, get_db

__all__ = [
    "Base", "User", "UserPortfolio", "DailyAnalysisLog",
    "AsyncSessionLocal", "get_db",
]
```

**Step 6: 寫單元測試確認 ORM model 欄位**

```python
# backend/tests/test_db_models.py
from ai_stock_sentinel.db.models import DailyAnalysisLog, User, UserPortfolio


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
```

**Step 7: 執行測試**

```bash
cd backend && pytest tests/test_db_models.py -v
```

Expected: 3 tests PASSED

**Step 8: Commit**

```bash
git add backend/src/ai_stock_sentinel/db/ backend/tests/test_db_models.py backend/requirements.txt
git commit -m "feat: add SQLAlchemy ORM models for users, portfolio, and analysis log"
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

## Task 4: `POST /analyze/position` 結果寫回 DB

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Create: `backend/tests/test_analysis_log_writer.py`

**Step 1: 寫失敗測試**

```python
# backend/tests/test_analysis_log_writer.py
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ai_stock_sentinel import api


@pytest.mark.asyncio
async def test_analysis_result_upserted_to_db():
    """analyze/position 執行後，結果應 UPSERT 至 daily_analysis_log。"""
    db = AsyncMock()

    mock_log_data = {
        "symbol": "2330.TW",
        "signal_confidence": 72.5,
        "action_tag": "Hold",
        "recommended_action": "持續觀察",
        "indicators": {"rsi_14": 68.5, "ma5": 975.0},
        "final_verdict": "均線多頭排列，建議持有。",
    }

    with patch("ai_stock_sentinel.api.upsert_analysis_log") as mock_upsert:
        mock_upsert.return_value = None
        await api.upsert_analysis_log(db, mock_log_data)
        mock_upsert.assert_called_once_with(db, mock_log_data)
```

**Step 2: 建立 upsert_analysis_log 函式並掛入 api.py**

在 `backend/src/ai_stock_sentinel/api.py` 新增：

```python
# api.py 新增 import
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_stock_sentinel.db import get_db

# 新增 upsert 函式
async def upsert_analysis_log(db: AsyncSession, data: dict) -> None:
    """UPSERT 分析結果至 daily_analysis_log。"""
    import json
    await db.execute(
        text("""
            INSERT INTO daily_analysis_log (
                symbol, record_date, signal_confidence, action_tag,
                recommended_action, indicators, final_verdict,
                prev_action_tag, prev_confidence
            ) VALUES (
                :symbol, CURRENT_DATE, :signal_confidence, :action_tag,
                :recommended_action, :indicators::jsonb, :final_verdict,
                (SELECT action_tag FROM daily_analysis_log
                 WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1),
                (SELECT signal_confidence FROM daily_analysis_log
                 WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1)
            )
            ON CONFLICT (symbol, record_date) DO UPDATE SET
                signal_confidence  = EXCLUDED.signal_confidence,
                action_tag         = EXCLUDED.action_tag,
                recommended_action = EXCLUDED.recommended_action,
                indicators         = EXCLUDED.indicators,
                final_verdict      = EXCLUDED.final_verdict
        """),
        {
            "symbol":             data.get("symbol"),
            "signal_confidence":  data.get("signal_confidence"),
            "action_tag":         data.get("action_tag"),
            "recommended_action": data.get("recommended_action"),
            "indicators":         json.dumps(data.get("indicators") or {}),
            "final_verdict":      data.get("final_verdict"),
        }
    )
    await db.commit()
```

將 `analyze_position` 路由改為 async 並注入 DB：

```python
@app.post("/analyze/position", response_model=AnalyzeResponse)
async def analyze_position(
    payload: PositionAnalyzeRequest,
    graph=Depends(get_graph),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AnalyzeResponse:
    # ... 原有 graph.invoke 邏輯不變 ...

    response = _build_response(result)

    # 寫回 DB
    await upsert_analysis_log(db, {
        "symbol":             payload.symbol,
        "signal_confidence":  result.get("signal_confidence"),
        "action_tag":         result.get("action_plan_tag"),
        "recommended_action": result.get("recommended_action"),
        "indicators":         _extract_indicators(result),
        "final_verdict":      result.get("analysis"),
    })

    return response
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

**Step 3: 執行既有 API 測試確認不破壞**

```bash
cd backend && pytest tests/test_api.py -v
```

Expected: 全部 PASSED

**Step 4: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_analysis_log_writer.py
git commit -m "feat: upsert analysis result to daily_analysis_log after position diagnosis"
```

---

## Task 5: n8n 每日診斷流（Workflow A）

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

**Step 4: 建立 Workflow A（每日診斷流）**

節點設定：

```
[Cron Trigger]
  Schedule: 30 18 * * 1-5（台灣時間週一至週五 18:30）

[Postgres Node] - 查詢活躍持倉
  Query: SELECT user_id, symbol, entry_price, quantity, entry_date
         FROM user_portfolio WHERE is_active = TRUE

[Split In Batches]
  Batch Size: 1

[HTTP Request Node] - 呼叫分析 API
  Method: POST
  URL: https://<your-api-domain>/analyze/position
  Headers: { "Authorization": "Bearer {{ $env.API_KEY }}" }
  Body: {
    "symbol":      "{{ $json.symbol }}",
    "entry_price": {{ $json.entry_price }},
    "entry_date":  "{{ $json.entry_date }}",
    "quantity":    {{ $json.quantity }}
  }

[Wait Node]
  Duration: 2 seconds（避免 API 過載）

[Webhook Node] - 觸發風險預警流
  HTTP Method: POST
  URL: <Workflow B 的 Webhook URL>
```

**Step 5: 設定 n8n 環境變數**

n8n → Settings → Variables：
- `API_KEY`: FastAPI 的 Bearer Token

**Step 6: 手動觸發測試**

點擊 "Execute Workflow" 測試一次，確認：
- Postgres Node 成功讀取持倉
- HTTP Request 成功呼叫 API 並收到 200 回應
- DB 的 `daily_analysis_log` 有新紀錄寫入

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

[Postgres Node] - 查詢今日預警持股
  Query: SELECT symbol, action_tag, signal_confidence, recommended_action,
                prev_action_tag, prev_confidence
         FROM daily_analysis_log
         WHERE record_date = CURRENT_DATE
           AND action_tag IN ('Exit', 'Trim')

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
INSERT INTO daily_analysis_log (symbol, record_date, action_tag, signal_confidence, recommended_action)
VALUES ('TEST.TW', CURRENT_DATE, 'Exit', 79.5, '測試預警訊息');
```

確認收到後刪除測試資料：

```sql
DELETE FROM daily_analysis_log WHERE symbol = 'TEST.TW';
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

- [ ] PostgreSQL 容器正常運行，三張表建立成功
- [ ] SQLAlchemy ORM model 測試全部通過
- [ ] `history_loader` 測試全部通過
- [ ] `POST /analyze/position` 呼叫後 `daily_analysis_log` 有寫入紀錄
- [ ] 既有 `tests/test_api.py` 全部通過（無回歸）
- [ ] n8n Workflow A 手動執行成功
- [ ] n8n Workflow B Telegram 預警訊息收到
- [ ] n8n Workflow C 週報格式正確

---

*文件版本：v1.0 | 建立日期：2026-03-11 | 對應需求：`docs/ai-stock-sentinel-automation-review-spec.md` Phase 7*
