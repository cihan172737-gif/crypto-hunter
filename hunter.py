import os
import time
import requests

# --- CONFIG ---
SYMBOLS = os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT").split(",")
BYBIT_BASE = os.getenv("BYBIT_BASE", "https://api.bybit.com")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ALERT_ONLY = os.getenv("ALERT_ONLY", "1") == "1"
SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "0") == "1"

# Funding threshold (ör: 0.0005 = %0.05)
THRESHOLD = float(os.getenv("THRESHOLD", "0.0005"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; crypto-hunter/1.0; +https://github.com/)",
    "Accept": "application/json,text/plain,*/*",
}

def tg_send(text: str):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env eksik. Mesaj:\n", text)
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text}
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()

def bybit_funding(symbol: str) -> float:
    # Bybit v5 endpoint (linear perpetual)
    url = f"{BYBIT_BASE}/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)

    if r.status_code != 200:
        raise RuntimeError(f"Bybit HTTP {r.status_code}: {r.text[:200]}")

    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit retCode {data.get('retCode')}: {data.get('retMsg')}")

    lst = data.get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError(f"Bybit empty list for {symbol}")

    item = lst[0]
    fr = item.get("fundingRate")
    if fr is None:
        raise RuntimeError(f"fundingRate missing for {symbol}: {item}")

    return float(fr)

def main():
    lines = []
    hits = []

    for s in SYMBOLS:
        s = s.strip()
        if not s:
            continue
        try:
            fr = bybit_funding(s)
            lines.append(f"{s} funding: {fr}")
            if abs(fr) >= THRESHOLD:
                hits.append(f"⚠️ {s} funding: {fr} (>= {THRESHOLD})")
        except Exception as e:
            lines.append(f"{s} ERROR: {e}")

        time.sleep(0.3)

    if ALERT_ONLY:
        msg = "Bybit Funding Alerts\n\n" + ("\n".join(hits) if hits else "No alerts.")
        if hits or SEND_IF_EMPTY:
            tg_send(msg)
        print(msg)
    else:
        msg = "Bybit Funding Scan\n\n" + "\n".join(lines)
        tg_send(msg)
        print(msg)

if __name__ == "__main__":
    main()
