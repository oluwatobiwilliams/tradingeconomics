import os
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
import requests

load_dotenv()

app = Flask(__name__)

API_KEY = os.getenv("TE_API_KEY")
BASE_URL = "https://api.tradingeconomics.com"

DEFAULT_TICKERS = [
    "VOLVB:SS",     # Volvo (Sweden)
    "ERICB:SS",     # Ericsson (Sweden)
    "HMB:SS",       # H&M (Sweden)
    "SAND:SS",      # Sandvik (Sweden)
    "GMEXICOB:MM",  # Grupo Mexico (Mexico)
    "GFNORTEO:MM",  # Banorte (Mexico)
    "FPH:NZ",       # Fisher & Paykel Healthcare (NZ)
]

TICKER_LABELS = {
    "VOLVB:SS": "VOLVO",
    "ERICB:SS": "ERICSSON",
    "HMB:SS": "H&M",
    "SAND:SS": "SANDVIK",
    "GMEXICOB:MM": "G.MEXICO",
    "GFNORTEO:MM": "BANORTE",
    "FPH:NZ": "F&P HLTH",
}

PERIOD_DAYS = {
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "5y": 1825,
    "10y": 3650,
    "20y": 7300,
}


def fetch_historical(symbols, period):
    days = PERIOD_DAYS.get(period, 180)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    d1 = start_date.strftime("%Y-%m-%d")
    d2 = end_date.strftime("%Y-%m-%d")

    symbols_str = ",".join(symbols)
    url = f"{BASE_URL}/markets/historical/{symbols_str}"
    params = {"c": API_KEY, "d1": d1, "d2": d2, "f": "json"}

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def normalize_data(raw_data, symbols):
    """Group by symbol, normalize prices, then align all series to a shared date spine
    using forward-fill so every series has a value on every date."""
    grouped = {s: {} for s in symbols}  # symbol -> {iso_date: close}

    for row in raw_data:
        symbol = row.get("Symbol")
        close = row.get("Close")
        if symbol in grouped and close is not None:
            raw_date = row.get("Date", "")
            try:
                iso_date = datetime.strptime(raw_date, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                iso_date = raw_date
            grouped[symbol][iso_date] = close

    # Build a sorted union of all dates, starting from the latest first date
    # across all symbols so every series has a valid opening value
    first_dates = [min(pts) for pts in grouped.values() if pts]
    if not first_dates:
        return {}
    spine_start = max(first_dates)
    all_dates = sorted({d for pts in grouped.values() for d in pts if d >= spine_start})
    if not all_dates:
        return {}

    result = {}
    for symbol, date_map in grouped.items():
        if not date_map:
            continue
        # Forward-fill: carry the last known close to dates where the market was closed
        filled = []
        last_close = None
        for d in all_dates:
            if d in date_map:
                last_close = date_map[d]
            if last_close is not None:
                filled.append({"date": d, "close": last_close})

        if not filled or filled[0]["close"] == 0:
            continue

        base = filled[0]["close"]
        label = TICKER_LABELS.get(symbol, symbol.split(":")[0])
        result[label] = [
            {"date": p["date"], "value": round(p["close"] / base, 4)}
            for p in filled
        ]

    return result


@app.route("/")
def index():
    tickers_meta = [
        {"symbol": s, "label": TICKER_LABELS.get(s, s.split(":")[0])}
        for s in DEFAULT_TICKERS
    ]
    return render_template("index.html", tickers_meta=tickers_meta)


@app.route("/api/historical")
def api_historical():
    tickers = request.args.get("tickers", "")
    period = request.args.get("period", "6m")

    if not tickers:
        symbols = DEFAULT_TICKERS
    else:
        symbols = [t.strip() for t in tickers.split(",") if t.strip()]

    try:
        raw = fetch_historical(symbols, period)
        normalized = normalize_data(raw, symbols)

        # Compute best/worst performers
        performances = {}
        for label, points in normalized.items():
            if points:
                performances[label] = round((points[-1]["value"] - 1) * 100, 1)

        best = max(performances, key=performances.get) if performances else None
        worst = min(performances, key=performances.get) if performances else None

        return jsonify({
            "series": normalized,
            "best": {"ticker": best, "change": performances.get(best)} if best else None,
            "worst": {"ticker": worst, "change": performances.get(worst)} if worst else None,
        })
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    app.run(debug=True, port=5000)
