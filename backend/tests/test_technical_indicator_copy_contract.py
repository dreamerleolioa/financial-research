from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TECHNICAL_INDICATORS_SOURCE = ROOT / "frontend/src/lib/technicalIndicators.ts"

FORBIDDEN_INTERNAL_LAYER_TOKENS = [
    "technical_profile",
    "primary_score_inputs",
    "risk_overheat_filters",
    "secondary_evidence",
    "display_only",
    "score_summary",
    "capped_total",
    "technical_score",
    "Primary",
    "Risk",
    "Secondary",
    "Display-only",
    "bucket impact",
]

REQUIRED_NEUTRAL_COPY_TOKENS = [
    "股票名稱",
    "股票代碼",
    "資料狀態",
    "現價",
    "成交量",
    "均線 MA5/20/60",
    "AVWAP",
    "千張大戶持股比例",
    "技術指標：資料不足",
]

ALLOWED_COPY_FUNCTION_CALLS = {
    "buildChipStabilityCopyRows",
    "buildPhase1AvwapCopyRows",
    "formatIndicatorNumber",
    "formatMovingAverages",
    "formatPrice",
    "formatVolume",
    "getAnalyzeSymbolName",
    "getTechnicalIndicatorLabel",
    "indicatorPair",
    "price",
    "pricePair",
}

IGNORED_JAVASCRIPT_CALLS = {
    "if",
    "join",
    "map",
}


def test_technical_indicator_copy_does_not_export_internal_layering_contract() -> None:
    source = TECHNICAL_INDICATORS_SOURCE.read_text(encoding="utf-8")

    hits = [token for token in FORBIDDEN_INTERNAL_LAYER_TOKENS if token in source]

    assert hits == []


def test_technical_indicator_copy_only_calls_approved_payload_helpers() -> None:
    function_body = _extract_function_body(
        TECHNICAL_INDICATORS_SOURCE.read_text(encoding="utf-8"),
        "buildTechnicalIndicatorsCopyText",
    )
    called_functions = {
        match.group(1)
        for match in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)\s*\(", function_body)
    }
    unexpected_calls = sorted(called_functions - ALLOWED_COPY_FUNCTION_CALLS - IGNORED_JAVASCRIPT_CALLS)

    assert unexpected_calls == []


def test_technical_indicator_copy_keeps_neutral_raw_context_payload() -> None:
    source = TECHNICAL_INDICATORS_SOURCE.read_text(encoding="utf-8")

    missing = [token for token in REQUIRED_NEUTRAL_COPY_TOKENS if token not in source]

    assert missing == []


def _extract_function_body(source: str, function_name: str) -> str:
    signature = f"function {function_name}"
    signature_index = source.index(signature)
    body_start = source.index("{", signature_index)
    depth = 0

    for index in range(body_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[body_start + 1:index]

    raise AssertionError(f"Could not find complete body for {function_name}")
