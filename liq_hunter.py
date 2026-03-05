import os
import time
import requests
from datetime import datetime, timezone

# ------------------ CONFIG ------------------
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SYMBOLS_ENV = os.getenv("SYMBOLS", "").strip()
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT"]

# thresholds (rate not percent)
# 0.0025 = 0.25%
FUND_MIN = float(os.getenv("FUND_MIN", "0.0025"))
OI_MIN_PCT = float(os.getenv("OI_MIN_PCT", "5"))          # %5
VOL_SPIKE_MULT = float(os.getenv("VOL_SPIKE_MULT", "1.8")) # last 15m vol >= 1.8x prev 15m

# Binance/Bybit endpoints
BINANCE_FAPI = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; liq-hunter/1.0)",
    "Accept": "application/json",
}

# ------------------ UTILS ------------------
def now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env missing. Message:\n", text)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TG_CHAT,
            "text": text,
            "disable_web_page_preview": True
        }, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            print("Telegram error:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram send error:", repr(e))

def load_symbols():
    if SYMBOLS_ENV:
        return [s.strip().upper() for s in SYMBOLS_ENV.replace("\n", ",").split(",") if s.strip()]
    if os.path.exists("symbols.txt"):
        out = []
        with open("symbols.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip().upper()
                if not line or line.startswith("#"):
                    continue
                out += [p for p in line.replace(" ", "").split(",") if p]
        return out or DEFAULT_SYMBOLS
    return DEFAULT_SYMBOLS

def f(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

# ------------------ BINANCE ------------------
def binance_funding(symbol: str):
    # premiumIndex gives lastFundingRate + nextFundingTime
    url = f"{BINANCE_FAPI}/fapi/v1/premiumIndex"
    r = requests.get(url, params={"symbol": symbol}, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    js = r.json()
    return f(js.get("lastFundingRate"), 0.0), int(js.get("nextFundingTime", 0))

def binance_oi_change_pct(symbol: str):
    # openInterestHist gives series; use last 2 points (15m)
    url = f"{BINANCE_FAPI}/futures/data/openInterestHist"
    params = {"symbol": symbol, "period": "15m", "limit": 2}
    r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    arr = r.json()
    if not isinstance(arr, list) or len(arr) < 2:
        return None
    a = f(arr[-2].get("sumOpenInterest"), None)
    b = f(arr[-1].get("sumOpenInterest"), None)
    if not a or not b:
        return None
    return (b - a) / a * 100.0

def binance_vol_spike(symbol: str):
    # compare last 15m quote volume vs previous 15m from klines
    url = f"{BINANCE_FAPI}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": "15m", "limit": 3}
    r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    kl = r.json()
    if not isinstance(kl, list) or len(kl) < 3:
        return None
    # kline fields: [openTime, open, high, low, close, volume, closeTime, quoteVolume, ...]
    prev_qv = f(kl[-2][7], 0.0)
    last_qv = f(kl[-1][7], 0.0)
    if prev_qv <= 0:
        return None
    return last_qv / prev_qv

def binance_levels(symbol: str):
    # use last 4h high/low from 15m klines (16 candles)
    url = f"{BINANCE_FAPI}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": "15m", "limit": 16}
    r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    kl = r.json()
    highs = [f(x[2], None) for x in kl]
    lows  = [f(x[3], None) for x in kl]
    highs = [x for x in highs if x is not None]
    lows  = [x for x in lows if x is not None]
    if not highs or not lows:
        return None, None
    return max(highs), min(lows)

# ------------------ BYBIT ------------------
def bybit_funding(symbol: str):
    url = f"{BYBIT}/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    js = r.json()
    if str(js.get("retCode")) != "0":
        raise RuntimeError(f"retCode={js.get('retCode')} {js.get('retMsg')}")
    item = (js.get("result") or {}).get("list", [None])[0] or {}
    return f(item.get("fundingRate"), 0.0)

def bybit_oi_change_pct(symbol: str):
    # v5 open-interest endpoint (gives list); use last 2 points (15min)
    url = f"{BYBIT}/v5/market/open-interest"
    params = {"category": "linear", "symbol": symbol, "intervalTime": "15min", "limit": 2}
    r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    js = r.json()
    if str(js.get("retCode")) != "0":
        return None
    lst = (js.get("result") or {}).get("list") or []
    if len(lst) < 2:
        return None
    a = f(lst[-2].get("openInterest"), None)
    b = f(lst[-1].get("openInterest"), None)
    if not a or not b:
        return None
    return (b - a) / a * 100.0

def bybit_vol_spike(symbol: str):
    # compare last 15m turnover vs previous 15m from kline
    url = f"{BYBIT}/v5/market/kline"
    params = {"category": "linear", "symbol": symbol, "interval": "15", "limit": 3}
    r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    js = r.json()
    if str(js.get("retCode")) != "0":
        return None
    kl = (js.get("result") or {}).get("list") or []
    if len(kl) < 3:
        return None
    # bybit kline list item: [startTime, open, high, low, close, volume, turnover]
    prev_turn = f(kl[-2][6], 0.0)
    last_turn = f(kl[-1][6], 0.0)
    if prev_turn <= 0:
        return None
    return last_turn / prev_turn

def bybit_levels(symbol: str):
    url = f"{BYBIT}/v5/market/kline"
    params = {"category": "linear", "symbol": symbol, "interval": "15", "limit": 16}
    r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    js = r.json()
    if str(js.get("retCode")) != "0":
        return None, None
    kl = (js.get("result") or {}).get("list") or []
    highs = [f(x[2], None) for x in kl]
    lows  = [f(x[3], None) for x in kl]
    highs = [x for x in highs if x is not None]
    lows  = [x for x in lows if x is not None]
    if not highs or not lows:
        return None, None
    return max(highs), min(lows)

# ------------------ RADAR ------------------
def evaluate(symbol: str):
    out = []

    # Binance
    try:
        fund_bi, next_fund_ts = binance_funding(symbol)
        oi_bi = binance_oi_change_pct(symbol)
        volm_bi = binance_vol_spike(symbol)
        hi_bi, lo_bi = binance_levels(symbol)
        out.append(("Binance", fund_bi, oi_bi, volm_bi, hi_bi, lo_bi, next_fund_ts))
    except Exception as e:
        out.append(("Binance", None, None, None, None, None, None, f"err={repr(e)}"))

    # Bybit
    try:
        fund_by = bybit_funding(symbol)
        oi_by = bybit_oi_change_pct(symbol)
        volm_by = bybit_vol_spike(symbol)
        hi_by, lo_by = bybit_levels(symbol)
        out.append(("Bybit", fund_by, oi_by, volm_by, hi_by, lo_by, None))
    except Exception as e:
        out.append(("Bybit", None, None, None, None, None, None, f"err={repr(e)}"))

    return out

def main():
    symbols = load_symbols()
    hits = []
    errs = []

    for sym in symbols:
        rows = evaluate(sym)
        for row in rows:
            ex = row[0]
            if len(row) == 8 and isinstance(row[7], str):
                errs.append(f"{sym} {ex} {row[7]}")
                continue

            ex, fund, oi_pct, vol_mult, hi, lo, next_ts, _ = row  # _ none
            if fund is None:
                continue

            # Filters
            if abs(fund) < FUND_MIN:
                continue
            if oi_pct is None or oi_pct < OI_MIN_PCT:
                continue
            if vol_mult is None or vol_mult < VOL_SPIKE_MULT:
                continue

            direction = "POS" if fund > 0 else "NEG"
            bias = "↓ Longs crowded (flush risk)" if fund > 0 else "↑ Shorts crowded (squeeze risk)"

            hits.append({
                "symbol": sym,
                "exchange": ex,
                "fund": fund,
                "dir": direction,
                "oi": oi_pct,
                "volm": vol_mult,
                "hi": hi,
                "lo": lo,
                "bias": bias,
            })

        time.sleep(0.15)

    if hits:
        hits.sort(key=lambda x: (abs(x["fund"]), x["oi"], x["volm"]), reverse=True)
        lines = []
        lines.append("🚨 LIQUIDATION HUNTER (PRO) | Binance + Bybit")
        lines.append(f"Filters: |fund|≥{FUND_MIN*100:.2f}%  OI≥{OI_MIN_PCT:.0f}%  VolSpike≥{VOL_SPIKE_MULT:.1f}x")
        lines.append("")

        for h in hits[:10]:
            lines.append(f"✅ {h['symbol']} | {h['exchange']}")
            lines.append(f"   fund={h['fund']*100:.3f}% ({h['dir']}) | OI15m={h['oi']:.1f}% | Vol15m={h['volm']:.2f}x")
            if h["hi"] is not None and h["lo"] is not None:
                lines.append(f"   Liquidity levels (4h swing): HIGH={h['hi']:.4f}  LOW={h['lo']:.4f}")
            lines.append(f"   Bias: {h['bias']}")
            lines.append("")

        lines.append("Next step (manual): Heatmap/CVD ile onayla, sonra giriş/stop/TP.")
        lines.append(now_str())
        tg_send("\n".join(lines))
    else:
        print("No PRO setups.", now_str())
        if errs:
            print("Errors(first 5):", errs[:5])

if __name__ == "__main__":
    main()
