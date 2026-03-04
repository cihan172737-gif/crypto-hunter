import os
import time
import math
import requests
from datetime import datetime, timezone

# ============================================================
# CONFIG (env ile kontrol edilebilir)
# ============================================================
BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")
TIMEOUT = int(os.getenv("TIMEOUT", "20"))

# Virgülle sembol listesi: BTCUSDT,ETHUSDT...
SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT").split(",") if s.strip()]

# Funding eşiği (abs)
MIN_ABS_FUNDING = float(os.getenv("MIN_ABS_FUNDING", "0.0005"))  # 0.05%

# Likidite filtreleri (24h)
MIN_TURNOVER_24H = float(os.getenv("MIN_TURNOVER_24H", "50000000"))  # 50M USDT
MIN_VOLUME_24H = float(os.getenv("MIN_VOLUME_24H", "0"))  # istersen 0 değil, örn 10000

# A+ skor parametreleri
TOP_N = int(os.getenv("TOP_N", "10"))
FUNDING_HISTORY_LIMIT = int(os.getenv("FUNDING_HISTORY_LIMIT", "12"))  # son 12 funding ~ 4 gün (8h)
OI_LIMIT = int(os.getenv("OI_LIMIT", "12"))
LS_RATIO_LIMIT = int(os.getenv("LS_RATIO_LIMIT", "12"))

# Telegram
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

ALERT_ONLY = os.getenv("ALERT_ONLY", "1") == "1"  # 1: sadece fırsat varsa gönder
SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "0") == "1"

HEADERS = {"User-Agent": "crypto-hunter/2.0"}

# ============================================================
# LOW LEVEL HTTP
# ============================================================
def _get(path: str, params: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    r = requests.get(url, params=params or {}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    # Bybit v5 response: { retCode, retMsg, result: {...} }
    if isinstance(j, dict) and "retCode" in j and j.get("retCode") != 0:
        raise RuntimeError(f"Bybit retCode={j.get('retCode')} retMsg={j.get('retMsg')} path={path}")
    return j

def _post(url: str, payload: dict) -> dict:
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

# ============================================================
# TELEGRAM
# ============================================================
def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env eksik (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). Mesajı konsola basıyorum:\n")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}
    try:
        res = _post(url, payload)
        if not res.get("ok", False):
            print("Telegram error:", res)
    except Exception as e:
        print("Telegram gönderim hatası:", e)

# ============================================================
# BYBIT DATA
# ============================================================
def get_tickers_linear() -> list[dict]:
    # /v5/market/tickers?category=linear -> list
    j = _get("/v5/market/tickers", {"category": "linear"})
    return j.get("result", {}).get("list", []) or []

def get_funding_history(symbol: str, limit: int) -> list[float]:
    # /v5/market/funding/history?category=linear&symbol=...&limit=...
    # bazı hesaplarda/regionlarda limit/format değişebilir; hata olursa boş dön
    try:
        j = _get("/v5/market/funding/history", {"category": "linear", "symbol": symbol, "limit": limit})
        rows = j.get("result", {}).get("list", []) or []
        out = []
        for it in rows:
            fr = it.get("fundingRate")
            if fr is None or fr == "":
                continue
            out.append(float(fr))
        return out
    except Exception:
        return []

def get_open_interest_history(symbol: str, limit: int) -> list[float]:
    # /v5/market/open-interest?category=linear&symbol=...&intervalTime=5min&limit=...
    # intervalTime: 5min/15min/30min/1h/4h/1d vs değişebilir; 15min daha stabil olur
    try:
        j = _get("/v5/market/open-interest", {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": "15min",
            "limit": limit
        })
        rows = j.get("result", {}).get("list", []) or []
        out = []
        for it in rows:
            oi = it.get("openInterest")
            if oi is None or oi == "":
                continue
            out.append(float(oi))
        return out
    except Exception:
        return []

def get_long_short_ratio(symbol: str, limit: int) -> list[float]:
    # /v5/market/account-ratio?category=linear&symbol=...&period=15min&limit=...
    # Dönen alan çoğu zaman longShortRatio / buyRatio / sellRatio vs
    try:
        j = _get("/v5/market/account-ratio", {
            "category": "linear",
            "symbol": symbol,
            "period": "15min",
            "limit": limit
        })
        rows = j.get("result", {}).get("list", []) or []
        out = []
        for it in rows:
            val = it.get("longShortRatio")
            if val is None or val == "":
                continue
            out.append(float(val))
        return out
    except Exception:
        return []

# ============================================================
# SCORING
# ============================================================
def pct_change(a: float, b: float) -> float:
    # (b-a)/a
    if a == 0:
        return 0.0
    return (b - a) / a

def safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def fmt_pct(x: float) -> str:
    return f"{x*100:.4f}%"

def ms_to_local(ms: int) -> str:
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"

def score_symbol(
    funding_now: float,
    turnover_24h: float,
    volume_24h: float,
    funding_hist: list[float],
    oi_hist: list[float],
    ls_hist: list[float],
) -> tuple[float, list[str]]:
    """
    Basit ama işe yarar A+ skoru:
    - Likidite yüksekse +puan
    - Funding eşiği aşıldıysa +puan (ama aşırı uçta ise risk notu)
    - Funding trendi (ort/son) +puan
    - OI trend (son vs ort) +puan
    - Long/Short dengesizliği (1'den uzaklık) +puan / risk notu
    """
    reasons = []
    score = 0.0

    # Likidite
    # log ölçek ile aşırı şişmesin
    liq = max(turnover_24h, 0.0)
    if liq > 0:
        score += min(25.0, math.log10(liq + 1) * 3)  # max ~25
    if turnover_24h >= MIN_TURNOVER_24H:
        reasons.append(f"liq ok (${turnover_24h:,.0f} turn24h)")
    else:
        reasons.append(f"liq low (${turnover_24h:,.0f} turn24h)")

    # Funding ana sinyal
    abs_fr = abs(funding_now)
    if abs_fr >= MIN_ABS_FUNDING:
        score += 30.0
        reasons.append(f"funding hit {fmt_pct(funding_now)}")
    else:
        reasons.append(f"funding low {fmt_pct(funding_now)}")

    # Funding trend (geçmiş)
    if len(funding_hist) >= 3:
        avg_fr = sum(funding_hist) / len(funding_hist)
        # trend: son - ort
        trend = funding_hist[0] - avg_fr  # Bybit history list çoğu zaman newest->oldest
        score += min(10.0, abs(trend) * 2000)  # kaba ölçek
        reasons.append(f"fund avg {fmt_pct(avg_fr)}")

        # Aşırı uç risk notu
        if abs(funding_now) >= 0.003:  # 0.30%/8h gibi
            reasons.append("risk: funding extreme")

    # Open Interest trend
    if len(oi_hist) >= 3:
        oi_latest = oi_hist[0]
        oi_avg = sum(oi_hist) / len(oi_hist)
        oi_chg = pct_change(oi_avg, oi_latest)
        score += max(0.0, min(15.0, abs(oi_chg) * 100))  # %10 => +10 gibi
        reasons.append(f"OI chg ~{oi_chg*100:.1f}%")

    # Long/Short imbalance
    if len(ls_hist) >= 3:
        ls_latest = ls_hist[0]
        imbalance = abs(ls_latest - 1.0)  # 1.0 dengeli
        score += max(0.0, min(10.0, imbalance * 20))  # 0.2 => +4
        reasons.append(f"L/S {ls_latest:.2f}")
        if imbalance >= 0.35:
            reasons.append("risk: LS imbalanced")

    # Volume check (sadece bilgi)
    if volume_24h > 0:
        reasons.append(f"vol24h {volume_24h:,.0f}")

    return score, reasons

# ============================================================
# MAIN SCAN
# ============================================================
def scan():
    tickers = get_tickers_linear()
    wanted = set(SYMBOLS)

    # tickers içinden sadece istediğimiz semboller
    tmap = {}
    for it in tickers:
        sym = it.get("symbol")
        if sym in wanted:
            tmap[sym] = it

    results = []
    for sym in SYMBOLS:
        it = tmap.get(sym)
        if not it:
            continue

        funding_now = safe_float(it.get("fundingRate"), default=None)
        if funding_now is None:
            continue

        turnover_24h = safe_float(it.get("turnover24h"), default=0.0) or 0.0
        volume_24h = safe_float(it.get("volume24h"), default=0.0) or 0.0
        last_price = it.get("lastPrice") or "?"
        next_funding_ms = int(safe_float(it.get("nextFundingTime"), default=0) or 0)

        # Likidite filtresi (hard filter)
        if turnover_24h < MIN_TURNOVER_24H:
            # çok düşükse tamamen ele (istersen env ile kapatırsın)
            continue
        if volume_24h < MIN_VOLUME_24H:
            continue

        # Ek metrikler
        funding_hist = get_funding_history(sym, FUNDING_HISTORY_LIMIT)
        oi_hist = get_open_interest_history(sym, OI_LIMIT)
        ls_hist = get_long_short_ratio(sym, LS_RATIO_LIMIT)

        score, reasons = score_symbol(
            funding_now=funding_now,
            turnover_24h=turnover_24h,
            volume_24h=volume_24h,
            funding_hist=funding_hist,
            oi_hist=oi_hist,
            ls_hist=ls_hist,
        )

        results.append({
            "symbol": sym,
            "score": score,
            "funding": funding_now,
            "nextFundingTime": next_funding_ms,
            "price": last_price,
            "turnover24h": turnover_24h,
            "volume24h": volume_24h,
            "reasons": reasons,
        })

    # Skora göre sırala
    results.sort(key=lambda x: x["score"], reverse=True)
    return results

def build_message(rows: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        f"Bybit A+ Futures Scan • {now}\n"
        f"Eşik: |funding| ≥ {fmt_pct(MIN_ABS_FUNDING)} | "
        f"min turn24h: ${MIN_TURNOVER_24H:,.0f}\n"
    )

    if not rows:
        return header + "\nSonuç: kriterlere uyan A+ aday yok."

    out = [header, ""]
    for i, r in enumerate(rows[:TOP_N], 1):
        out.append(
            f"{i}) {r['symbol']}  | score: {r['score']:.1f}\n"
            f"   funding: {fmt_pct(r['funding'])} | next: {ms_to_local(r['nextFundingTime'])}\n"
            f"   price: {r['price']} | turn24h: ${r['turnover24h']:,.0f} | vol24h: {r['volume24h']:,.0f}\n"
            f"   why: " + " • ".join(r["reasons"][:6])
        )
        out.append("")
    return "\n".join(out).strip()

def main():
    try:
        rows = scan()

        # ALERT_ONLY: funding eşiği geçen var mı?
        hits = [r for r in rows if abs(r["funding"]) >= MIN_ABS_FUNDING]
        msg = build_message(rows)

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
