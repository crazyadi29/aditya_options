"""
main.py — Multi-user Telegram bot.

Anyone who runs /start is subscribed. All subscribers receive scheduled alerts.
Commands reply only to the user who sent them.

Schedule:
  09:10  Daily state reset
  09:25  Sector analysis  → broadcast to all subscribers
  09:30–15:20  Stock scan every 5 min → broadcast to all subscribers

Commands:
  /start       → Subscribe + help
  /stop        → Unsubscribe
  /sectors     → Run sector analysis now (replies to caller)
  /scan        → Run stock scan now (replies to caller)
  /watchlist   → Current watchlist
  /status      → Bot + market status
  /subscribers → How many users subscribed (admin info)
  /reset       → Reset daily state
  /expiry      → Show current monthly expiry
"""

import asyncio
import logging
import signal as sys_signal
from datetime import datetime

import pytz
from telegram.ext import Application, CommandHandler
from telegram.error import Forbidden, BadRequest

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config        import BOT_TOKEN, CHAT_ID
from nse_client    import NSEClient
from sector_engine import SectorEngine
from option_engine import OptionEngine
from signal_engine import SignalEngine
from subscribers   import SubscriberManager

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── Global State ──────────────────────────────────────────────

_app        = None
_nse        = None
_sector_eng = None
_option_eng = None
_signal_eng = None
_subs       = None   # SubscriberManager

_state = {
    "sector_done": False,
    "sectors":     [],
    "trending":    {},
    "watchlist":   {"long": [], "short": []},
    "alerted":     set(),
}


# ── Timing ────────────────────────────────────────────────────

def _now():            return datetime.now(IST)
def _is_weekday():     return _now().weekday() < 5
def _time_str():       return _now().strftime("%d %b %Y  %I:%M %p IST")

def _market_open():
    if not _is_weekday():
        return False
    t = (_now().hour, _now().minute)
    return (9, 15) <= t <= (15, 25)


# ── Async wrappers ────────────────────────────────────────────

async def _async_refresh_cookies():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _nse.refresh_cookies)


# ── Messaging helpers ─────────────────────────────────────────

async def _send_to(chat_id: int, text: str):
    """Send a message to one chat. Returns True on success."""
    try:
        await _app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return True
    except Forbidden:
        # User blocked the bot → remove from subscribers
        log.warning(f"User {chat_id} blocked the bot — removing subscriber")
        _subs.remove(chat_id)
        return False
    except BadRequest as e:
        log.error(f"BadRequest to {chat_id}: {e}")
        return False
    except Exception as e:
        log.error(f"Send failed to {chat_id}: {e}")
        return False


async def _broadcast(text: str):
    """Send to every subscriber."""
    chat_ids = _subs.all()
    if not chat_ids:
        log.warning("No subscribers — broadcast skipped")
        return
    log.info(f"Broadcasting to {len(chat_ids)} subscribers…")
    # Send sequentially to avoid Telegram rate limits (30 msg/sec)
    for cid in chat_ids:
        await _send_to(cid, text)
        await asyncio.sleep(0.05)


async def _deliver(text: str, target_chat_id=None):
    """If target specified → send to that chat only. Else → broadcast."""
    if target_chat_id is not None:
        await _send_to(target_chat_id, text)
    else:
        await _broadcast(text)


# ── Core Jobs ─────────────────────────────────────────────────

async def job_daily_reset():
    _state["sector_done"]        = False
    _state["sectors"]            = []
    _state["trending"]           = {}
    _state["watchlist"]["long"]  = []
    _state["watchlist"]["short"] = []
    _state["alerted"].clear()
    _signal_eng.reset()
    _option_eng.reset()
    log.info("✅ Daily state reset complete")


async def job_sector_analysis(target_chat_id=None):
    log.info("═══ SECTOR ANALYSIS START ═══")
    await _async_refresh_cookies()

    sectors       = _sector_eng.analyse()
    trending      = _sector_eng.get_trending(sectors)
    longs, shorts = _sector_eng.build_watchlist(trending)

    _state["sectors"]            = sectors
    _state["trending"]           = trending
    _state["watchlist"]["long"]  = longs
    _state["watchlist"]["short"] = shorts
    _state["sector_done"]        = True

    msg = _build_sector_summary(sectors, trending, longs, shorts)
    await _deliver(msg, target_chat_id)
    log.info("═══ SECTOR ANALYSIS DONE ═══")


async def job_stock_scan(target_chat_id=None):
    if not _state["sector_done"]:
        if target_chat_id:
            await _send_to(target_chat_id, "⚠️ Run /sectors first to build the watchlist.")
        return
    if not _market_open() and target_chat_id is None:
        # Scheduled scan only — skip when market closed
        log.info("Market closed — skipping scheduled scan")
        return

    log.info("─── Stock Scan ───")
    all_stocks = _state["watchlist"]["long"] + _state["watchlist"]["short"]
    alerts_sent = 0

    for stock in all_stocks:
        sym = stock["symbol"]
        if sym in _state["alerted"] and target_chat_id is None:
            continue
        try:
            sig = _signal_eng.check(stock)
            if sig:
                if target_chat_id is None:
                    _state["alerted"].add(sym)
                alert_msg = _build_alert(sig)
                await _deliver(alert_msg, target_chat_id)
                alerts_sent += 1
        except Exception as exc:
            log.error(f"Scan error [{sym}]: {exc}")

    if target_chat_id and alerts_sent == 0:
        await _send_to(target_chat_id, "📡 Scan complete — no stocks meet all 3 conditions right now.")

    log.info(f"─── Done | Alerts this run: {alerts_sent} | Total today: {len(_state['alerted'])} ───")


# ── Message Builders (return strings) ─────────────────────────

def _build_sector_summary(sectors, trending, longs, shorts) -> str:
    lines = [
        "🏭 *SECTOR ANALYSIS — COMPLETE*",
        f"🕐 `{_time_str()}`",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    def _block(title, items):
        lines.append(f"\n{title}")
        if not items:
            lines.append("  —")
            return
        for s in items:
            arrow = "▲" if s["change_pct"] >= 0 else "▼"
            lines.append(f"  {arrow} *{s['label']}*  `{s['change_pct']:+.2f}%`")

    _block("🟢🟢 *MOST GREEN (Top 3)*",    trending.get("most_green",  []))
    _block("🟢   *LEAST GREEN*",            trending.get("least_green", []))
    _block("🔴🔴 *MOST RED (Top 3)*",      trending.get("most_red",    []))
    _block("🔴   *LEAST RED*",              trending.get("least_red",   []))

    # ── #1 gainer per sector (all sectors) ────────────────────
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🏆 *#1 GAINER IN EACH SECTOR*")
    for sec in sectors:
        gainers = sec.get("top_gainers", [])
        if not gainers:
            lines.append(f"  {sec['label']}  —  no data")
            continue
        g = gainers[0]
        lines.append(
            f"  {sec['label']}  →  `{g['symbol']}`  `{g['change_pct']:+.2f}%`"
        )

    # ── Top 3 gainers per top 3 GREEN sectors (9 LONG) ────────
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🟢 *TOP 3 GAINERS FROM EACH GREEN SECTOR*")
    for sec in trending.get("most_green", []):
        lines.append(f"\n  *{sec['label']}*  `{sec['change_pct']:+.2f}%`")
        for g in sec.get("top_gainers", [])[:3]:
            lines.append(f"    ▲ `{g['symbol']:<12}`  `{g['change_pct']:+.2f}%`")

    # ── Top 3 losers per top 3 RED sectors (9 SHORT) ──────────
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🔴 *TOP 3 LOSERS FROM EACH RED SECTOR*")
    for sec in trending.get("most_red", []):
        lines.append(f"\n  *{sec['label']}*  `{sec['change_pct']:+.2f}%`")
        for l in sec.get("top_losers", [])[:3]:
            lines.append(f"    ▼ `{l['symbol']:<12}`  `{l['change_pct']:+.2f}%`")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📋 Watchlist → 🟢 *{len(longs)} LONG*  |  🔴 *{len(shorts)} SHORT*",
        "",
        "🟢 *LONG:*  " + (",  ".join(f"`{s['symbol']}`" for s in longs)  or "—"),
        "🔴 *SHORT:* " + (",  ".join(f"`{s['symbol']}`" for s in shorts) or "—"),
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "_⚡ Scanning every 5 min..._",
    ]
    return "\n".join(lines)


def _build_alert(sig: dict) -> str:
    bias   = sig["bias"]
    emoji  = "🟢" if bias == "LONG" else "🔴"
    action = "BUY CE" if bias == "LONG" else "BUY PE"

    passing = sig.get("options_passing", [])
    atm     = sig.get("option_atm")

    if passing:
        opt_block = f"\n📦 *OPTIONS ALSO MEETING 3 CONDITIONS ({len(passing)}) ✅*\n"
        for o in passing:
            tag = "ATM" if o["strike"] == atm else "OTM"
            opt_block += (
                f"  • `{o['name']}`  [{tag}]   "
                f"LTP `₹{o['ltp']:.2f}`   Vol `{o['volume']:,}`\n"
            )
        opt_block += f"  Expiry: `{_option_eng.expiry}`\n"
    else:
        opt_block = "\n_No options meet the 3 conditions yet — stock alert only._\n"

    nse_link   = f"https://www.nseindia.com/get-quotes/derivatives?symbol={sig['symbol']}"
    chart_link = f"https://chartink.com/stocks/{sig['symbol'].lower()}.html"

    return (
        f"{emoji} *F&O ALERT — {action}* {emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *{sig['symbol']}*  |  {sig['sector']}\n"
        f"⏰ `{_time_str()}`\n"
        f"\n"
        f"📊 *STOCK — 3 CONDITIONS MET ✅*\n"
        f"  Spot       : `₹{sig['price']:.2f}`\n"
        f"  7 EMA      : `₹{sig['ema7']:.2f}`\n"
        f"  21 EMA     : `₹{sig['ema21']:.2f}`\n"
        f"  EMA Signal : `{sig['ema_sig']}`\n"
        f"  Supertrend : `{sig['st_dir']}` @ `₹{sig['st_val']:.2f}` ✅\n"
        f"  Volume     : `{sig['vol_curr']:.0f}` > SMA `{sig['vol_sma']:.0f}` ✅\n"
        f"  Candle 1   : `{sig['c1_dir'].upper()}` `({sig['c1_move']:.2f}%)`\n"
        f"  Candle 2   : `{sig['c2_dir'].upper()}` `({sig['c2_move']:.2f}%)`\n"
        f"{opt_block}"
        f"\n🔍 [NSE Chain]({nse_link}) | [Chart]({chart_link})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_⚡ Auto-alert — no manual push_"
    )


# ── Telegram Commands ─────────────────────────────────────────

async def cmd_start(update, context):
    chat_id = update.effective_chat.id
    new     = _subs.add(chat_id)

    welcome = "🎉 *Welcome!* You are now subscribed." if new \
              else "✅ You are already subscribed."

    await update.message.reply_text(
        f"{welcome}\n\n"
        f"🤖 *NSE F&O Auto-Alert Bot*\n\n"
        f"📌 *Strategy*\n"
        f"  9:25 AM — Sector analysis\n"
        f"  9:30+ — Stock scan every 5 min (auto-alerts)\n\n"
        f"✅ *3 Conditions*\n"
        f"  1️⃣ Volume > 20-period Volume SMA\n"
        f"  2️⃣ Supertrend (7, 3) = UP / DOWN\n"
        f"  3️⃣ EMA 7/21 crossover above/below Supertrend\n\n"
        f"📋 *Commands*\n"
        f"  /sectors     — Sector analysis now\n"
        f"  /scan        — Stock scan now\n"
        f"  /watchlist   — Current watchlist\n"
        f"  /status      — Bot status\n"
        f"  /expiry      — Monthly expiry\n"
        f"  /subscribers — How many users subscribed\n"
        f"  /stop        — Unsubscribe from alerts",
        parse_mode="Markdown",
    )


async def cmd_stop(update, context):
    chat_id = update.effective_chat.id
    removed = _subs.remove(chat_id)
    if removed:
        await update.message.reply_text(
            "👋 Unsubscribed. You won't receive scheduled alerts anymore.\n"
            "Send /start to re-subscribe."
        )
    else:
        await update.message.reply_text("You weren't subscribed.")


async def cmd_sectors(update, context):
    chat_id = update.effective_chat.id
    _subs.add(chat_id)   # auto-subscribe on use
    await update.message.reply_text("🔍 Running sector analysis…")
    await job_sector_analysis(target_chat_id=chat_id)


async def cmd_scan(update, context):
    chat_id = update.effective_chat.id
    _subs.add(chat_id)

    if not _market_open():
        await update.message.reply_text(
            "🔴 *Market is CLOSED*\n\n"
            "Stock scan works only during market hours:\n"
            "  _Mon–Fri, 9:15 AM – 3:25 PM IST_\n\n"
            "You can still run /sectors anytime to see the last session's data.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("📡 Scanning stocks…")
    await job_stock_scan(target_chat_id=chat_id)


async def cmd_watchlist(update, context):
    longs  = _state["watchlist"]["long"]
    shorts = _state["watchlist"]["short"]
    if not longs and not shorts:
        await update.message.reply_text("⚠️ No watchlist. Run /sectors first.")
        return
    lines = [f"📋 *Watchlist*  |  Alerted today: {len(_state['alerted'])}\n"]
    lines.append(f"🟢 *LONG ({len(longs)})*")
    for s in longs:
        lines.append(f"  `{s['symbol']}` — {s['sector']}")
    lines.append(f"\n🔴 *SHORT ({len(shorts)})*")
    for s in shorts:
        lines.append(f"  `{s['symbol']}` — {s['sector']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_status(update, context):
    mkt  = "🟢 OPEN" if _market_open() else "🔴 CLOSED"
    sect = "✅ Done"  if _state["sector_done"] else "⏳ Pending"
    you  = "✅ Yes"   if _subs.has(update.effective_chat.id) else "❌ No"
    await update.message.reply_text(
        f"*Bot Status*\n\n"
        f"Market       : {mkt}\n"
        f"Time         : `{_time_str()}`\n"
        f"Sector Phase : {sect}\n"
        f"Watchlist    : {len(_state['watchlist']['long'])} L | "
        f"{len(_state['watchlist']['short'])} S\n"
        f"Alerted      : {len(_state['alerted'])} today\n"
        f"Subscribers  : {_subs.count()}\n"
        f"You subbed   : {you}\n"
        f"Expiry       : `{_option_eng.expiry}`",
        parse_mode="Markdown",
    )


async def cmd_subscribers(update, context):
    await update.message.reply_text(
        f"👥 Total subscribers: *{_subs.count()}*",
        parse_mode="Markdown",
    )


async def cmd_expiry(update, context):
    await update.message.reply_text(
        f"📅 Monthly expiry: `{_option_eng.expiry}`",
        parse_mode="Markdown",
    )


async def cmd_reset(update, context):
    await job_daily_reset()
    await update.message.reply_text("✅ Daily state reset.")


# ── Entry Point ───────────────────────────────────────────────

async def _run():
    global _app, _nse, _sector_eng, _option_eng, _signal_eng, _subs

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("bot.log"),
            logging.StreamHandler(),
        ],
    )

    # ── Validate env vars before anything else ────────────────
    if not BOT_TOKEN or BOT_TOKEN.strip() == "":
        log.error("❌ BOT_TOKEN is missing! Set it in environment variables.")
        raise SystemExit(1)
    log.info(f"✅ BOT_TOKEN loaded ({len(BOT_TOKEN)} chars)")

    # ── Initialise components with error handling ─────────────
    try:
        _nse        = NSEClient()
        _sector_eng = SectorEngine(_nse)
        _option_eng = OptionEngine(_nse)
        _signal_eng = SignalEngine(_nse, _option_eng)
        _subs       = SubscriberManager()
    except Exception as e:
        log.error(f"❌ Component init failed: {e}")
        raise

    # Seed subscriber list with CHAT_ID from env (if valid)
    if CHAT_ID and str(CHAT_ID).strip():
        try:
            _subs.add(int(CHAT_ID))
        except ValueError:
            log.warning(f"Invalid CHAT_ID in env: {CHAT_ID!r} — ignoring")

    # ── Build Telegram app ────────────────────────────────────
    _app = Application.builder().token(BOT_TOKEN).build()
    _app.add_handler(CommandHandler("start",       cmd_start))
    _app.add_handler(CommandHandler("stop",        cmd_stop))
    _app.add_handler(CommandHandler("sectors",     cmd_sectors))
    _app.add_handler(CommandHandler("scan",        cmd_scan))
    _app.add_handler(CommandHandler("watchlist",   cmd_watchlist))
    _app.add_handler(CommandHandler("status",      cmd_status))
    _app.add_handler(CommandHandler("subscribers", cmd_subscribers))
    _app.add_handler(CommandHandler("expiry",      cmd_expiry))
    _app.add_handler(CommandHandler("reset",       cmd_reset))

    # Global error handler — prevents bot crash from any handler exception
    async def _on_error(update, context):
        log.error(f"Handler error: {context.error}", exc_info=context.error)
    _app.add_error_handler(_on_error)

    # ── Scheduler ─────────────────────────────────────────────
    scheduler = AsyncIOScheduler(timezone=IST)

    scheduler.add_job(job_daily_reset,     "cron", day_of_week="mon-fri", hour=9, minute=10, id="daily_reset")
    scheduler.add_job(job_sector_analysis, "cron", day_of_week="mon-fri", hour=9, minute=25, id="sectors")

    scheduler.add_job(job_stock_scan, "cron", day_of_week="mon-fri", hour="9",              minute="30,35,40,45,50,55",                      id="scan_9h")
    scheduler.add_job(job_stock_scan, "cron", day_of_week="mon-fri", hour="10,11,12,13,14", minute="0,5,10,15,20,25,30,35,40,45,50,55",       id="scan_10_14h")
    scheduler.add_job(job_stock_scan, "cron", day_of_week="mon-fri", hour="15",             minute="0,5,10,15,20",                           id="scan_15h")

    scheduler.add_job(_async_refresh_cookies, "interval", minutes=25, id="cookies")

    # ── Start bot + scheduler ─────────────────────────────────
    await _app.initialize()
    await _app.start()
    # drop_pending_updates=True prevents replay of old commands on restart
    # allowed_updates only listens for messages (reduces Telegram polling load)
    await _app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=["message"],
    )
    scheduler.start()

    # Warm up NSE session (don't crash bot if NSE is down)
    try:
        await _async_refresh_cookies()
    except Exception as e:
        log.warning(f"Initial NSE refresh failed (continuing anyway): {e}")

    log.info(f"🤖 Bot started. {_subs.count()} subscriber(s) loaded.")

    # ── Keep running until signal or exception ────────────────
    stop_event = asyncio.Event()

    # Signal handlers only work on Unix. On Windows / some containers,
    # add_signal_handler throws NotImplementedError — handle gracefully.
    loop = asyncio.get_running_loop()
    try:
        for s in (sys_signal.SIGINT, sys_signal.SIGTERM):
            loop.add_signal_handler(s, stop_event.set)
        log.info("Signal handlers registered")
    except (NotImplementedError, RuntimeError) as e:
        log.warning(f"Signal handlers unavailable ({e}) — bot runs until killed")

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Interrupted")

    # ── Graceful shutdown ─────────────────────────────────────
    log.info("Shutting down…")
    try:
        scheduler.shutdown(wait=False)
        if _app.updater.running:
            await _app.updater.stop()
        await _app.stop()
        await _app.shutdown()
    except Exception as e:
        log.warning(f"Shutdown warning: {e}")
    log.info("Stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        raise
