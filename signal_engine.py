"""
signal_engine.py — Stock + Option condition checks.

3 CONDITIONS FOR STOCK (all must pass):
  ✅ Volume > 20-period Volume SMA
  ✅ Supertrend (7,3) = UP (long) or DOWN (short)
  ✅ EMA 7/21 crossover:
       BULLISH — 7 crosses 21 from below, while price is ABOVE supertrend
       BEARISH — 7 crosses 21 from above, while price is BELOW supertrend
       Continuation also valid (price above both EMAs + above ST = bull, etc.)

SAME 3 CONDITIONS ON OPTION LTP HISTORY:
  → If option also passes all 3 → include option name in alert
  → If option does not pass    → still alert for stock, option line omitted
"""

import logging
from typing import Dict, List, Optional, Tuple

from technicals import (
    aggregate_to_5min,
    latest_ema, prev_ema,
    calc_volume_sma,
    latest_supertrend, calc_supertrend,
    candle_direction, candle_body_pct,
)
from config import (
    EMA_FAST, EMA_SLOW,
    VOLUME_SMA_PERIOD,
    SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER,
)

log = logging.getLogger(__name__)


class SignalEngine:

    def __init__(self, nse_client, option_engine):
        self.nse = nse_client
        self.opt = option_engine

    # ── Public ────────────────────────────────────────────────────

    def check(self, stock: Dict) -> Optional[Dict]:
        """
        Run 3-condition check on stock.
        If passes, also fetch option data + check option conditions.
        Returns signal dict or None.
        """
        symbol = stock["symbol"]
        bias   = stock["bias"]

        # ── Fetch 5-min candles ───────────────────────────────────
        candles = self._fetch_candles(symbol)
        if len(candles) < EMA_SLOW + 2:
            log.debug(f"{symbol}: Only {len(candles)} candles")
            return None

        closes  = [c["close"]  for c in candles]
        volumes = [c["volume"] for c in candles]
        price   = closes[-1]

        # ── Run 3 conditions ─────────────────────────────────────
        passes, reason, indicators = self._three_conditions(
            closes, volumes, candles, bias
        )
        if not passes:
            log.debug(f"{symbol}: FAIL — {reason}")
            return None

        # ── Fetch all option candidates (ATM + top OTM) ───────────
        opt_group = self.opt.get_candidates(symbol, price, bias)

        # ── Check each candidate; collect passers ────────────────
        passing_options = []  # list of dicts for options that pass
        all_candidates  = []  # list of all candidates (for display even if fail)

        if opt_group:
            side = opt_group["side"]
            for cand in opt_group["candidates"]:
                entry = {
                    "name":   f"{symbol}{cand['strike']}{side}",
                    "strike": cand["strike"],
                    "side":   side,
                    "ltp":    cand["ltp"],
                    "volume": cand["volume"],
                }
                all_candidates.append(entry)

                passes, reason = self.opt.check_conditions(
                    symbol, cand["strike"], side, bias
                )
                if passes:
                    passing_options.append(entry)
                    log.info(f"  ✅ Option {entry['name']} PASSES")
                else:
                    log.debug(f"  ✗ {entry['name']}: {reason}")

        c1 = candles[0] if len(candles) >= 1 else None
        c2 = candles[1] if len(candles) >= 2 else None

        signal = {
            "symbol":      symbol,
            "name":        stock.get("name", symbol),
            "sector":      stock.get("sector", ""),
            "bias":        bias,
            "price":       price,
            "ema7":        indicators["ema7"],
            "ema21":       indicators["ema21"],
            "ema_sig":     indicators["ema_sig"],
            "vol_curr":    indicators["vol_curr"],
            "vol_sma":     indicators["vol_sma"],
            "st_val":      indicators["st_val"],
            "st_dir":      indicators["st_dir"],
            "c1_dir":      candle_direction(c1) if c1 else "—",
            "c1_move":     abs(candle_body_pct(c1)) if c1 else 0.0,
            "c2_dir":      candle_direction(c2) if c2 else "—",
            "c2_move":     abs(candle_body_pct(c2)) if c2 else 0.0,
            "option_atm":       opt_group["atm"] if opt_group else None,
            "option_side":      opt_group["side"] if opt_group else None,
            "options_passing":  passing_options,   # list of option dicts that passed
            "options_all":      all_candidates,    # for reference display
        }
        log.info(
            f"✅ SIGNAL → {symbol} {bias} | EMA:{indicators['ema_sig']} | "
            f"ST:{indicators['st_dir']} | Vol:{indicators['vol_curr']:.0f}>"
            f"{indicators['vol_sma']:.0f} | Opts passing: {len(passing_options)}"
        )
        return signal

    # ── 3-Condition Check ─────────────────────────────────────────

    def _three_conditions(
        self,
        closes:  List[float],
        volumes: List[float],
        candles: List[Dict],
        bias:    str,
    ) -> Tuple[bool, str, Dict]:
        """
        Returns (passes, fail_reason, indicators_dict).
        """
        price = closes[-1]

        # ── 1. Volume > 20 SMA ────────────────────────────────────
        vol_sma  = calc_volume_sma(volumes, VOLUME_SMA_PERIOD) or 0
        curr_vol = volumes[-1]
        if vol_sma > 0 and curr_vol <= vol_sma:
            return False, f"vol {curr_vol:.0f} ≤ SMA {vol_sma:.0f}", {}

        # ── 2. Supertrend (7,3) ───────────────────────────────────
        st_val, st_dir = latest_supertrend(candles, SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER)
        if st_dir is None:
            return False, "supertrend N/A", {}
        if bias == "LONG"  and st_dir != "UP":
            return False, f"ST={st_dir} need UP", {}
        if bias == "SHORT" and st_dir != "DOWN":
            return False, f"ST={st_dir} need DOWN", {}

        # ── 3. EMA 7/21 crossover vs supertrend ──────────────────
        ema7_now   = latest_ema(closes, EMA_FAST)
        ema7_prev  = prev_ema(closes,   EMA_FAST)
        ema21_now  = latest_ema(closes, EMA_SLOW)
        ema21_prev = prev_ema(closes,   EMA_SLOW)

        if None in (ema7_now, ema7_prev, ema21_now, ema21_prev, st_val):
            return False, "EMA data insufficient", {}

        above_st   = price > st_val
        below_st   = price < st_val
        above_both = price > ema7_now  and price > ema21_now
        below_both = price < ema7_now  and price < ema21_now

        bullish_cross = (ema7_prev <= ema21_prev) and (ema7_now > ema21_now)
        bearish_cross = (ema7_prev >= ema21_prev) and (ema7_now < ema21_now)

        if bias == "LONG":
            # Fresh cross above ST OR continuation (price above both EMAs and above ST)
            ema_ok = (bullish_cross and above_st) or (above_both and above_st)
            ema_sig = "BUY✅"  if bullish_cross and above_st else \
                      "BULL✅" if above_both    and above_st else "NEUTRAL"
        else:
            ema_ok = (bearish_cross and below_st) or (below_both and below_st)
            ema_sig = "SELL✅"  if bearish_cross and below_st else \
                      "BEAR✅"  if below_both    and below_st else "NEUTRAL"

        if not ema_ok:
            return False, f"EMA/ST fail — ema7:{ema7_now:.1f} ema21:{ema21_now:.1f} ST:{st_val:.1f}", {}

        indicators = {
            "ema7":     ema7_now,
            "ema21":    ema21_now,
            "ema_sig":  ema_sig,
            "vol_curr": curr_vol,
            "vol_sma":  vol_sma,
            "st_val":   st_val,
            "st_dir":   st_dir,
        }
        return True, "OK", indicators

    # ── Helpers ───────────────────────────────────────────────────

    def _fetch_candles(self, symbol: str) -> List[Dict]:
        raw = self.nse.get_intraday_chart(symbol)
        if not raw:
            return []
        graph = (
            raw.get("grapthData")
            or raw.get("graphData")
            or raw.get("data")
            or []
        )
        return aggregate_to_5min(graph)

    def reset(self):
        log.info("SignalEngine daily state reset")
