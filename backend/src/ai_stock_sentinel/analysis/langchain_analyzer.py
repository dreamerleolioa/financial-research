from __future__ import annotations

import json
from dataclasses import asdict
from importlib import import_module
from typing import Any

from ai_stock_sentinel.models import AnalysisDetail, StockSnapshot

_SYSTEM_PROMPT = """\
你是一位謹慎的台股研究專家，採用 Skeptic Mode（懷疑論審查模式）。
請在產生最終結論前，先進行資料對照與衝突審查。

【原始數據解讀常模 (Raw Data Reference Manual)】：
請使用以下標準來解讀 raw_data_snapshot 中的數值，禁止憑空定義指標到位階：
- 布林帶寬 (Bollinger Bandwidth)：
  - < 0.08 ➔ 極度擠壓（Squeeze），代表波動率降至歷史低點，近期必定面臨爆發性單邊突破。
  - > 0.25 ➔ 極度發散，單邊動能可能過熱，需防範均值回歸。
- 趨勢強度 (ADX)：
  - > 25 ➔ 存在強烈單邊趨勢，此時均線與順勢策略極度有效。
  - < 18 ➔ 盤整，均線極易黃金/死亡交叉「雙巴」，應以區間震盪指標為準。
- 資金流向 (MFI)：
  - > 80 ➔ 資金極度湧入（擁擠），需防高檔獲利了結。
  - < 20 ➔ 資金過度流出，籌碼恐慌出清。
- 量價確認 (OBV)：
  - 股價創近期新高，但 OBV 未創高 ➔ 量價背離（Divergence），暗示主力暗中出貨，假突破機率高。
- 估值百分位 (PE Percentile)：
  - < 20% ➔ 歷史極度低估，具備強大安全邊際。
  - > 80% ➔ 歷史極度高估，溢價過高。

【推理與審查規範】：
1. 你的首要任務是找出數據中的「背離」與「矛盾」（例如：消息面極度樂觀，但籌碼面外資連續出貨）。
2. 不要盲目合理化 rule-based 的系統標籤。若你發現「原始數據快照」與系統給出的標籤存在明顯衝突，你必須在 thought_process 中主動發起挑戰，並在 final_verdict 中給出你獨立的專業研判。

【分維度寫作指南（主從聯動，禁止跨維度混寫其他非關聯資料）】：
- tech_insight：以技術指標為核心。允許引入籌碼流向作為量價配合的佐證（例如：指出均線突破是否伴隨三大法人大買），但禁止提及任何新聞具體事件。
- inst_insight：以法人、大戶與散戶持股結構為核心。允許引入價格位階作為建倉成本對照（例如：指出法人是否在歷史低檔建倉），但禁止提及 RSI、MACD 具體技術數值或新聞事件。
- news_insight：分析市場情緒與新聞實質影響。著重事件的「預期兌現」或「利多/利空出盡」評估，禁止提及技術指標與籌碼數值。
- fundamental_insight：僅參考基本面估值資料（PE 位階、殖利率、每股盈餘趨勢）；禁止提及技術指標或法人動向。
- final_verdict：整合三維訊號，解釋為何導向當前信心分數與策略；此段允許跨維度整合推論。

規範：
- 優先閱讀「系統信號摘要」。該摘要是 rule-based 計算結果；institutional_flow、sentiment_label、confidence_score 不得自行改寫。technical_signal 若與原始數據明顯衝突，可在輸出欄位提出修正，並必須在 thought_process 與 final_verdict 說明原因。
- tech_insight 必須用 2-3 句說清楚：先講技術結論，再列主要依據。若 KD / ADX / OBV / ATR / MFI / Donchian 有資料，至少引用其中三項；若與均線、MACD 或布林通道矛盾，必須點出矛盾。
- final_verdict 必須解釋 confidence_score 的來源：說明技術、籌碼、消息面是共振、分歧，或資料不足；避免只寫「可留意」這類無法執行的結論。
- LLM 不得修改 confidence_score 或 cross_validation_note，這兩個欄位由 rule-based 計算已完成。
- 輸出格式：必須輸出合法 JSON，格式如下：
{{
  "thought_process": "在這裡進行對照與衝突審查。請用懷疑論視角，對照原始數據與系統標籤，指出任何背離、矛盾或疑點（限 150 字內）。",
  "summary": "2-3 句事實型摘要（可與 final_verdict 相同）",
  "risks": ["風險點 1", "風險點 2"],
  "technical_signal": "若你強烈反對系統的 rule-based 標籤，請在此輸出你研判的 bullish|bearish|sideways；否則沿用系統標籤",
  "institutional_flow": "從已提供的籌碼資料中讀取 flow_label，直接填入，不得修改",
  "sentiment_label": "從已提供的 cleaned_news 資料中讀取 sentiment_label，直接填入，不得修改",
  "tech_insight": "技術面獨立分析段落",
  "inst_insight": "籌碼面獨立分析段落",
  "news_insight": "消息面獨立分析段落",
  "fundamental_insight": "基本面估值分析段落（若無資料則填 null）",
  "final_verdict": "三維整合仲裁段落"
}}
- 不得輸出 JSON 以外的任何文字。
"""

import hashlib as _hashlib
PROMPT_HASH: str = _hashlib.md5(_SYSTEM_PROMPT.encode()).hexdigest()[:8]


def _position_value(value: Any, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    return f"{value}{suffix}"

_POSITION_SYSTEM_PROMPT = """
你正在診斷使用者的持有倉位，而非尋找新的買點。

核心任務：
1. 以購入成本價（entry_price）為錨點，判斷當前持股是否值得繼續持有
2. 法人資金方向是最重要的出場訊號——若法人轉為出貨，獲利中亦須警示了結
3. 你的輸出必須聚焦「出場」、「減碼」、「防守」，不得建議加碼或新進場
4. 禁止以樂觀語氣淡化風險；若存在出場理由，必須明確標記

出場觸發條件（任一命中即須評估出場）：
- flow_label = distribution 且 profit_loss_pct > 0（獲利出場警示）
- flow_label = distribution 且 position_status = under_water（停損評估）
- technical_signal = bearish 且 close < trailing_stop（跌破防守線）
"""

_HUMAN_PROMPT = """\
請分析以下股票資料：

【系統信號摘要（優先閱讀，不得改寫 key labels）】
{signal_summary}

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

【基本面估值】
{fundamental_context}

【信心分數】{confidence_score}／100
【交叉驗證備注】{cross_validation_note}

【指標常模速查對照表 (Indicator Reference Cheat Sheet)】
- 布林帶寬 (Bandwidth)：< 0.08 代表即將變盤突破；> 0.25 代表發散過熱。
- ADX 趨勢度：> 25 趨勢強勁；< 18 代表震盪整理，均線易失真。
- MFI 資金流：> 80 擁擠過熱；< 20 超賣。
- PE 百分位：< 20% 歷史便宜區；> 80% 歷史昂貴區。
- 量價背離：股價與 OBV 趨勢相反時為強烈警戒訊號。

請依步驟一至四完成分析，最後輸出 summary 與 risks。
{history_section}

【原始數據快照 (Raw Data Snapshot)】
以下是技術、籌碼、基本面原始計算數值：
{raw_data_snapshot}

請依步驟一至四完成分析，最後輸出 summary 與 risks。"""


def build_position_history_section(prev_context: dict | None) -> str:
    """將昨日上下文格式化為 Prompt 的【訊號連續性】區塊。

    所有數值來自 DB 讀取，此函式僅做格式化，不推斷任何數值。
    """
    if prev_context is None:
        return ""
    return (
        f"\n【訊號連續性分析（昨日數據來自 DB，非 LLM 推斷）】\n"
        f"- 昨日建議：{prev_context.get('prev_action_tag', 'N/A')}"
        f"（信心：{prev_context.get('prev_confidence', 'N/A')}）\n"
        f"- 昨日 RSI：{prev_context.get('prev_rsi', 'N/A')}\n"
        f"- 昨日均線排列：{prev_context.get('prev_ma_alignment', 'N/A')}\n"
        f"- 昨日 MACD 方向：{prev_context.get('prev_macd_bias', 'N/A')}\n"
        f"- 昨日布林位階：{prev_context.get('prev_bollinger_position', 'N/A')}\n"
        f"請在 final_verdict 中說明今日訊號與昨日的連續性或轉向原因。\n"
    )


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
        fundamental_context: str | None = None,
        history_section: str | None = None,
        signal_summary: str | None = None,
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
            fundamental_context or "",
            history_section or "",
            signal_summary or "",
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
        fundamental_context: str | None = None,
        position_context: dict | None = None,
        prev_context: dict | None = None,
        signal_summary: str | None = None,
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
            fundamental_context=fundamental_context,
            history_section=build_position_history_section(prev_context),
            signal_summary=signal_summary,
        )

        output_parsers = import_module("langchain_core.output_parsers")
        prompts = import_module("langchain_core.prompts")
        exceptions_module = import_module("langchain_core.exceptions")
        StrOutputParser = getattr(output_parsers, "StrOutputParser")
        JsonOutputParser = getattr(output_parsers, "JsonOutputParser")
        ChatPromptTemplate = getattr(prompts, "ChatPromptTemplate")
        OutputParserException = getattr(exceptions_module, "OutputParserException")

        system_content = _SYSTEM_PROMPT
        human_content = _HUMAN_PROMPT
        if position_context is not None:
            system_content = _SYSTEM_PROMPT + _POSITION_SYSTEM_PROMPT
            pc = position_context
            position_block = (
                "\n【持倉資訊】\n"
                f"- 購入成本價（entry_price）：{pc.get('entry_price', 'N/A')}\n"
                f"- 當前損益：{pc.get('profit_loss_pct', 0.0):.2f}%\n"
                f"- 倉位狀態：{pc.get('position_status', 'unknown')}（{pc.get('position_narrative', '')}）\n"
                f"- 動態防守位：{pc.get('trailing_stop', 'N/A')}（{pc.get('trailing_stop_reason', '')}）\n"
                f"- 距離防守位：{_position_value(pc.get('distance_to_trailing_stop_pct'), '%')}\n"
                f"- 距離 20 日支撐：{_position_value(pc.get('distance_to_support_pct'), '%')}\n"
                f"- 未實現損益：{_position_value(pc.get('unrealized_pnl'))}\n"
                f"- 持有天數：{_position_value(pc.get('holding_days'))}\n"
                f"- 系統建議動作：{pc.get('recommended_action', 'N/A')}\n"
                f"- 系統出場/減碼理由：{pc.get('exit_reason', None) or '未觸發'}\n\n"
                "請根據以上持倉資訊，從「防守」視角撰寫 tech_insight、inst_insight、final_verdict。"
                "必須明確提示出場或減碼條件。\n"
            )
            human_content = _HUMAN_PROMPT + position_block

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_content),
            ("human", human_content),
        ])
        invoke_kwargs = {
            "symbol": snapshot.symbol,
            "current_price": snapshot.current_price,
            "previous_close": snapshot.previous_close,
            "day_open": snapshot.day_open,
            "day_high": snapshot.day_high,
            "day_low": snapshot.day_low,
            "volume": snapshot.volume,
            "recent_closes": snapshot.recent_closes,
            "signal_summary": signal_summary or "（本次無系統信號摘要）",
            "news_summary": news_summary or "（本次無新聞摘要）",
            "technical_context": technical_context or "（無技術敘事）",
            "institutional_context": institutional_context or "（無籌碼敘事）",
            "confidence_score": confidence_score if confidence_score is not None else 50,
            "cross_validation_note": cross_validation_note or "（無交叉驗證備注）",
            "fundamental_context": fundamental_context or "（本次無基本面資料）",
            "history_section": build_position_history_section(prev_context),
            "raw_data_snapshot": json.dumps({
                "snapshot": asdict(snapshot),
                "technical_summary": technical_context,
                "institutional_summary": institutional_context,
                "fundamental_summary": fundamental_context,
            }, ensure_ascii=False),
        }
        try:
            json_chain = prompt | self.llm | JsonOutputParser()
            data = json_chain.invoke(invoke_kwargs)
            return self._parse_analysis_from_dict(data)
        except (json.JSONDecodeError, OutputParserException):
            str_chain = prompt | self.llm | StrOutputParser()
            raw = str_chain.invoke(invoke_kwargs)
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
                fundamental_insight=data.get("fundamental_insight") or None,
                thought_process=data.get("thought_process") or None,
            )
        except (json.JSONDecodeError, TypeError, AttributeError):
            return AnalysisDetail(summary=raw)

    @staticmethod
    def _parse_analysis_from_dict(data: dict) -> AnalysisDetail:
        """從已解析的 dict 建立 AnalysisDetail（JsonOutputParser 路徑）。"""
        return AnalysisDetail(
            summary=str(data.get("summary") or "（AI 分析摘要缺失）"),
            risks=[str(r) for r in data.get("risks", [])[:3]],
            technical_signal=str(data.get("technical_signal", "sideways")),
            institutional_flow=data.get("institutional_flow") or None,
            sentiment_label=data.get("sentiment_label") or None,
            tech_insight=data.get("tech_insight") or None,
            inst_insight=data.get("inst_insight") or None,
            news_insight=data.get("news_insight") or None,
            final_verdict=data.get("final_verdict") or None,
            fundamental_insight=data.get("fundamental_insight") or None,
            thought_process=data.get("thought_process") or None,
        )
