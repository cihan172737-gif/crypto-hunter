import os
import time
import math
import requests
from datetime import datetime, timezone

# ----------------------------
# GUARD: eski binance kalıntısı varsa dur
# ----------------------------
def _guard_no_binance():
    try:
        with open(__file__, "r", encoding="utf-8") as f:
            s = f.read().lower()
        bad = ["binance", "/fapi/", "premiumindex", "fapi.binance.com"]
        if any(x in s for x in bad):
            raise RuntimeError("Binance kalıntısı bulundu (binance/fapi/premiumIndex). Dosya tam Bybit olmalı.")
    except Exception as e:
        # GitHub runner'da bile bu check çalışır; sorun olursa açık mesaj verir
        if "Binance kalıntısı" in str(e):
            raise

_guard_no_binance()

# ----------------------------
# CONFIG
# ----------------------------
BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")
TIMEOUT = int(os.getenv("TIMEOUT", "20"))

SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT").split(",") if s.strip()]
MIN_ABS_FUNDING = float(os.getenv("MIN_ABS_FUNDING", "0.0005"))  # 0.05%

MIN_TURNOVER_24H = float(os.getenv("MIN_TURNOVER_24H", "50000000"))  # 50M
MIN_VOLUME_24H = float(os.getenv("MIN_VOLUME_24H", "0"))

TOP_N = int(os.getenv("TOP_N", "10"))
FUNDING_HISTORY_LIMIT = int(os.getenv("FUNDING_HISTORY_LIMIT", "12"))
OI_LIMIT = int(os.getenv("OI_LIMIT", "12"))
LS_RATIO_LIMIT = int(os.getenv("LS_RATIO_LIMIT", "12"))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

ALERT_ONLY = os.getenv("ALERT_ONLY", "1") == "1"
SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "0") == "1"

HEADERS = {"User-Agent": "crypto-hunter/2.1"}

# ----------------------------
# HELPERS
# ----------------------------
def _get(path: str, params: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    r = requests.get(url, params=params or {}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if isinstance(j, dict) and "retCode" in j and j.get("retCode") != 0:
        raise RuntimeError(f"Bybit retCode={j.get('retCode')} retMsg={j.get('retMsg')} path={path}")
    return j

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

def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram env eksik. Mesaj:\n", text)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    if r.status_code >= 300:
        print("Telegram gönderim hatası:", r.status_code, r.text)

# ----------------------------
# BYBIT ENDPOINTS
# ----------------------------
def get_tickers_linear() -> list[dict]:
    j = _get("/v5/market/tickers", {"category": "linear"})
    return j.get("result", {}).get("list", []) or []

def get_funding_history(symbol: str, limit: int) -> list[float]:
    try:
        j = _get("/v5/market/funding/history", {"category": "linear", "symbol": symbol, "limit": limit})
        rows = j.get("result", {}).get("list", []) or []
        out = []
        for it in rows:
            fr = it.get("fundingRate")
            if fr not in (None, ""):
                out.append(float(fr))
        return out
    except Exception:
        return []

def get_open_interest_history(symbol: str, limit: int) -> list[float]:
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
            if oi not in (None, ""):
                out.append(float(oi))
        return out
    except Exception:
        return []

def get_long_short_ratio(symbol: str, limit: int) -> list[float]:
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
            v = it.get("longShortRatio")
            if v not in (None, ""):
                out.append(float(v))
        return out
    except Exception:
        return []

# ----------------------------
# SCORING
# ----------------------------
def pct_change(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return (b - a) / a

def score_symbol(funding_now: float, turnover_24h: float, volume_24h: float,
                 funding_hist: list[float], oi_hist: list[float], ls_hist: list[float]) -> tuple[float, list[str]]:
    reasons = []
    score = 0.0

    # Likidite
    if turnover_24h > 0:
        score += min(25.0, math.log10(turnover_24h + 1) * 3)
    if turnover_24h >= MIN_TURNOVER_24H:
        reasons.append(f"liq ok (${turnover_24h:,.0f})")
    else:
        reasons.append(f"liq low (${turnover_24h:,.0f})")

    # Funding
    if abs(funding_now) >= MIN_ABS_FUNDING:
        score += 30.0
        reasons.append(f"fund {fmt_pct(funding_now)}")
    else:
        reasons.append(f"fund low {fmt_pct(funding_now)}")

    # Funding trend
    if len(funding_hist) >= 3:
        avg_fr = sum(funding_hist) / len(funding_hist)
        trend = funding_hist[0] - avg_fr
        score += min(10.0, abs(trend) * 2000)
        reasons.append(f"avg {fmt_pct(avg_fr)}")
        if abs(funding_now) >= 0.003:
            reasons.append("risk: funding extreme")

    # OI trend
    if len(oi_hist) >= 3:
        oi_latest = oi_hist[0]
        oi_avg = sum(oi_hist) / len(oi_hist)
        oi_chg = pct_change(oi_avg, oi_latest)
        score += max(0.0, min(15.0, abs(oi_chg) * 100))
        reasons.append(f"OI {oi_chg*100:.1f}%")

    # Long/Short
    if len(ls_hist) >= 3:
        ls_latest = ls_hist[0]
        imbalance = abs(ls_latest - 1.0)
        score += max(0.0, min(10.0, imbalance * 20))
        reasons.append(f"L/S {ls_latest:.2f}")
        if imbalance >= 0.35:
            reasons.append("risk: LS imbalanced")

    if volume_24h > 0:
        reasons.append(f"vol {volume_24h:,.0f}")

    return score, reasons

# ----------------------------
# MAIN
# ----------------------------
def scan():
    tickers = get_tickers_linear()
    wanted = set(SYMBOLS)
    tmap = {it.get("symbol"): it for it in tickers if it.get("symbol") in wanted}

    out = []
    for sym in SYMBOLS:
        it = tmap.get(sym)
        if not it:
            continue

        funding_now = safe_float(it.get("fundingRate"), None)
        if funding_now is None:
            continue

        turnover_24h = safe_float(it.get("turnover24h"), 0.0) or 0.0
        volume_24h = safe_float(it.get("volume24h"), 0.0) or 0.0
        price = it.get("lastPrice") or "?"
        next_funding = int(safe_float(it.get("nextFundingTime"), 0) or 0)

        # hard filters
        if turnover_24h < MIN_TURNOVER_24H:
            continue
        if volume_24h < MIN_VOLUME_24H:
            continue

        funding_hist = get_funding_history(sym, FUNDING_HISTORY_LIMIT)
        oi_hist = get_open_interest_history(sym, OI_LIMIT)
        ls_hist = get_long_short_ratio(sym, LS_RATIO_LIMIT)

        score, reasons = score_symbol(funding_now, turnover_24h, volume_24h, funding_hist, oi_hist, ls_hist)

        out.append({
            "symbol": sym,
            "score": score,
            "funding": funding_now,
            "next": next_funding,
            "price": price,
            "turn": turnover_24h,
            "vol": volume_24h,
            "reasons": reasons
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out

def build_message(rows):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        f"Bybit A+ Futures Scan • {now}\n"
        f"Eşik: |fund| ≥ {fmt_pct(MIN_ABS_FUNDING)} | min turn24h: ${MIN_TURNOVER_24H:,.0f}\n"
    )
    if not rows:
        return header + "\nSonuç: kriterlere uyan aday yok."

    lines = [header, ""]
    for i, r in enumerate(rows[:TOP_N], 1):
        lines.append(
            f"{i}) {r['symbol']} | score {r['score']:.1f}\n"
            f"   fund: {fmt_pct(r['funding'])} | next: {ms_to_local(r['next'])}\n"
            f"   price: {r['price']} | turn24h: ${r['turn']:,.0f} | vol24h: {r['vol']:,.0f}\n"
            f"   why: " + " • ".join(r["reasons"][:6])
        )
        lines.append("")
    return "\n".join(lines).strip()

def main():
    rows = scan()
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

if __name__ == "__main__":
    main()
