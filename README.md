# 📊 NSE F&O Auto-Alert Telegram Bot

Fully automated F&O trading alert bot. No manual push needed.
Alerts fire automatically the moment a stock meets all criteria.

---

## 🧠 Strategy Logic

### Phase 1 — Sector Analysis (9:25 AM)
After the first two 5-min candles form (9:15–9:25), the bot:
- Fetches all NSE sector indices
- Classifies them into 4 groups:
  - 🟢🟢 Most Green   → Top 3 bullish sectors
  - 🟢   Least Green  → Weakest positive sector
  - 🔴🔴 Most Red     → Top 3 bearish sectors
  - 🔴   Least Red    → Weakest negative sector
- Picks top 4 F&O stocks from each trending sector
- Builds LONG and SHORT watchlists

### Phase 2 — Stock Scan (Every 5 min, 9:30–3:20 PM)
For every stock in the watchlist, ALL of these must pass:

| # | Check | LONG | SHORT |
|---|-------|------|-------|
| 1 | First 2 candles | At least one bullish with ≥0.4% body | At least one bearish with ≥0.4% body |
| 2 | 20 SMA | Stock above 20 SMA | Stock below 20 SMA |
| 3 | 9 EMA  | Stock above 9 EMA  | Stock below 9 EMA  |
| 4 | OI Buildup (CE/PE) | CE OI change ≥5% on ATM or top 3 OTM | PE OI change ≥5% on ATM or top 3 OTM |
| 5 | Premium 9 EMA | CE LTP above 9 EMA | PE LTP above 9 EMA |

If all 5 pass → **Instant Telegram alert** 🔔

---

## ⚙️ Setup

### 1. Install dependencies
```bash
cd fo_alert_bot
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env and fill in BOT_TOKEN and CHAT_ID
```

**Getting your BOT_TOKEN:**
1. Open Telegram → search `@BotFather`
2. Send `/newbot` → follow instructions
3. Copy the token

**Getting your CHAT_ID:**
1. Open Telegram → search `@userinfobot`
2. Send `/start`
3. Copy the `Id` number shown

### 3. Run the bot
```bash
python main.py
```

---

## 📱 Telegram Commands

| Command | What it does |
|---------|-------------|
| `/start` | Show help and strategy summary |
| `/sectors` | Run sector analysis right now |
| `/scan` | Run stock scan right now |
| `/watchlist` | View today's LONG + SHORT watchlist |
| `/status` | Market status, scan count, alerts fired |
| `/expiry` | Show current monthly expiry date |
| `/reset` | Reset all state (use when testing) |

---

## 📁 File Structure

```
fo_alert_bot/
├── main.py           ← Bot + scheduler + Telegram commands
├── config.py         ← All settings (tweak here)
├── nse_client.py     ← NSE website API calls
├── sector_engine.py  ← Phase 1: Sector analysis
├── option_engine.py  ← Option chain OI analysis
├── signal_engine.py  ← Full signal check per stock
├── technicals.py     ← EMA, SMA, candle utilities
├── requirements.txt
├── .env.example
└── bot.log           ← Created at runtime
```

---

## ⚙️ Tuning (config.py)

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `MIN_CANDLE_MOVE_PCT` | 0.4 | Min % candle body to count as directional |
| `OI_CHANGE_MIN_PCT` | 5.0 | Min % OI change to flag as writer buildup |
| `TOP_N_SECTORS` | 3 | How many green/red sectors to track |
| `STOCKS_PER_SECTOR` | 4 | Stocks to watch per sector |
| `TOP_OTM_WRITERS` | 3 | OTM strikes checked (by OI rank) |
| `EMA_FAST` | 9 | EMA period (stock + premium) |
| `SMA_TREND` | 20 | SMA trend filter |

---

## 🔔 Sample Alert Format

```
🟢 F&O AUTO-ALERT — BUY CE 🟢
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 RELIANCE  |  💻 IT
⏰ 18 Apr 2026  10:15 AM IST

Signal:  BUY CE  2960
Premium: ₹45.30
Monthly Expiry:  29-MAY-2026

📊 STOCK TECHNICALS
  Spot    : ₹2972.50
  20 SMA  : ₹2940.10  ✅ Above
  9 EMA   : ₹2960.80  ✅ Above
  Candle1 : BULLISH (0.62%)
  Candle2 : BULLISH (0.48%)

🔗 OPTION CHAIN (CE | Monthly)
  ATM Strike : 2960
  Signal @   : 2960
  OI         : 1,24,500
  ΔOI        : +18,750 (+15.1%) ✅
  Volume     : 45,200
  IV         : 22.4%

🏆 TOP OTM WRITERS
  🥇 Strike 2980 — OI 98,000 | ΔOI +12,000 | Vol 38,000
  🥈 Strike 3000 — OI 87,500 | ΔOI +9,500  | Vol 29,000
  🥉 Strike 3020 — OI 72,000 | ΔOI +7,200  | Vol 21,000

🔍 NSE Option Chain | Chartink Chart
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ Auto-alert | No manual push needed
```

---

## ⚠️ Notes

- **NSE scraping**: NSE.in requires proper cookies. The bot refreshes them every 25 minutes automatically.
- **Ban list**: Stocks in F&O ban period won't have option chain data — they'll be silently skipped.
- **Premium EMA history**: Resets every day at 9:10 AM. The first few scans use a simpler proxy until 9 data points are collected.
- **Run on a server**: For reliable 5-min scanning, host on a VPS (AWS/GCP/DigitalOcean) in Mumbai region for lowest latency to NSE.
