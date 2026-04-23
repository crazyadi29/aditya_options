"""
logic/nse_scraper.py — Option chain scraper for the FastAPI backend.

Wraps NSEClient + OptionEngine so main.py can call:

    from logic.nse_scraper import scraper
    data = scraper.get_option_chain("RELIANCE")

Public API
----------
scraper.get_option_chain(symbol) — fetch and return structured option chain data
"""

import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


class _NSEScraper:
    """
    Thin facade over NSEClient that returns option chain data in a
    shape convenient for the FastAPI response layer.

    The underlying NSEClient and OptionEngine are initialised lazily on
    the first call so that importing this module never triggers network I/O.
    """

    def __init__(self):
        self._nse = None
        self._opt = None

    # ── Lazy init ─────────────────────────────────────────────────────────────

    def _ensure_ready(self):
        if self._nse is not None:
            return
        from nse_client    import NSEClient
        from option_engine import OptionEngine
        self._nse = NSEClient()
        self._opt = OptionEngine(self._nse)
        try:
            self._nse.refresh_cookies()
        except Exception as exc:
            log.warning(f"NSE cookie refresh failed at scraper init: {exc}")

    # ── Public ────────────────────────────────────────────────────────────────

    def get_option_chain(self, symbol: str) -> Optional[Dict]:
        """
        Fetch the full option chain for *symbol* from NSE and return a
        structured dict suitable for JSON serialisation.

        Parameters
        ----------
        symbol : str
            NSE equity symbol, e.g. "RELIANCE", "TCS", "NIFTY"

        Returns
        -------
        {
            "symbol":        str,
            "expiry":        str,          # monthly expiry locked by OptionEngine
            "underlying":    float,        # spot price
            "strikes":       [int, ...],   # sorted list of all available strikes
            "chain": [
                {
                    "strike":      int,
                    "ce_ltp":      float | None,
                    "ce_oi":       int   | None,
                    "ce_oi_chg":   int   | None,
                    "ce_volume":   int   | None,
                    "pe_ltp":      float | None,
                    "pe_oi":       int   | None,
                    "pe_oi_chg":   int   | None,
                    "pe_volume":   int   | None,
                },
                ...
            ],
        }
        or None if the fetch fails.
        """
        self._ensure_ready()

        raw = self._nse.get_option_chain(symbol)
        if not raw:
            log.warning(f"get_option_chain: no data from NSE for {symbol}")
            return None

        records    = raw.get("records", {})
        all_rows   = records.get("data", [])
        underlying = float(records.get("underlyingValue", 0) or 0)
        expiry     = self._opt.expiry

        # Filter to the monthly expiry we care about
        chain_rows = [
            r for r in all_rows
            if r.get("expiryDate", "").upper() == expiry
        ]

        # Build a strike → row mapping
        strike_map: Dict[int, Dict] = {}
        for row in chain_rows:
            strike = row.get("strikePrice")
            if strike is None:
                continue
            strike = int(strike)
            if strike not in strike_map:
                strike_map[strike] = row

        strikes = sorted(strike_map.keys())

        chain: List[Dict] = []
        for strike in strikes:
            row = strike_map[strike]
            ce  = row.get("CE", {}) or {}
            pe  = row.get("PE", {}) or {}
            chain.append({
                "strike":    strike,
                "ce_ltp":    _opt_float(ce.get("lastPrice")),
                "ce_oi":     _opt_int(ce.get("openInterest")),
                "ce_oi_chg": _opt_int(ce.get("changeinOpenInterest")),
                "ce_volume": _opt_int(ce.get("totalTradedVolume")),
                "pe_ltp":    _opt_float(pe.get("lastPrice")),
                "pe_oi":     _opt_int(pe.get("openInterest")),
                "pe_oi_chg": _opt_int(pe.get("changeinOpenInterest")),
                "pe_volume": _opt_int(pe.get("totalTradedVolume")),
            })

        return {
            "symbol":     symbol,
            "expiry":     expiry,
            "underlying": underlying,
            "strikes":    strikes,
            "chain":      chain,
        }


# ── Module-level singleton ────────────────────────────────────────────────────

scraper = _NSEScraper()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _opt_float(val) -> Optional[float]:
    if val is None or val == "-" or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _opt_int(val) -> Optional[int]:
    if val is None or val == "-" or val == "":
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None
