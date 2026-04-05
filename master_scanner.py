import yfinance as yf
import logging
from datetime import datetime
from sector_analyzer import SectorAnalyzer
from option_chain import OptionChainAnalyzer

logger = logging.getLogger(__name__)


class MasterScanner:
    """
    Full pipeline:
    1. Sector analysis → find trending/weak sectors
    2. Stock screening → find stocks near breakout with EMA + Volume confirmation
    3. Option chain → validate with OI, OI change, PCR
    4. Return combined signals for CE and PE
    """

    def __init__(self):
        self.sector_analyzer = SectorAnalyzer()
        self.oi_analyzer = OptionChainAnalyzer()

    def run_full_scan(self) -> dict:
        """
        Returns:
        {
          "market_context": {...},
          "ce_signals": [...],
          "pe_signals": [...],
          "sector_summary": [...],
          "scan_time": "...",
        }
        """
        result = {
            "ce_signals": [],
            "pe_signals": [],
            "market_context": {},
            "sector_summary": [],
            "scan_time": datetime.now().strftime("%d %b %Y, %H:%M"),
        }

        try:
            # ── Step 1: Sector Analysis ────────────────────────────────
            logger.info("Running sector analysis...")
            sector_data = self.sector_analyzer.analyze()

            result["market_context"] = {
                "nifty_change": sector_data["nifty_change"],
                "nifty_trend": sector_data["nifty_trend"],
                "top_sectors": [
                    {"name": s["name"], "change": s["change"]}
                    for s in sector_data["top_sectors"]
                ],
                "weak_sectors": [
                    {"name": s["name"], "change": s["change"]}
                    for s in sector_data["bottom_sectors"]
                ],
            }

            result["sector_summary"] = [
                {"name": s["name"], "change": s["change"]}
                for s in sector_data["sectors"]
            ]

            # ── Step 2 & 3: CE candidates ──────────────────────────────
            logger.info(f"Scanning {len(sector_data['watchlist_ce'])} CE candidate stocks...")
            for stock in sector_data["watchlist_ce"]:
                signal = self._build_signal(stock, "CE")
                if signal:
                    result["ce_signals"].append(signal)

            # ── Step 4 & 5: PE candidates ──────────────────────────────
            logger.info(f"Scanning {len(sector_data['watchlist_pe'])} PE candidate stocks...")
            for stock in sector_data["watchlist_pe"]:
                signal = self._build_signal(stock, "PE")
                if signal:
                    result["pe_signals"].append(signal)

            # ── Also scan Nifty & BankNifty index options ──────────────
            for index_sym, ticker in [("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK")]:
                spot = self._get_spot(ticker)
                if spot:
                    oi_data = self.oi_analyzer.analyze(index_sym, spot)
                    if oi_data and oi_data["oi_signal"] != "UNKNOWN":
                        if oi_data["oi_signal"] == "BULLISH":
                            result["ce_signals"].insert(0, self._index_signal(index_sym, spot, oi_data, "CE"))
                        elif oi_data["oi_signal"] == "BEARISH":
                            result["pe_signals"].insert(0, self._index_signal(index_sym, spot, oi_data, "PE"))

            # Sort by final score
            result["ce_signals"].sort(key=lambda x: x.get("final_score", 0), reverse=True)
            result["pe_signals"].sort(key=lambda x: x.get("final_score", 0), reverse=True)

            # Cap at top 5 each
            result["ce_signals"] = result["ce_signals"][:5]
            result["pe_signals"] = result["pe_signals"][:5]

        except Exception as e:
            logger.error(f"Master scan error: {e}")

        return result

    def _build_signal(self, stock: dict, signal_type: str) -> dict | None:
        """Build a full signal combining stock analysis + option chain."""
        try:
            symbol = stock["symbol"]
            spot = stock["ltp"]

            # Fetch option chain
            oi_data = self.oi_analyzer.analyze(symbol, spot)

            # Score the option chain data
            oi_score = 0
            oi_notes = []

            if oi_data and oi_data["source"] != "UNAVAILABLE":
                pcr = oi_data.get("pcr", 1.0)
                oi_signal = oi_data.get("oi_signal", "NEUTRAL")

                if signal_type == "CE":
                    if oi_signal == "BULLISH":
                        oi_score += 3
                        oi_notes.append("PCR bullish")
                    if oi_data.get("strong_support_walls"):
                        oi_score += 2
                        oi_notes.append(f"Strong PE wall at ₹{oi_data['strong_support_walls'][0]['strike']}")
                    if oi_data.get("top_ce_oi_change"):
                        top_change = oi_data["top_ce_oi_change"][0]
                        if top_change.get("oi_change_pct", 0) > 25:
                            oi_score += 2
                            oi_notes.append(f"CE OI buildup +{top_change['oi_change_pct']:.0f}%")
                else:
                    if oi_signal == "BEARISH":
                        oi_score += 3
                        oi_notes.append("PCR bearish")
                    if oi_data.get("strong_resistance_walls"):
                        oi_score += 2
                        oi_notes.append(f"Strong CE wall at ₹{oi_data['strong_resistance_walls'][0]['strike']}")
                    if oi_data.get("top_pe_oi_change"):
                        top_change = oi_data["top_pe_oi_change"][0]
                        if top_change.get("oi_change_pct", 0) > 25:
                            oi_score += 2
                            oi_notes.append(f"PE OI buildup +{top_change['oi_change_pct']:.0f}%")

            final_score = stock["score"] + oi_score

            # Build EMA label
            if stock["above_ema9"] and stock["above_ema15"]:
                ema_status = "Above 9EMA & 15EMA ✅"
            elif stock["above_ema9"]:
                ema_status = "Above 9EMA only 🔶"
            elif stock["above_ema15"]:
                ema_status = "Above 15EMA only 🔶"
            else:
                ema_status = "Below both EMAs 🔴"

            # Breakout status
            if signal_type == "CE":
                if stock["already_broke_up"]:
                    breakout_status = f"✅ Broke above resistance ₹{stock['resistance']}"
                elif stock["near_breakout_up"]:
                    breakout_status = f"⚡ Near resistance ₹{stock['resistance']} ({stock['dist_to_resistance']:.1f}% away)"
                else:
                    breakout_status = f"Resistance at ₹{stock['resistance']}"
            else:
                if stock["already_broke_down"]:
                    breakout_status = f"✅ Broke below support ₹{stock['support']}"
                elif stock["near_breakout_down"]:
                    breakout_status = f"⚡ Near support ₹{stock['support']} ({stock['dist_to_support']:.1f}% away)"
                else:
                    breakout_status = f"Support at ₹{stock['support']}"

            return {
                "symbol": symbol,
                "sector": stock["sector"],
                "signal_type": signal_type,
                "ltp": stock["ltp"],
                "ema9": stock["ema9"],
                "ema15": stock["ema15"],
                "ema_status": ema_status,
                "vol_ratio": stock["vol_ratio"],
                "breakout_status": breakout_status,
                "oi_data": oi_data,
                "oi_notes": oi_notes,
                "stock_score": stock["score"],
                "oi_score": oi_score,
                "final_score": final_score,
                "pcr": oi_data.get("pcr", 0) if oi_data else 0,
                "oi_signal": oi_data.get("oi_signal", "N/A") if oi_data else "N/A",
            }

        except Exception as e:
            logger.error(f"Signal build error for {stock.get('symbol')}: {e}")
            return None

    def _index_signal(self, symbol: str, spot: float, oi_data: dict, signal_type: str) -> dict:
        """Build signal for index (Nifty/BankNifty)."""
        walls = oi_data.get("strong_support_walls" if signal_type == "CE" else "strong_resistance_walls", [])
        wall_note = f"Wall at ₹{walls[0]['strike']}" if walls else ""

        return {
            "symbol": symbol,
            "sector": "INDEX",
            "signal_type": signal_type,
            "ltp": spot,
            "ema9": 0,
            "ema15": 0,
            "ema_status": "Index — see chart",
            "vol_ratio": 0,
            "breakout_status": wall_note,
            "oi_data": oi_data,
            "oi_notes": [f"PCR {oi_data['pcr']}", f"OI signal: {oi_data['oi_signal']}"],
            "stock_score": 0,
            "oi_score": 5,
            "final_score": 5,
            "pcr": oi_data.get("pcr", 0),
            "oi_signal": oi_data.get("oi_signal", "N/A"),
        }

    def _get_spot(self, ticker_symbol: str) -> float | None:
        try:
            t = yf.Ticker(ticker_symbol)
            hist = t.history(period="1d", interval="5m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except:
            return None
