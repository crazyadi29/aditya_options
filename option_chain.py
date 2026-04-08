import requests
import pandas as pd
import numpy as np
import logging
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")

logger = logging.getLogger(__name__)

# NSE option chain API headers (required to avoid 403)
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Accept": "*/*",
    "Connection": "keep-alive",
}

# OI thresholds
OI_ABSOLUTE_THRESHOLD  = 1_000_000   # 10 lakh contracts
OI_CHANGE_PCT_THRESHOLD = 25         # 25% change in OI


class OptionChainAnalyzer:
    """
    Fetches NSE option chain for a symbol and analyzes:
    - Highest OI strikes (support/resistance walls)
    - Largest OI Change strikes (fresh buildup)
    - Put-Call Ratio (PCR)
    - CE vs PE OI bias
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(NSE_HEADERS)
        self._init_nse_session()

    def _init_nse_session(self):
        """Hit NSE homepage first to get cookies."""
        try:
            self.session.get("https://www.nseindia.com", timeout=10)
        except Exception as e:
            logger.warning(f"NSE session init failed: {e}")

    def analyze(self, symbol: str, spot_price: float) -> dict | None:
        """
        Fetch and analyze option chain for a symbol.
        symbol: "NIFTY", "BANKNIFTY", or stock name like "RELIANCE"
        """
        try:
            raw = self._fetch_option_chain(symbol)
            if not raw:
                return self._fallback_analysis(symbol, spot_price)

            return self._process_chain(raw, symbol, spot_price)

        except Exception as e:
            logger.error(f"Option chain error for {symbol}: {e}")
            return self._fallback_analysis(symbol, spot_price)

    def _fetch_option_chain(self, symbol: str) -> dict | None:
        """Fetch option chain from NSE API."""
        try:
            if symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
                url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
            else:
                url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"

            response = self.session.get(url, timeout=15)
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"NSE API returned {response.status_code} for {symbol}")
                return None
        except Exception as e:
            logger.debug(f"NSE fetch failed for {symbol}: {e}")
            return None

    def _process_chain(self, raw: dict, symbol: str, spot_price: float) -> dict:
        """Process raw option chain data into signals."""
        try:
            records = raw.get("records", {}).get("data", [])
            expiry_dates = raw.get("records", {}).get("expiryDates", [])
            near_expiry = expiry_dates[0] if expiry_dates else None

            ce_data = []
            pe_data = []

            for record in records:
                strike = record.get("strikePrice", 0)

                # Only look at near-expiry data
                if near_expiry and record.get("expiryDate") != near_expiry:
                    continue

                if "CE" in record:
                    ce = record["CE"]
                    ce_data.append({
                        "strike": strike,
                        "oi": ce.get("openInterest", 0),
                        "oi_change": ce.get("changeinOpenInterest", 0),
                        "oi_change_pct": self._safe_pct(ce.get("changeinOpenInterest", 0), ce.get("openInterest", 0)),
                        "volume": ce.get("totalTradedVolume", 0),
                        "ltp": ce.get("lastPrice", 0),
                        "iv": ce.get("impliedVolatility", 0),
                    })

                if "PE" in record:
                    pe = record["PE"]
                    pe_data.append({
                        "strike": strike,
                        "oi": pe.get("openInterest", 0),
                        "oi_change": pe.get("changeinOpenInterest", 0),
                        "oi_change_pct": self._safe_pct(pe.get("changeinOpenInterest", 0), pe.get("openInterest", 0)),
                        "volume": pe.get("totalTradedVolume", 0),
                        "ltp": pe.get("lastPrice", 0),
                        "iv": pe.get("impliedVolatility", 0),
                    })

            if not ce_data or not pe_data:
                return self._fallback_analysis(symbol, spot_price)

            ce_df = pd.DataFrame(ce_data)
            pe_df = pd.DataFrame(pe_data)

            # ── Total OI & PCR ─────────────────────────────────────────
            total_ce_oi = ce_df["oi"].sum()
            total_pe_oi = pe_df["oi"].sum()
            pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

            # ── Max Pain ───────────────────────────────────────────────
            max_pain_strike = self._calculate_max_pain(ce_df, pe_df)

            # ── Resistance (highest CE OI = call writing wall) ─────────
            top_ce_oi = ce_df.nlargest(3, "oi")[["strike", "oi", "oi_change", "oi_change_pct"]].to_dict("records")
            # ── Support (highest PE OI = put writing wall) ─────────────
            top_pe_oi = pe_df.nlargest(3, "oi")[["strike", "oi", "oi_change", "oi_change_pct"]].to_dict("records")

            # ── Biggest OI change (fresh buildup) ─────────────────────
            top_ce_change = ce_df[ce_df["oi_change"] > 0].nlargest(3, "oi_change")[["strike","oi","oi_change","oi_change_pct"]].to_dict("records")
            top_pe_change = pe_df[pe_df["oi_change"] > 0].nlargest(3, "oi_change")[["strike","oi","oi_change","oi_change_pct"]].to_dict("records")

            # ── ATM strikes ────────────────────────────────────────────
            atm_ce = ce_df.iloc[(ce_df["strike"] - spot_price).abs().argsort()[:1]].to_dict("records")
            atm_pe = pe_df.iloc[(pe_df["strike"] - spot_price).abs().argsort()[:1]].to_dict("records")

            # ── Signal direction ───────────────────────────────────────
            # PCR > 1.2: bullish (more put writing = floor support)
            # PCR < 0.8: bearish (more call writing = ceiling resistance)
            if pcr > 1.2:
                oi_signal = "BULLISH"
            elif pcr < 0.8:
                oi_signal = "BEARISH"
            else:
                oi_signal = "NEUTRAL"

            # ── Flag large OI walls ────────────────────────────────────
            strong_resistance = [r for r in top_ce_oi if r["oi"] >= OI_ABSOLUTE_THRESHOLD or abs(r["oi_change_pct"]) >= OI_CHANGE_PCT_THRESHOLD]
            strong_support    = [r for r in top_pe_oi if r["oi"] >= OI_ABSOLUTE_THRESHOLD or abs(r["oi_change_pct"]) >= OI_CHANGE_PCT_THRESHOLD]

            return {
                "symbol": symbol,
                "spot": spot_price,
                "expiry": near_expiry,
                "pcr": round(pcr, 2),
                "oi_signal": oi_signal,
                "max_pain": max_pain_strike,
                "total_ce_oi": int(total_ce_oi),
                "total_pe_oi": int(total_pe_oi),
                "top_ce_oi_strikes": top_ce_oi,
                "top_pe_oi_strikes": top_pe_oi,
                "top_ce_oi_change": top_ce_change,
                "top_pe_oi_change": top_pe_change,
                "atm_ce": atm_ce[0] if atm_ce else {},
                "atm_pe": atm_pe[0] if atm_pe else {},
                "strong_resistance_walls": strong_resistance,
                "strong_support_walls": strong_support,
                "source": "NSE_LIVE",
            }

        except Exception as e:
            logger.error(f"Chain processing error: {e}")
            return self._fallback_analysis(symbol, spot_price)

    def _calculate_max_pain(self, ce_df: pd.DataFrame, pe_df: pd.DataFrame) -> float:
        """Calculate max pain strike price."""
        try:
            strikes = sorted(set(ce_df["strike"].tolist() + pe_df["strike"].tolist()))
            min_pain = float("inf")
            max_pain_strike = strikes[len(strikes)//2]

            for s in strikes:
                ce_pain = ce_df[ce_df["strike"] <= s]["oi"].sum() * 0  # simplified
                pe_pain = pe_df[pe_df["strike"] >= s]["oi"].sum() * 0
                # Proper max pain calculation
                ce_loss = sum(max(0, s - row["strike"]) * row["oi"] for _, row in ce_df.iterrows())
                pe_loss = sum(max(0, row["strike"] - s) * row["oi"] for _, row in pe_df.iterrows())
                total = ce_loss + pe_loss
                if total < min_pain:
                    min_pain = total
                    max_pain_strike = s

            return max_pain_strike
        except:
            return 0

    def _safe_pct(self, change: float, base: float) -> float:
        if base == 0:
            return 0.0
        return round((change / base) * 100, 2)

    def _fallback_analysis(self, symbol: str, spot_price: float) -> dict:
        """Return a minimal structure when NSE API is unavailable."""
        return {
            "symbol": symbol,
            "spot": spot_price,
            "expiry": "N/A",
            "pcr": 0.0,
            "oi_signal": "UNKNOWN",
            "max_pain": 0,
            "total_ce_oi": 0,
            "total_pe_oi": 0,
            "top_ce_oi_strikes": [],
            "top_pe_oi_strikes": [],
            "top_ce_oi_change": [],
            "top_pe_oi_change": [],
            "atm_ce": {},
            "atm_pe": {},
            "strong_resistance_walls": [],
            "strong_support_walls": [],
            "source": "UNAVAILABLE",
        }
