from __future__ import annotations

from importlib import import_module
from typing import Any

from ai_stock_sentinel.models import StockSnapshot

_SYSTEM_PROMPT = """\
你是一位謹慎的台股研究助理，採用 Skeptic Mode（懷疑論模式）。
請嚴格按照以下四步驟進行分析，不得跳過任何步驟：

步驟一【提取】：逐條列出輸入資料中的客觀事實（數值、日期、來源），不加入主觀判斷。
步驟二【對照】：將技術面訊號、籌碼面訊號、基本面（新聞情緒）三方資料並列比較。
步驟三【衝突檢查】：明確指出三方資料中是否存在矛盾或異常；若有，提出具體衝突點。
步驟四【輸出】：只輸出有資料支撐的事實與推論，禁止補造未在輸入資料中出現的來源或數字。

規範：
- LLM 不得修改 confidence_score 或 cross_validation_note，這兩個欄位由 rule-based 計算已完成。
- 輸出格式：summary（2-3 句）+ risks（條列式，至多 3 點）。
"""

_HUMAN_PROMPT = """\
請分析以下股票資料：

【基本快照】
- Symbol: {symbol}
- Current Price: {current_price}
- Previous Close: {previous_close}
- Open: {day_open} / High: {day_high} / Low: {day_low}
- Volume: {volume}
- Recent Closes: {recent_closes}

【技術面敘事】
{technical_context}

【籌碼面敘事】
{institutional_context}

【信心分數】{confidence_score}／100
【交叉驗證備注】{cross_validation_note}

請依步驟一至四完成分析，最後輸出 summary 與 risks。
"""


class LangChainStockAnalyzer:
    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    @staticmethod
    def _has_langchain() -> bool:
        try:
            import_module("langchain_core")
            return True
        except ImportError:
            return False

    def analyze(
        self,
        snapshot: StockSnapshot,
        *,
        technical_context: str | None = None,
        institutional_context: str | None = None,
        confidence_score: int | None = None,
        cross_validation_note: str | None = None,
    ) -> str:
        if not self._has_langchain():
            return (
                "LangChain 尚未安裝，已保留分析介面。\n"
                "安裝 requirements 後可注入 BaseChatModel 啟用分析。"
            )

        if self.llm is None:
            return (
                "LLM 尚未設定（缺少 API Key 或模型），已保留 LangChain 分析介面。\n"
                "你可以注入任何 BaseChatModel 來啟用自動分析。"
            )

        output_parsers = import_module("langchain_core.output_parsers")
        prompts = import_module("langchain_core.prompts")
        StrOutputParser = getattr(output_parsers, "StrOutputParser")
        ChatPromptTemplate = getattr(prompts, "ChatPromptTemplate")

        prompt = ChatPromptTemplate.from_messages([
            ("system", _SYSTEM_PROMPT),
            ("human", _HUMAN_PROMPT),
        ])
        chain = prompt | self.llm | StrOutputParser()
        return chain.invoke(
            {
                "symbol": snapshot.symbol,
                "current_price": snapshot.current_price,
                "previous_close": snapshot.previous_close,
                "day_open": snapshot.day_open,
                "day_high": snapshot.day_high,
                "day_low": snapshot.day_low,
                "volume": snapshot.volume,
                "recent_closes": snapshot.recent_closes,
                "technical_context": technical_context or "（無技術敘事）",
                "institutional_context": institutional_context or "（無籌碼敘事）",
                "confidence_score": confidence_score if confidence_score is not None else 50,
                "cross_validation_note": cross_validation_note or "（無交叉驗證備注）",
            }
        )
