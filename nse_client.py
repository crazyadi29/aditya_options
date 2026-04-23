"""
nse_client.py — All NSE website API calls in one place.

NSE requires:
  1. Visiting homepage first to set cookies
  2. Sending Referer + proper User-Agent headers
  3. Re-initialising cookies when they expire (401/403)
"""

import time
import logging
import requests

log = logging.getLogger(__name__)

_BASE    = "https://www.nseindia.com"
_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.nseindia.com/",
}


class NSEClient:
    """Thin wrapper around NSE website APIs."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._ready = False

    # ── Cookie / Session Management ──────────────────────────────

    def refresh_cookies(self):
        """
        Hit NSE homepage + market page to receive valid session cookies.
        Must be called once at startup and periodically (every ~30 min).
        """
        try:
            r1 = self._session.get(f"{_BASE}/", timeout=12)
            log.debug(f"NSE home: {r1.status_code}")
            time.sleep(0.8)
            r2 = self._session.get(
                f"{_BASE}/market-data/live-equity-market", timeout=12
            )
            log.debug(f"NSE market page: {r2.status_code}")
            self._ready = True
            log.info("✅ NSE session/cookies refreshed")
        except Exception as exc:
            log.warning(f"NSE cookie refresh failed: {exc}")

    def _get(self, url: str, retries: int = 3):
        """GET with automatic cookie re-init on auth errors."""
        if not self._ready:
            self.refresh_cookies()

        for attempt in range(1, retries + 1):
            try:
                resp = self._session.get(url, timeout=15)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (401, 403):
                    log.warning(f"NSE auth error ({resp.status_code}), refreshing cookies…")
                    self.refresh_cookies()
                else:
                    log.warning(f"NSE {resp.status_code} on {url}")
            except requests.exceptions.RequestException as exc:
                log.warning(f"Request attempt {attempt} failed: {exc}")
            time.sleep(1.5 * attempt)
        return None

    # ── Sector / Index Data ──────────────────────────────────────

    def get_sector_data(self, index_name: str):
        """
        Returns metadata + constituent stocks for a given NSE index.
        E.g. index_name = "NIFTY IT"
        """
        url = f"{_BASE}/api/equity-stockIndices?index={requests.utils.quote(index_name)}"
        return self._get(url)

    def get_fo_stocks(self):
        """All stocks currently in F&O segment (Securities in F&O index)."""
        url = f"{_BASE}/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"
        return self._get(url)

    def get_ban_list(self):
        """Stocks currently in F&O ban period."""
        url = f"{_BASE}/api/ban-list"
        data = self._get(url)
        if data and isinstance(data, dict):
            # May come as {"data": "SYMBOL1,SYMBOL2,..."}
            raw = data.get("data", "")
            if isinstance(raw, str):
                return [s.strip() for s in raw.split(",") if s.strip()]
            if isinstance(raw, list):
                return raw
        return []

    # ── Option Chain ─────────────────────────────────────────────

    def get_option_chain(self, symbol: str):
        """
        Full option chain for a stock.
        Includes OI, OI change, volume, LTP for each CE/PE.
        """
        url = f"{_BASE}/api/option-chain-equities?symbol={symbol}"
        return self._get(url)

    # ── Intraday Chart Data ───────────────────────────────────────

    def get_intraday_chart(self, symbol: str):
        """
        1-minute OHLCV data for the current session.
        Returns raw JSON; key 'grapthData' contains [[ts_ms, close], ...].
        Some endpoints return [[ts_ms, open, high, low, close, volume], ...].
        """
        url = f"{_BASE}/api/chart-databyindex?index={symbol}&indices=false"
        return self._get(url)

    # ── Quote ────────────────────────────────────────────────────

    def get_quote(self, symbol: str):
        """Live quote for a stock."""
        url = f"{_BASE}/api/quote-equity?symbol={symbol}"
        return self._get(url)
