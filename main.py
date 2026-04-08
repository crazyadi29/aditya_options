import sys
import os
sys.path.append(os.path.dirname(__file__))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"status": "ok", "message": "API is running"}

@app.get("/api/market-trend")
def market_trend():
    from logic.market_logic import get_market_trend
    return JSONResponse(content=get_market_trend())

@app.get("/api/sectors")
def sectors():
    from logic.market_logic import get_sector_performances
    return JSONResponse(content=get_sector_performances())

@app.get("/api/stock-analysis")
def stock_analysis(sector: str, trend: str):
    from logic.market_logic import analyze_sector_stocks, SECTORS
    if sector not in SECTORS:
        return JSONResponse(content={"error": "Invalid sector"})
    return JSONResponse(content=analyze_sector_stocks(sector, trend))

@app.get("/api/option-chain/{symbol}")
def option_chain(symbol: str):
    from logic.nse_scraper import scraper
    data = scraper.get_option_chain(symbol)
    return JSONResponse(content=data)

@app.get("/api/chart-data/{symbol}")
def api_chart_data(symbol: str):
    from logic.market_logic import get_chart_data
    data = get_chart_data(symbol)
    if not data or "error" in data:
        return JSONResponse(content={"error": "No data"}, status_code=404)
    return JSONResponse(content=data)
