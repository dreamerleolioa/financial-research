from __future__ import annotations

import os
import re
from typing import Literal

from pydantic import BaseModel, Field


class CleanedNews(BaseModel):
    date: str = Field(description="新聞日期，建議格式 YYYY-MM-DD，未知可填 unknown")
    title: str = Field(description="新聞標題")
    mentioned_numbers: list[str] = Field(
        description="文章提及的重要數字，保留原字串格式"
    )
    sentiment_label: Literal["positive", "neutral", "negative"] = Field(
        description="整體情緒標籤"
    )


class FinancialNewsCleaner:
    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model

    def _heuristic_clean(self, content: str) -> CleanedNews:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        title = lines[0][:120] if lines else "unknown"

        date_match = re.search(
            r"(20\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01]))", content
        )
        date = (
            date_match.group(1).replace("/", "-").replace(".", "-")
            if date_match
            else "unknown"
        )

        normalized_content = content
        if date_match:
            normalized_content = normalized_content.replace(date_match.group(1), " ")

        number_matches = re.findall(
            r"\$?\d[\d,]*(?:\.\d+)?%?|Q[1-4]",
            normalized_content,
            flags=re.IGNORECASE,
        )
        seen = set()
        mentioned_numbers: list[str] = []
        for value in number_matches:
            pure_number = re.sub(r"[^\d.]", "", value)
            is_significant_integer = False
            if pure_number and pure_number.replace(".", "", 1).isdigit():
                try:
                    numeric = float(pure_number)
                    is_significant_integer = numeric >= 10 or "." in pure_number
                except ValueError:
                    is_significant_integer = False

            if (
                value.upper().startswith("Q")
                or "%" in value
                or "," in value
                or "$" in value
                or is_significant_integer
            ) and value not in seen:
                seen.add(value)
                mentioned_numbers.append(value)

        lowered = content.lower()
        if any(
            word in lowered
            for word in [
                "surge",
                "beat",
                "growth",
                "record high",
                "大漲",
                "創高",
                "上漲",
                "利多",
                "成長",
            ]
        ):
            sentiment = "positive"
        elif any(
            word in lowered
            for word in ["drop", "miss", "decline", "loss", "大跌", "虧損", "下跌", "利空"]
        ):
            sentiment = "negative"
        else:
            sentiment = "neutral"

        return CleanedNews(
            date=date,
            title=title,
            mentioned_numbers=mentioned_numbers[:20],
            sentiment_label=sentiment,
        )

    def _llm_clean(self, content: str) -> CleanedNews:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return self._heuristic_clean(content)

        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            return self._heuristic_clean(content)

        llm = ChatOpenAI(api_key=api_key, model=self.model, temperature=0)
        structured_llm = llm.with_structured_output(CleanedNews)

        prompt = (
            "你是財經新聞資料清潔工。\n"
            "請把輸入文字整理為固定 JSON 結構，欄位必須是：\n"
            "date, title, mentioned_numbers, sentiment_label。\n"
            "\n"
            "規則：\n"
            "1) date 盡量標準化成 YYYY-MM-DD；找不到則填 unknown。\n"
            "2) title 使用新聞主標題，若無明確標題就抓最像標題的一句。\n"
            "3) mentioned_numbers 只保留具資訊價值的數字（價格、百分比、EPS、營收等），保留字串格式。\n"
            "4) sentiment_label 僅可為 positive / neutral / negative。\n"
            "5) 不要額外輸出任何欄位。\n"
            "\n"
            f"輸入內容：\n{content}"
        )

        try:
            return structured_llm.invoke(prompt)
        except Exception:
            return self._heuristic_clean(content)

    def clean(self, content: str) -> CleanedNews:
        return self._llm_clean(content)
