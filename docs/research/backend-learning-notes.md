# 後端自學筆記：AI Stock Sentinel

> 本文件記錄在 `backend/` 實作過程中學習到的 Python 技術，按模組章節組織。
> 每章包含概念說明、在本專案的用途、程式碼片段、學習資源。
> 請隨著專案演進持續補充。

---

## 目錄

1. [Python 基礎概念](#1-python-基礎概念)
2. [環境設定](#2-環境設定)
3. [資料驗證：Pydantic](#3-資料驗證pydantic)
4. [Web API：FastAPI](#4-web-apifastapi)
5. [資料來源](#5-資料來源)
6. [LLM 整合：LangChain](#6-llm-整合langchain)
7. [工作流程：LangGraph](#7-工作流程langgraph)
8. [測試：pytest + httpx](#8-測試pytest--httpx)
9. [並行執行：asyncio + ThreadPoolExecutor](#9-並行執行asyncio--threadpoolexecutor)
10. [資料庫：SQLAlchemy + Alembic](#10-資料庫sqlalchemy--alembic)
11. [使用者認證：JWT + Google OAuth](#11-使用者認證jwt--google-oauth)

---

## 1. Python 基礎概念

### 1.1 dataclass

**這是什麼**

`@dataclass` 是 Python 3.7+ 的裝飾器，讓你不用手寫 `__init__`、`__repr__` 就能定義資料結構。

**在本專案的用途**

用來定義股票快照（`StockSnapshot`）與分析結果（`AnalysisDetail`），負責承載各節點之間傳遞的資料。

**程式碼片段**（`backend/src/ai_stock_sentinel/models.py`）

```python
from dataclasses import dataclass, field

@dataclass(slots=True)
class AnalysisDetail:
    summary: str
    risks: list[str] = field(default_factory=list)  # 可變預設值要用 field()
    technical_signal: Literal["bullish", "bearish", "sideways"] = "sideways"
    tech_insight: str | None = None
```

`field(default_factory=list)` 是重要細節：如果直接寫 `risks: list[str] = []`，所有實例會共用同一個 list，導致 bug。

**`slots=True`（Python 3.10+）**

`@dataclass(slots=True)` 讓 dataclass 使用 `__slots__` 取代預設的 `__dict__` 儲存屬性：

- 減少每個 instance 的記憶體佔用（省掉 `__dict__` overhead）
- 屬性存取稍微更快
- 無法動態新增不在定義中的屬性（對 dataclass 通常不是問題）

限制：有繼承關係時，父類別與子類別都需要正確設定 `slots=True`。

本專案所有 dataclass 皆無繼承關係，已全數套用此優化。

**`frozen=True`（不可變快照）**

`@dataclass(slots=True, frozen=True)` 讓建立後的 instance 變成唯讀，嘗試修改屬性會拋出 `FrozenInstanceError`：

- 確保資料在 LangGraph 流轉過程中不被意外修改
- 自動獲得 `__hash__`，可作為 dict key 或放入 set
- 與 `slots=True` 組合使用，效能與安全性兼顧

限制：`__post_init__` 中若需要賦值（如 `StockSnapshot` 計算衍生欄位），無法使用 `frozen=True`，因為 `__post_init__` 執行時 instance 已被凍結。`QualityResult` 採用先建立再逐步填值的模式，同樣不適用。

本專案套用情況：

| Dataclass | `frozen=True` | 原因 |
|---|---|---|
| `AnalysisDetail` | ✅ | 分析結果只讀 |
| `Settings` | ✅ | 設定值應唯讀 |
| `FundamentalData` | ✅ | Provider 一次性填入 |
| `RawNewsItem` | ✅ | 純資料載體 |
| `InstitutionalFlowData` | ✅ | Provider 一次性填入 |
| `StockSnapshot` | ❌ | `__post_init__` 需要計算賦值 |
| `QualityResult` | ❌ | 建立後逐步填值的設計模式 |

**`__post_init__`**

`dataclass` 建構後會自動呼叫 `__post_init__`，適合放衍生計算邏輯。與 `slots=True` 完全相容：

```python
@dataclass(slots=True)
class StockSnapshot:
    recent_closes: list[float]
    support_20d: float | None = None

    def __post_init__(self) -> None:
        window = self.recent_closes[-20:]
        self.support_20d = min(window) * 0.99  # 自動計算支撐位
```

**學習資源**

- [Python Docs — dataclasses](https://docs.python.org/3/library/dataclasses.html)
- [Real Python — Data Classes in Python](https://realpython.com/python-data-classes/)

---

### 1.2 TypedDict

**這是什麼**

`TypedDict` 讓 `dict` 有靜態型別標注，讓 IDE 與 mypy 能做型別檢查，但 runtime 仍是普通 dict。

**在本專案的用途**

LangGraph 的狀態（`GraphState`）用 `TypedDict` 定義，每個節點讀寫同一個 state dict，型別標注讓補全與錯誤提示更準確。

**程式碼片段**（`backend/src/ai_stock_sentinel/graph/state.py`）

```python
from typing import TypedDict, Any

class GraphState(TypedDict):
    symbol: str
    snapshot: dict[str, Any] | None
    data_sufficient: bool
    retry_count: int
    signal_confidence: int | None
```

**學習資源**

- [Python Docs — typing.TypedDict](https://docs.python.org/3/library/typing.html#typing.TypedDict)

---

### 1.3 Protocol（鴨子型別介面）

**這是什麼**

「鴨子型別」來自一句英文諺語：

> "If it walks like a duck and quacks like a duck, it's a duck."
> 如果它走路像鴨子、叫聲像鴨子，那它就是鴨子。

意思是：**不看它是什麼類別，只看它能做什麼**。不需要明確繼承介面，只要物件有對應的方法，就算符合介面。

`Protocol` 是 Python 3.8+ 將這個概念正式化的語法，讓鴨子型別可以被靜態型別工具（mypy、Pylance）檢查。

**在本專案的用途**

`StockAnalyzer`、`FundamentalProvider`、`InstitutionalFlowProvider` 都用 `Protocol` 定義，讓不同實作（Anthropic / OpenAI）可以互換，而不需要繼承同一個 base class。

**程式碼片段**（`backend/src/ai_stock_sentinel/analysis/interface.py`）

```python
from typing import Protocol

class StockAnalyzer(Protocol):
    def analyze(self, context: str) -> AnalysisDetail:
        ...
```

只要某個 class 有 `analyze` 方法且簽名相符，就是合法的 `StockAnalyzer`。

**學習資源**

- [Python Docs — typing.Protocol](https://docs.python.org/3/library/typing.html#typing.Protocol)
- [Real Python — Python Protocols](https://realpython.com/python-protocol/)

---

### 1.4 async / await

**這是什麼**

Python 的非同步語法。`async def` 定義協程（coroutine），`await` 讓出控制權等待 I/O，讓程式在等待期間能處理其他工作。

**在本專案的用途**

FastAPI 的路由函式可以是 `async def`，資料庫查詢（AsyncSession）也需要 `await`。

**程式碼片段**（概念示意）

```python
# 同步版（會阻塞整個伺服器）
def fetch_data():
    time.sleep(1)
    return data

# 非同步版（等待時釋放控制權）
async def fetch_data():
    await asyncio.sleep(1)
    return data
```

**學習資源**

- [Python Docs — asyncio](https://docs.python.org/3/library/asyncio.html)
- [Real Python — Async IO](https://realpython.com/async-io-python/)

---

## 2. 環境設定

### 2.1 python-dotenv

**這是什麼**

從 `.env` 檔案讀取環境變數，讓敏感資訊（API key）不寫死在程式碼裡。

**在本專案的用途**

管理 `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`FINMIND_API_TOKEN` 等金鑰。`.env` 加入 `.gitignore`，`.env.example` 則保留範本供其他人參考。

**程式碼片段**（`backend/src/ai_stock_sentinel/config.py`）

```python
import os
from dataclasses import dataclass

@dataclass
class Settings:
    anthropic_api_key: str | None
    anthropic_model: str

def load_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
    )
```

`os.getenv("KEY", "default")` 第二個參數是找不到時的預設值。

**學習資源**

- [python-dotenv 官方文件](https://saurabh-kumar.com/python-dotenv/)

---

### 2.2 pyproject.toml

**這是什麼**

Python 專案的現代設定檔（PEP 518），取代舊的 `setup.py`，定義套件名稱、版本、依賴等。

**在本專案的用途**

`backend/pyproject.toml` 定義 `ai_stock_sentinel` 套件，讓 `from ai_stock_sentinel.api import ...` 可以正確解析。

**學習資源**

- [Python Packaging — pyproject.toml](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)

---

## 3. 資料驗證：Pydantic

**這是什麼**

Pydantic 用 Python 型別標注做執行期資料驗證，輸入不符型別時自動丟出錯誤訊息。FastAPI 原生整合 Pydantic。

**在本專案的用途**

API 的 request / response 都用 Pydantic `BaseModel` 定義，包含輸入驗證（`min_length=1`）與序列化。

**程式碼片段**（`backend/src/ai_stock_sentinel/api.py`）

```python
from pydantic import BaseModel, Field

class AnalyzeRequest(BaseModel):
    symbol: str = Field(default="2330.TW", min_length=1)
    news_text: str | None = None

class AnalyzeResponse(BaseModel):
    snapshot: dict[str, Any] = Field(default_factory=dict)
    signal_confidence: int | None = None
    errors: list[ErrorDetail] = Field(default_factory=list)

    class ErrorDetail(BaseModel):  # 可以在 BaseModel 裡面定義巢狀 Model
        code: str
        message: str
```

**學習資源**

- [Pydantic 官方文件](https://docs.pydantic.dev/)
- [FastAPI — Request Body](https://fastapi.tiangolo.com/tutorial/body/)

---

## 4. Web API：FastAPI

**這是什麼**

FastAPI 是現代 Python Web 框架，基於型別標注自動產生 API 文件（Swagger UI），支援 async。

**在本專案的用途**

提供三個端點：`POST /analyze`（一般分析）、`POST /analyze/position`（持倉診斷）、`GET /health`。

**程式碼片段**（`backend/src/ai_stock_sentinel/api.py`）

```python
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI Stock Sentinel API", version="v1")

# CORS：允許前端（localhost:5173）跨域呼叫
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Depends：依賴注入，每次請求自動執行 get_graph()
@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    payload: AnalyzeRequest,
    graph=Depends(get_graph),   # FastAPI 自動注入
) -> AnalyzeResponse:
    result = graph.invoke(initial_state)
    return _build_response(result)
```

`Depends` 是 FastAPI 的依賴注入機制，適合處理「每次請求都需要的共用資源」（DB 連線、圖物件等）。

**學習資源**

- [FastAPI 官方文件](https://fastapi.tiangolo.com/)
- [FastAPI — Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)

---

## 5. 資料來源

### 5.1 yfinance

**這是什麼**

非官方 Yahoo Finance Python 客戶端，可免費取得股票歷史價格、即時資訊。

**在本專案的用途**

`YFinanceCrawler` 抓取股票的當日價格、近三個月收盤歷史，作為技術分析的原始資料。

**程式碼片段**（`backend/src/ai_stock_sentinel/data_sources/yfinance_client.py`）

```python
import yfinance as yf

ticker = yf.Ticker("2330.TW")
info = ticker.fast_info                        # 即時資訊（快速版）
history = ticker.history(period="3mo", interval="1d")  # 近三個月日線

current_price = float(info.last_price)
recent_closes = history["Close"].dropna().tolist()
```

**學習資源**

- [yfinance GitHub](https://github.com/ranaroussi/yfinance)
- [yfinance 文件](https://yfinance-docs.readthedocs.io/)

---

### 5.2 Google News RSS

**這是什麼**

Google News 提供 RSS feed，可用股票關鍵字訂閱相關新聞，不需要 API key。

**在本專案的用途**

`RssNewsClient` 抓取股票相關新聞，提供給 LLM 做消息面分析。

**學習資源**

- [Python feedparser](https://feedparser.readthedocs.io/)
- Google News RSS URL 格式：`https://news.google.com/rss/search?q={query}&hl=zh-TW`

---

### 5.3 FinMind / TWSE / TPEX API

**這是什麼**

- **FinMind**：台股開放資料平台，提供法人買賣超、基本面資料（需 token）
- **TWSE**：台灣證券交易所官方 API（免費）
- **TPEX**：櫃檯買賣中心 API（免費，OTC 股票）

**在本專案的用途**

取得三大法人籌碼資料（外資、投信、自營商）。專案實作了 **Provider Router 模式**：優先嘗試 FinMind，失敗時依序 fallback 到 TWSE、TPEX。

**設計模式：Provider Router**

```python
# router 依序嘗試多個 provider，遇到成功即回傳
for provider in [finmind_provider, twse_provider, tpex_provider]:
    result = provider.fetch(symbol)
    if not result.get("error"):
        return result
return {"error": "all providers failed"}
```

**學習資源**

- [FinMind 文件](https://finmindtrade.com/analysis/#/Finish/api)
- [TWSE 開放資料](https://www.twse.com.tw/zh/page/trading/exchange/MI_INDEX.html)

---

## 6. LLM 整合：LangChain

**這是什麼**

LangChain 是 LLM 應用開發框架，提供統一介面讓你切換不同模型（Claude、GPT），並管理 prompt、structured output。

**在本專案的用途**

`LangChainAnalyzer` 呼叫 Claude（主）或 GPT（備援）做多維度股票分析，輸出結構化 JSON（`AnalysisDetail`）。

**程式碼片段**

```python
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

# 建立模型
llm = ChatAnthropic(
    model="claude-sonnet-4-5",
    temperature=0.2,       # 低溫度 = 輸出更穩定
    api_key=api_key,
)

# 帶入 system prompt + user prompt
messages = [
    ("system", SYSTEM_PROMPT),
    ("human", user_context),
]
response = llm.invoke(messages)
```

**Skeptic Mode System Prompt**

本專案的 system prompt 強制 LLM 分四步驟分析，並限制各維度（技術面、籌碼面、消息面）不得互相混寫，避免 LLM 幻覺跨維度引用不存在的數據。

**學習資源**

- [LangChain 官方文件](https://python.langchain.com/docs/)
- [LangChain — ChatAnthropic](https://python.langchain.com/docs/integrations/chat/anthropic/)
- [Anthropic API 文件](https://docs.anthropic.com/)

---

## 7. 工作流程：LangGraph

**這是什麼**

LangGraph 是基於 LangChain 的狀態機框架，讓複雜的多步驟 AI 工作流程可以用「節點 + 邊」的圖結構表達，支援條件分支與回圈。

**在本專案的用途**

整個股票分析流程（抓資料 → 判斷資料充分性 → 清理新聞 → 技術評分 → LLM 分析 → 策略生成）是一個 LangGraph `StateGraph`，各步驟是節點，資料共享 `GraphState`。

**程式碼片段**（`backend/src/ai_stock_sentinel/graph/builder.py`）

```python
from langgraph.graph import StateGraph, END

graph = StateGraph(GraphState)

# 加入節點
graph.add_node("crawl", crawl_node)
graph.add_node("judge", judge_node)

# 固定邊
graph.set_entry_point("crawl")
graph.add_edge("crawl", "judge")

# 條件邊（根據 state 決定下一步）
def _route(state: GraphState) -> str:
    if state["data_sufficient"]:
        return "clean"
    if state["retry_count"] >= 3:
        return "clean"   # 超過重試上限，強制往下走
    return "fetch_news"

graph.add_conditional_edges("judge", _route, {
    "clean": "clean",
    "fetch_news": "fetch_news",
})

graph.add_edge("clean", END)
compiled = graph.compile()

# 執行
result = compiled.invoke(initial_state)
```

**工作流程圖**

```
START → crawl → fetch_institutional → fetch_fundamental → judge
                                                             ↓
                                          data_sufficient? ─┤
                                                    yes     ↓    no + retry < 3
                                                   clean ← fetch_news
                                                     ↓
                                              quality_gate → preprocess → score → analyze → strategy → END
```

**學習資源**

- [LangGraph 官方文件](https://langchain-ai.github.io/langgraph/)
- [LangGraph — StateGraph 概念](https://langchain-ai.github.io/langgraph/concepts/low_level/)

---

## 8. 測試：pytest + httpx

**這是什麼**

- **pytest**：Python 最主流的測試框架，簡潔的 `assert` 語法、fixture 機制
- **httpx**：現代 HTTP 客戶端，支援 async，FastAPI 測試常用

**在本專案的用途**

後端約有 25 個測試檔案，涵蓋 API 端點、各分析模組、資料來源。使用 `unittest.mock` 做依賴隔離，避免測試時真的呼叫外部 API。

**程式碼片段**

```python
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from ai_stock_sentinel import api

def test_analyze_returns_200():
    mock_result = {"snapshot": {}, "analysis": "測試結論"}

    # patch 替換掉真正的 graph，避免呼叫外部 API
    with patch("ai_stock_sentinel.api.get_graph") as mock_graph:
        mock_graph.return_value.invoke.return_value = mock_result
        client = TestClient(api.app)
        resp = client.post("/analyze", json={"symbol": "2330.TW"})
        assert resp.status_code == 200
```

**Mock 常用方式**

| 情境 | 用法 |
|------|------|
| 替換函式回傳值 | `MagicMock(return_value=...)` |
| 替換 async 函式 | `AsyncMock(return_value=...)` |
| 替換整個模組路徑 | `patch("module.path.function")` |

**執行測試**

```bash
cd backend
pytest -v                        # 執行全部測試
pytest tests/test_api.py -v      # 只跑特定檔案
pytest -k "test_analyze" -v      # 只跑名稱包含 test_analyze 的測試
```

**學習資源**

- [pytest 官方文件](https://docs.pytest.org/)
- [FastAPI — Testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [unittest.mock 官方文件](https://docs.python.org/3/library/unittest.mock.html)

---

## 9. 並行執行：asyncio + ThreadPoolExecutor

### 9.1 為什麼需要並行

當工作流程中有多個互相**獨立**的 I/O 操作（例如同時打兩個外部 API），依序執行會讓總等待時間疊加：

```
串行：[fetch_institutional 2s] → [fetch_fundamental 2s] = 4s
並行：[fetch_institutional 2s]
     [fetch_fundamental 2s]   = ~2s（同時跑）
```

### 9.2 兩種並行模型

| 模型 | 適合場景 | Python 工具 |
|------|---------|------------|
| **多執行緒（threading）** | I/O 密集（HTTP、DB、檔案）| `ThreadPoolExecutor` |
| **非同步（async）** | 大量 I/O、高並發 | `asyncio` |

本專案的 fetcher 都是同步的 HTTP 呼叫，使用 **ThreadPoolExecutor** 最省改動。

### 9.3 asyncio + run_in_executor 模式

`run_in_executor` 把同步函式丟進 thread pool，讓它可以被 `await`，進而用 `asyncio.gather` 並行等待多個結果：

**程式碼片段**（`backend/src/ai_stock_sentinel/graph/nodes.py`）

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def _run():
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=2) as pool:
        # 把兩個同步函式各自丟入 thread pool
        inst_future = loop.run_in_executor(pool, institutional_fetcher, symbol)
        fund_future = loop.run_in_executor(pool, fundamental_fetcher, symbol, current_price)
        # 並行等待兩個結果
        return await asyncio.gather(inst_future, fund_future)

inst_result, fund_result = asyncio.run(_run())
```

- `run_in_executor(pool, fn, *args)`：在 thread pool 裡執行 `fn(*args)`，回傳 awaitable
- `asyncio.gather(*awaitables)`：並行等待所有 awaitable，按順序回傳結果
- `asyncio.run(coro)`：在同步環境中啟動一個 async 函式並等待它完成

### 9.4 為什麼不直接改成 async fetcher

底層 provider（FinMind、TWSE、TPEX）使用同步的 `requests` 函式庫發 HTTP。要改成真正的 async 需要：

1. 把所有 provider 換成 `httpx.AsyncClient`
2. 所有呼叫路徑都要加 `async def` / `await`

改動範圍大、風險高，但效能收益跟 `run_in_executor` 相近。對 I/O 密集型任務，**thread pool 方案已足夠**。

### 9.5 測試並行行為

測試並行最直接的方式是讓兩個 fetcher 各自 sleep，驗證總時間接近單個 sleep 而非兩個相加：

**程式碼片段**（`backend/tests/test_fetch_external_data_node.py`）

```python
import time

def test_runs_fetchers_concurrently():
    def slow_inst(symbol):
        time.sleep(0.2)
        return {"flow_label": "neutral"}

    def slow_fund(symbol, price):
        time.sleep(0.2)
        return {"ttm_eps": 10.0}

    start = time.monotonic()
    fetch_external_data_node(
        state,
        institutional_fetcher=slow_inst,
        fundamental_fetcher=slow_fund,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.35  # 並行應在 ~0.2s 完成，而非 ~0.4s
```

**學習資源**

- [Python Docs — asyncio](https://docs.python.org/3/library/asyncio.html)
- [Python Docs — concurrent.futures](https://docs.python.org/3/library/concurrent.futures.html)
- [Real Python — Python Concurrency](https://realpython.com/python-concurrency/)

---

---

## 10. 資料庫：SQLAlchemy + Alembic

### 10.1 SQLAlchemy ORM（DeclarativeBase + Mapped）

**這是什麼**

SQLAlchemy 是 Python 最主流的 ORM，讓你用 Python class 操作資料庫，不需要手寫 SQL。SQLAlchemy 2.0 引入了新的 `Mapped` 型別標注語法，讓欄位定義更清晰。

**在本專案的用途**

定義 `User`、`UserPortfolio`、`DailyAnalysisLog` 三張表的 ORM model，供 FastAPI 路由透過 session 操作資料庫。

**程式碼片段**（`backend/src/ai_stock_sentinel/db/session.py`）

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

class Base(DeclarativeBase):
    pass

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency – yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- `pool_pre_ping=True`：每次使用連線前先 ping，避免用到斷線的連線
- `autocommit=False`：需要手動呼叫 `db.commit()` 才會寫入
- `autoflush=False`：不自動同步 ORM 狀態到 DB，避免意外的 SQL

**Mapped 型別標注語法**（`backend/src/ai_stock_sentinel/db/models.py`）

```python
from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

class UserPortfolio(Base):
    __tablename__ = "user_portfolio"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- `Mapped[int]` 表示欄位不可為 NULL；`Mapped[int | None]` 表示可為 NULL
- `server_default=func.now()`：讓資料庫自動填入當前時間（INSERT 時）
- `onupdate=func.now()`：ORM 執行 UPDATE 時自動更新時間戳
- `DateTime(timezone=True)`：存為 TIMESTAMPTZ，含時區資訊，避免跨時區 bug

**JSONB 欄位**（PostgreSQL 專屬）

```python
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Index

class DailyAnalysisLog(Base):
    __tablename__ = "daily_analysis_log"
    __table_args__ = (
        Index("idx_log_indicators_gin", "indicators", postgresql_using="gin"),
    )

    indicators: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

- `JSONB`：PostgreSQL 的二進位 JSON 格式，支援 indexing 和查詢（比 JSON 快）
- `postgresql_using="gin"`：GIN（Generalized Inverted Index），適合 JSONB 的 key 查詢

**FastAPI 中使用 DB session**

```python
from fastapi import Depends
from sqlalchemy.orm import Session
from ai_stock_sentinel.db.session import get_db

@app.post("/some-route")
def some_route(db: Session = Depends(get_db)):
    user = db.get(User, user_id)        # 按主鍵查詢
    users = db.query(User).filter(User.email == email).first()  # 條件查詢
    db.add(new_user)
    db.commit()
    db.refresh(new_user)  # 讀取 DB 自動填入的欄位（id、created_at）
```

---

### 10.2 Alembic Schema 版本管理

**這是什麼**

Alembic 是 SQLAlchemy 官方的 migration 工具，讓你把 schema 變更（新增表、加欄位）記錄成版本化的 migration 檔案，可以 upgrade（往前）也可以 downgrade（回滾）。

**在本專案的用途**

取代手動 SQL，每次改 ORM model 後自動 diff 出 migration，部署時自動套用。

**常用指令**

```bash
# 產生 migration（自動 diff ORM model 和現有 DB 的差異）
cd backend && PYTHONPATH=src alembic revision --autogenerate -m "add user table"

# 套用所有未執行的 migration
cd backend && PYTHONPATH=src alembic upgrade head

# 回滾一個版本
cd backend && PYTHONPATH=src alembic downgrade -1

# 查看目前版本
cd backend && PYTHONPATH=src alembic current
```

**env.py 設定重點**（`backend/alembic/env.py`）

```python
# 確保所有 model 都被 import，讓 Base.metadata 完整
from ai_stock_sentinel.db.session import Base
import ai_stock_sentinel.user_models.user   # noqa: F401
import ai_stock_sentinel.db.models          # noqa: F401

target_metadata = Base.metadata

def get_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return url
```

- `Base.metadata` 是 autogenerate 的資料來源，必須確保所有 model 都被 import 過
- `DATABASE_URL` 動態讀取，不 hardcode，本地和雲端各自用自己的 .env

**自動化部署**（`backend/src/ai_stock_sentinel/api.py`）

```python
@app.on_event("startup")
def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
```

FastAPI 啟動時自動跑 `alembic upgrade head`，idempotent（已是最新就不做任何事）。本地和 Render 雲端部署都會自動同步 schema。

**學習資源**

- [SQLAlchemy 2.0 ORM 文件](https://docs.sqlalchemy.org/en/20/orm/)
- [Alembic 官方文件](https://alembic.sqlalchemy.org/en/latest/)

---

## 11. 使用者認證：JWT + Google OAuth

### 11.1 JWT（JSON Web Token）

**這是什麼**

JWT 是一種自包含的 token 格式，伺服器簽發後不需要存 session，客戶端每次請求帶上 token，伺服器驗證簽名即可確認身份。

結構：`header.payload.signature`（Base64 編碼，用 `.` 分隔）

**在本專案的用途**

Google OAuth 登入成功後，伺服器簽發 JWT，前端存在 localStorage，之後每次 API 請求放在 `Authorization: Bearer <token>` header。

**程式碼片段**（`backend/src/ai_stock_sentinel/auth/jwt_handler.py`）

```python
import jwt  # PyJWT

_ALGORITHM = "HS256"
_EXPIRE_DAYS = 7

def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=_ALGORITHM)

def decode_access_token(token: str) -> dict:
    """Decode and verify JWT. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, JWT_SECRET, algorithms=[_ALGORITHM])
```

- `sub`（subject）：JWT 標準欄位，存放 user_id
- `exp`（expiration）：到期時間，PyJWT 驗證時自動檢查
- `HS256`：HMAC-SHA256，用 `JWT_SECRET` 環境變數簽名/驗證

### 11.2 Google OAuth（id_token 驗證）

**這是什麼**

Google OAuth 的前端 Sign-In 流程：使用者點「用 Google 登入」→ Google 回傳 id_token → 前端把 id_token 傳給後端驗證。

後端不需要走完整的 OAuth code exchange，直接用 Google 提供的 public key 驗證 id_token 的簽名。

**在本專案的用途**

`verify_google_id_token()` 驗證前端傳來的 id_token，提取 Google sub（唯一識別符）和 email。

**程式碼片段**（`backend/src/ai_stock_sentinel/auth/google_verifier.py`）

```python
from google.oauth2 import id_token as google_id_token
import google.auth.transport.requests

def verify_google_id_token(token: str) -> GoogleUserInfo:
    request = google.auth.transport.requests.Request()
    idinfo = google_id_token.verify_oauth2_token(
        token, request, audience=GOOGLE_CLIENT_ID
    )
    return GoogleUserInfo(
        sub=idinfo["sub"],    # Google 帳號唯一識別符
        email=idinfo["email"],
        name=idinfo.get("name"),
        picture=idinfo.get("picture"),
    )
```

- `audience` 必須符合 `GOOGLE_CLIENT_ID`，防止 token 被其他應用重複使用
- `google-auth` 函式庫自動向 Google 取得 public key 並驗證簽名

### 11.3 完整登入流程

**登入（`backend/src/ai_stock_sentinel/auth/router.py`）**

```python
@router.post("/google", response_model=TokenResponse)
def google_login(payload: GoogleLoginRequest, db: Session = Depends(get_db)):
    # 1. 驗證 Google id_token
    google_info = verify_google_id_token(payload.id_token)

    # 2. 查詢或建立使用者（upsert 概念）
    user = db.query(User).filter(User.google_sub == google_info.sub).first()
    if user is None:
        user = User(google_sub=google_info.sub, email=google_info.email, ...)
        db.add(user)
        db.commit()
    else:
        user.name = google_info.name   # 更新最新資料
        db.commit()

    # 3. 簽發 JWT
    token = create_access_token(user_id=user.id, email=user.email)
    return TokenResponse(access_token=token)
```

**驗證（`backend/src/ai_stock_sentinel/auth/dependencies.py`）**

```python
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_access_token(credentials.credentials)  # 驗證 JWT
    user = db.get(User, int(payload["sub"]))                 # 查詢 DB
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user
```

使用方式：在任何需要登入的路由加上 `current_user: User = Depends(get_current_user)`

**軟刪除模式**

使用者停用不刪除資料，設定 `deleted_at = datetime.now()`。查詢時檢查 `user.deleted_at is not None`，確保軟刪除的帳號無法登入。

**學習資源**

- [PyJWT 官方文件](https://pyjwt.readthedocs.io/)
- [Google Identity — Verify the Google ID token](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token)
- [FastAPI — Security](https://fastapi.tiangolo.com/tutorial/security/)

---

*最後更新：2026-03-11*
