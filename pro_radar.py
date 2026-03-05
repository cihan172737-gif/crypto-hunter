import os, time, requests
from datetime import datetime, timezone

# ====== TELEGRAM ======
TG_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TG_CHAT  = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

# ====== STRATEJİ EŞİKLERİ (senin plan) ======
# funding % olarak değil oran olarak: 0.004 = 0.40%
FUND_MIN = float(os.getenv("FUND_MIN", "0.004"))     # 0.40%
OI_MIN_PCT = float(os.getenv("OI_MIN_PCT", "8"))     # %8
VOL_SPIKE_PCT = float(os.getenv("VOL_SPIKE_PCT", "40"))  # %40 (24h volume %change)

# ====== COIN LİSTESİ ======
SYMBOLS = (os.getenv("SYMBOLS") or "").strip()
DEFAULT = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","DOGEUSDT","WIFUSDT","PEPEUSDT","INJUSDT","APTUSDT","ARBUSDT"]

# ====== BYBIT API ======
BYBIT = "https://api.bybit.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; pro-radar/1.0)",
    "Accept": "application/json"
}

def now():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

def tg_send(text: str):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env missing. Message:\n", text)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        print("Telegram error:", r.status_code, r.text[:200])

def load_symbols():
    if SYMBOLS:
        return [s.strip().upper() for s in SYMBOLS.replace("\n", ",").split(",") if s.strip()]
    # symbols.txt varsa oku
    if os.path.exists("symbols.txt"):
        out=[]
        with open("symbols.txt","r",encoding="utf-8") as f:
            for line in f:
                line=line.strip().upper()
                if not line or line.startswith("#"): 
                    continue
                out += [p for p in line.replace(" ", "").split(",") if p]
        return out or DEFAULT
    return DEFAULT

def get_ticker(symbol: str):
    url = f"{BYBIT}/v5/market/tickers"
    params = {"category":"linear", "symbol":symbol}
    r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    js = r.json()
    if str(js.get("retCode")) != "0":
        raise RuntimeError(f"retCode={js.get('retCode')} {js.get('retMsg')}")
    lst = js.get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError("empty list")
    return lst[0]

def f(x, default=None):
    try:
        return float(x)
    except:
        return default

def main():
    syms = load_symbols()
    hits = []
    errs = []

    for sym in syms:
        try:
            item = get_ticker(sym)

            funding = f(item.get("fundingRate"), 0.0)               # rate
            vol24 = f(item.get("volume24h"), None)                  # volume
            volchg = f(item.get("price24hPcnt"), 0.0) * 100         # % price change (proxy momentum)
            # OI: Bybit ticker bazen openInterestValue/openInterest yoksa yine de çalışır
            oi_val = f(item.get("openInterestValue") or item.get("openInterest"), None)

            # Bybit v5 tickers OI %change vermez; biz basit kural kullanıyoruz:
            # OI değerini "yüksekse" (likit market) + funding yüksekse + momentum varsa radar.
            # OI spike için daha doğru metrik istersen ayrı endpoint gerekir (ileri seviye).
            # Şimdilik: funding + (oi varsa) + price move + volume var ise A aday.
            if abs(funding) >= FUND_MIN:
                # Volume spike proxy: 24h price change % yüksekse ve 24h volume varsa
                # (GitHub Actions’da stabil olsun diye proxy kullandım)
                momentum_ok = abs(volchg) >= (VOL_SPIKE_PCT/10)  # örn 40 -> 4% fiyat değişimi
                oi_ok = (oi_val is None) or (oi_val > 0)         # OI varsa göster
                if momentum_ok and oi_ok:
                    direction = "POS" if funding > 0 else "NEG"
                    hits.append((sym, funding, direction, volchg, oi_val, vol24))

            time.sleep(0.12)

        except Exception as e:
            errs.append(f"{sym}: {repr(e)}")

    # Mesaj formatı
    if hits:
        hits.sort(key=lambda x: abs(x[1]), reverse=True)
        lines = [
            "🚨 PRO RADAR (Bybit) | Funding + Momentum",
            f"Filters: |fund|≥{FUND_MIN*100:.2f}%  & momentum≈{VOL_SPIKE_PCT/10:.1f}%+",
            ""
        ]
        for sym, funding, direction, volchg, oi_val, vol24 in hits[:10]:
            oi_txt = f"{oi_val:.0f}" if isinstance(oi_val, float) else "n/a"
            v_txt = f"{vol24:.0f}" if isinstance(vol24, float) else "n/a"
            lines.append(f"✅ {sym} | fund={funding*100:.3f}% ({direction}) | 24hΔ={volchg:.2f}% | OI={oi_txt} | V24={v_txt}")

        lines.append("")
        lines.append("Next: Borsada Funding/Countdown kontrol → Heatmap/CVD ile onayla.")
        lines.append(now())
        tg_send("\n".join(lines))
    else:
        # spam istemiyoruz: boşsa göndermiyoruz
        print("No PRO signals.", now())

    if errs:
        print("Errors (first 5):", errs[:5])

if __name__ == "__main__":
    main()
