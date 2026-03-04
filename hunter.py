# hunter.py — Bybit Funding Alerts (GitHub Actions uyumlu, sağlam sürüm)

import os
import time
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

# ✅ Tek doğru base: api.bybit.com (api2 yüzünden 404 alıyordun)
BYBIT_BASE = os.getenv("BYBIT_BASE", "https://api.bybit.com").strip() or "https://api.bybit.com"

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# 1 ise: alarm yoksa da "Scan OK" gönder
SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "1") == "1"

# Eşikler (funding rate mutlak değeri)
THRESH_LOW  = float(os.getenv("THRESH_LOW",  "0.0003"))  # %0.03
THRESH_MID  = float(os.getenv("THRESH_MID",  "0.0005"))  # %0.05
THRESH_HIGH = float(os.getenv("THRESH_HIGH", "0.0008"))  # %0.08

# Likidite filtresi (turnover24h). 0 ise kapalı.
MIN_TURNOVER_24H = float(os.getenv("MIN_TURNOVER_24H", "0"))

# Semboller env’den gelirse onu kullanır; yoksa symbols.txt; yoksa default 50
SYMBOLS_ENV = os.getenv("SYMBOLS", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; crypto-hunter/1.0)",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ---------------- HELPERS ----------------

def now_str() -> str:
    # UTC -> local timezone (Actions'ta UTC gibi düşün)
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

def load_symbols() -> List[str]:
    # 1) SYMBOLS env
    if SYMBOLS_ENV:
        parts = [p.strip().upper() for p in SYMBOLS_ENV.replace("\n", ",").split(",")]
        parts = [p for p in parts if p]
        return parts or DEFAULT_SYMBOLS_50

    # 2) symbols.txt
    try:
        if os.path.exists("symbols.txt"):
            with open("symbols.txt", "r", encoding="utf-8") as f:
                syms: List[str] = []
                for line in f:
                    line = line.strip().upper()
                    if not line or line.startswith("#"):
                        continue
                    for p in line.replace(" ", "").split(","):
                        if p:
                            syms.append(p)
                return syms or DEFAULT_SYMBOLS_50
    except Exception:
        pass

    # 3) default
    return DEFAULT_SYMBOLS_50

def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("⚠ Telegram env eksik. Mesaj aşağıda:\n", text)
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT,
        "text": text,
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            print("⚠ Telegram HTTP:", r.status_code, r.text[:200])
    except Exception as e:
        print("⚠ Telegram gönderim hatası:", repr(e))

def http_get_json(url: str, params: dict) -> Tuple[Optional[dict], Optional[str], int]:
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
    url = f"{BYBIT_BASE}/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    js, err, _ = http_get_json(url, params)
    if err:
        return None, err
    if not js:
        return None, "Empty response"
    return js, None

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
        to = item.get("turnover24h") or item.get("turnover24H")

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
    return f"{x*100:.3f}%"

# ---------------- MAIN ----------------

def main() -> None:
    symbols = load_symbols()

    alerts: List[str] = []
    errors: List[str] = []

    for sym in symbols:
        js, err = bybit_ticker(sym)
        if err:
            errors.append(f"{sym} | Error: {err}")
            time.sleep(0.10)
            continue

        fr, turnover, perr = parse_ticker(js)
        if perr:
            errors.append(f"{sym} | Error: {perr}")
            time.sleep(0.10)
            continue

        if fr is None:
            errors.append(f"{sym} | Error: fundingRate missing")
            time.sleep(0.10)
            continue

        if MIN_TURNOVER_24H > 0 and (turnover is None or turnover < MIN_TURNOVER_24H):
            time.sleep(0.10)
            continue

        lvl = classify(abs(fr))
        if lvl:
            direction = "POS" if fr > 0 else "NEG"
            alerts.append(f"🚨 {lvl} | {sym} | funding={fmt_pct(fr)} ({direction})")

        time.sleep(0.15)  # rate limit için

    header = "Bybit Funding Alerts"
    stamp = now_str()

    msg_lines = [header]

    # hataları da görünür yapalım
    if errors:
        msg_lines.append("⚠ Errors (first 10):")
        msg_lines.extend(errors[:10])

    if alerts:
        msg_lines.append("")
        msg_lines.append(f"✅ Alerts ({len(alerts)}):")
        msg_lines.extend(alerts[:50])
        msg_lines.append("")
        msg_lines.append(f"🕒 {stamp}")
        tg_send("\n".join(msg_lines))
        print("\n".join(msg_lines))
        return

    # No alerts
    if SEND_IF_EMPTY:
        msg_lines.append("")
        msg_lines.append("Scan OK ✅")
        msg_lines.append("No alerts.")
        msg_lines.append(stamp)
        tg_send("\n".join(msg_lines))
        print("\n".join(msg_lines))
    else:
        print(f"{header}\nNo alerts.\n{stamp}")
        if errors:
            print("Errors(first 10):")
            for e in errors[:10]:
                print(e)

if __name__ == "__main__":
    main()
