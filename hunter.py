import os
import requests
from datetime import datetime

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TG_CHAT  = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# Coinglass endpoint (bazı günler format değişebiliyor; biz kırılmayacağız)
COINGLASS_URL = "https://open-api.coinglass.com/public/v2/funding"

SYMBOLS = ["BTC","ETH","SOL","XRP","BNB","DOGE","ADA","AVAX","LINK","TRX"]

THRESH = float(os.getenv("THRESH", "0.0005"))  # abs(funding) > 0.0005 ise alarm

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def tg_send(text: str) -> None:
    # 1) Env kontrolü (yoksa direkt fail)
    if not TG_TOKEN or not TG_CHAT:
        raise SystemExit(f"❌ Telegram ENV missing. TOKEN? {bool(TG_TOKEN)} CHAT? {bool(TG_CHAT)}")

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}

    r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)

    # 2) Telegram cevabını kontrol et (200 değilse job fail)
    if r.status_code != 200:
        raise SystemExit(f"❌ Telegram HTTP {r.status_code}: {r.text[:300]}")

def get_all_funding_rows():
    try:
        r = requests.get(COINGLASS_URL, timeout=HTTP_TIMEOUT)
        # JSON değilse de job fail olmasın, ama loglayalım
        j = r.json()
    except Exception as e:
        return [], f"Coinglass request/json error: {repr(e)}"

    # Format değişikliklerine dayanıklı parse:
    # Bazı cevaplar: {"data":[...]} bazıları: {"data":{"data":[...]}} gibi gelebiliyor.
    data = j.get("data")
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            return inner, None

    # data yoksa hata metnini taşıyalım
    return [], f"Coinglass unexpected response keys: {list(j.keys())[:10]}"

def main():
    # Başlangıç mesajı (bu gelmezse Telegram tarafı kesin hata)
    tg_send(f"🚀 Hunter started ({now()})")

    rows, err = get_all_funding_rows()

    alerts = []
    if rows:
        for coin in SYMBOLS:
            for x in rows:
                if (x.get("symbol") == coin) or (x.get("base") == coin):
                    fr_raw = x.get("fundingRate")
                    try:
                        fr = float(fr_raw)
                    except:
                        fr = 0.0

                    if abs(fr) > THRESH:
                        alerts.append(f"🚨 {coin} funding {(fr*100):.3f}%")

    msg = "Funding Scan (Coinglass)\n\n"
    if err:
        msg += f"⚠️ Coinglass warning: {err}\n\n"

    if alerts:
        msg += "\n".join(alerts[:30])
    else:
        msg += "Scan OK ✅\nNo alerts"

    msg += "\n\n" + now()

    tg_send(msg)
    print(msg)

if __name__ == "__main__":
    main()
