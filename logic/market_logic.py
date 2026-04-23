"""
logic/market_logic.py — Market data helpers for the FastAPI backend.

Wraps NSEClient + SectorEngine so main.py can import clean functions
without duplicating any scraping or analysis logic.

Public API
----------
SECTORS                          — list of all tracked NSE sector index names
get_market_trend()               — Nifty trend + top/weak sector summary
get_sector_performances()        — all sectors with their % change
analyze_sector_stocks(sector, trend) — watchlist stocks for one sector
get_chart_data(symbol)           — yfinance OHLCV + EMA columns
"""

import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# ── Sector list (mirrors config.SECTOR_INDICES keys) ─────────────────────────

SECTORS: List[str] = [
    "NIFTY IT",
    "NIFTY BANK",
    "NIFTY AUTO",
    "NIFTY PHARMA",
    "NIFTY FMCG",
    "NIFTY METAL",
    "NIFTY REALTY",
    "NIFTY ENERGY",
    "NIFTY FINANCIAL SERVICES",
    "NIFTY MEDIA",
    "NIFTY CONSUMER DURABLES",
    "NIFTY OIL AND GAS",
    "NIFTY PSU BANK",
    "NIFTY PRIVATE BANK",
]

# ── Lazy-initialised shared instances ────────────────────────────────────────
# We create one NSEClient + SectorEngine per process and reuse them across
# requests so we don't hammer NSE with repeated cookie refreshes.

_nse    = None
_sector = None


def _get_engines():
    """Return (nse_client, sector_engine), initialising on first call."""
    global _nse, _sector
    if _nse is None:
        from nse_client    import NSEClient
        from sector_engine import SectorEngine
        _nse    = NSEClient()
        _sector = SectorEngine(_nse)
        try:
            _nse.refresh_cookies()
        except Exception as exc:
            log.warning(f"NSE cookie refresh failed at startup: {exc}")
    return _nse, _sector


# ── Public functions ──────────────────────────────────────────────────────────

def get_market_trend() -> Dict:
    """
    Return a summary of the current market trend.

    Returns
    -------
    {
        "trend":        "BULLISH" | "BEARISH" | "NEUTRAL",
        "top_sectors":  [{"index": str, "label": str, "change_pct": float}, ...],
        "weak_sectors": [{"index": str, "label": str, "change_pct": float}, ...],
    }
    """
    _, sector_eng = _get_engines()

    try:
        sectors  = sector_eng.analyse()
        trending = sector_eng.get_trending(sectors)
    except Exception as exc:
        log.error(f"get_market_trend failed: {exc}")
        return {"trend": "NEUTRAL", "top_sectors": [], "weak_sectors": []}

    green = trending.get("most_green", [])
    red   = trending.get("most_red",   [])

    if len(green) > len(red):
        trend = "BULLISH"
    elif len(red) > len(green):
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    def _slim(sectors_list):
        return [
            {
                "index":      s["index"],
                "label":      s["label"],
                "change_pct": s["change_pct"],
            }
            for s in sectors_list
        ]

    return {
        "trend":        trend,
        "top_sectors":  _slim(green),
        "weak_sectors": _slim(red),
    }


def get_sector_performances() -> List[Dict]:
    """
    Return all tracked sectors with their current % change.

    Returns
    -------
    [
        {"index": str, "label": str, "change_pct": float, "last": float},
        ...
    ]
    Sorted best → worst by change_pct.
    """
    _, sector_eng = _get_engines()

    try:
        sectors = sector_eng.analyse()
    except Exception as exc:
        log.error(f"get_sector_performances failed: {exc}")
        return []

    return [
        {
            "index":      s["index"],
            "label":      s["label"],
            "change_pct": s["change_pct"],
            "last":       s.get("last", 0.0),
        }
        for s in sectors
    ]


def analyze_sector_stocks(sector: str, trend: str) -> List[Dict]:
    """
    Return watchlist stocks for a given sector and market trend direction.

    Parameters
    ----------
    sector : str
        NSE index name, e.g. "NIFTY IT"
    trend  : str
        "BULLISH" → return top gainers; anything else → return top losers

    Returns
    -------
    [
        {"symbol": str, "name": str, "change_pct": float, "last": float, "bias": str},
        ...
    ]
    """
    nse, _ = _get_engines()

    try:
        data = nse.get_sector_data(sector)
    except Exception as exc:
        log.error(f"analyze_sector_stocks NSE fetch failed [{sector}]: {exc}")
        return []

    if not data:
        return []

    rows = data.get("data", [])
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

    if trend.upper() == "BULLISH":
        stocks.sort(key=lambda x: x["change_pct"], reverse=True)
        bias = "LONG"
    else:
        stocks.sort(key=lambda x: x["change_pct"])
        bias = "SHORT"

    for s in stocks:
        s["bias"] = bias

    return stocks[:10]   # top 10 candidates


def get_chart_data(symbol: str) -> Optional[Dict]:
    """
    Fetch recent OHLCV data for *symbol* via yfinance and attach EMA columns.

    Uses the ".NS" suffix for NSE-listed equities.

    Returns
    -------
    {
        "symbol": str,
        "dates":  [str, ...],          # ISO date strings
        "open":   [float, ...],
        "high":   [float, ...],
        "low":    [float, ...],
        "close":  [float, ...],
        "volume": [int, ...],
        "ema7":   [float | None, ...],
        "ema21":  [float | None, ...],
    }
    or None on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance is not installed — add it to requirements.txt")
        return None

    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        df     = ticker.history(period="3mo", interval="1d")
    except Exception as exc:
        log.error(f"get_chart_data yfinance fetch failed [{symbol}]: {exc}")
        return None

    if df is None or df.empty:
        log.warning(f"get_chart_data: no data returned for {symbol}")
        return None

    from technicals import calc_ema

    closes = df["Close"].tolist()
    ema7   = calc_ema(closes, 7)
    ema21  = calc_ema(closes, 21)

    return {
        "symbol": symbol,
        "dates":  [d.strftime("%Y-%m-%d") for d in df.index],
        "open":   [round(v, 2) for v in df["Open"].tolist()],
        "high":   [round(v, 2) for v in df["High"].tolist()],
        "low":    [round(v, 2) for v in df["Low"].tolist()],
        "close":  [round(v, 2) for v in closes],
        "volume": [int(v) for v in df["Volume"].tolist()],
        "ema7":   [round(v, 2) if v is not None else None for v in ema7],
        "ema21":  [round(v, 2) if v is not None else None for v in ema21],
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_float(val) -> float:
    try:
        if isinstance(val, str):
            val = val.replace(",", "").replace("%", "").strip()
            if val in ("", "-"):
                return 0.0
        return float(val)
    except (TypeError, ValueError):
        return 0.0
