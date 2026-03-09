from __future__ import annotations

import json
from importlib import import_module
from typing import Any

from ai_stock_sentinel.models import AnalysisDetail, StockSnapshot

_SYSTEM_PROMPT = """\
你是一位謹慎的台股研究助理，採用 Skeptic Mode（懷疑論模式）。
請嚴格按照以下四步驟進行分析，不得跳過任何步驟：

步驟一【識別市場情緒訊號】：從新聞識別 sentiment_label（positive / negative / neutral）。
判斷依據為事件本身的性質（法說會動態、政策利多/利空、法人評等調整、供應鏈事件等），
不依賴財務數字的有無。若新聞中碰巧出現百分比或金額數字，可作為輔助情緒佐證，
但新聞不是財務報告來源，不應嘗試從中整理結構化財務指標。
步驟二【對照】：將技術面訊號、籌碼面訊號、消息面情緒三方資料並列比較。
步驟三【衝突檢查】：明確指出三方資料中是否存在矛盾或異常；若有，提出具體衝突點。
步驟四【輸出】：只輸出有資料支撐的事實與推論，禁止補造未在輸入資料中出現的來源或數字。

分維度輸出規範（禁止跨維度混寫）：
- tech_insight：僅參考技術面資料（均線排列、RSI 位階、支撐壓力位）；禁止提及法人買賣超或新聞事件
- inst_insight：僅參考籌碼面資料（三大法人買賣超、融資券動向）；禁止提及均線數值、RSI、新聞事件
- news_insight：僅參考消息面資料（事件性質、市場情緒傾向）；禁止提及具體技術指標數值（如 RSI=62）
- final_verdict：整合三維訊號，解釋為何導向當前信心分數與策略；此段允許跨維度整合推論

規範：
- LLM 不得修改 confidence_score 或 cross_validation_note，這兩個欄位由 rule-based 計算已完成。
- 輸出格式：必須輸出合法 JSON，格式如下：
{{
  "summary": "2-3 句事實型摘要（可與 final_verdict 相同）",
  "risks": ["風險點 1", "風險點 2"],
  "technical_signal": "bullish|bearish|sideways",
  "institutional_flow": "從已提供的籌碼資料中讀取 flow_label，直接填入，不得修改",
  "sentiment_label": "從已提供的 cleaned_news 資料中讀取 sentiment_label，直接填入，不得修改",
  "tech_insight": "技術面獨立分析段落",
  "inst_insight": "籌碼面獨立分析段落",
  "news_insight": "消息面獨立分析段落",
  "final_verdict": "三維整合仲裁段落"
}}
- 不得輸出 JSON 以外的任何文字。
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

【消息面摘要】
{news_summary}

【技術面敘事】
{technical_context}

【籌碼面敘事】
{institutional_context}

【信心分數】{confidence_score}／100
【交叉驗證備注】{cross_validation_note}

請依步驟一至四完成分析，最後輸出 summary 與 risks。
"""


class LangChainStockAnalyzer:
    _COST_PER_MILLION_INPUT_TOKENS = 3.0  # USD, claude-sonnet-4
    _COST_THRESHOLD_USD = 1.0

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    def _estimate_cost(
        self,
        snapshot: StockSnapshot,
        *,
        news_summary: str | None,
        technical_context: str | None,
        institutional_context: str | None,
        confidence_score: int | None,
        cross_validation_note: str | None,
    ) -> None:
        combined = "".join([
            _SYSTEM_PROMPT,
            str(snapshot.symbol),
            str(snapshot.current_price),
            str(snapshot.previous_close),
            str(snapshot.day_open),
            str(snapshot.day_high),
            str(snapshot.day_low),
            str(snapshot.volume),
            str(snapshot.recent_closes),
            news_summary or "",
            technical_context or "",
            institutional_context or "",
            str(confidence_score if confidence_score is not None else 50),
            cross_validation_note or "",
        ])
        estimated_tokens = len(combined) / 4
        estimated_cost = (estimated_tokens / 1_000_000) * self._COST_PER_MILLION_INPUT_TOKENS

        if estimated_cost > self._COST_THRESHOLD_USD:
            raise ValueError(
                f"估算 input token 數：{int(estimated_tokens):,}，"
                f"預估費用：${estimated_cost:.4f} USD，"
                f"超過安全門檻 ${self._COST_THRESHOLD_USD} USD，已中止 LLM 呼叫。"
            )

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
        news_summary: str | None = None,
        technical_context: str | None = None,
        institutional_context: str | None = None,
        confidence_score: int | None = None,
        cross_validation_note: str | None = None,
    ) -> AnalysisDetail:
        if not self._has_langchain():
            return AnalysisDetail(
                summary=(
                    "LangChain 尚未安裝，已保留分析介面。\n"
                    "安裝 requirements 後可注入 BaseChatModel 啟用分析。"
                )
            )

        if self.llm is None:
            return AnalysisDetail(
                summary=(
                    "LLM 尚未設定（缺少 API Key 或模型），已保留 LangChain 分析介面。\n"
                    "你可以注入任何 BaseChatModel 來啟用自動分析。"
                )
            )

        self._estimate_cost(
            snapshot,
            news_summary=news_summary,
            technical_context=technical_context,
            institutional_context=institutional_context,
            confidence_score=confidence_score,
            cross_validation_note=cross_validation_note,
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
        raw = chain.invoke(
            {
                "symbol": snapshot.symbol,
                "current_price": snapshot.current_price,
                "previous_close": snapshot.previous_close,
                "day_open": snapshot.day_open,
                "day_high": snapshot.day_high,
                "day_low": snapshot.day_low,
                "volume": snapshot.volume,
                "recent_closes": snapshot.recent_closes,
                "news_summary": news_summary or "（本次無新聞摘要）",
                "technical_context": technical_context or "（無技術敘事）",
                "institutional_context": institutional_context or "（無籌碼敘事）",
                "confidence_score": confidence_score if confidence_score is not None else 50,
                "cross_validation_note": cross_validation_note or "（無交叉驗證備注）",
            }
        )
        return self._parse_analysis(raw)

    @staticmethod
    def _parse_analysis(raw: str) -> AnalysisDetail:
        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:]
            if inner and inner[-1].strip() == "```":
                inner = inner[:-1]
            text = "\n".join(inner).strip()
        try:
            data = json.loads(text)
            return AnalysisDetail(
                summary=str(data.get("summary", raw)),
                risks=[str(r) for r in data.get("risks", [])[:3]],
                technical_signal=str(data.get("technical_signal", "sideways")),
                institutional_flow=data.get("institutional_flow") or None,
                sentiment_label=data.get("sentiment_label") or None,
                tech_insight=data.get("tech_insight") or None,
                inst_insight=data.get("inst_insight") or None,
                news_insight=data.get("news_insight") or None,
                final_verdict=data.get("final_verdict") or None,
            )
        except (json.JSONDecodeError, TypeError, AttributeError):
            return AnalysisDetail(summary=raw)
