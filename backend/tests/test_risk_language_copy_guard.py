from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRIMARY_SURFACE_FILES = [
    ROOT / "frontend/src/pages/DailyRadarPage.tsx",
    ROOT / "frontend/src/pages/AnalyzePage.tsx",
    *sorted((ROOT / "frontend/src/components/daily-radar").glob("*.tsx")),
    ROOT / "frontend/src/features/daily-radar/presentation.ts",
]
RAW_CODE_GUARD_FILES = [
    *PRIMARY_SURFACE_FILES,
    ROOT / "frontend/src/components/TechnicalIndicatorsPanel.tsx",
    ROOT / "frontend/src/lib/technicalIndicators.ts",
]

COMMAND_LANGUAGE_TERMS = [
    "建議買",
    "建議賣",
    "買進",
    "賣出",
    "加碼",
    "減碼",
    "出場",
    "必買",
    "目標價",
    "勝率",
    "操作建議",
    "交易建議",
    "投資建議",
]

ALLOWLISTED_PRIMARY_COPY: dict[str, dict[str, list[str]]] = {
    "frontend/src/components/daily-radar/CandidateList.tsx": {
        "勝率": ['<p className="mt-1 text-xs text-text-muted">依系統內部排序排列；排序不代表勝率或交易建議。</p>'],
        "交易建議": ['<p className="mt-1 text-xs text-text-muted">依系統內部排序排列；排序不代表勝率或交易建議。</p>'],
    },

}

FORBIDDEN_EXACT_PRIMARY_COPY = [
    "操作建議",
    "新倉策略建議",
    "出場警示",
    "續抱／減碼／出場指令",
    "加碼記錄",
    "出場 / 結案",
    "建議部位規模",
    "停損位",
    "預設停損規則",
    "加碼條件",
    "確認加碼",
    "出場視角",
    "出場檢討",
    "出場批次",
    "加碼次數",
    "破位後賣出比例",
    "操作時間線",
    "交易檢討結論",
    "下次操作規則",
]


def test_primary_frontend_surfaces_use_risk_language_with_allowlist() -> None:
    hits: list[str] = []
    for path in PRIMARY_SURFACE_FILES:
        text = path.read_text(encoding="utf-8")
        relative_path = str(path.relative_to(ROOT))
        for phrase in FORBIDDEN_EXACT_PRIMARY_COPY:
            if phrase in text:
                hits.append(f"{relative_path}: {phrase}")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for term in COMMAND_LANGUAGE_TERMS:
                if term not in line:
                    continue
                if _is_allowlisted(relative_path, term, line):
                    continue
                hits.append(f"{relative_path}:{line_number}: {term}: {line.strip()}")

    assert hits == []


def test_copy_guard_allowlist_documents_intent() -> None:
    for relative_path, terms in ALLOWLISTED_PRIMARY_COPY.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        for allowed_lines in terms.values():
            assert all(line in text for line in allowed_lines)


def test_copy_guard_allowlist_does_not_allow_broad_command_phrases() -> None:
    path = "frontend/src/components/daily-radar/CandidateList.tsx"
    assert not _is_allowlisted(path, "勝率", "系統保證勝率")
    assert not _is_allowlisted(path, "交易建議", "交易建議：立即買進")


def test_deprecated_compatibility_fields_are_marked_secondary_in_specs() -> None:
    backend_spec = (ROOT / "docs/specs/backend-api-technical-spec.md").read_text(encoding="utf-8")
    position_spec = (ROOT / "docs/specs/ai-stock-sentinel-position-diagnosis-spec.md").read_text(encoding="utf-8")

    assert "command_language_deprecated" in backend_spec
    assert "legacy/internal compatibility" in backend_spec
    assert "不得作為 primary user-facing copy" in backend_spec
    assert "command_language_deprecated" in position_spec
    assert "legacy/internal compatibility" in position_spec


def test_primary_frontend_surfaces_expose_risk_language_copy() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in PRIMARY_SURFACE_FILES)

    for phrase in [
        "風險狀態",
        "紀律觸發",
        "觀察條件",
        "風險控制參考",
        "相容欄位（secondary）",
    ]:
        assert phrase in combined


def test_primary_surfaces_do_not_render_internal_codes_as_fallback_copy() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in RAW_CODE_GUARD_FILES)
    forbidden_snippets = [
        "TECHNICAL_LABELS[kind][value]?.label ?? value",
        "PHASE1_ANCHOR_LABEL[key] ?? key",
        "REVIEW_DIMENSION_STATUS_LABEL[dimension.status] ?? dimension.status",
        "state.split(\"_\").join(\" \")",
        "firstError.code",
        "firstError.message",
        "${label.missing_reason}",
        "matched_rules.slice",
        "REPEAT_STATUS_LABEL[candidate.repeat_status]",
        "POSITION_EVENT_SOURCE_LABEL[timelineEvent.source]",
        "LIFECYCLE_PROVENANCE_LABEL[provenance]",
        "`其他資料項目（${value}）`",
        "`其他事件（${value}）`",
    ]

    assert [snippet for snippet in forbidden_snippets if snippet in combined] == []
    for readable_fallback in ["其他狀態", "其他技術訊號", "其他 AVWAP 觀察線"]:
        assert readable_fallback in combined


def _is_allowlisted(relative_path: str, term: str, line: str) -> bool:
    snippets = ALLOWLISTED_PRIMARY_COPY.get(relative_path, {}).get(term, [])
    if line.strip() in snippets:
        return True
    if relative_path == "frontend/src/pages/PortfolioPage.tsx" and term == "投資建議":
        return any(snippet in line for snippet in snippets)
    return False
