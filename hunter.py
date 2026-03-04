# hunter.py
# Bybit Funding Alerts (GitHub Actions / Render uyumlu)
# - 50 coin tarama (varsayılan liste)
# - 3 seviyeli alarm (LOW/MID/HIGH)
# - Telegram’a sadece alarm veya istersen “Scan OK” da gönderir
# - Bybit 403 olursa farklı base URL’leri dener ve hataları raporlar

import os
import time
import json
import requests
from datetime import datetime, timezone
from typing import List, Tuple, Optional

# ---------------- CONFIG ----------------
DEFAULT_SYMBOLS_50 = [
    "BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","TRXUSDT",
    "MATICUSDT","DOTUSDT","LTCUSDT","BCHUSDT","ATOMUSDT","FILUSDT","APTUSDT","ARBUSDT","OPUSDT","SUIUSDT",
    "INJUSDT","NEARUSDT","ETCUSDT","XLMUSDT","ICPUSDT","AAVEUSDT","UNIUSDT","IMXUSDT","SEIUSDT","FTMUSDT",
    "RUNEUSDT","GALAUSDT","PEPEUSDT","WIFUSDT","TONUSDT","SHIBUSDT","HBARUSDT","EGLDUSDT","THETAUSDT","SNXUSDT",
    "KAVAUSDT","ALGOUSDT","FLOWUSDT","SANDUSDT","MANAUSDT","AXSUSDT","CHZUSDT","ENJUSDT","DYDXUSDT","KSMUSDT",
]

# GitHub Actions IP'leri Bybit'te bazen 403 yiyebiliyor; birkaç base deniyoruz
BYBIT_BASES = [
    os.getenv("BYBIT_BASE", "").strip(),
    "https://api.bybit.com",
    "https://api2.bybit.com",
]
BYBIT_BASES = [b for b in BYBIT_BASES if b]

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# 1 ise: sadece alarm gönder (no alerts olduğunda gönderme)
ALERT_ONLY = os.getenv("ALERT_ONLY", "1") == "1"
# 1 ise: no alerts olsa bile Scan OK gönder
SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "1") == "1"

# 3 seviyeli eşikler (funding rate mutlak değeri)
# örnek: 0.0003 = %0.03
THRESH_LOW  = float(os.getenv("THRESH_LOW",  "0.0003"))
THRESH_MID  = float(os.getenv("THRESH_MID",  "0.0005"))
THRESH_HIGH = float(os.getenv("THRESH_HIGH", "0.0008"))

# likidite filtresi (turnover24h). 0 ise kapalı.
MIN_TURNOVER_24H = float(os.getenv("MIN_TURNOVER_24H", "0"))

# semboller env’den gelirse onu kullanır; yoksa symbols.txt; yoksa default 50
SYMBOLS_ENV = os.getenv("SYMBOLS", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; crypto-hunter/1.0; +https://github.com/)",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# --------------- HELPERS ---------------
def now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

def load_symbols() -> List[str]:
    if SYMBOLS_ENV:
        # "BTCUSDT,ETHUSDT" gibi
        parts = [p.strip().upper() for p in SYMBOLS_ENV.replace("\n", ",").split(",")]
        parts = [p for p in parts if p]
        return parts

    # symbols.txt varsa oku
    try:
        if os.path.exists("symbols.txt"):
            with open("symbols.txt", "r", encoding="utf-8") as f:
                syms = []
                for line in f:
                    line = line.strip().upper()
                    if not line or line.startswith("#"):
                        continue
                    # satırda virgül varsa böl
                    for p in line.replace(" ", "").split(","):
                        if p:
                            syms.append(p)
                return syms if syms else DEFAULT_SYMBOLS_50
    except Exception:
        pass

    return DEFAULT_SYMBOLS_50

def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env eksik. Mesaj:\n", text)
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT,
        "text": text,
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        print("Telegram gönderim hatası:", repr(e))

def http_get_json(url: str, params: dict) -> Tuple[Optional[dict], Optional[str], int]:
    """
    returns: (json, error_text, status_code)
    """
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
        status = r.status_code
        if status != 200:
            return None, f"{status} Client Error: {r.reason} for url: {r.url}", status
        try:
            return r.json(), None, status
        except Exception:
            return None, f"Invalid JSON for url: {r.url}", status
    except Exception as e:
        return None, str(e), 0

def bybit_ticker(symbol: str) -> Tuple[Optional[dict], Optional[str]]:
    """
    Bybit v5 market/tickers:
    https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT
    """
    params = {"category": "linear", "symbol": symbol}

    last_err = None
    for base in BYBIT_BASES:
        url = f"{base}/v5/market/tickers"
        js, err, _ = http_get_json(url, params)
        if err:
            last_err = err
            # 403 gibi durumlarda diğer base'i dene
            continue
        if not js:
            last_err = "Empty response"
            continue
        return js, None

    return None, last_err or "Unknown error"

def parse_ticker(js: dict) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    returns: (funding_rate, turnover24h, error)
    """
    try:
        if str(js.get("retCode")) != "0":
            return None, None, f"retCode={js.get('retCode')} retMsg={js.get('retMsg')}"
        result = js.get("result") or {}
        lst = result.get("list") or []
        if not lst:
            return None, None, "No ticker list"
        item = lst[0] or {}

        fr = item.get("fundingRate")
        to = item.get("turnover24h") or item.get("turnover24H")  # bazen isim farkı
        funding = float(fr) if fr is not None and fr != "" else None
        turnover = float(to) if to is not None and to != "" else None
        return funding, turnover, None
    except Exception as e:
        return None, None, f"Parse error: {e}"

def classify(abs_fr: float) -> Optional[str]:
    if abs_fr >= THRESH_HIGH:
        return "HIGH"
    if abs_fr >= THRESH_MID:
        return "MID"
    if abs_fr >= THRESH_LOW:
        return "LOW"
    return None

def fmt_pct(x: float) -> str:
    # fundingRate örn 0.0005 => %0.05
    return f"{x*100:.3f}%"

# --------------- MAIN ---------------
def main() -> None:
    symbols = load_symbols()

    alerts: List[str] = []
    errors: List[str] = []

    for sym in symbols:
        js, err = bybit_ticker(sym)
        if err:
            errors.append(f"{sym} | Error: {err}")
            continue

        fr, turnover, perr = parse_ticker(js)
        if perr:
            errors.append(f"{sym} | Error: {perr}")
            continue

        if fr is None:
            errors.append(f"{sym} | Error: fundingRate missing")
            continue

        if MIN_TURNOVER_24H > 0 and (turnover is None or turnover < MIN_TURNOVER_24H):
            # likidite düşükse pas geç
            continue

        lvl = classify(abs(fr))
        if lvl:
            direction = "POS" if fr > 0 else "NEG"
            # NEG funding = long’lar para alıyor, POS funding = short’lar para alıyor (genel yorum)
            alerts.append(f"🚨 {lvl} | {sym} | funding={fmt_pct(fr)} ({direction})")

        # ufak delay: rate limit riskini azalt
        time.sleep(0.15)

    header = "Bybit Funding Alerts"
    stamp = now_str()

    msg_lines = [header]

    # 403 gibi durumlarda önce hatayı gör
    if errors:
        msg_lines.append(f"⚠️ Errors (first 10):")
        msg_lines.extend(errors[:10])

    if alerts:
        msg_lines.append("")
        msg_lines.append(f"✅ Alerts ({len(alerts)}):")
        msg_lines.extend(alerts[:50])  # çok uzamasın
        msg_lines.append("")
        msg_lines.append(f"🕒 {stamp}")

        tg_send("\n".join(msg_lines))
        print("\n".join(msg_lines))
        return

    # No alerts
    if not ALERT_ONLY and SEND_IF_EMPTY:
        msg_lines.append("Scan OK ✅")
        msg_lines.append("No alerts.")
        msg_lines.append(stamp)
        tg_send("\n".join(msg_lines))
        print("\n".join(msg_lines))
    else:
        # sadece logla
        print(f"{header}\nNo alerts.\n{stamp}")
        if errors:
            print("Errors(first 10):")
            for e in errors[:10]:
                print(e)

if __name__ == "__main__":
    main()
