"""Tests for news quality gate (NQ-1 ~ NQ-4).

TDD: write failing tests first, then implement the quality_gate module.
"""
from __future__ import annotations

import pytest

from ai_stock_sentinel.analysis.quality_gate import QualityGate, QualityResult


# ---------------------------------------------------------------------------
# NQ-1: Title quality check
# ---------------------------------------------------------------------------


class TestTitleQualityCheck:
    def test_timestamp_title_is_flagged(self):
        result = QualityGate.check_title("Mon, 03 Mar 2026 08:00:00")
        assert "TITLE_LOW_QUALITY" in result.flags

    def test_rfc2822_timestamp_title_is_flagged(self):
        result = QualityGate.check_title("Mon, 03 Mar 2026 08:00:00 +0000")
        assert "TITLE_LOW_QUALITY" in result.flags

    def test_pure_url_title_is_flagged(self):
        result = QualityGate.check_title("https://example.com/news/12345")
        assert "TITLE_LOW_QUALITY" in result.flags

    def test_pure_source_code_title_is_flagged(self):
        result = QualityGate.check_title("2330.TW")
        assert "TITLE_LOW_QUALITY" in result.flags

    def test_normal_title_is_not_flagged(self):
        result = QualityGate.check_title("台積電 2 月營收年增 18.2%，創歷史新高")
        assert "TITLE_LOW_QUALITY" not in result.flags

    def test_english_news_title_is_not_flagged(self):
        result = QualityGate.check_title("TSMC Q1 revenue beats expectations by 12%")
        assert "TITLE_LOW_QUALITY" not in result.flags

    def test_empty_title_is_flagged(self):
        result = QualityGate.check_title("")
        assert "TITLE_LOW_QUALITY" in result.flags

    def test_iso_date_only_title_is_flagged(self):
        # Pure date like "2026-03-03" is not meaningful as a title
        result = QualityGate.check_title("2026-03-03")
        assert "TITLE_LOW_QUALITY" in result.flags


# ---------------------------------------------------------------------------
# NQ-2: Date normalization
# ---------------------------------------------------------------------------


class TestDateNormalization:
    def test_iso8601_date_is_accepted(self):
        result = QualityGate.normalize_date("2026-03-03")
        assert result.date == "2026-03-03"
        assert "DATE_UNKNOWN" not in result.flags

    def test_rfc2822_date_is_converted_to_iso(self):
        result = QualityGate.normalize_date("Mon, 03 Mar 2026 08:00:00 +0000")
        assert result.date == "2026-03-03"
        assert "DATE_UNKNOWN" not in result.flags

    def test_rfc2822_no_tz_date_is_converted(self):
        result = QualityGate.normalize_date("Mon, 03 Mar 2026 08:00:00")
        assert result.date == "2026-03-03"
        assert "DATE_UNKNOWN" not in result.flags

    def test_unknown_string_sets_flag(self):
        result = QualityGate.normalize_date("unknown")
        assert result.date == "unknown"
        assert "DATE_UNKNOWN" in result.flags

    def test_unparseable_string_sets_flag(self):
        result = QualityGate.normalize_date("not-a-date-at-all")
        assert result.date == "unknown"
        assert "DATE_UNKNOWN" in result.flags

    def test_empty_string_sets_flag(self):
        result = QualityGate.normalize_date("")
        assert result.date == "unknown"
        assert "DATE_UNKNOWN" in result.flags

    def test_iso_datetime_strips_time(self):
        result = QualityGate.normalize_date("2026-03-03T08:00:00+00:00")
        assert result.date == "2026-03-03"
        assert "DATE_UNKNOWN" not in result.flags


# ---------------------------------------------------------------------------
# NQ-3: mentioned_numbers denoising
# ---------------------------------------------------------------------------


class TestMentionedNumbersDenoising:
    def test_year_fragment_is_removed(self):
        result = QualityGate.filter_numbers(["2026", "18.2%", "2,600"])
        assert "2026" not in result.numbers

    def test_month_fragment_is_removed(self):
        # standalone small integer like "3" (month) with no financial context
        result = QualityGate.filter_numbers(["3", "12.5%"])
        assert "3" not in result.numbers

    def test_percentage_is_kept(self):
        result = QualityGate.filter_numbers(["18.2%", "2026"])
        assert "18.2%" in result.numbers

    def test_currency_amount_is_kept(self):
        result = QualityGate.filter_numbers(["$150", "2026", "03"])
        assert "$150" in result.numbers

    def test_comma_separated_amount_is_kept(self):
        result = QualityGate.filter_numbers(["2,600", "2026"])
        assert "2,600" in result.numbers

    def test_eps_like_decimal_is_kept(self):
        result = QualityGate.filter_numbers(["5.83", "2026", "3"])
        assert "5.83" in result.numbers

    def test_no_financial_numbers_flag_when_empty_after_filter(self):
        result = QualityGate.filter_numbers(["2026", "03", "2025"])
        assert "NO_FINANCIAL_NUMBERS" in result.flags

    def test_no_flag_when_financial_numbers_present(self):
        result = QualityGate.filter_numbers(["2026", "18.2%"])
        assert "NO_FINANCIAL_NUMBERS" not in result.flags

    def test_quarter_notation_is_kept(self):
        result = QualityGate.filter_numbers(["Q1", "2026"])
        assert "Q1" in result.numbers

    def test_empty_list_triggers_flag(self):
        result = QualityGate.filter_numbers([])
        assert "NO_FINANCIAL_NUMBERS" in result.flags

    def test_two_digit_integer_is_kept(self):
        # PE ratio 25, ROE 32, etc. — 2-digit integers without financial markers
        # should NOT be filtered (they carry financial meaning)
        result = QualityGate.filter_numbers(["25", "50", "99"])
        assert "25" in result.numbers
        assert "50" in result.numbers

    def test_single_digit_is_discarded(self):
        # Single digits (1-9) are likely month/day fragments
        result = QualityGate.filter_numbers(["3", "18.2%"])
        assert "3" not in result.numbers


# ---------------------------------------------------------------------------
# NQ-4: Quality score
# ---------------------------------------------------------------------------


class TestQualityScore:
    def test_no_flags_gives_full_score(self):
        result = QualityGate.compute_quality(flags=[])
        assert result.quality_score == 100

    def test_title_low_quality_deducts_35(self):
        result = QualityGate.compute_quality(flags=["TITLE_LOW_QUALITY"])
        assert result.quality_score == 65

    def test_date_unknown_deducts_15(self):
        result = QualityGate.compute_quality(flags=["DATE_UNKNOWN"])
        assert result.quality_score == 85

    def test_no_financial_numbers_deducts_20(self):
        result = QualityGate.compute_quality(flags=["NO_FINANCIAL_NUMBERS"])
        assert result.quality_score == 80

    def test_all_three_flags_give_score_30(self):
        result = QualityGate.compute_quality(
            flags=["TITLE_LOW_QUALITY", "DATE_UNKNOWN", "NO_FINANCIAL_NUMBERS"]
        )
        # 100 - 35 - 15 - 20 = 30
        assert result.quality_score == 30

    def test_score_never_goes_below_zero(self):
        # Feed many flags (hypothetical future flags) — score must clamp at 0
        many_flags = ["TITLE_LOW_QUALITY", "DATE_UNKNOWN", "NO_FINANCIAL_NUMBERS"] * 5
        result = QualityGate.compute_quality(flags=many_flags)
        assert result.quality_score >= 0

    def test_score_never_exceeds_100(self):
        result = QualityGate.compute_quality(flags=[])
        assert result.quality_score <= 100

    def test_quality_result_contains_flags(self):
        flags = ["DATE_UNKNOWN"]
        result = QualityGate.compute_quality(flags=flags)
        assert result.quality_flags == flags


# ---------------------------------------------------------------------------
# S-4: Pipeline integration — all four gate methods in sequence
# ---------------------------------------------------------------------------


class TestQualityGatePipeline:
    """Simulate what quality_gate_node does: run all four checks, aggregate flags."""

    def _run_pipeline(self, title: str, date: str, numbers: list[str]) -> dict:
        flags: list[str] = []
        flags.extend(QualityGate.check_title(title).flags)
        flags.extend(QualityGate.normalize_date(date).flags)
        flags.extend(QualityGate.filter_numbers(numbers).flags)
        score_result = QualityGate.compute_quality(flags)
        return {
            "quality_score": score_result.quality_score,
            "quality_flags": score_result.quality_flags,
        }

    def test_high_quality_news_scores_100(self):
        result = self._run_pipeline(
            title="台積電 Q1 EPS 5.83 元，年增 15.2%",
            date="2026-03-03",
            numbers=["5.83", "15.2%", "2,600"],
        )
        assert result["quality_score"] == 100
        assert result["quality_flags"] == []

    def test_timestamp_title_and_unknown_date_score_50(self):
        result = self._run_pipeline(
            title="Mon, 03 Mar 2026 08:00:00",
            date="unknown",
            numbers=["18.2%"],
        )
        # -35 (TITLE) -15 (DATE) = 50
        assert result["quality_score"] == 50
        assert "TITLE_LOW_QUALITY" in result["quality_flags"]
        assert "DATE_UNKNOWN" in result["quality_flags"]

    def test_all_three_flags_score_30(self):
        result = self._run_pipeline(
            title="Mon, 03 Mar 2026 08:00:00",
            date="unknown",
            numbers=["2026", "03"],
        )
        # -35 -15 -20 = 30
        assert result["quality_score"] == 30
        assert len(result["quality_flags"]) == 3

    def test_rfc2822_date_is_normalized_no_flag(self):
        result = self._run_pipeline(
            title="台積電 2 月營收創高",
            date="Mon, 03 Mar 2026 08:00:00 +0000",
            numbers=["18.2%"],
        )
        assert "DATE_UNKNOWN" not in result["quality_flags"]
        assert result["quality_score"] == 100
