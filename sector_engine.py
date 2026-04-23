"""
sector_engine.py — Phase 1 of the strategy.

After first 15 min of market (9:15–9:30):
  • Fetch all tracked sector performances from NSE
  • Extract top gainers + top losers for each sector
  • Classify trending: most_green / least_green / most_red / least_red
  • Build watchlists:
      LONG  = top 3 gainers from each of top 3 green sectors  (9 stocks)
      SHORT = top 3 losers  from each of top 3 red   sectors  (9 stocks)
"""

import logging
from typing import Dict, List, Tuple

from config import SECTOR_INDICES, TOP_N_SECTORS

log = logging.getLogger(__name__)

# How many gainers/losers to pick per sector
GAINERS_PER_SECTOR = 3


class SectorEngine:

    def __init__(self, nse_client):
        self.nse = nse_client

    # ── Public ────────────────────────────────────────────────────

    def analyse(self) -> List[Dict]:
        """
        Fetch all sector data from NSE.
        Each sector dict includes:
          change_pct  — actual % change (not points!)
          top_gainers — top 5 stocks by positive % change
          top_losers  — top 5 stocks by negative % change
        """
        results = []
        for index_name, label in SECTOR_INDICES.items():
            data = self.nse.get_sector_data(index_name)
            if not data:
                log.warning(f"No data for {index_name}")
                continue

            meta       = data.get("metadata", {})
            change_pct = _extract_pct(meta)            # ← FIXED
            last       = _safe_float(meta.get("last", 0))
            all_stocks = self._extract_all_stocks(data.get("data", []))

            # Sorted lists (not just top weight)
            sorted_by_chg = sorted(all_stocks, key=lambda x: x["change_pct"], reverse=True)
            top_gainers   = sorted_by_chg[:5]                     # best %
            top_losers    = sorted_by_chg[-5:][::-1]              # worst % (most negative first)

            results.append({
                "index":       index_name,
                "label":       label,
                "change_pct":  change_pct,
                "last":        last,
                "top_gainers": top_gainers,
                "top_losers":  top_losers,
            })
            log.info(f"  {label}: {change_pct:+.2f}%  ({len(all_stocks)} stocks)")

        return sorted(results, key=lambda x: x["change_pct"], reverse=True)

    def get_trending(self, sectors: List[Dict]) -> Dict:
        green = [s for s in sectors if s["change_pct"] > 0]
        red   = [s for s in sectors if s["change_pct"] < 0]
        return {
            "most_green":  green[:TOP_N_SECTORS],
            "least_green": green[-1:] if green else [],
            "most_red":    red[-TOP_N_SECTORS:],
            "least_red":   red[:1]    if red   else [],
        }

    def build_watchlist(self, trending: Dict) -> Tuple[List[Dict], List[Dict]]:
        """
        LONG  = top 3 gainers from each of the top 3 green sectors  → ~9 stocks
        SHORT = top 3 losers  from each of the top 3 red   sectors  → ~9 stocks
        De-duplicated by symbol.
        """
        seen         = set()
        long_stocks  = []
        short_stocks = []

        # LONG candidates — top gainers of top green sectors
        for sector in trending.get("most_green", []):
            for st in sector.get("top_gainers", [])[:GAINERS_PER_SECTOR]:
                if st["symbol"] in seen:
                    continue
                seen.add(st["symbol"])
                long_stocks.append({**st, "bias": "LONG", "sector": sector["label"]})

        # SHORT candidates — top losers of top red sectors
        for sector in trending.get("most_red", []):
            for st in sector.get("top_losers", [])[:GAINERS_PER_SECTOR]:
                if st["symbol"] in seen:
                    continue
                seen.add(st["symbol"])
                short_stocks.append({**st, "bias": "SHORT", "sector": sector["label"]})

        log.info(f"Watchlist → LONG: {len(long_stocks)}, SHORT: {len(short_stocks)}")
        return long_stocks, short_stocks

    # ── Private ───────────────────────────────────────────────────

    def _extract_all_stocks(self, rows: list) -> List[Dict]:
        stocks = []
        for row in rows:
            sym = row.get("symbol", "")
            if not sym or sym.startswith("NIFTY") or sym.startswith("SENSEX"):
                continue
            stocks.append({
                "symbol":     sym,
                "name":       row.get("meta", {}).get("companyName", sym),
                "change_pct": _safe_float(row.get("pChange",   0)),
                "last":       _safe_float(row.get("lastPrice", 0)),
            })
        return stocks


# ── Helpers ──────────────────────────────────────────────────────

def _extract_pct(meta: dict) -> float:
    """
    NSE returns % change under different keys in different endpoints.
    Priority: percentChange → percChange → pChange → calculated from last/previousClose
    NEVER use 'change' alone — that's absolute points, not percentage.
    """
    for key in ("percentChange", "percChange", "pChange"):
        if key in meta and meta[key] not in (None, "", "-"):
            return _safe_float(meta[key])

    # Fallback: calculate from last + previousClose
    last = _safe_float(meta.get("last",           0))
    prev = _safe_float(meta.get("previousClose",  0))
    if prev > 0:
        return (last - prev) / prev * 100.0
    return 0.0


def _safe_float(val) -> float:
    try:
        if isinstance(val, str):
            val = val.replace(",", "").replace("%", "").strip()
            if val in ("", "-"):
                return 0.0
        return float(val)
    except (TypeError, ValueError):
        return 0.0
