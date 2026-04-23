"""
main.py — FastAPI backend for the NSE F&O dashboard.

Endpoints
---------
GET  /                          health check
GET  /api/market/trend          overall market trend + top/weak sectors
GET  /api/market/sectors        all sector performances
GET  /api/sector/{sector}/stocks  watchlist stocks for one sector
GET  /api/chart/{symbol}        OHLCV + EMA chart data (via yfinance)
GET  /api/option-chain/{symbol} full option chain for a symbol

The Telegram bot runs separately via `python bot.py`.
"""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from logic.market_logic import (
    SECTORS,
    get_market_trend,
    get_sector_performances,
    analyze_sector_stocks,
    get_chart_data,
)
from logic.nse_scraper import scraper

log = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ── App ───────────────────────────────────────────────────────

app = FastAPI(
    title="NSE F&O Dashboard API",
    description="Market trend, sector performance, and option chain data for NSE F&O.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────

@app.get("/", tags=["health"])
def health():
    """Health check — confirms the API is running."""
    return {"status": "ok", "service": "NSE F&O Dashboard API"}


@app.get("/api/market/trend", tags=["market"])
def market_trend():
    """
    Overall market trend derived from sector performances.

    Returns BULLISH / BEARISH / NEUTRAL plus the top green and top red sectors.
    """
    try:
        return get_market_trend()
    except Exception as exc:
        log.error(f"/api/market/trend error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch market trend")


@app.get("/api/market/sectors", tags=["market"])
def sector_performances():
    """
    All tracked NSE sector indices with their current % change.

    Sorted best → worst.
    """
    try:
        return {"sectors": get_sector_performances()}
    except Exception as exc:
        log.error(f"/api/market/sectors error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch sector performances")


@app.get("/api/sector/{sector}/stocks", tags=["sector"])
def sector_stocks(sector: str, trend: str = "BULLISH"):
    """
    Watchlist stocks for a given sector.

    - **sector**: NSE index name, e.g. `NIFTY IT` (URL-encode spaces as `%20`)
    - **trend**: `BULLISH` (default) returns top gainers; `BEARISH` returns top losers
    """
    if sector not in SECTORS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown sector '{sector}'. Valid sectors: {SECTORS}",
        )
    try:
        stocks = analyze_sector_stocks(sector, trend)
        return {"sector": sector, "trend": trend, "stocks": stocks}
    except Exception as exc:
        log.error(f"/api/sector/{sector}/stocks error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch sector stocks")


@app.get("/api/chart/{symbol}", tags=["chart"])
def chart_data(symbol: str):
    """
    Daily OHLCV data for *symbol* (NSE equity) with EMA-7 and EMA-21 columns.

    Data is sourced from yfinance using the `.NS` suffix.
    """
    data = get_chart_data(symbol.upper())
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No chart data found for symbol '{symbol}'. "
                   "Ensure it is a valid NSE equity symbol.",
        )
    return data


@app.get("/api/option-chain/{symbol}", tags=["options"])
def option_chain(symbol: str):
    """
    Full option chain for *symbol* filtered to the current monthly expiry.

    Returns CE and PE LTP, OI, OI change, and volume for every available strike.
    """
    data = scraper.get_option_chain(symbol.upper())
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No option chain data found for symbol '{symbol}'. "
                   "Ensure it is a valid NSE F&O symbol.",
        )
    return data
