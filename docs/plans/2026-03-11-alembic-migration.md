# Alembic Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 引入 Alembic 對 PostgreSQL schema 做版本化管理，取代現有手動 SQL 初始化方式。

**Architecture:** 以現有 SQLAlchemy ORM models（`Base` 在 `db/session.py`，`User` 在 `user_models/user.py`）作為 autogenerate 來源；`UserPortfolio`、`DailyAnalysisLog` 兩張缺少的 ORM model 先補上，再跑 autogenerate 產生初始 migration。本地 DB 強制 drop & recreate 讓 Alembic 從乾淨狀態接管。GIN index 需手動確認。

**Tech Stack:** Alembic 1.x、SQLAlchemy 2.x（sync，psycopg2-binary driver）、PostgreSQL 17

---

## Task 1: 安裝 Alembic 並初始化

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/alembic.ini`（由 alembic init 自動產生）
- Create: `backend/alembic/env.py`（由 alembic init 自動產生，後續修改）

**Step 1: 安裝 alembic**

```bash
cd backend && pip install alembic
```

**Step 2: 在 requirements.txt 新增 alembic**

在 `backend/requirements.txt` 末尾加入：

```
alembic>=1.13.0
```

**Step 3: 初始化 alembic**

```bash
cd backend && alembic init alembic
```

Expected: 產生 `backend/alembic.ini` 和 `backend/alembic/` 目錄，含 `env.py`、`script.py.mako`、`versions/`

**Step 4: Commit**

```bash
cd backend && git add requirements.txt alembic.ini alembic/
git commit -m "chore: init alembic migration scaffold"
```

---

## Task 2: 補齊缺少的 ORM Models

目前 `UserPortfolio` 和 `DailyAnalysisLog` 只存在計畫文件，尚未實作。需先補上，autogenerate 才能偵測到。

**Files:**
- Create: `backend/src/ai_stock_sentinel/db/models.py`

**Step 1: 建立 `db/models.py`**

```python
# backend/src/ai_stock_sentinel/db/models.py
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.user_models.user import User  # noqa: F401 – 確保 User 已被 Base 收錄


class UserPortfolio(Base):
    __tablename__ = "user_portfolio"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_portfolio_active_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )


class DailyAnalysisLog(Base):
    __tablename__ = "daily_analysis_log"
    __table_args__ = (
        UniqueConstraint("symbol", "record_date", name="uq_log_symbol_date"),
        Index("idx_log_indicators_gin", "indicators", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    signal_confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    action_tag: Mapped[str | None] = mapped_column(String(20), nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    indicators: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    final_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    prev_action_tag: Mapped[str | None] = mapped_column(String(20), nullable=True)
    prev_confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
```

**Step 2: 確認 models 可被 import**

```bash
cd backend && python -c "from ai_stock_sentinel.db.models import UserPortfolio, DailyAnalysisLog; print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add backend/src/ai_stock_sentinel/db/models.py
git commit -m "feat: add UserPortfolio and DailyAnalysisLog ORM models"
```

---

## Task 3: 設定 alembic/env.py

讓 Alembic 知道要用哪個 `Base` 和 `DATABASE_URL`。

**Files:**
- Modify: `backend/alembic/env.py`
- Modify: `backend/alembic.ini`

**Step 1: 修改 `alembic.ini`，移除 hardcoded sqlalchemy.url**

找到這行：
```
sqlalchemy.url = driver://user:pass@localhost/dbname
```

改為（留空，讓 env.py 動態設定）：
```
sqlalchemy.url =
```

**Step 2: 替換 `alembic/env.py`**

完整替換為：

```python
# backend/alembic/env.py
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── 確保所有 model 都被 import，讓 Base.metadata 完整 ──
from ai_stock_sentinel.db.session import Base
import ai_stock_sentinel.user_models.user  # noqa: F401
import ai_stock_sentinel.db.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return url


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

**Step 3: 確認 env.py 可載入（不跑 migration，只確認 import 正常）**

```bash
cd backend && python -c "
import os; os.environ.setdefault('DATABASE_URL', 'postgresql://x:x@localhost/x')
from alembic.config import Config
from alembic.script import ScriptDirectory
cfg = Config('alembic.ini')
print('env.py OK')
"
```

Expected: `env.py OK`

**Step 4: Commit**

```bash
git add backend/alembic/env.py backend/alembic.ini
git commit -m "chore: configure alembic env.py with project Base and DATABASE_URL"
```

---

## Task 4: Drop & Recreate 本地 DB，產生初始 migration

**Step 1: 確認 DATABASE_URL**

```bash
cd backend && grep DATABASE_URL .env
```

Expected: 顯示 `postgresql://...` 連線字串

**Step 2: Drop 並重建本地 DB**

```bash
# 連進 psql 執行（密碼在 .env 的 DATABASE_URL）
psql postgresql://myuser:mypassword@localhost:5432/postgres -c "DROP DATABASE IF EXISTS financial_research;"
psql postgresql://myuser:mypassword@localhost:5432/postgres -c "CREATE DATABASE financial_research;"
```

> 注意：把 user/password/dbname 換成 .env 裡實際的值

Expected: `DROP DATABASE` 和 `CREATE DATABASE`

**Step 3: Autogenerate 初始 migration**

```bash
cd backend && alembic revision --autogenerate -m "initial schema"
```

Expected: 在 `alembic/versions/` 產生新檔案，如 `xxxx_initial_schema.py`

**Step 4: 檢查產生的 migration 檔案**

開啟 `alembic/versions/xxxx_initial_schema.py`，確認：
- `op.create_table("users", ...)` 存在
- `op.create_table("user_portfolio", ...)` 存在
- `op.create_table("daily_analysis_log", ...)` 存在
- GIN index：確認 `idx_log_indicators_gin` 是否正確。若產生的是一般 index，手動改為：
  ```python
  op.create_index(
      "idx_log_indicators_gin",
      "daily_analysis_log",
      ["indicators"],
      postgresql_using="gin",
  )
  ```

**Step 5: 套用 migration 至本地 DB**

```bash
cd backend && alembic upgrade head
```

Expected: 輸出套用步驟，無錯誤

**Step 6: 確認三張表建立成功**

```bash
psql postgresql://myuser:mypassword@localhost:5432/financial_research -c "\dt"
```

Expected: 列出 `users`、`user_portfolio`、`daily_analysis_log`、`alembic_version`

**Step 7: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat: add initial alembic migration for users, user_portfolio, daily_analysis_log"
```

---

## Task 5: 更新 README / 開發指南

**Files:**
- Modify: `docs/plans/2026-03-11-phase7-infrastructure.md`（在 Task 1 頂端加說明）

**Step 1: 在 phase7-infrastructure 計畫書 Task 1 開頭加一段注意事項**

在 `docs/plans/2026-03-11-phase7-infrastructure.md` 的 `## Task 1` 前插入：

```markdown
> ⚠️ Schema 管理已改由 **Alembic** 負責（見 `docs/plans/2026-03-11-alembic-migration.md`）。
> `db/init/01_schema.sql` 僅保留作為參考，不再執行。
> 部署新環境時，請執行 `alembic upgrade head` 而非手動 SQL。
```

**Step 2: Commit**

```bash
git add docs/plans/2026-03-11-phase7-infrastructure.md
git commit -m "docs: note that schema is now managed by alembic"
```

---

## 完成檢查清單

- [x] `alembic` 安裝成功，`requirements.txt` 已更新
- [x] `alembic init alembic` 產生正確目錄結構
- [x] `UserPortfolio`、`DailyAnalysisLog` ORM models 可被 import
- [x] `alembic/env.py` 正確指向 `Base` 和 `DATABASE_URL`
- [x] 本地 DB 重建後 `alembic upgrade head` 無錯誤
- [x] 三張表 + `alembic_version` 出現在 DB
- [x] GIN index 確認正確（`postgresql_using="gin"`）
- [x] Phase 7 計畫書已標注 schema 改由 Alembic 管理
- [x] Code review 修正：timezone-aware datetime、updated_at onupdate、constraint 名稱
- [x] App startup 自動執行 `alembic upgrade head`（本地 + Render 皆適用）

---

*文件版本：v1.0 | 建立日期：2026-03-11*
