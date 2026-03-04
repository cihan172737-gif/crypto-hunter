import os
import time
import requests
from typing import List, Tuple, Optional
from datetime import datetime, timezone

# =========================
# CONFIG
# =========================

BYBIT_BASE = os.getenv("BYBIT_BASE", "https://api.bybit.com").rstrip("/")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ALERT_ONLY = os.getenv("ALERT_ONLY", "1") == "1"        # 1: sadece sinyal bas
SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "0") == "1"  # 1: sinyal yoksa da mesaj at
SCAN_OK = os.getenv("SCAN_OK", "1") == "1"              # 1: her taramada Scan OK gönder

# 3'lü funding eşikleri (mutlak değer)
# 0.0010 = %0.10, 0.0015 = %0.15, 0.0020 = %0.20
LEVELS = os.getenv("FUNDING_LEVELS", "0.0010,0.0015,0.0020")
FUNDING_LEVELS = sorted([float(x.strip()) for x in LEVELS.split(",") if x.strip()])

# filtreler (daha az gürültü)
# 5dk mumlarda minimum hareket (0.003 = %0.30)
MIN_5M_MOVE = float(os.getenv("MIN_5M_MOVE", "0.003"))
# son 5dk volume / önceki 5dk volume oranı (1.5 = %50 artış)
MIN_VOL_SPIKE = float(os.getenv("MIN_VOL_SPIKE", "1.5"))

# Bybit kline ayarları
KLINE_INTERVAL = os.getenv("KLINE_INTERVAL", "5")  # 5 dakikalık mum
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "10"))  # düşük tutuyoruz (rate limit/403 azaltır)

# İstekler arası bekleme (rate limit/403 azaltır)
REQ_SLEEP = float(os.getenv("REQ_SLEEP", "0.35"))

# =========================
# DEFAULT SYMBOLS (50 adet, yüksek hacim)
# =========================
DEFAULT_SYMBOLS_50 = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","AVAXUSDT","DOGEUSDT","LINKUSDT","MATICUSDT",
    "DOTUSDT","LTCUSDT","TRXUSDT","ATOMUSDT","NEARUSDT","OPUSDT","ARBUSDT","APTUSDT","SUIUSDT","INJUSDT",
    "TIAUSDT","SEIUSDT","FILUSDT","XLMUSDT","ETCUSDT","BCHUSDT","ICPUSDT","UNIUSDT","AAVEUSDT","MKRUSDT",
    "RUNEUSDT","GALAUSDT","SANDUSDT","APEUSDT","WIFUSDT","PEPEUSDT","SHIBUSDT","1000BONKUSDT","FLOKIUSDT","JUPUSDT",
    "DYDXUSDT","IMXUSDT","STXUSDT","KASUSDT","FETUSDT","RNDRUSDT","TAOUSDT","PYTHUSDT","LDOUSDT","CRVUSDT",
]

# =========================
# HTTP helpers
# =========================

SESSION = requests.Session()
BASE_HEADERS = {
    # 403 fix: Bybit bazı ortamlarda UA/Accept bekliyor
    "User-Agent": "Mozilla/5.0 (compatible; crypto-hunter/1.0)",
    "Accept": "application/json",
}


def bybit_get(path: str, params: dict) -> dict:
    url = f"{BYBIT_BASE}{path}"
    r = SESSION.get(url, params=params, headers=BASE_HEADERS, timeout=TIMEOUT)
    # 403/429 olursa loglamak için raise
    r.raise_for_status()
    data = r.json()
    return data


# =========================
# Telegram
# =========================

def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env eksik. Mesaj:\n", text)
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}
    r = SESSION.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()


# =========================
# Symbols
# =========================

def load_symbols() -> List[str]:
    # 1) ENV SYMBOLS varsa onu kullan
    env = os.getenv("SYMBOLS", "").strip()
    if env:
        syms = [x.strip().upper() for x in env.split(",") if x.strip()]
        return syms

    # 2) symbols.txt varsa oku (senin eklediğin dosya)
    if os.path.exists("symbols.txt"):
        out = []
        with open("symbols.txt", "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip().upper()
                if not s or s.startswith("#"):
                    continue
                # virgülle yazdıysan parçala
                if "," in s:
                    out.extend([x.strip().upper() for x in s.split(",") if x.strip()])
                else:
                    out.append(s)
        # uniq
        uniq = []
        seen = set()
        for s in out:
            if s not in seen:
                uniq.append(s)
                seen.add(s)
        return uniq[:50] if len(uniq) > 50 else uniq

    # 3) yoksa default 50
    return DEFAULT_SYMBOLS_50


# =========================
# Market data
# =========================

def get_funding(symbol: str) -> Optional[float]:
    # Bybit v5 tickers ile funding rate (linear perp)
    # endpoint: /v5/market/tickers?category=linear&symbol=BTCUSDT
    data = bybit_get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
    if data.get("retCode") != 0:
        return None
    lst = (data.get("result") or {}).get("list") or []
    if not lst:
        return None
    item = lst[0]
    fr = item.get("fundingRate")
    if fr is None:
        return None
    try:
        return float(fr)
    except:
        return None


def get_kline_5m(symbol: str) -> Optional[List[List[str]]]:
    # endpoint: /v5/market/kline?category=linear&symbol=BTCUSDT&interval=5&limit=10
    data = bybit_get("/v5/market/kline", {
        "category": "linear",
        "symbol": symbol,
        "interval": KLINE_INTERVAL,
        "limit": KLINE_LIMIT
    })
    if data.get("retCode") != 0:
        return None
    # result.list: newest -> oldest (Bybit'te çoğu zaman newest first)
    kl = (data.get("result") or {}).get("list")
    if not kl:
        return None
    return kl


def calc_move_and_vol_spike(kl: List[List[str]]) -> Tuple[float, float]:
    """
    kl rows (strings) like:
    [ startTime, open, high, low, close, volume, turnover ]
    Move: last close vs prev close
    Vol spike: last volume / prev volume
    """
    # En güvenlisi: zamana göre sırala (oldest -> newest)
    rows = sorted(kl, key=lambda x: int(x[0]))
    if len(rows) < 3:
        return 0.0, 0.0

    prev = rows[-2]
    last = rows[-1]

    prev_close = float(prev[4])
    last_close = float(last[4])

    prev_vol = float(prev[5])
    last_vol = float(last[5])

    move = (last_close - prev_close) / prev_close if prev_close else 0.0
    vol_spike = (last_vol / prev_vol) if prev_vol else 0.0
    return move, vol_spike


def pick_level(abs_fr: float) -> Optional[float]:
    # 3'lü seviyeden hangisine girdiğini döndürür (en yüksek yakalanan)
    hit = None
    for lv in FUNDING_LEVELS:
        if abs_fr >= lv:
            hit = lv
    return hit


# =========================
# Main scan
# =========================

def scan() -> Tuple[List[str], List[str]]:
    symbols = load_symbols()
    alerts = []
    errors = []

    for i, sym in enumerate(symbols, start=1):
        try:
            fr = get_funding(sym)
            time.sleep(REQ_SLEEP)

            if fr is None:
                continue

            abs_fr = abs(fr)
            lvl = pick_level(abs_fr)
            if lvl is None:
                continue

            kl = get_kline_5m(sym)
            time.sleep(REQ_SLEEP)

            if not kl:
                continue

            move, vol_spike = calc_move_and_vol_spike(kl)

            # Filtre: funding yüksek + 5m hareket + volume spike
            if abs(move) < MIN_5M_MOVE:
                continue
            if vol_spike < MIN_VOL_SPIKE:
                continue

            direction = "LONG squeeze riski" if fr > 0 else "SHORT squeeze riski"
            alerts.append(
                f"🔥 {sym}\n"
                f"Funding: {fr:+.6f} (seviye ≥ {lvl:.4f})\n"
                f"5m Move: {move*100:+.2f}%\n"
                f"Vol Spike: x{vol_spike:.2f}\n"
                f"Yorum: {direction}"
            )

        except requests.HTTPError as e:
            # 403/429 vb.
            errors.append(f"{sym} | Error: {str(e)}")
        except Exception as e:
            errors.append(f"{sym} | Error: {type(e).__name__}: {e}")

    return alerts, errors


def main():
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    title = "Bybit Funding Alerts"

    alerts, errors = scan()

    # Hataları da bildirelim (çok fazla olursa kısalt)
    err_text = ""
    if errors:
        # Çok spam olmasın diye ilk 10
        first = errors[:10]
        err_text = "\n\n⚠️ Errors (first 10):\n" + "\n".join(first)

    if alerts:
        msg = f"{title}\n\n" + "\n\n".join(alerts) + f"\n\n🕒 {now}" + err_text
        tg_send(msg)
        print("Sent alerts:", len(alerts))
        return

    # Alert yoksa
    if SEND_IF_EMPTY:
        base = f"{title}\n\nNo alerts.\n🕒 {now}"
        if SCAN_OK:
            base = f"{title}\n\nScan OK ✅\nNo alerts.\n🕒 {now}"
        if err_text:
            base += err_text
        tg_send(base)
        print("Sent empty status.")
    else:
        # Telegram'a boş göndermeyelim ama console'a yazalım
        print(f"{title} | No alerts. | {now}")
        if errors:
            print("Errors:", errors[:10])


if __name__ == "__main__":
    main()
