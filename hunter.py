import os
import time
import requests
from typing import List, Tuple, Optional

# ---------------- CONFIG ----------------
SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()]

BYBIT_BASE = os.getenv("BYBIT_BASE", "https://api.bybit.com").rstrip("/")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ALERT_ONLY = os.getenv("ALERT_ONLY", "1") == "1"
SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "0") == "1"

# 3'lü funding seviyeleri (negatif)
# ör: "-0.0003,-0.0006,-0.0010"
FUNDING_LEVELS = []
for x in os.getenv("FUNDING_LEVELS", "-0.0005,-0.0010,-0.0015").split(","):
    x = x.strip()
    if not x:
        continue
    try:
        FUNDING_LEVELS.append(float(x))
    except ValueError:
        pass
FUNDING_LEVELS = sorted(FUNDING_LEVELS)  # daha negatif en küçük

# Kline/filtre
KLINE_INTERVAL = os.getenv("KLINE_INTERVAL", "60")  # 60=1h
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "50"))
VOL_SPIKE = float(os.getenv("VOL_SPIKE", "1.8"))
MOMENTUM_MODE = os.getenv("MOMENTUM_MODE", "close>prevclose").strip().lower()

HEADERS = {
    "User-Agent": "crypto-hunter/1.0",
    "Accept": "application/json,text/plain,*/*",
}

# --------------- TELEGRAM ---------------
def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env eksik. Mesaj:\n", text)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    # Telegram hata verirse logda görelim
    if r.status_code != 200:
        print("Telegram error:", r.status_code, r.text)

# --------------- BYBIT API ---------------
def bybit_get(path: str, params: dict) -> dict:
    url = f"{BYBIT_BASE}{path}"
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    # v5 genelde {"retCode":0,"result":{...}}
    if isinstance(data, dict) and data.get("retCode", 0) != 0:
        raise RuntimeError(f"Bybit retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
    return data

def get_funding_rate(symbol: str) -> Optional[float]:
    """
    Bybit v5 market tickers linear -> fundingRate alanı
    """
    data = bybit_get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
    lst = (((data or {}).get("result") or {}).get("list")) or []
    if not lst:
        return None
    item = lst[0] or {}
    fr = item.get("fundingRate")
    if fr is None:
        return None
    try:
        return float(fr)
    except ValueError:
        return None

def get_kline(symbol: str, interval: str, limit: int) -> List[Tuple[float, float, float, float]]:
    """
    /v5/market/kline -> list: [start, open, high, low, close, volume, turnover]
    Döneni: (open, close, volume, start_ts)
    """
    data = bybit_get("/v5/market/kline", {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": str(limit),
    })
    rows = (((data or {}).get("result") or {}).get("list")) or []
    out = []
    for row in rows:
        # row genelde string liste
        try:
            start_ts = float(row[0])
            o = float(row[1])
            c = float(row[4])
            v = float(row[5])
            out.append((o, c, v, start_ts))
        except Exception:
            continue
    # Bybit bazen ters sırada döndürür; start_ts küçükten büyüğe alalım
    out.sort(key=lambda x: x[3])
    return out

# --------------- FILTERS ---------------
def funding_tier(fr: float) -> Optional[int]:
    """
    fr negatifse ve FUNDING_LEVELS seviyelerinden birini geçiyorsa tier döndürür.
    Tier 1 = mild, Tier 2 = mid, Tier 3 = strong (daha negatif daha güçlü)
    """
    if fr is None:
        return None

    # ör levels: [-0.0010, -0.0006, -0.0003] değil; biz sorted yaptık daha negatif en başta.
    # tier mantığı: fr <= level ise "tuttu" say.
    # kaç tanesini geçtiyse ona göre güç.
    hit = 0
    for lvl in sorted(FUNDING_LEVELS, reverse=False):  # en negatif -> daha az negatif
        if fr <= lvl:
            hit += 1
    if hit <= 0:
        return None
    # hit=1 -> en negatif eşiği geçti demek (strong), ama kullanıcı tier sırası istiyor:
    # Biz bunu 1..3 şeklinde normalize edelim (3 en güçlü)
    # Örn: 3 seviye varsa hit=3 => mild dahil hepsi geçti (yani en az negatiften bile küçük) bu daha güçlü değil.
    # Bu yüzden tier'ı "en sıkı eşiğe göre" hesaplayalım:
    # strongest = fr <= min(levels)
    strongest = min(FUNDING_LEVELS)
    mid = sorted(FUNDING_LEVELS)[1] if len(FUNDING_LEVELS) >= 2 else strongest
    mild = max(FUNDING_LEVELS)

    if fr <= strongest:
        return 3
    if fr <= mid:
        return 2
    if fr <= mild:
        return 1
    return None

def check_volume_spike(kl: List[Tuple[float, float, float, float]], spike: float) -> Tuple[bool, float]:
    """
    Son mum hacmi / önceki ortalama
    """
    if len(kl) < 10:
        return (False, 0.0)
    last_v = kl[-1][2]
    prev = [x[2] for x in kl[-21:-1]] if len(kl) >= 21 else [x[2] for x in kl[:-1]]
    avg = sum(prev) / max(len(prev), 1)
    ratio = (last_v / avg) if avg > 0 else 0.0
    return (ratio >= spike, ratio)

def check_momentum(kl: List[Tuple[float, float, float, float]], mode: str) -> Tuple[bool, str]:
    if len(kl) < 2:
        return (False, "no-data")
    o, c, _, _ = kl[-1]
    prev_c = kl[-2][1]

    if mode == "close>open":
        return (c > o, f"{c:.4f}>{o:.4f}")
    # default: close>prevclose
    return (c > prev_c, f"{c:.4f}>{prev_c:.4f}")

# --------------- MAIN ---------------
def main():
    start = time.strftime("%Y-%m-%d %H:%M:%S")
    alerts = []

    print("Bybit Funding Alerts")
    print("Scan start:", start)
    print("Symbols:", len(SYMBOLS))

    for sym in SYMBOLS:
        try:
            fr = get_funding_rate(sym)
            if fr is None:
                continue

            tier = funding_tier(fr)
            if tier is None:
                continue

            kl = get_kline(sym, KLINE_INTERVAL, KLINE_LIMIT)
            vol_ok, vol_ratio = check_volume_spike(kl, VOL_SPIKE)
            mom_ok, mom_dbg = check_momentum(kl, MOMENTUM_MODE)

            # 3'lü filtre: funding tier + hacim + momentum
            if not (vol_ok and mom_ok):
                continue

            # Mesaj formatı
            fr_pct = fr * 100
            tier_label = {1: "TIER-1", 2: "TIER-2", 3: "TIER-3"}.get(tier, "TIER")
            msg = (
                f"ALERT ✅ {tier_label}\n"
                f"{sym}\n"
                f"Funding: {fr_pct:.4f}%\n"
                f"Volume Spike: {vol_ratio:.2f}x (>= {VOL_SPIKE}x)\n"
                f"Momentum: {MOMENTUM_MODE} ({mom_dbg})\n"
                f"TF: {KLINE_INTERVAL}m"
            )
            alerts.append(msg)

        except Exception as e:
            print("Error on", sym, str(e))

    # Telegram'a gönder
    if alerts:
        # Çok uzunsa parça parça gönder
        joined = "\n\n".join(alerts)
        tg_send(joined)
        print("Alerts:", len(alerts))
    else:
        print("No alerts.")
        if SEND_IF_EMPTY:
            tg_send(f"Bybit Funding Alerts\nScan OK ✅\nNo alerts.\n{start}")

if __name__ == "__main__":
    main()
