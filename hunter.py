import os
import requests
from datetime import datetime

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TG_CHAT  = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# Final ayarlar
THRESH = float(os.getenv("THRESH", "0.0025"))  # 0.25% => 0.0025
TOP_N = int(os.getenv("TOP_N", "5"))
MAJOR_ONLY = os.getenv("MAJOR_ONLY", "1") == "1"
MAJORS = {"BTC", "ETH", "SOL", "XRP", "BNB"}

# Sadece Binance + Bybit
BIG_EXCHANGES = {"BINANCE (FUTURES)", "BYBIT (FUTURES)"}

# IP blok yemeyen kaynak (GitHub Actions için stabil)
SOURCE_URL = "https://api.coingecko.com/api/v3/derivatives"

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def tg_send(text: str) -> None:
    # ENV eksikse “Success ama mesaj yok” olmasın: job fail
    if not TG_TOKEN or not TG_CHAT:
        raise SystemExit(f"❌ Telegram ENV missing. TOKEN? {bool(TG_TOKEN)} CHAT? {bool(TG_CHAT)}")

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True},
        timeout=HTTP_TIMEOUT
    )
    if r.status_code != 200:
        raise SystemExit(f"❌ Telegram HTTP {r.status_code}: {r.text[:250]}")

def base_from_symbol(symbol: str) -> str:
    s = (symbol or "").upper()
    for sep in ["-", "_", "/"]:
        if sep in s:
            return s.split(sep)[0]
    # BTCUSDT gibi gelenler için
    if s.startswith("BTC"): return "BTC"
    if s.startswith("ETH"): return "ETH"
    if s.startswith("SOL"): return "SOL"
    if s.startswith("XRP"): return "XRP"
    if s.startswith("BNB"): return "BNB"
    return s[:4]

def main():
    # Başlangıç ping (istersen sonra kapatırız)
    tg_send(f"🚀 Hunter started ({now()})")

    try:
        r = requests.get(SOURCE_URL, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            tg_send(f"⚠️ Source HTTP {r.status_code}\n{r.text[:200]}\n\n{now()}")
            return

        data = r.json()
        if not isinstance(data, list):
            tg_send(f"⚠️ Unexpected source format\n\n{now()}")
            return

        # Her coin için “en iyi (en yüksek) POS funding”i seçeceğiz
        best = {}  # base -> (funding_rate, market)

        for x in data:
            market = (x.get("market") or "").upper()
            if market not in BIG_EXCHANGES:
                continue

            sym = x.get("symbol") or ""
            base = base_from_symbol(sym)

            if MAJOR_ONLY and base not in MAJORS:
                continue

            fr = x.get("funding_rate")
            if fr is None:
                continue
            try:
                fr = float(fr)
            except:
                continue

            # SADECE POS funding (arbitrage kolay)
            if fr <= 0:
                continue

            # Eşik
            if fr < THRESH:
                continue

            # best-per-coin seç
            prev = best.get(base)
            if (prev is None) or (fr > prev[0]):
                best[base] = (fr, market)

        # Mesaj oluştur
        if not best:
            tg_send(
                "Funding Scan (POS only | Binance+Bybit)\n"
                "Scan OK ✅\nNo opportunities\n"
                f"Threshold: {THRESH*100:.2f}%\n\n{now()}"
            )
            return

        # Top N sırala
        rows = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:TOP_N]

        lines = [
            "🚨 TOP POS FUNDING (Binance+Bybit)",
            f"Threshold: {THRESH*100:.2f}%",
            "Strategy: Short-perp + Spot-long",
            ""
        ]

        for base, (fr, market) in rows:
            # CoinGecko bazen 1.00% gibi “cap” değer gösterebiliyor, şüpheli olanı işaretleyelim
            cap_note = " ⚠️(check)" if fr >= 0.0095 else ""
            lines.append(f"✅ {base} | {market.title()} | {fr*100:.2f}%{cap_note}")

        lines.append(f"\n{now()}")
        tg_send("\n".join(lines))

    except Exception as e:
        tg_send(f"⚠️ Exception: {repr(e)}\n\n{now()}")

if __name__ == "__main__":
    main()
