from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json


@dataclass(frozen=True, slots=True)
class InstitutionalFlowRow:
    symbol: str
    market: str
    name: str | None
    trade_date: date
    foreign_net_shares: int
    investment_trust_net_shares: int
    dealer_net_shares: int
    total_net_shares: int
    row_origin: str = "reported"


@dataclass(frozen=True, slots=True)
class InstitutionalReport:
    market: str
    trade_date: date
    source_provider: str
    source_dataset: str
    rows: tuple[InstitutionalFlowRow, ...]

    @property
    def payload_hash(self) -> str:
        payload = {
            "market": self.market,
            "rows": [
                {
                    "dealer_net_shares": row.dealer_net_shares,
                    "foreign_net_shares": row.foreign_net_shares,
                    "investment_trust_net_shares": row.investment_trust_net_shares,
                    "market": row.market,
                    "name": row.name,
                    "row_origin": row.row_origin,
                    "symbol": row.symbol,
                    "total_net_shares": row.total_net_shares,
                    "trade_date": row.trade_date.isoformat(),
                }
                for row in sorted(self.rows, key=lambda item: item.symbol)
            ],
            "source_dataset": self.source_dataset,
            "source_provider": self.source_provider,
            "trade_date": self.trade_date.isoformat(),
        }
        normalized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()


__all__ = ["InstitutionalFlowRow", "InstitutionalReport"]
