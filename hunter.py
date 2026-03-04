import os
import time
import requests
from datetime import datetime, timezone

# ----------------------------
# CONFIG
# ----------------------------
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]  # Bybit linear perp sembolleri (USDT)
TIMEFRAME_HOURS = 8  # funding periyodu genelde 8h
MIN_ABS_FUNDING = 0.0005  # 0.05% (8h)

ALERT_ONLY = os.getenv("ALERT_ONLY", "1") == "1"
SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "0") == "1"

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

# Bybit v5 public API
BASE_URL = "https://api.bybit.com"
TIMEOUT = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; crypto-hunter/1.0)"
}

# ----------------------------
# HELPERS
# ----------------------------
def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env eksik: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    if r.status_code >= 300:
        print("Telegram gönderim hatası:", r.status_code, r.text)

def bybit_linear_tickers():
    """
    Bybit: v5/market/tickers?category=linear
    Dönen listede fundingRate ve nextFundingTime var.
    """
    url = f"{BASE_URL}/v5/market/tickers"
    params = {"category": "linear"}  # USDT perpetual/futures
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit API hata: {data}")
    return data["result"]["list"]

def fmt_pct(x: float) -> str:
    return f"{x*100:.4f}%"

def ms_to_dt(ms: int) -> str:
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"

def scan_bybit_funding():
    tickers = bybit_linear_tickers()

    wanted = set(SYMBOLS)
    hits = []

    for it in tickers:
        sym = it.get("symbol")
        if sym not in wanted:
            continue

        # fundingRate bazı anlarda boş/None gelebilir
        fr_raw = it.get("fundingRate")
        if fr_raw is None or fr_raw == "":
            continue

        try:
            fr = float(fr_raw)
        except ValueError:
            continue

        if abs(fr) >= MIN_ABS_FUNDING:
            next_funding_ms = int(it.get("nextFundingTime") or 0)
            last_price = it.get("lastPrice") or "?"
            # 24h turnover/volume alanları bazen farklı gelebilir
            turnover_24h = it.get("turnover24h") or it.get("turnover") or "?"
            vol_24h = it.get("volume24h") or it.get("volume") or "?"

            hits.append({
                "symbol": sym,
                "funding": fr,
                "nextFundingTime": next_funding_ms,
                "lastPrice": last_price,
                "turnover24h": turnover_24h,
                "volume24h": vol_24h,
            })

    # funding mutlak değere göre büyükten küçüğe
    hits.sort(key=lambda x: abs(x["funding"]), reverse=True)
    return hits

def build_message(hits):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"Bybit A+ Funding Scan ({TIMEFRAME_HOURS}h) • {now}\nEşik: |funding| ≥ {fmt_pct(MIN_ABS_FUNDING)}\n"

    if not hits:
        return header + "\nSonuç: eşik üstü fırsat yok."

    lines = [header, ""]
    for i, h in enumerate(hits, 1):
        lines.append(
            f"{i}) {h['symbol']} | funding: {fmt_pct(h['funding'])} | next: {ms_to_dt(h['nextFundingTime'])}\n"
            f"   price: {h['lastPrice']} | vol24h: {h['volume24h']} | turn24h: {h['turnover24h']}"
        )
    return "\n".join(lines)

def main():
    try:
        hits = scan_bybit_funding()
        msg = build_message(hits)

        if ALERT_ONLY:
            if hits:
                tg_send(msg)
            else:
                if SEND_IF_EMPTY:
                    tg_send(msg)
                else:
                    print(msg)
        else:
            tg_send(msg)

    except Exception as e:
        err = f"Bybit scan ERROR: {e}"
        print(err)
        tg_send(err)

if __name__ == "__main__":
    main()
