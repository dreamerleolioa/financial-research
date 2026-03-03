from __future__ import annotations

from importlib import import_module
from typing import Any

from ai_stock_sentinel.models import StockSnapshot


class LangChainStockAnalyzer:
    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm
        self.prompt_template = [
            (
                "system",
                "你是一位謹慎的台股研究助理。請基於輸入資料做簡短觀察，不提供投資保證。",
            ),
            (
                "human",
                """
請分析以下股票快照資料，輸出三點：
1) 價格與前收差異
2) 當日區間與波動觀察
3) 可能後續需要補充的資料

資料：
- Symbol: {symbol}
- Currency: {currency}
- Current Price: {current_price}
- Previous Close: {previous_close}
- Open: {day_open}
- High: {day_high}
- Low: {day_low}
- Volume: {volume}
- Recent Closes: {recent_closes}
- Fetched At (UTC): {fetched_at}
""",
            ),
        ]

    @staticmethod
    def _has_langchain() -> bool:
        try:
            import_module("langchain_core")

            return True
        except ImportError:
            return False

    def analyze(self, snapshot: StockSnapshot) -> str:
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

        prompt = ChatPromptTemplate.from_messages(self.prompt_template)
        chain = prompt | self.llm | StrOutputParser()
        return chain.invoke(
            {
                "symbol": snapshot.symbol,
                "currency": snapshot.currency,
                "current_price": snapshot.current_price,
                "previous_close": snapshot.previous_close,
                "day_open": snapshot.day_open,
                "day_high": snapshot.day_high,
                "day_low": snapshot.day_low,
                "volume": snapshot.volume,
                "recent_closes": snapshot.recent_closes,
                "fetched_at": snapshot.fetched_at,
            }
        )
