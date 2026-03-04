# hunter.py — Funding Alerts (Binance Futures) + Telegram DEBUG

import os
import time
import requests
from datetime import datetime

# ---------- CONFIG ----------
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "1") == "1"

THRESH_LOW  = float(os.getenv("THRESH_LOW",  "0.0003"))
THRESH_MID  = float(os.getenv("THRESH_MID",  "0.0005"))
THRESH_HIGH = float(os.getenv("THRESH_HIGH", "0.0008"))

# Binance Futures funding endpoint
BINANCE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

# Binance’ta kesin bulunan majörler (404/400 yememek için)
DEFAULT_SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","TRXUSDT",
    "DOTUSDT","LTCUSDT","BCHUSDT","ATOMUSDT","NEARUSDT","UNIUSDT","AAVEUSDT","ETCUSDT","XLMUSDT","ICPUSDT"
]

# ---------- HELPERS ----------
def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def tg_send(text: str) -> None:
    # DEBUG: env gerçekten geliyor mu?
    if not TG_TOKEN or not TG_CHAT:
        print("❌ Telegram ENV missing!")
        print("TELEGRAM_BOT_TOKEN set?", bool(TG_TOKEN))
        print("TELEGRAM_CHAT_ID set?", bool(TG_CHAT))
        print("Message would be:\n", text)
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}

    try:
        r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        # DEBUG: Telegram ne döndü?
        print("Telegram status:", r.status_code)
        if r.status_code != 200:
            print("Telegram response:", r.text[:300])
    except Exception as e:
        print("❌ Telegram exception:", repr(e))

def get_funding(symbol: str):
    try:
        r = requests.get(BINANCE_URL, params={"symbol": symbol}, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return None, f"{r.status_code} {r.text[:120]}"
        data = r.json()
        fr = float(data["lastFundingRate"])
        return fr, None
    except Exception as e:
        return None, str(e)

def classify(fr: float):
    a = abs(fr)
    if a >= THRESH_HIGH:
        return "HIGH"
    if a >= THRESH_MID:
        return "MID"
    if a >= THRESH_LOW:
        return "LOW"
    return None

# ---------- MAIN ----------
def main():
    # 1) Telegram’a anında test mesajı at (bu gelmiyorsa %100 ENV/Token/ChatID)
    tg_send(f"🚀 Hunter started ({now()})")

    alerts = []
    errors = []

    for sym in DEFAULT_SYMBOLS:
        fr, err = get_funding(sym)
        if err:
            errors.append(f"{sym} | {err}")
            time.sleep(0.12)
            continue

        lvl = classify(fr)
        if lvl:
            direction = "POS" if fr > 0 else "NEG"
            alerts.append(f"🚨 {lvl} | {sym} | funding={(fr*100):.3f}% ({direction})")

        time.sleep(0.12)

    msg_lines = ["Funding Alerts (Binance)"]

    if errors:
        msg_lines.append("⚠️ Errors (first 10):")
        msg_lines.extend(errors[:10])

    if alerts:
        msg_lines.append("")
        msg_lines.append(f"✅ Alerts ({len(alerts)}):")
        msg_lines.extend(alerts[:50])
        msg_lines.append("")
        msg_lines.append(now())
        tg_send("\n".join(msg_lines))
        print("\n".join(msg_lines))
        return

    if SEND_IF_EMPTY:
        msg_lines.append("")
        msg_lines.append("Scan OK ✅")
        msg_lines.append("No alerts.")
        msg_lines.append(now())
        tg_send("\n".join(msg_lines))
        print("\n".join(msg_lines))
    else:
        print("No alerts. " + now())

if __name__ == "__main__":
    main()
