import yfinance as yf
import pandas as pd
import numpy as np
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ─── SECTOR DEFINITIONS ───────────────────────────────────────────────────────
# Each sector: Yahoo Finance ticker + constituent Nifty 50 stocks

SECTORS = {
    "IT":          {"index": "^CNXIT",    "stocks": ["TCS.NS","INFY.NS","WIPRO.NS","HCLTECH.NS","TECHM.NS","LTIM.NS","PERSISTENT.NS"]},
    "BANKING":     {"index": "^NSEBANK",  "stocks": ["HDFCBANK.NS","ICICIBANK.NS","SBIN.NS","KOTAKBANK.NS","AXISBANK.NS","INDUSINDBK.NS","BANKBARODA.NS"]},
    "AUTO":        {"index": "^CNXAUTO",  "stocks": ["MARUTI.NS","TATAMOTORS.NS","M&M.NS","BAJAJ-AUTO.NS","HEROMOTOCO.NS","EICHERMOT.NS"]},
    "PHARMA":      {"index": "^CNXPHARMA","stocks": ["SUNPHARMA.NS","DRREDDY.NS","CIPLA.NS","DIVISLAB.NS","APOLLOHOSP.NS","LUPIN.NS"]},
    "FMCG":        {"index": "^CNXFMCG",  "stocks": ["HINDUNILVR.NS","ITC.NS","NESTLEIND.NS","BRITANNIA.NS","DABUR.NS","MARICO.NS"]},
    "METAL":       {"index": "^CNXMETAL", "stocks": ["TATASTEEL.NS","JSWSTEEL.NS","HINDALCO.NS","COALINDIA.NS","VEDL.NS","NMDC.NS"]},
    "ENERGY":      {"index": "^CNXENERGY","stocks": ["RELIANCE.NS","ONGC.NS","NTPC.NS","POWERGRID.NS","BPCL.NS","IOC.NS"]},
    "REALTY":      {"index": "^CNXREALTY","stocks": ["DLF.NS","LODHA.NS","GODREJPROP.NS","OBEROIRLTY.NS","PRESTIGE.NS"]},
    "INFRA":       {"index": "^CNXINFRA", "stocks": ["LT.NS","ULTRACEMCO.NS","ADANIPORTS.NS","ADANIENT.NS","BHEL.NS"]},
    "FINANCE":     {"index": "^CNXFINANCE","stocks": ["BAJFINANCE.NS","BAJAJFINSV.NS","HDFCLIFE.NS","SBILIFE.NS","CHOLAFIN.NS","MUTHOOTFIN.NS"]},
}

NIFTY_INDEX = "^NSEI"


class SectorAnalyzer:
    """
    1. Checks if Nifty is positive or negative
    2. Ranks all sectors by % change
    3. Returns top performing (for CE) and most negative (for PE) sectors
    4. Returns individual stocks near breakout in those sectors
    """

    def analyze(self) -> dict:
        """Full sector analysis. Returns structured result."""
        result = {
            "nifty_change": 0.0,
            "nifty_trend": "NEUTRAL",
            "sectors": [],
            "top_sectors": [],       # CE candidates
            "bottom_sectors": [],    # PE candidates
            "watchlist_ce": [],      # stocks to watch for CE
            "watchlist_pe": [],      # stocks to watch for PE
        }

        try:
            # ── Step 1: Nifty direction ────────────────────────────────
            nifty_change = self._get_change(NIFTY_INDEX)
            result["nifty_change"] = round(nifty_change, 2)

            if nifty_change > 0.1:
                result["nifty_trend"] = "POSITIVE"
            elif nifty_change < -0.1:
                result["nifty_trend"] = "NEGATIVE"
            else:
                result["nifty_trend"] = "NEUTRAL"

            # ── Step 2: Rank all sectors ───────────────────────────────
            sector_data = []
            for name, config in SECTORS.items():
                chg = self._get_change(config["index"])
                sector_data.append({
                    "name": name,
                    "change": round(chg, 2),
                    "stocks": config["stocks"],
                })

            sector_data.sort(key=lambda x: x["change"], reverse=True)
            result["sectors"] = sector_data

            # ── Step 3: Pick CE and PE sectors ────────────────────────
            if result["nifty_trend"] in ("POSITIVE", "NEUTRAL"):
                # Top 3 performing sectors for CE
                result["top_sectors"] = [s for s in sector_data if s["change"] > 0][:3]
                # Most negative sector for PE
                result["bottom_sectors"] = [s for s in sector_data if s["change"] < 0][-2:]
            else:
                # Market negative: least negative for CE, most negative for PE
                positives = [s for s in sector_data if s["change"] >= 0]
                result["top_sectors"] = positives[:2] if positives else sector_data[:2]
                result["bottom_sectors"] = sector_data[-3:]

            # ── Step 4: Scan stocks in those sectors ──────────────────
            ce_stocks = []
            for sector in result["top_sectors"]:
                for stock in sector["stocks"]:
                    analysis = self._analyze_stock(stock, sector["name"])
                    if analysis:
                        ce_stocks.append(analysis)

            pe_stocks = []
            for sector in result["bottom_sectors"]:
                for stock in sector["stocks"]:
                    analysis = self._analyze_stock(stock, sector["name"], for_pe=True)
                    if analysis:
                        pe_stocks.append(analysis)

            # Sort by breakout score
            result["watchlist_ce"] = sorted(ce_stocks, key=lambda x: x["score"], reverse=True)[:6]
            result["watchlist_pe"] = sorted(pe_stocks, key=lambda x: x["score"], reverse=True)[:6]

        except Exception as e:
            logger.error(f"Sector analysis error: {e}")

        return result

    def _get_change(self, ticker_symbol: str) -> float:
        """Get today's % change for a ticker."""
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="2d", interval="1d")
            if len(hist) >= 2:
                prev_close = hist["Close"].iloc[-2]
                curr_close = hist["Close"].iloc[-1]
                return ((curr_close - prev_close) / prev_close) * 100
            # Try intraday
            hist = ticker.history(period="1d", interval="5m")
            if not hist.empty:
                info = ticker.fast_info
                prev = getattr(info, "previous_close", None) or hist["Close"].iloc[0]
                curr = hist["Close"].iloc[-1]
                return ((curr - prev) / prev) * 100
        except Exception as e:
            logger.debug(f"Change fetch failed {ticker_symbol}: {e}")
        return 0.0

    def _analyze_stock(self, ticker_symbol: str, sector: str, for_pe: bool = False) -> dict | None:
        """
        Analyze a stock for:
        - 9 EMA and 15 EMA position
        - 20 SMA Volume breakout
        - Price near breakout (within 1% of 20-bar high or low)
        Returns a score + all signals.
        """
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="30d", interval="15m")

            if hist.empty or len(hist) < 25:
                # Fallback to daily
                hist = ticker.history(period="60d", interval="1d")
                if hist.empty or len(hist) < 25:
                    return None

            close = hist["Close"]
            high  = hist["High"]
            low   = hist["Low"]
            vol   = hist["Volume"]

            # ── EMAs ──────────────────────────────────────────────────
            ema9  = close.ewm(span=9,  adjust=False).mean()
            ema15 = close.ewm(span=15, adjust=False).mean()
            sma20_vol = vol.rolling(20).mean()

            curr_price  = close.iloc[-1]
            curr_ema9   = ema9.iloc[-1]
            curr_ema15  = ema15.iloc[-1]
            curr_vol    = vol.iloc[-1]
            avg_vol     = sma20_vol.iloc[-1]

            above_ema9  = curr_price > curr_ema9
            above_ema15 = curr_price > curr_ema15
            vol_ratio   = curr_vol / avg_vol if avg_vol > 0 else 1.0

            # ── Breakout proximity ────────────────────────────────────
            resistance = high.iloc[-21:-1].max()
            support    = low.iloc[-21:-1].min()

            dist_to_resistance = ((resistance - curr_price) / curr_price) * 100
            dist_to_support    = ((curr_price - support)    / curr_price) * 100

            near_breakout_up   = 0 <= dist_to_resistance <= 1.5   # within 1.5% of resistance
            near_breakout_down = 0 <= dist_to_support    <= 1.5   # within 1.5% of support
            already_broke_up   = curr_price > resistance
            already_broke_down = curr_price < support

            # ── Score ─────────────────────────────────────────────────
            score = 0
            if for_pe:
                if not above_ema9:   score += 2
                if not above_ema15:  score += 2
                if near_breakout_down or already_broke_down: score += 3
                if vol_ratio >= 2.0: score += 2
                if vol_ratio >= 3.0: score += 1
                relevant = not above_ema9 or near_breakout_down or already_broke_down
            else:
                if above_ema9:   score += 2
                if above_ema15:  score += 2
                if near_breakout_up or already_broke_up: score += 3
                if vol_ratio >= 2.0: score += 2
                if vol_ratio >= 3.0: score += 1
                relevant = above_ema9 or near_breakout_up or already_broke_up

            if score < 3 or not relevant:
                return None

            name = ticker_symbol.replace(".NS", "")
            return {
                "symbol": name,
                "sector": sector,
                "ltp": round(curr_price, 2),
                "ema9": round(curr_ema9, 2),
                "ema15": round(curr_ema15, 2),
                "above_ema9": above_ema9,
                "above_ema15": above_ema15,
                "vol_ratio": round(vol_ratio, 2),
                "resistance": round(resistance, 2),
                "support": round(support, 2),
                "near_breakout_up": near_breakout_up,
                "near_breakout_down": near_breakout_down,
                "already_broke_up": already_broke_up,
                "already_broke_down": already_broke_down,
                "dist_to_resistance": round(dist_to_resistance, 2),
                "dist_to_support": round(dist_to_support, 2),
                "score": score,
                "for_pe": for_pe,
            }

        except Exception as e:
            logger.debug(f"Stock analysis failed {ticker_symbol}: {e}")
            return None
