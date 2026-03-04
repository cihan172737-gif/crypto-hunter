import os
import time
import requests
from datetime import datetime, timezone

# =========================
# CONFIG
# =========================

BYBIT_BASE = os.getenv("BYBIT_BASE", "https://api.bybit.com").rstrip("/")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ALERT_ONLY = os.getenv("ALERT_ONLY", "1") == "1"
SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "1") == "1"

# Funding eşikleri (oran)
FUNDING_NEG = float(os.getenv("FUNDING_NEG", "-0.0005"))  # <= bu ise negatif funding squeeze adayı
FUNDING_POS = float(os.getenv("FUNDING_POS", "0.0005"))   # >= bu ise long crowded / risk

# Hareket eşiği (%)
MOVE_PCT = float(os.getenv("MOVE_PCT", "0.35"))  # 15dk hareket eşiği (%)

# Hacim spike (son mum hacmi / önceki ortalama)
VOL_FACTOR = float(os.getenv("VOL_FACTOR", "1.8"))

# Open Interest değişimi (%)
OI_CHANGE_PCT = float(os.getenv("OI_CHANGE_PCT", "1.2"))  # 15dk içinde >=%1.2 artış/azalış

# Kline interval (Bybit: "1","3","5","15","30","60"...)
KLINE_INTERVAL = os.getenv("KLINE_INTERVAL", "5")  # 5 dk
WINDOW_MIN = int(os.getenv("WINDOW_MIN", "15"))    # 15 dk analiz

# Varsayılan semboller (dosya yoksa buradan)
DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,TRXUSDT,DOTUSDT,MATICUSDT,LTCUSDT,ATOMUSDT,OPUSDT,ARBUSDT,APTUSDT,INJUSDT,NEARUSDT,UNIUSDT"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; crypto-hunter/1.0)",
    "Accept": "application/json,text/plain,*/*",
}

# =========================
# TELEGRAM
# =========================

def tg_send(text: str):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env eksik. Mesaj:\n", text)
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()

# =========================
# BYBIT HELPERS
# =========================

def bybit_get(path: str, params: dict):
    url = f"{BYBIT_BASE}{path}"
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if str(data.get("retCode")) != "0":
        raise RuntimeError(f"Bybit retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
    return data.get("result", {})

def funding_rate(symbol: str) -> float:
    # Tickers: fundingRate genelde burada geliyor (linear)
    res = bybit_get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
    lst = res.get("list", [])
    if not lst:
        return 0.0
    fr = lst[0].get("fundingRate")
    try:
        return float(fr)
    except Exception:
        return 0.0

def last_price_24h(symbol: str):
    res = bybit_get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
    lst = res.get("list", [])
    if not lst:
        return None
    x = lst[0]
    # lastPrice, prevPrice24h, volume24h
    def f(k, d=0.0):
        try:
            return float(x.get(k, d))
        except Exception:
            return d
    return {
        "last": f("lastPrice"),
        "prev24h": f("prevPrice24h"),
        "vol24h": f("volume24h"),
        "turn24h": f("turnover24h"),
    }

def kline(symbol: str, interval: str, limit: int = 30):
    # list: [startTime, open, high, low, close, volume, turnover]
    res = bybit_get("/v5/market/kline", {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": str(limit),
    })
    rows = res.get("list", [])
    # Bybit genelde ters (en yeni önce) döner -> kronolojik yapalım
    rows = list(reversed(rows))
    out = []
    for r in rows:
        try:
            out.append({
                "t": int(r[0]),
                "o": float(r[1]),
                "h": float(r[2]),
                "l": float(r[3]),
                "c": float(r[4]),
                "v": float(r[5]),
                "to": float(r[6]),
            })
        except Exception:
            continue
    return out

def open_interest_series(symbol: str, interval: str = "5", limit: int = 30):
    # intervalTime: "5min","15min","30min","1h"... Bybit OI endpoint formatı
    interval_map = {"1": "1min", "3": "3min", "5": "5min", "15": "15min", "30": "30min", "60": "1h"}
    interval_time = interval_map.get(interval, "5min")

    res = bybit_get("/v5/market/open-interest", {
        "category": "linear",
        "symbol": symbol,
        "intervalTime": interval_time,
        "limit": str(limit),
    })
    rows = res.get("list", [])
    rows = list(reversed(rows))
    out = []
    for r in rows:
        try:
            out.append({
                "t": int(r.get("timestamp")),
                "oi": float(r.get("openInterest")),
            })
        except Exception:
            continue
    return out

# =========================
# SYMBOLS
# =========================

def load_symbols():
    # Önce symbols.txt varsa onu oku
    for fname in ["symbols.txt", "SYMBOLS.txt"]:
        if os.path.exists(fname):
            with open(fname, "r", encoding="utf-8") as f:
                lines = []
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    # virgül varsa parçala
                    parts = [p.strip() for p in s.replace(" ", "").split(",") if p.strip()]
                    lines.extend(parts)
                return sorted(list(dict.fromkeys(lines)))  # unique
    # Yoksa env SYMBOLS, o da yoksa default
    raw = os.getenv("SYMBOLS", DEFAULT_SYMBOLS)
    syms = [x.strip() for x in raw.split(",") if x.strip()]
    return sorted(list(dict.fromkeys(syms)))

# =========================
# SIGNAL LOGIC (3'lü)
# =========================

def pct(a: float, b: float) -> float:
    # a->b yüzde değişim
    if a == 0:
        return 0.0
    return (b - a) / a * 100.0

def scan_symbol(symbol: str):
    # 15dk için kaç mum? interval 5 ise 3 mum
    step_min = int(KLINE_INTERVAL)
    need = max(6, int(WINDOW_MIN / step_min) + 6)

    kl = kline(symbol, KLINE_INTERVAL, limit=need)
    if len(kl) < int(WINDOW_MIN / step_min) + 2:
        return []

    oi = open_interest_series(symbol, KLINE_INTERVAL, limit=need)
    fr = funding_rate(symbol)

    # 15dk penceresi: son N mum
    n = max(1, int(WINDOW_MIN / step_min))
    window = kl[-n:]
    prev = kl[-(n+1)]

    # fiyat hareketi: pencere ilk open -> son close
    p_open = window[0]["o"]
    p_close = window[-1]["c"]
    move = pct(p_open, p_close)

    # hacim spike: son mum hacmi / önceki (n-1) ortalama
    last_v = window[-1]["v"]
    base_vs = [x["v"] for x in window[:-1]] or [prev["v"]]
    avg_v = sum(base_vs) / max(1, len(base_vs))
    vol_ratio = (last_v / avg_v) if avg_v > 0 else 0.0

    # Open Interest değişimi (%): pencere başı -> pencere sonu
    oi_move = 0.0
    if len(oi) >= n:
        oi_open = oi[-n]["oi"]
        oi_close = oi[-1]["oi"]
        oi_move = pct(oi_open, oi_close)

    alerts = []

    # 1) Funding Squeeze (LONG adayı)
    # Negatif funding + fiyat yukarı + hacim spike + OI artıyor
    if (fr <= FUNDING_NEG) and (move >= MOVE_PCT) and (vol_ratio >= VOL_FACTOR) and (oi_move >= OI_CHANGE_PCT):
        alerts.append(
            f"🟢 {symbol} | FUNDING SQUEEZE (LONG adayı)\n"
            f"Funding: {fr:.6f}\n"
            f"Move({WINDOW_MIN}m): {move:.2f}%\n"
            f"Vol spike: x{vol_ratio:.2f}\n"
            f"OI({WINDOW_MIN}m): {oi_move:.2f}%"
        )

    # 2) Long Liquidation / Trap (risk)  — Funding pozitif + sert düşüş + hacim spike + OI düşüyor
    if (fr >= FUNDING_POS) and (move <= -MOVE_PCT) and (vol_ratio >= VOL_FACTOR) and (oi_move <= -OI_CHANGE_PCT):
        alerts.append(
            f"🔴 {symbol} | LONG LIQUIDATION (dikkat)\n"
            f"Funding: {fr:.6f}\n"
            f"Move({WINDOW_MIN}m): {move:.2f}%\n"
            f"Vol spike: x{vol_ratio:.2f}\n"
            f"OI({WINDOW_MIN}m): {oi_move:.2f}%"
        )

    # 3) Open Interest Expansion (trend gücü) — fiyat & OI aynı yönde güçlü
    if (abs(move) >= MOVE_PCT) and (abs(oi_move) >= OI_CHANGE_PCT):
        direction = "📈 BULL" if (move > 0 and oi_move > 0) else "📉 BEAR" if (move < 0 and oi_move < 0) else "⚠️ MIXED"
        alerts.append(
            f"🟡 {symbol} | OI EXPANSION ({direction})\n"
            f"Move({WINDOW_MIN}m): {move:.2f}%\n"
            f"OI({WINDOW_MIN}m): {oi_move:.2f}%\n"
            f"Funding: {fr:.6f}\n"
            f"Vol spike: x{vol_ratio:.2f}"
        )

    return alerts

def main():
    symbols = load_symbols()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    all_alerts = []
    for s in symbols:
        try:
            all_alerts.extend(scan_symbol(s))
            time.sleep(0.15)  # rate limit için küçük bekleme
        except Exception as e:
            all_alerts.append(f"⚠️ {s} | Error: {e}")

    if all_alerts:
        text = "Bybit Funding Alerts\n\n" + "\n\n".join(all_alerts) + f"\n\n⏱ {ts}"
        tg_send(text)
        print(text)
    else:
        if SEND_IF_EMPTY:
            text = f"Bybit Funding Alerts\n\nScan OK ✅\nNo alerts.\n{ts}"
            tg_send(text)
            print(text)
        else:
            print("No alerts.")

if __name__ == "__main__":
    main()
