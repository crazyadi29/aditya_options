"""
╔══════════════════════════════════════════════════════════════╗
║         NSE F&O Auto-Alert Bot  —  config.py                 ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ───────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID   = os.getenv("CHAT_ID",   "")

# ── Strategy Parameters ────────────────────────────────────────
EMA_FAST              = 7      # 7 EMA (fast)
EMA_SLOW              = 21     # 21 EMA (slow)
VOLUME_SMA_PERIOD     = 20     # Volume must be > 20-period Volume SMA
MIN_CANDLE_MOVE_PCT   = 0.4    # Min % body move in first 2 candles
OI_CHANGE_MIN_PCT     = 5.0    # Min % OI change to count as buildup
TOP_N_SECTORS         = 3      # Top N green / red sectors
STOCKS_PER_SECTOR     = 4      # First N stocks per trending sector
TOP_OTM_WRITERS       = 3      # ATM + top 3 OTM strikes by OI

# ── Supertrend Parameters ──────────────────────────────────────
SUPERTREND_PERIOD     = 7      # ATR lookback period
SUPERTREND_MULTIPLIER = 3.0    # ATR multiplier

# ── Market Timing (IST) ────────────────────────────────────────
SECTOR_ANALYSIS_AT   = (9, 25)   # Run after first 2 candles form
STOCK_SCAN_FROM      = (9, 30)   # Start scanning stocks
MARKET_CLOSE         = (15, 25)  # Stop scanning

# ── NSE Sectors → Labels ───────────────────────────────────────
SECTOR_INDICES = {
    "NIFTY IT":                   "💻 IT",
    "NIFTY BANK":                 "🏦 Bank",
    "NIFTY AUTO":                 "🚗 Auto",
    "NIFTY PHARMA":               "💊 Pharma",
    "NIFTY FMCG":                 "🛒 FMCG",
    "NIFTY METAL":                "⚙️ Metal",
    "NIFTY REALTY":               "🏠 Realty",
    "NIFTY ENERGY":               "⚡ Energy",
    "NIFTY FINANCIAL SERVICES":   "💰 Fin.Svcs",
    "NIFTY MEDIA":                "📺 Media",
    "NIFTY CONSUMER DURABLES":    "📦 Cons.Dur",
    "NIFTY OIL AND GAS":          "🛢️ Oil&Gas",
    "NIFTY PSU BANK":             "🏛️ PSU Bank",
    "NIFTY PRIVATE BANK":         "🏢 Pvt Bank",
}
