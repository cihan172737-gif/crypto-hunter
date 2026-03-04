import os
import requests

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

TIMEOUT = 20

def get_funding(symbol: str) -> float:
    # Bybit V5 endpoint (tek sembol)
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}

    r = requests.get(url, params=params, timeout=TIMEOUT)

    # 1) HTTP sorunları
    if r.status_code != 200:
        raise RuntimeError(f"Bybit HTTP {r.status_code}: {r.text[:200]}")

    # 2) JSON değilse (boş/HTML vb.)
    ct = (r.headers.get("Content-Type") or "").lower()
    if "application/json" not in ct:
        raise RuntimeError(f"Bybit non-JSON response: {ct} | body: {r.text[:200]}")

    data = r.json()

    # 3) Bybit retCode kontrol
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit retCode={data.get('retCode')} retMsg={data.get('retMsg')}")

    lst = (data.get("result") or {}).get("list") or []
    if not lst:
        raise RuntimeError(f"Bybit empty list for {symbol}. Raw: {str(data)[:200]}")

    fr = lst[0].get("fundingRate")
    if fr in (None, ""):
        raise RuntimeError(f"Bybit missing fundingRate for {symbol}. Raw: {str(lst[0])[:200]}")

    return float(fr)

def send_telegram(msg: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env eksik. Mesaj:\n", msg)
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": msg, "disable_web_page_preview": True}
    rr = requests.post(url, json=payload, timeout=TIMEOUT)
    if rr.status_code != 200:
        print("Telegram HTTP error:", rr.status_code, rr.text[:200])

def main():
    msg = "Bybit Funding Scan\n\n"
    for s in SYMBOLS:
        fr = get_funding(s)
        msg += f"{s} funding: {fr}\n"

    send_telegram(msg)

if __name__ == "__main__":
    main()
