"""
technicals.py — Indicators used by the signal engine.

Indicators:
  • EMA (any period)
  • EMA Crossover  — 7 crosses 21
  • Volume SMA     — current volume vs 20-period average
  • Supertrend     — ATR-based trend filter (period=10, mult=3.0)
  • 5-min candle aggregation from 1-min NSE data
"""

from datetime import datetime
from typing import List, Dict, Optional, Tuple
import pytz

IST = pytz.timezone("Asia/Kolkata")


# ═══════════════════════════════════════════════════════════════
# EMA / SMA
# ═══════════════════════════════════════════════════════════════

def calc_ema(prices: List[float], period: int) -> List[Optional[float]]:
    """Full EMA series — same length as prices. First (period-1) = None."""
    if len(prices) < period:
        return [None] * len(prices)
    k    = 2.0 / (period + 1)
    seed = sum(prices[:period]) / period
    out  = [None] * (period - 1) + [seed]
    for p in prices[period:]:
        out.append(p * k + out[-1] * (1.0 - k))
    return out


def latest_ema(prices: List[float], period: int) -> Optional[float]:
    """Latest EMA value, or None if insufficient data."""
    s = calc_ema(prices, period)
    r = [v for v in s if v is not None]
    return r[-1] if r else None


def prev_ema(prices: List[float], period: int) -> Optional[float]:
    """Second-to-last EMA value (candle before the latest)."""
    s = calc_ema(prices, period)
    r = [v for v in s if v is not None]
    return r[-2] if len(r) >= 2 else None


def calc_volume_sma(volumes: List[float], period: int = 20) -> Optional[float]:
    """Simple average of last `period` volume bars."""
    if len(volumes) < period:
        return None
    return sum(volumes[-period:]) / period


# ═══════════════════════════════════════════════════════════════
# EMA CROSSOVER  (7 / 21)
# ═══════════════════════════════════════════════════════════════

def ema_crossover_signal(
    closes: List[float],
    fast:   int = 7,
    slow:   int = 21,
) -> str:
    """
    Returns:
      'BUY'     — price above both EMAs AND fast just crossed above slow
      'SELL'    — price below both EMAs AND fast just crossed below slow
      'BULL'    — price above both EMAs, continuation (no fresh cross)
      'BEAR'    — price below both EMAs, continuation (no fresh cross)
      'NEUTRAL' — mixed / insufficient data
    """
    if len(closes) < slow + 2:
        return "NEUTRAL"

    price     = closes[-1]
    fast_now  = latest_ema(closes, fast)
    fast_prev = prev_ema(closes, fast)
    slow_now  = latest_ema(closes, slow)
    slow_prev = prev_ema(closes, slow)

    if None in (fast_now, fast_prev, slow_now, slow_prev):
        return "NEUTRAL"

    above_both    = price > fast_now  and price > slow_now
    below_both    = price < fast_now  and price < slow_now
    bullish_cross = (fast_prev <= slow_prev) and (fast_now > slow_now)
    bearish_cross = (fast_prev >= slow_prev) and (fast_now < slow_now)

    if above_both and bullish_cross:  return "BUY"
    if below_both and bearish_cross:  return "SELL"
    if above_both:                    return "BULL"
    if below_both:                    return "BEAR"
    return "NEUTRAL"


# ═══════════════════════════════════════════════════════════════
# SUPERTREND
# ═══════════════════════════════════════════════════════════════

def calc_supertrend(
    candles:    List[Dict],
    period:     int   = 10,
    multiplier: float = 3.0,
) -> Tuple[List[Optional[float]], List[Optional[str]]]:
    """
    Classic Supertrend indicator.

    Returns:
      (supertrend_values, directions)
      direction per candle: 'UP' (bullish) | 'DOWN' (bearish) | None
    """
    n = len(candles)
    if n < period + 1:
        return [None] * n, [None] * n

    # True Range
    tr = []
    for i, c in enumerate(candles):
        if i == 0:
            tr.append(c["high"] - c["low"])
        else:
            pc = candles[i - 1]["close"]
            tr.append(max(
                c["high"] - c["low"],
                abs(c["high"] - pc),
                abs(c["low"]  - pc),
            ))

    # ATR — Wilder smoothing (RMA)
    atr = [None] * n
    atr[period - 1] = sum(tr[:period]) / period
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    # Basic bands
    upper_basic = [None] * n
    lower_basic = [None] * n
    for i in range(period - 1, n):
        hl2 = (candles[i]["high"] + candles[i]["low"]) / 2.0
        band = multiplier * atr[i]
        upper_basic[i] = hl2 + band
        lower_basic[i] = hl2 - band

    # Final bands + direction
    upper_final = [None] * n
    lower_final = [None] * n
    direction   = [None] * n

    for i in range(period - 1, n):
        if i == period - 1:
            upper_final[i] = upper_basic[i]
            lower_final[i] = lower_basic[i]
            direction[i]   = "UP"
            continue

        pu = upper_final[i - 1]
        pl = lower_final[i - 1]
        pc = candles[i - 1]["close"]
        cc = candles[i]["close"]

        upper_final[i] = upper_basic[i] if (upper_basic[i] < pu or pc > pu) else pu
        lower_final[i] = lower_basic[i] if (lower_basic[i] > pl or pc < pl) else pl

        pd = direction[i - 1]
        if   pd == "DOWN" and cc > upper_final[i]: direction[i] = "UP"
        elif pd == "UP"   and cc < lower_final[i]: direction[i] = "DOWN"
        elif cc > lower_final[i]:                  direction[i] = "UP"
        else:                                       direction[i] = "DOWN"

    # Return the relevant band (support when UP, resistance when DOWN)
    st_values = [
        lower_final[i] if direction[i] == "UP" else upper_final[i]
        for i in range(n)
    ]
    return st_values, direction


def latest_supertrend(
    candles: List[Dict], period: int = 10, multiplier: float = 3.0
) -> Tuple[Optional[float], Optional[str]]:
    """Returns (value, direction) for the latest candle."""
    vals, dirs = calc_supertrend(candles, period, multiplier)
    return (vals[-1], dirs[-1]) if vals else (None, None)


# ═══════════════════════════════════════════════════════════════
# 5-MIN CANDLE AGGREGATION
# ═══════════════════════════════════════════════════════════════

def aggregate_to_5min(raw: list) -> List[Dict]:
    """
    Convert 1-min NSE tick list → 5-min OHLCV candles.
    Accepted formats: [ts_ms, close] | [ts_ms,O,H,L,C] | [ts_ms,O,H,L,C,V]
    """
    if not raw:
        return []

    buckets: Dict[datetime, Dict] = {}
    for row in raw:
        if len(row) < 2:
            continue
        dt   = datetime.fromtimestamp(row[0] / 1000, tz=IST)
        bmin = (dt.minute // 5) * 5
        key  = dt.replace(minute=bmin, second=0, microsecond=0)

        if   len(row) == 2: o = h = l = c = float(row[1]); vol = 0.0
        elif len(row) == 5: o,h,l,c = (float(x) for x in row[1:5]); vol = 0.0
        else:               o,h,l,c = (float(x) for x in row[1:5]); vol = float(row[5])

        if key not in buckets:
            buckets[key] = {"time": key, "open": o, "high": h,
                            "low": l, "close": c, "volume": vol}
        else:
            b = buckets[key]
            b["high"]   = max(b["high"], h)
            b["low"]    = min(b["low"],  l)
            b["close"]  = c
            b["volume"] += vol

    return sorted(buckets.values(), key=lambda x: x["time"])


# ═══════════════════════════════════════════════════════════════
# CANDLE HELPERS
# ═══════════════════════════════════════════════════════════════

def candle_body_pct(c: Dict) -> float:
    if c["open"] == 0: return 0.0
    return (c["close"] - c["open"]) / c["open"] * 100.0


def candle_direction(c: Dict) -> str:
    body   = c["close"] - c["open"]
    range_ = c["high"]  - c["low"]
    if range_ == 0 or abs(body) / range_ < 0.3: return "doji"
    return "bullish" if body > 0 else "bearish"
