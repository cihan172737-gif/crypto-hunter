import os
import requests
from datetime import datetime

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

TG_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TG_CHAT  = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# Ayarlar
THRESH = float(os.getenv("THRESH", "0.005"))  # 0.50% (0.005 => %0.50)
TOP_N = int(os.getenv("TOP_N", "5"))

# Sadece Binance + Bybit
ONLY_EXCHANGES = {"BINANCE", "BYBIT"}

# Sadece majör coinler
MAJOR_ONLY = os.getenv("MAJOR_ONLY", "1") == "1"
MAJORS = {"BTC", "ETH", "SOL", "XRP", "BNB"}

# Kaynak (IP block yemiyor)
SOURCE_URL = "https://api.coingecko.com/api/v3/derivatives"

# CoinGecko market isimleri
EXCHANGE_MARKETS = {
    "BINANCE": {"BINANCE (FUTURES)"},
    "BYBIT": {"BYBIT (FUTURES)"},
}

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        raise SystemExit("❌ Telegram ENV missing (token/chat_id).")

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
    if s.startswith("BTC"): return "BTC"
    if s.startswith("ETH"): return "ETH"
    if s.startswith("SOL"): return "SOL"
    if s.startswith("XRP"): return "XRP"
    if s.startswith("BNB"): return "BNB"
    return s[:4]

def allowed_market(market: str) -> bool:
    m = (market or "").upper()
    allowed = set()
    for ex in ONLY_EXCHANGES:
        allowed |= EXCHANGE_MARKETS.get(ex, set())
    return m in allowed

def main():
    # Başlangıç ping (istersen kaldırırız)
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

        rows = []
        for x in data:
            market = x.get("market") or ""
            if not allowed_market(market):
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

            # ✅ SADECE POS funding
            if fr <= 0:
                continue

            if fr >= THRESH:
                rows.append((fr, f"✅ {base} | {market} | {fr*100:.2f}% | Short-perp + Spot-long"))

        rows.sort(reverse=True, key=lambda t: t[0])

        if rows:
            lines = [
                "🚨 TOP POS FUNDING (Binance+Bybit)",
                f"Threshold: {THRESH*100:.2f}%",
                ""
            ]
            lines += [x[1] for x in rows[:TOP_N]]
            lines.append(f"\n{now()}")
            tg_send("\n".join(lines))
        else:
            tg_send(
                "Funding Scan (POS only)\n"
                "Scan OK ✅\nNo opportunities\n"
                f"Threshold: {THRESH*100:.2f}%\n\n{now()}"
            )

    except Exception as e:
        tg_send(f"⚠️ Exception: {repr(e)}\n\n{now()}")

if __name__ == "__main__":
    main()
