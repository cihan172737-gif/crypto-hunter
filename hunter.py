import os
import time
import random
import requests

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

TIMEOUT = 25
MAX_RETRIES = 4

# Bybit bazen bazı bölgelerde / bazı IP aralıklarında 403 verebiliyor.
# Bu yüzden birden fazla resmi domain deniyoruz.
BASE_CANDIDATES = [
    "https://api.bybit.com",
    "https://api.bytick.com",
    "https://api.bybitglobal.com",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bybit.com/",
    "Connection": "keep-alive",
}

def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env eksik. Mesaj:\n", text)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        if r.status_code != 200:
            print("Telegram HTTP error:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram exception:", e)

def fetch_json_with_fallback(path: str, params: dict) -> dict:
    last_err = None

    # her run’da sırayı biraz karıştır (bazı domainler anlık bloklanabiliyor)
    bases = BASE_CANDIDATES[:]
    random.shuffle(bases)

    for base in bases:
        url = f"{base}{path}"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)

                # 200 değilse body'yi göster (HTML dönüyor olabilir)
                if r.status_code != 200:
                    last_err = RuntimeError(f"HTTP {r.status_code} from {base} :: {r.text[:180]}")
                    # 403/429 gibi durumlarda biraz bekleyip tekrar dene
                    if r.status_code in (403, 429):
                        time.sleep(1.5 * attempt + random.random())
                        continue
                    break

                # JSON değilse
                ct = (r.headers.get("Content-Type") or "").lower()
                if "application/json" not in ct:
                    last_err = RuntimeError(f"Non-JSON from {base} ({ct}) :: {r.text[:180]}")
                    # WAF sayfası olabilir, retry
                    time.sleep(1.0 * attempt + random.random())
                    continue

                data = r.json()

                # Bybit retCode kontrol
                if isinstance(data, dict) and data.get("retCode") not in (0, None):
                    last_err = RuntimeError(f"Bybit retCode={data.get('retCode')} retMsg={data.get('retMsg')} @ {base}")
                    # bazen geçici; retry
                    time.sleep(0.8 * attempt + random.random())
                    continue

                return data

            except Exception as e:
                last_err = e
                time.sleep(1.0 * attempt + random.random())

    raise RuntimeError(f"All endpoints failed. Last error: {last_err}")

def get_funding(symbol: str) -> float:
    data = fetch_json_with_fallback(
        "/v5/market/tickers",
        {"category": "linear", "symbol": symbol}
    )

    lst = (data.get("result") or {}).get("list") or []
    if not lst:
        raise RuntimeError(f"Empty list for {symbol}. Raw: {str(data)[:200]}")

    fr = lst[0].get("fundingRate")
    if fr in (None, ""):
        raise RuntimeError(f"Missing fundingRate for {symbol}. Raw: {str(lst[0])[:200]}")

    return float(fr)

def main():
    lines = ["Bybit Funding Scan", ""]
    for s in SYMBOLS:
        fr = get_funding(s)
        lines.append(f"{s} funding: {fr}")

    msg = "\n".join(lines)
    tg_send(msg)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = f"Bybit scan ERROR: {e}"
        print(err)
        tg_send(err)
        raise
