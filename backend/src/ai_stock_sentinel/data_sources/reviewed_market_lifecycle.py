"""Reviewed official events, replayable without scraping or inferring missing prices.

This is a deliberately bounded evidence register, not a complete security master.
New entries require a dated official source; unknown symbols retain normal errors.
"""
from dataclasses import asdict, dataclass
from datetime import date


EVIDENCE_VERSION = "reviewed-taiwan-market-lifecycle-v1"


@dataclass(frozen=True)
class ListingEvidence:
    symbol: str
    listing_date: date
    evidence_date: date
    source_url: str

    def payload(self):
        return _payload(self)


@dataclass(frozen=True)
class TerminationEvidence:
    symbol: str
    published_date: date
    trading_stop_date: date
    termination_date: date
    source_url: str

    def payload(self):
        return _payload(self)


def _payload(evidence):
    return {"evidence_version": EVIDENCE_VERSION, **{
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in asdict(evidence).items()
    }}


# TPEx announcement 11500054131: share conversion, not a temporary halt.
_TERMINATIONS = (TerminationEvidence(
    symbol="8183.TWO", published_date=date(2026, 9, 3),
    trading_stop_date=date(2026, 9, 23), termination_date=date(2026, 10, 1),
    source_url="https://www.tpex.org.tw/storage/eb_data/11509/11500054131.html",
),)

# TPEx company profile dated 1151021: SecuritiesCompanyCode=7856,
# DateOfListing=20260922. Do not use a current profile for an earlier cutoff.
_LISTINGS = (ListingEvidence(
    symbol="7856.TWO", listing_date=date(2026, 9, 22), evidence_date=date(2026, 10, 21),
    source_url="https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
),)


def listing_evidence(symbol: str, *, as_of_date: date) -> ListingEvidence | None:
    return next((event for event in _LISTINGS if event.symbol == symbol
                 and event.listing_date <= event.evidence_date <= as_of_date), None)


def termination_evidence(symbol: str, *, as_of_date: date) -> TerminationEvidence | None:
    return next((event for event in _TERMINATIONS if event.symbol == symbol
                 and event.published_date <= event.trading_stop_date <= event.termination_date <= as_of_date), None)
