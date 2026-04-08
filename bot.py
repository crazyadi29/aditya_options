# imports
import os
import logging
from datetime import datetime, time as dtime
import pytz
from telegram.ext import Application, CommandHandler, ContextTypes
from master_scanner import MasterScanner

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── IST TIMEZONE HELPERS ─────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN  = dtime(9, 15)   # 9:15 AM IST
MARKET_CLOSE = dtime(15, 30)  # 3:30 PM IST

def get_ist_time():
    return datetime.now(IST)

def is_market_open():
    now = get_ist_time()
    # Weekend check (Saturday=5, Sunday=6)
    if now.weekday() >= 5:
        return False
    # Check if current time is within market hours
    current_time = now.time()
    return MARKET_OPEN <= current_time <= MARKET_CLOSE

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID             = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID_HERE")
SCAN_INTERVAL_MIN   = 15

scanner = MasterScanner()

async def start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *NSE Smart Options Bot v2*\n\n"
        "🧠 *Intelligence layers:*\n"
        "1️⃣ Nifty direction → sector trend analysis\n"
        "2️⃣ Top/weak sector identification\n"
        "3️⃣ Stock breakout scan (9EMA, 15EMA, 20 SMA Vol)\n"
        "4️⃣ Option chain OI & OI change validation\n\n"
        "📊 *Scope:* Nifty 50 + All sector indices\n"
        "⏱ *Frequency:* Every 15 min (market hours)\n\n"
        "*Commands:*\n"
        "/scan — Run manual scan now\n"
        "/sectors — Sector heatmap\n"
        "/chain NIFTY — Option chain for any symbol\n"
        "/status — Bot status\n"
        "/help — Strategy explained\n",
        parse_mode="Markdown"
    )

async def help_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Strategy Logic*\n\n"
        "*Step 1 — Market Direction*\n"
        "• Nifty +ve → top sectors → CE candidates\n"
        "• Nifty -ve → least -ve sectors → CE\n"
        "• Most -ve sectors → PE candidates\n\n"
        "*Step 2 — Stock Screening*\n"
        "• Price within 1.5% of 20-bar resistance/support\n"
        "• Volume ≥ 2x 20 SMA volume\n"
        "• 9 EMA & 15 EMA position checked\n\n"
        "*Step 3 — Option Chain Validation*\n"
        "• PCR > 1.2 = Bullish\n"
        "• PCR < 0.8 = Bearish\n"
        "• OI wall > 10 lakh contracts = flagged\n"
        "• OI change > 25% = fresh buildup flagged\n\n"
        "*Scoring:* Stock score + OI score → final rank",
        parse_mode="Markdown"
    )

async def scan_now(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Running full scan... ~30–60 seconds ⏳")
    try:
        result = scanner.run_full_scan()
        messages = format_full_result(result, manual=True)
        for msg in messages:
            await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Scan error: {str(e)}")

async def sectors_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Fetching sector data...")
    try:
        from sector_analyzer import SectorAnalyzer
        data = SectorAnalyzer().analyze()
        await update.message.reply_text(format_sector_heatmap(data), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def chain_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /chain NIFTY or /chain RELIANCE")
        return
    symbol = args[0].upper()
    await update.message.reply_text(f"🔗 Fetching option chain for {symbol}...")
    try:
        import yfinance as yf
        ticker_map = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
        t = yf.Ticker(ticker_map.get(symbol, f"{symbol}.NS"))
        hist = t.history(period="1d", interval="5m")
        spot = float(hist["Close"].iloc[-1]) if not hist.empty else 0.0
        from option_chain import OptionChainAnalyzer
        oi = OptionChainAnalyzer().analyze(symbol, spot)
        await update.message.reply_text(format_option_chain(oi, symbol, spot), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def status_cmd(update, context: ContextTypes.DEFAULT_TYPE):
    now = get_ist_time()
    market_status = "🟢 Open" if is_market_open() else "🔴 Closed"

    message = (
        f"🤖 *Bot Status*\n\n"
        f"Market: {market_status}\n"
        f"Time (IST): `{now.strftime('%d %b %Y %H:%M:%S')}`\n"
        f"Scan every: {SCAN_INTERVAL_MIN} minutes\n"
        f"Mode: Sector → Stock → OI Chain"
    )
    await update.message.reply_text(message, parse_mode="Markdown")

async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    if not is_market_open():
        return
    logger.info(f"Running scheduled scan at {get_ist_time().strftime('%H:%M:%S')} IST...")
    try:
        result = scanner.run_full_scan()
        if result["ce_signals"] or result["pe_signals"]:
            for msg in format_full_result(result, manual=False):
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Scheduled scan error: {e}")

# ─── FORMATTERS ───────────────────────────────────────────────────────────────

def format_full_result(result: dict, manual: bool) -> list:
    messages = []
    ctx = result["market_context"]
    now = result["scan_time"]
    nifty_icon = "📈" if ctx.get("nifty_change", 0) >= 0 else "📉"

    msg1 = f"{'🔍 Manual Scan' if manual else '🚨 ALERT'} — {now}\n\n"
    msg1 += f"*MARKET OVERVIEW*\n"
    msg1 += f"{nifty_icon} Nifty: `{ctx.get('nifty_change', 0):+.2f}%` ({ctx.get('nifty_trend','?')})\n\n"
    if ctx.get("top_sectors"):
        msg1 += "🟢 *Bullish Sectors (CE):*\n"
        for s in ctx["top_sectors"]:
            msg1 += f"  • {s['name']}: `{s['change']:+.2f}%`\n"
    if ctx.get("weak_sectors"):
        msg1 += "\n🔴 *Weak Sectors (PE):*\n"
        for s in ctx["weak_sectors"]:
            msg1 += f"  • {s['name']}: `{s['change']:+.2f}%`\n"
    messages.append(msg1)

    if result["ce_signals"]:
        msg2 = f"🟢 *CE SIGNALS — {len(result['ce_signals'])} found*\n" + "━"*26 + "\n\n"
        for sig in result["ce_signals"]:
            msg2 += format_signal_block(sig)
        messages.append(msg2)

    if result["pe_signals"]:
        msg3 = f"🔴 *PE SIGNALS — {len(result['pe_signals'])} found*\n" + "━"*26 + "\n\n"
        for sig in result["pe_signals"]:
            msg3 += format_signal_block(sig)
        messages.append(msg3)

    if not result["ce_signals"] and not result["pe_signals"]:
        messages.append("❌ *No strong signals this scan.*\n_Market may be consolidating._")

    messages.append("⚠️ _For informational purposes only. Not financial advice._")
    return messages

def format_signal_block(sig: dict) -> str:
    icon = "🟢" if sig["signal_type"] == "CE" else "🔴"
    stars = "⭐" * min(sig.get("final_score", 0), 8)
    b = f"{icon} *{sig['symbol']}* [{sig['sector']}] {stars}\n"
    b += f"LTP: `₹{sig['ltp']:.2f}`\n"
    if sig.get("ema9"):
        b += f"9EMA: `₹{sig['ema9']:.2f}` | 15EMA: `₹{sig['ema15']:.2f}`\n"
    b += f"EMA Position: {sig['ema_status']}\n"
    if sig.get("vol_ratio", 0) > 0:
        b += f"Volume: `{sig['vol_ratio']:.1f}x` above avg\n"
    b += f"Breakout: {sig['breakout_status']}\n"
    if sig.get("pcr"):
        pcr_icon = "🐂" if sig["pcr"] > 1.2 else ("🐻" if sig["pcr"] < 0.8 else "⚖️")
        b += f"PCR: `{sig['pcr']:.2f}` {pcr_icon} | OI Bias: `{sig['oi_signal']}`\n"
    if sig.get("oi_notes"):
        b += f"📌 {' | '.join(sig['oi_notes'][:2])}\n"
    b += "\n"
    return b

def format_sector_heatmap(data: dict) -> str:
    nifty_icon = "📈" if data["nifty_change"] >= 0 else "📉"
    msg = f"*SECTOR HEATMAP*\n{nifty_icon} Nifty: `{data['nifty_change']:+.2f}%`\n\n"
    for s in data.get("sectors", []):
        bar = "🟢" if s["change"] >= 0.5 else ("🔴" if s["change"] <= -0.5 else "🟡")
        msg += f"{bar} `{s['name']:<12}` `{s['change']:+.2f}%`\n"
    return msg

def format_option_chain(oi: dict, symbol: str, spot: float) -> str:
    if not oi or oi.get("source") == "UNAVAILABLE":
        return f"❌ Option chain unavailable for {symbol}."
    msg = f"*{symbol} Option Chain*\n"
    msg += f"Spot: `₹{spot:.2f}` | Expiry: `{oi.get('expiry','N/A')}`\n"
    msg += f"PCR: `{oi['pcr']:.2f}` | Signal: `{oi['oi_signal']}`\n"
    msg += f"Max Pain: `₹{oi.get('max_pain', 'N/A')}`\n\n"
    if oi.get("top_ce_oi_strikes"):
        msg += "🔴 *CE OI Walls (Resistance):*\n"
        for r in oi["top_ce_oi_strikes"][:3]:
            msg += f"  ₹{r['strike']} → OI: `{r['oi']:,}` | Δ: `{r['oi_change_pct']:+.1f}%`\n"
    if oi.get("top_pe_oi_strikes"):
        msg += "\n🟢 *PE OI Walls (Support):*\n"
        for r in oi["top_pe_oi_strikes"][:3]:
            msg += f"  ₹{r['strike']} → OI: `{r['oi']:,}` | Δ: `{r['oi_change_pct']:+.1f}%`\n"
    if oi.get("strong_resistance_walls"):
        msg += f"\n⚠️ Strong resistance at ₹{oi['strong_resistance_walls'][0]['strike']}\n"
    if oi.get("strong_support_walls"):
        msg += f"⚠️ Strong support at ₹{oi['strong_support_walls'][0]['strike']}\n"
    return msg

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Set TELEGRAM_BOT_TOKEN environment variable first!")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.job_queue.scheduler.timezone = IST
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("scan",    scan_now))
    app.add_handler(CommandHandler("sectors", sectors_cmd))
    app.add_handler(CommandHandler("chain",   chain_cmd))
    app.add_handler(CommandHandler("status",  status_cmd))

    app.job_queue.run_repeating(scheduled_scan, interval=SCAN_INTERVAL_MIN * 60, first=15)

    print("🤖 NSE Smart Options Bot v2 started!")
    print(f"   Sector → EMA → OI Chain pipeline active")
    print(f"   Scanning every {SCAN_INTERVAL_MIN} min during 9:15–3:30 IST")
    app.run_polling()

if __name__ == "__main__":
    main()
