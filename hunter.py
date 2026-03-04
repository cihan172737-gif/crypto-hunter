import os
import requests
from datetime import datetime

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TG_CHAT  = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def tg_send(text: str) -> None:
    # Telegram env yoksa "success ama mesaj yok" olmasın diye job'u patlat
    if not TG_TOKEN or not TG_CHAT:
        raise SystemExit(f"❌ Telegram ENV missing. TOKEN? {bool(TG_TOKEN)} CHAT? {bool(TG_CHAT)}")

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}, timeout=HTTP_TIMEOUT)

    # Telegram 200 dönmezse job FAIL olsun ki hemen fark edesin
    if r.status_code != 200:
        raise SystemExit(f"❌ Telegram HTTP {r.status_code}: {r.text[:300]}")

def main():
    # 1) MESAJ GARANTİ: daha en başta ping
    tg_send(f"🚀 Hunter started ({now()})")

    # 2) Veri kaynağı (şimdilik CoinGecko derivatives — ücretsiz)
    # Not: CoinGecko bazen rate limit yapabilir; biz hatayı yakalayıp Telegram'a yazacağız.
    url = "https://api.coingecko.com/api/v3/derivatives"

    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            tg_send(f"⚠️ Data source HTTP {r.status_code}\n{r.text[:200]}\n\n{now()}")
            return

        data = r.json()
        if not isinstance(data, list):
            tg_send(f"⚠️ Unexpected data format from source. Keys: {list(data.keys())[:10]}\n\n{now()}")
            return

        # basit tarama: funding_rate alanı olanları topla
        alerts = []
        THRESH = float(os.getenv("THRESH", "0.0005"))  # 0.0005 => %0.05

        for x in data:
            fr = x.get("funding_rate")
            sym = x.get("symbol") or ""
            market = x.get("market") or ""
            if fr is None:
                continue
            try:
                fr = float(fr)
            except:
                continue

            if abs(fr) >= THRESH:
                alerts.append(f"🚨 {sym} | {market} | funding={(fr*100):.3f}%")

        if alerts:
            msg = "Funding Scan\n\n" + "\n".join(alerts[:30]) + f"\n\n{now()}"
        else:
            msg = f"Funding Scan\n\nScan OK ✅\nNo alerts\n\n{now()}"

        tg_send(msg)

    except Exception as e:
        # 3) API patlarsa bile Telegram’a hata mesajı gitsin
        tg_send(f"⚠️ Exception: {repr(e)}\n\n{now()}")

if __name__ == "__main__":
    main()
