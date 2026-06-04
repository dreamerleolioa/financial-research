export interface PortfolioItem {
  id: number;
  symbol: string;
  entry_price: number;
  quantity: number;
  entry_date: string;
  notes: string | null;
}

export interface ClosedPortfolioItem {
  id: number;
  symbol: string;
  entry_price: number;
  quantity: number;
  entry_date: string;
  is_active: boolean;
  exit_date: string;
  exit_price: number;
  exit_quantity: number;
  exit_fees: number;
  exit_taxes: number;
  realized_pnl: number;
  realized_return_pct: number;
  holding_days: number;
  notes: string | null;
}
