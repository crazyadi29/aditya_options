// ─── State ────────────────────────────────────────────────────────────────────
let currentTrend = "positive";
const loadedCharts = {};
let wsRetryCount = 0;
let ws = null;
let lastSectorValues = {};  // For flash animation

// ─── Boot ──────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    connectWebSocket();
    loadSectors();  // Initial REST load for sector list

    document.getElementById("refresh-sectors").addEventListener("click", () => {
        loadSectors();
    });
});

// ─── WebSocket Live Feed ───────────────────────────────────────────────────────
function connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${location.host}/ws/live`;

    setWsStatus("connecting");
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        wsRetryCount = 0;
        setWsStatus("connected");
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === "tick") {
                handleLiveTick(data);
            }
        } catch (e) {
            console.error("WS parse error:", e);
        }
    };

    ws.onclose = () => {
        setWsStatus("disconnected");
        // Exponential backoff retry: max 30 sec
        const delay = Math.min(1000 * Math.pow(2, wsRetryCount), 30000);
        wsRetryCount++;
        setTimeout(connectWebSocket, delay);
    };

    ws.onerror = () => {
        setWsStatus("error");
        ws.close();
    };
}

function setWsStatus(state) {
    const dot = document.querySelector(".pulse-dot");
    const label = document.getElementById("ws-status");
    if (!dot || !label) return;

    dot.className = "pulse-dot";
    if (state === "connected") {
        dot.classList.add("connected");
        label.textContent = "Live feed connected · 5s ticks";
    } else if (state === "connecting") {
        label.textContent = "Connecting to live feed...";
    } else if (state === "disconnected") {
        dot.classList.add("error");
        label.textContent = "Reconnecting...";
    } else if (state === "error") {
        dot.classList.add("error");
        label.textContent = "Feed error — retrying...";
    }
}

// ─── Handle Live Tick ──────────────────────────────────────────────────────────
function handleLiveTick(data) {
    // 1. Update ticker bar
    updateTickerBar(data);

    // 2. Update Nifty card
    updateNiftyCard(data.trend, data.market_open, data.timestamp);

    // 3. Update sector list values (flash if changed)
    updateSectorValues(data.sectors);
}

function updateTickerBar(data) {
    const trend = data.trend || {};
    const sectors = data.sectors || [];

    // Nifty in ticker
    const niftyEl = document.getElementById("ticker-nifty");
    if (niftyEl && trend.current_price) {
        const dir = trend.change >= 0 ? "up" : "down";
        niftyEl.innerHTML = `NIFTY <span class="ticker-val ${dir}">₹${trend.current_price} (${trend.change >= 0 ? "+" : ""}${trend.change}%)</span>`;
    }

    // Sectors in ticker
    const secEl = document.getElementById("ticker-sectors");
    if (secEl && sectors.length) {
        secEl.innerHTML = sectors.map(s => {
            const dir = s.change >= 0 ? "up" : "down";
            const sign = s.change >= 0 ? "+" : "";
            return `<span class="ticker-sector-chip"><span class="ticker-val ${dir}">${s.sector.replace("Nifty ", "")} ${sign}${s.change}%</span></span>`;
        }).join('<span style="color:var(--border-bright); margin:0 0.3rem">·</span>');
    }

    // Time
    const timeEl = document.getElementById("ticker-time");
    if (timeEl && data.timestamp) timeEl.textContent = data.timestamp;

    // Market status pill
    const pill = document.getElementById("ticker-market-status");
    if (pill) {
        pill.className = `market-pill ${data.market_open ? "open" : "closed"}`;
        pill.textContent = data.market_open ? "MARKET OPEN" : "MARKET CLOSED";
    }
}

function updateNiftyCard(trend, marketOpen, timestamp) {
    if (!trend || !trend.current_price) return;
    currentTrend = trend.status;

    const container = document.getElementById("nifty-status-container");
    const dir = trend.change >= 0 ? "positive" : "negative";
    const sign = trend.change >= 0 ? "+" : "";

    container.innerHTML = `
        <div class="nifty-card ${dir}">
            <div class="nifty-badge ${dir}">NIFTY ${dir.toUpperCase()}</div>
            <div class="nifty-price">₹${trend.current_price?.toLocaleString("en-IN") || "—"}</div>
            <div class="nifty-change ${trend.change >= 0 ? 'up' : 'down'}">${sign}${trend.change}%</div>
            <div class="nifty-ts">${timestamp || ""} · ${marketOpen ? "🟢 Open" : "🔴 Closed"}</div>
        </div>
    `;
}

function updateSectorValues(sectors) {
    if (!sectors || !sectors.length) return;
    sectors.forEach(s => {
        // Find the sector item in the list
        const items = document.querySelectorAll(".sector-item");
        items.forEach(item => {
            const nameEl = item.querySelector(".sector-name");
            if (nameEl && nameEl.textContent === s.sector) {
                const valEl = item.querySelector(".sector-val");
                if (!valEl) return;
                const prev = lastSectorValues[s.sector];
                const isPos = s.change >= 0;
                const sign = isPos ? "+" : "";
                valEl.textContent = `${sign}${s.change}%`;
                valEl.className = `sector-val ${isPos ? "positive" : "negative"}`;

                // Flash animation if value changed
                if (prev !== undefined && prev !== s.change) {
                    valEl.classList.add(s.change > prev ? "flash-up" : "flash-down");
                    setTimeout(() => valEl.classList.remove("flash-up", "flash-down"), 700);
                }
                lastSectorValues[s.sector] = s.change;
            }
        });
    });
}

// ─── Initial REST Load ─────────────────────────────────────────────────────────
async function loadSectors() {
    const list = document.getElementById("sectors-list");
    list.innerHTML = `<div class="loading-overlay"><div class="loader"></div><span>Scanning...</span></div>`;

    try {
        const [trendRes, secRes] = await Promise.all([
            fetch("/api/market-trend"),
            fetch("/api/sectors")
        ]);
        const trendData = await trendRes.json();
        const sectorsData = await secRes.json();

        currentTrend = trendData.status;
        updateNiftyCard(trendData, trendData.market_open, trendData.timestamp);
        renderSectorList(sectorsData);

        if (sectorsData.length > 0) {
            loadSector(sectorsData[0].sector);
        }
    } catch (e) {
        list.innerHTML = `<div style="padding:1rem; color:var(--negative); font-family:var(--font-mono); font-size:0.75rem;">Error loading sectors</div>`;
        console.error("Sector load error", e);
    }
}

function renderSectorList(sectorsData) {
    const list = document.getElementById("sectors-list");
    let html = "";
    sectorsData.forEach(sec => {
        const isPos = sec.change >= 0;
        const sign = isPos ? "+" : "";
        lastSectorValues[sec.sector] = sec.change;
        html += `
            <div class="sector-item" onclick="loadSector('${sec.sector}')">
                <span class="sector-name">${sec.sector}</span>
                <span class="sector-val ${isPos ? "positive" : "negative"}">${sign}${sec.change}%</span>
            </div>
        `;
    });
    list.innerHTML = html;
}

// ─── Load Sector Stocks ────────────────────────────────────────────────────────
async function loadSector(sectorName) {
    // Mark active
    document.querySelectorAll(".sector-item").forEach(el => {
        el.classList.toggle("active", el.querySelector(".sector-name")?.textContent === sectorName);
    });

    document.getElementById("active-sector-title").textContent = `${sectorName} · Breakout Scanner`;
    const container = document.getElementById("stocks-container");
    const loader = document.getElementById("stocks-loader");

    // Clear old cards
    Array.from(container.children).forEach(c => {
        if (c.id !== "stocks-loader") c.remove();
    });
    loader.style.display = "flex";

    try {
        const res = await fetch(`/api/stock-analysis?sector=${encodeURIComponent(sectorName)}&trend=${currentTrend}`);
        const stocks = await res.json();
        loader.style.display = "none";

        if (!stocks.length) {
            container.innerHTML += `<div style="grid-column:1/-1; padding:2rem; text-align:center; color:var(--text-sub); font-family:var(--font-mono); font-size:0.8rem;">No breakout signals found for this sector.</div>`;
            return;
        }

        stocks.forEach(stock => renderStockCard(stock, container));
    } catch (e) {
        loader.style.display = "none";
        console.error("Sector load failed", e);
    }
}

// ─── Render Stock Card ─────────────────────────────────────────────────────────
function renderStockCard(stock, container) {
    const card = document.createElement("div");
    let signalClass = "none";
    if (stock.signal.includes("CALL")) signalClass = "call";
    if (stock.signal.includes("PUT")) signalClass = "put";
    card.className = `card stock-card${signalClass !== "none" ? " has-" + signalClass : ""}`;
    card.id = `stock-${stock.symbol}`;

    let consensusClass = "neutral";
    if (stock.analyst_consensus?.includes("BULLISH")) consensusClass = "bullish";
    if (stock.analyst_consensus?.includes("BEARISH")) consensusClass = "bearish";

    card.innerHTML = `
        <div class="stock-header">
            <div>
                <div class="stock-symbol">${stock.symbol}</div>
                <div class="stock-meta">
                    Vol: ${stock.volume_breakout ? '<span style="color:var(--positive)">HIGH ↑</span>' : 'Normal'} &nbsp;·&nbsp;
                    Res: ${stock.near_resistance ? '<span style="color:var(--negative)">Near</span>' : '—'} &nbsp;·&nbsp;
                    Sup: ${stock.near_support ? '<span style="color:var(--positive)">Near</span>' : '—'}
                </div>
            </div>
            <div class="stock-price">₹${stock.price}</div>
        </div>

        <div class="stock-stats">
            <div class="stat">
                <span class="stat-label">9 EMA</span>
                <span class="stat-val ${stock.price > stock.ema9 ? 'up' : 'down'}">₹${stock.ema9}</span>
            </div>
            <div class="stat">
                <span class="stat-label">15 EMA</span>
                <span class="stat-val ${stock.price > stock.ema15 ? 'up' : 'down'}">₹${stock.ema15}</span>
            </div>
        </div>

        <div class="analyst-badge ${consensusClass}">${stock.analyst_consensus || "No Consensus Data"}</div>

        <div class="signal-banner ${signalClass}">${stock.signal}</div>

        <button class="view-chart-btn" onclick="toggleChart('${stock.symbol}')">▸ View Chart</button>
        <div id="chart-wrapper-${stock.symbol}" class="chart-container-wrapper">
            <div id="chart-${stock.symbol}" class="tv-chart"></div>
        </div>
        <div id="options-${stock.symbol}" class="options-data" style="display:none;">
            <div style="text-align:center; font-family:var(--font-mono); font-size:0.72rem; color:var(--text-sub);">Loading OI data...</div>
        </div>
    `;

    container.appendChild(card);
    loadOptionChain(stock.symbol);
}

// ─── Chart ─────────────────────────────────────────────────────────────────────
window.toggleChart = async function(symbol) {
    const wrapper = document.getElementById(`chart-wrapper-${symbol}`);
    if (wrapper.style.display === "block") { wrapper.style.display = "none"; return; }
    wrapper.style.display = "block";
    if (loadedCharts[symbol]) return;

    const chartContainer = document.getElementById(`chart-${symbol}`);
    chartContainer.innerHTML = `<div style="text-align:center; padding-top:80px; color:var(--text-sub); font-family:var(--font-mono); font-size:0.75rem;">Loading chart...</div>`;

    try {
        const res = await fetch(`/api/chart-data/${symbol}`);
        const data = await res.json();
        chartContainer.innerHTML = "";

        if (data.error) {
            chartContainer.innerHTML = `<div style="text-align:center;padding-top:80px;color:var(--negative);font-family:var(--font-mono);font-size:0.75rem;">Data unavailable</div>`;
            return;
        }

        const chart = LightweightCharts.createChart(chartContainer, {
            layout: { background: { type: "solid", color: "transparent" }, textColor: "#6B7A99" },
            grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.04)" } },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            timeScale: { borderColor: "rgba(255,255,255,0.08)" },
        });

        chart.addCandlestickSeries({ upColor: "#00D68F", downColor: "#FF4D6A", borderVisible: false, wickUpColor: "#00D68F", wickDownColor: "#FF4D6A" }).setData(data.candles);
        chart.addLineSeries({ color: "#4F8EF7", lineWidth: 1, title: "9 EMA" }).setData(data.ema9);
        chart.addLineSeries({ color: "#A855F7", lineWidth: 1, title: "15 EMA" }).setData(data.ema15);
        chart.addLineSeries({ color: "#FF4D6A", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, title: "Res" }).setData(data.resistance);
        chart.addLineSeries({ color: "#00D68F", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, title: "Sup" }).setData(data.support);
        chart.timeScale().fitContent();
        loadedCharts[symbol] = chart;
    } catch (e) {
        chartContainer.innerHTML = `<div style="text-align:center;padding-top:80px;color:var(--negative);font-family:var(--font-mono);font-size:0.75rem;">Chart failed: ${e.message}</div>`;
    }
};

// ─── Option Chain ──────────────────────────────────────────────────────────────
async function loadOptionChain(symbol) {
    const el = document.getElementById(`options-${symbol}`);
    el.style.display = "block";
    try {
        const res = await fetch(`/api/option-chain/${symbol}`);
        const data = await res.json();
        const total = (data.call_oi || 0) + (data.put_oi || 0);
        const callPct = total ? ((data.call_oi / total) * 100).toFixed(1) : 50;
        const putPct  = total ? ((data.put_oi / total) * 100).toFixed(1) : 50;
        el.innerHTML = `
            <div style="display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:0.7rem;margin-bottom:0.4rem">
                <span style="color:var(--negative)">CE Wall</span>
                ${data.mock ? '<span style="color:orange;font-size:0.62rem">MOCK</span>' : ''}
                <span style="color:var(--positive)">PE Wall</span>
            </div>
            <div class="oi-visual">
                <div class="call-oi" style="width:${callPct}%"></div>
                <div class="put-oi" style="width:${putPct}%"></div>
            </div>
            <div class="oi-bar">
                <span style="color:var(--negative)">${fmt(data.call_oi)} OI</span>
                <span style="color:var(--positive)">${fmt(data.put_oi)} OI</span>
            </div>
        `;
    } catch {
        el.innerHTML = `<div style="font-family:var(--font-mono);font-size:0.7rem;color:var(--text-sub)">OI unavailable</div>`;
    }
}

function fmt(num) {
    if (!num) return "—";
    if (num >= 100000) return (num / 100000).toFixed(2) + "L";
    return num.toLocaleString();
}
