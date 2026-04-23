"""
option_engine.py — Option chain fetch + per-strike condition check.

NO writer / OI logic.

For each qualifying stock:
  • Find monthly expiry (last Thursday of current month)
  • Collect all CE (long bias) or PE (short bias) strikes with valid LTP
  • Track running LTP+volume history for each option across scans
  • Apply SAME 3 conditions (volume > 20 SMA, supertrend, EMA 7/21 vs ST)
  • Return list of options that pass (could be 0, 1, several)
"""

import logging
import calendar
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from config import (
    SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER,
    EMA_FAST, EMA_SLOW, VOLUME_SMA_PERIOD,
)
from technicals import latest_ema, prev_ema, calc_volume_sma, calc_supertrend

log = logging.getLogger(__name__)


# ── Expiry Calculation ────────────────────────────────────────

def monthly_expiry(ref: date = None) -> str:
    if ref is None:
        ref = date.today()
    last_day = calendar.monthrange(ref.year, ref.month)[1]
    d = date(ref.year, ref.month, last_day)
    while d.weekday() != 3:
        d -= timedelta(days=1)
    if d < ref:
        if ref.month == 12:
            return monthly_expiry(date(ref.year + 1, 1, 1))
        return monthly_expiry(date(ref.year, ref.month + 1, 1))
    return d.strftime("%d-%b-%Y").upper()


# ── Engine ────────────────────────────────────────────────────

class OptionEngine:

    def __init__(self, nse_client):
        self.nse    = nse_client
        self.expiry = monthly_expiry()
        # "SYMBOL_STRIKE_SIDE" → [{ltp, volume}, ...]
        self._opt_history: Dict[str, List[Dict]] = {}
        log.info(f"Monthly expiry locked to: {self.expiry}")

    def reset(self):
        self._opt_history.clear()

    # ── Get all candidate options for this bias ───────────────────

    def get_candidates(self, symbol: str, spot: float, bias: str) -> Optional[Dict]:
        """
        Returns dict with:
          atm:        ATM strike
          side:       'CE' or 'PE'
          spot:       live spot price
          candidates: list of option rows with valid LTP (all strikes traded)
                      each row = {strike, ltp, volume}
        Also appends current values to each option's running history.
        """
        raw = self.nse.get_option_chain(symbol)
        if not raw:
            return None

        records    = raw.get("records", {})
        all_rows   = records.get("data", [])
        spot_price = records.get("underlyingValue", spot) or spot

        chain = [r for r in all_rows if r.get("expiryDate", "").upper() == self.expiry]
        if not chain:
            return None

        strikes = sorted({r["strikePrice"] for r in chain if "strikePrice" in r})
        if not strikes:
            return None

        atm  = min(strikes, key=lambda x: abs(x - spot_price))
        side = "CE" if bias == "LONG" else "PE"

        # For LONG: check ATM + all OTM CE (strikes >= ATM)
        # For SHORT: check ATM + all OTM PE (strikes <= ATM)
        if bias == "LONG":
            target_strikes = [s for s in strikes if s >= atm]
        else:
            target_strikes = [s for s in strikes if s <= atm]

        # Keep reasonable range — 10 strikes max from ATM outward
        if bias == "LONG":
            target_strikes = sorted(target_strikes)[:10]
        else:
            target_strikes = sorted(target_strikes, reverse=True)[:10]

        candidates = []
        for row in chain:
            sp = row.get("strikePrice")
            if sp not in target_strikes or side not in row:
                continue
            s   = row[side]
            ltp = float(s.get("lastPrice",       0) or 0)
            vol = int(s.get("totalTradedVolume", 0) or 0)
            if ltp <= 0:   # skip strikes with no trades
                continue
            candidates.append({"strike": sp, "ltp": ltp, "volume": vol})

        # Update running history for each candidate
        for cand in candidates:
            key = f"{symbol}_{cand['strike']}_{side}"
            self._opt_history.setdefault(key, []).append({
                "ltp":    cand["ltp"],
                "volume": cand["volume"],
            })

        return {
            "atm":        atm,
            "side":       side,
            "spot":       spot_price,
            "candidates": candidates,
        }

    # ── Check option conditions (per strike) ──────────────────────

    def check_conditions(
        self, symbol: str, strike: int, side: str, bias: str
    ) -> Tuple[bool, str]:
        """
        Apply the same 3 stock conditions to this option's LTP/volume history.
        Returns (passes, reason).
        """
        key  = f"{symbol}_{strike}_{side}"
        hist = self._opt_history.get(key, [])

        need = max(EMA_SLOW + 2, SUPERTREND_PERIOD + 2)
        if len(hist) < need:
            return False, f"need {need} pts, have {len(hist)}"

        ltps    = [h["ltp"]    for h in hist]
        volumes = [h["volume"] for h in hist]

        # ── 1. Volume > 20 SMA ────────────────────────────────────
        vol_sma  = calc_volume_sma(volumes, VOLUME_SMA_PERIOD)
        curr_vol = volumes[-1]
        if vol_sma is not None and curr_vol <= vol_sma:
            return False, f"vol {curr_vol:.0f} ≤ SMA {vol_sma:.0f}"

        # ── 2. Supertrend on pseudo-candles (H=L=O=C) ────────────
        pseudo = [
            {"open": p, "high": p, "low": p, "close": p, "volume": volumes[i]}
            for i, p in enumerate(ltps)
        ]
        st_vals, st_dirs = calc_supertrend(pseudo, SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER)
        st_val = st_vals[-1] if st_vals else None
        st_dir = st_dirs[-1] if st_dirs else None

        if st_dir is None:
            return False, "ST N/A"
        if bias == "LONG"  and st_dir != "UP":
            return False, f"ST={st_dir}"
        if bias == "SHORT" and st_dir != "DOWN":
            return False, f"ST={st_dir}"

        # ── 3. EMA 7/21 vs Supertrend ────────────────────────────
        ema7_now   = latest_ema(ltps, EMA_FAST)
        ema7_prev  = prev_ema(ltps,   EMA_FAST)
        ema21_now  = latest_ema(ltps, EMA_SLOW)
        ema21_prev = prev_ema(ltps,   EMA_SLOW)

        if None in (ema7_now, ema7_prev, ema21_now, ema21_prev, st_val):
            return False, "EMA/ST data insufficient"

        price         = ltps[-1]
        above_st      = price > st_val
        below_st      = price < st_val
        above_both    = price > ema7_now and price > ema21_now
        below_both    = price < ema7_now and price < ema21_now
        bullish_cross = (ema7_prev <= ema21_prev) and (ema7_now > ema21_now)
        bearish_cross = (ema7_prev >= ema21_prev) and (ema7_now < ema21_now)

        if bias == "LONG":
            ok = (bullish_cross or above_both) and above_st
        else:
            ok = (bearish_cross or below_both) and below_st

        if not ok:
            return False, "EMA/ST align fail"
        return True, "OK"
