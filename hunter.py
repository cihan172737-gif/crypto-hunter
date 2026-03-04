import os
import requests
from datetime import datetime

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TG_CHAT  = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# Temkinli eşik: 0.10%  => 0.0010
THRESH = float(os.getenv("THRESH", "0.0010"))

# CoinGecko derivatives endpoint (genelde GitHub Actions'ta çalışıyor)
SOURCE_URL = "https://api.coingecko.com/api/v3/derivatives"

# Sadece büyük borsalar
BIG_EXCHANGES = {
    "Binance (Futures)",
    "Bybit (Futures)",
    "OKX (Futures)",
    "Bitget Futures",
}

# İstersen sadece majör coinler kalsın (temkinli yaklaşım)
# Boş bırakırsan (""), market filtresinden geçen her şeyi tarar.
MAJOR_ONLY = os.getenv("MAJOR_ONLY", "1") == "1"
MAJORS = {"BTC", "ETH", "SOL", "XRP", "BNB"}

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        raise SystemExit(f"❌ Telegram ENV missing. TOKEN? {bool(TG_TOKEN)} CHAT? {bool(TG_CHAT)}")

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True},
        timeout=HTTP_TIMEOUT
    )
    if r.status_code != 200:
        raise SystemExit(f"❌ Telegram HTTP {r.status_code}: {r.text[:300]}")

def pick_base(symbol: str) -> str:
    # BTCUSDT, BTC-USDT, BTC/USDT, BTC_USDT gibi formatlardan base'i çek
    s = (symbol or "").upper()
    for sep in ["-", "_", "/"]:
        if sep in s:
            return s.split(sep)[0]
    # BTCUSDT gibi geldiyse ilk 3-4 harf mantığı (majörler için yeterli)
    if s.startswith("BTC"): return "BTC"
    if s.startswith("ETH"): return "ETH"
    if s.startswith("SOL"): return "SOL"
    if s.startswith("XRP"): return "XRP"
    if s.startswith("BNB"): return "BNB"
    return s[:4]

def main():
    # Başlangıç ping (geliyorsa Telegram tamam)
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

        candidates = []
        for x in data:
            market = x.get("market") or ""
            if market not in BIG_EXCHANGES:
                continue

            sym = x.get("symbol") or ""
            base = pick_base(sym)

            if MAJOR_ONLY and base not in MAJORS:
                continue

            fr = x.get("funding_rate")
            if fr is None:
                continue
            try:
                fr = float(fr)
            except:
                continue

            if abs(fr) >= THRESH:
                direction = "POS" if fr > 0 else "NEG"
                # POS: longlar shortlara öder -> short taraf funding alır
                hint = "Short-perp + Spot-long" if fr > 0 else "Long-perp + Spot-short (zor)"
                candidates.append((abs(fr), f"🚨 {base} | {market} | {fr*100:.3f}% ({direction}) | {hint}"))

        candidates.sort(reverse=True, key=lambda t: t[0])

        if candidates:
            lines = ["Funding Opportunity (Big Exchanges)", f"Threshold: {THRESH*100:.2f}%\n"]
            lines.extend([c[1] for c in candidates[:15]])
            lines.append(f"\n{now()}")
            tg_send("\n".join(lines))
        else:
            tg_send(f"Funding Scan (Big Exchanges)\nScan OK ✅\nNo alerts\nThreshold: {THRESH*100:.2f}%\n\n{now()}")

    except Exception as e:
        tg_send(f"⚠️ Exception: {repr(e)}\n\n{now()}")

if __name__ == "__main__":
    main()
