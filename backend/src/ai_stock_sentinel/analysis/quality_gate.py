"""News summary quality gate.

Provides rule-based checks for:
- NQ-1: Title quality (timestamp / URL / source-code titles)
- NQ-2: Date normalization (ISO 8601, RFC 2822 → YYYY-MM-DD)
- NQ-3: mentioned_numbers denoising (filter date fragments, keep financial values)
- NQ-4: Quality score (0–100, rule-based deductions)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class QualityResult:
    """Generic result carrying flags and an optional value."""

    flags: list[str] = field(default_factory=list)
    # title check result
    # date normalization result
    date: str = ""
    # number filter result
    numbers: list[str] = field(default_factory=list)
    # quality score result
    quality_score: int = 100
    quality_flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# RFC 2822 / HTTP-date: "Mon, 03 Mar 2026 08:00:00" (optional timezone)
_RFC2822_RE = re.compile(
    r"^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}"
    r"\s+\d{2}:\d{2}:\d{2}",
    re.IGNORECASE,
)

# Pure ISO date (YYYY-MM-DD only, no surrounding text)
_ISO_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Pure URL
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

# Ticker / source code: all uppercase+digits+dot, e.g. "2330.TW", "AAPL", "SPX"
_SOURCE_CODE_RE = re.compile(r"^[A-Z0-9]{1,6}(?:\.[A-Z]{2,5})?$")

# Year-like 4-digit number (1900–2099) with no financial context
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")

# Single-digit standalone integer (no %, no $, no comma, no decimal) — likely month/day (1-9)
# Note: 2-digit integers (10-99) may carry financial meaning (PE, ROE, etc.) and are kept.
_SMALL_INT_RE = re.compile(r"^\d$")

# Financial value patterns
_PERCENT_RE = re.compile(r"%")
_DOLLAR_RE = re.compile(r"^\$")
_COMMA_RE = re.compile(r",")
_DECIMAL_RE = re.compile(r"\.\d")
_QUARTER_RE = re.compile(r"^Q[1-4]$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# QualityGate
# ---------------------------------------------------------------------------


class QualityGate:
    """Static methods for each quality dimension."""

    # ------------------------------------------------------------------ NQ-1
    @staticmethod
    def check_title(title: str) -> QualityResult:
        """Return QualityResult with TITLE_LOW_QUALITY if title is low quality."""
        result = QualityResult()
        stripped = title.strip()

        if not stripped:
            result.flags.append("TITLE_LOW_QUALITY")
            return result

        if _RFC2822_RE.match(stripped):
            result.flags.append("TITLE_LOW_QUALITY")
            return result

        if _ISO_DATE_ONLY_RE.match(stripped):
            result.flags.append("TITLE_LOW_QUALITY")
            return result

        if _URL_RE.match(stripped):
            result.flags.append("TITLE_LOW_QUALITY")
            return result

        if _SOURCE_CODE_RE.match(stripped):
            result.flags.append("TITLE_LOW_QUALITY")
            return result

        return result

    # ------------------------------------------------------------------ NQ-2
    @staticmethod
    def normalize_date(raw_date: str) -> QualityResult:
        """Normalize date to ISO 8601 (YYYY-MM-DD).

        - Accepts ISO 8601 and ISO datetime (strips time part)
        - Accepts RFC 2822 and converts to ISO
        - Anything else → "unknown" + DATE_UNKNOWN flag
        """
        result = QualityResult()
        stripped = raw_date.strip()

        if not stripped or stripped.lower() == "unknown":
            result.date = "unknown"
            result.flags.append("DATE_UNKNOWN")
            return result

        # ISO 8601 date only
        if _ISO_DATE_ONLY_RE.match(stripped):
            result.date = stripped
            return result

        # ISO 8601 datetime (e.g. 2026-03-03T08:00:00+00:00)
        iso_dt_match = re.match(r"^(\d{4}-\d{2}-\d{2})T", stripped)
        if iso_dt_match:
            result.date = iso_dt_match.group(1)
            return result

        # RFC 2822
        if _RFC2822_RE.match(stripped):
            try:
                dt = parsedate_to_datetime(stripped)
                result.date = dt.strftime("%Y-%m-%d")
                return result
            except Exception:
                pass

        result.date = "unknown"
        result.flags.append("DATE_UNKNOWN")
        return result

    # ------------------------------------------------------------------ NQ-3
    @staticmethod
    def filter_numbers(numbers: list[str]) -> QualityResult:
        """Filter date fragments from mentioned_numbers, keep financial values.

        Financial value = contains %, $, comma, decimal, or is Q1-Q4.
        """
        result = QualityResult()
        kept: list[str] = []

        for value in numbers:
            v = value.strip()
            # Quarter notation
            if _QUARTER_RE.match(v):
                kept.append(value)
                continue
            # Contains financial markers
            if (
                _PERCENT_RE.search(v)
                or _DOLLAR_RE.match(v)
                or _COMMA_RE.search(v)
                or _DECIMAL_RE.search(v)
            ):
                kept.append(value)
                continue
            # Year fragment → discard
            if _YEAR_RE.match(v):
                continue
            # Small integer (potential month/day) → discard
            if _SMALL_INT_RE.match(v):
                continue
            # Large integer without comma (could be price like 850 or noise)
            # Keep if >= 10 and not a year (already handled above)
            try:
                numeric = float(v.replace(",", ""))
                if numeric >= 10:
                    kept.append(value)
            except ValueError:
                # Non-numeric string: keep it (could be text label)
                kept.append(value)

        result.numbers = kept
        if not kept:
            result.flags.append("NO_FINANCIAL_NUMBERS")
        return result

    # ------------------------------------------------------------------ NQ-4
    @staticmethod
    def compute_quality(flags: list[str]) -> QualityResult:
        """Compute quality_score (0–100) based on accumulated flags."""
        _DEDUCTIONS: dict[str, int] = {
            "TITLE_LOW_QUALITY": 35,
            "DATE_UNKNOWN": 15,
            "NO_FINANCIAL_NUMBERS": 20,
        }
        score = 100
        for flag in flags:
            score -= _DEDUCTIONS.get(flag, 0)

        result = QualityResult()
        result.quality_score = max(0, min(100, score))
        result.quality_flags = list(flags)
        return result
