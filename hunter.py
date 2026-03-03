import os
import math
import requests
from datetime import datetime

BASE = "https://fapi.binance.com"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# A+ eşikler (kilit)
FUNDING_ABS = 0.0010        # 0.10% => 0.0010
OI_8H_PCT = 0.08            # +8%
RATIO_ONE_SIDE = 0.70       # %70 tek taraf
MAX_1H_MOVE = 0.012         # 1.2% üstü ise "breakout başladı" say, sinyal verme
NEAR_24H_EDGE = 0.005       # 24h high/low'a 0.5% yakınlık (direnç/destek proxy)

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]

ALERT_ONLY = os.environ.get("ALERT_ONLY", "1") == "1"
SEND_IF_EMPTY = os.environ.get("SEND_IF_EMPTY", "0") == "1"

def tg_send(msg: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TG_CHAT,
        "text": msg,
        "disable_web_page_preview": True
    }, timeout=25)
    r.raise_for_status()

def get_json(path, params=None):
    r = requests.get(f"{BASE}{path}", params=params, timeout=25)
    r.raise_for_status()
    return r.json()

def funding_rate(symbol: str) -> float:
    # premiumIndex: lastFundingRate alanı var (string)
    j = get_json("/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(j.get("lastFundingRate", 0.0))

def mark_price(symbol: str) -> float:
    j = get_json("/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(j.get("markPrice", 0.0))

def oi_change_8h(symbol: str) -> float | None:
    # 1h OI history’den 9 data al (şimdi ve 8 saat önce)
    j = get_json("/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "limit": 9})
    if not isinstance(j, list) or len(j) < 9:
        return None
    now_oi = float(j[-1]["sumOpenInterest"])
    old_oi = float(j[0]["sumOpenInterest"])
    if old_oi <= 0:
        return None
    return (now_oi - old_oi) / old_oi

def long_short_ratio(symbol: str) -> float | None:
    # Global long/short account ratio (1h)
    j = get_json("/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "1h", "limit": 1})
    if not isinstance(j, list) or not j:
        return None
    # longShortRatio >1 ise long ağırlık
    lsr = float(j[0]["longShortRatio"])
    # long yüzdesi = lsr / (1+lsr)
    long_pct = lsr / (1.0 + lsr)
    return long_pct

def one_hour_move(symbol: str) -> float | None:
    # 1h kline: close-open / open
    j = get_json("/fapi/v1/klines", {"symbol": symbol, "interval": "1h", "limit": 2})
    if not isinstance(j, list) or len(j) < 2:
        return None
    # son kapanmış mum: j[-2]
    o = float(j[-2][1])
    c = float(j[-2][4])
    if o <= 0:
        return None
    return abs(c - o) / o

def near_24h_extreme(symbol: str, price: float, side: str) -> bool:
    # side: "short" => 24h high'a yakın, "long" => 24h low'a yakın
    j = get_json("/fapi/v1/ticker/24hr", {"symbol": symbol})
    hi = float(j["highPrice"])
    lo = float(j["lowPrice"])
    if side == "short":
        return abs(hi - price) / max(hi, 1e-9) <= NEAR_24H_EDGE
    else:
        return abs(price - lo) / max(lo, 1e-9) <= NEAR_24H_EDGE

def build_signal(symbol: str):
    fr = funding_rate(symbol)
    price = mark_price(symbol)
    oi8 = oi_change_8h(symbol)
    lp = long_short_ratio(symbol)
    mv1h = one_hour_move(symbol)

    if oi8 is None or lp is None or mv1h is None:
        return None

    # breakout filtresi: son 1h çok hareketliyse sinyal verme
    if mv1h > MAX_1H_MOVE:
        return None

    # Tek taraf koşulu
    long_pct = lp
    short_pct = 1.0 - lp

    # SHORT A+
    if fr >= FUNDING_ABS and oi8 >= OI_8H_PCT and long_pct >= RATIO_ONE_SIDE:
        # direnç proxy: 24h high'a yakın
        if not near_24h_extreme(symbol, price, "short"):
            return None
        direction = "🔴 SHORT"
        return fr, oi8, long_pct, mv1h, price, direction

    # LONG A+
    if fr <= -FUNDING_ABS and oi8 >= OI_8H_PCT and short_pct >= RATIO_ONE_SIDE:
        # destek proxy: 24h low'a yakın
        if not near_24h_extreme(symbol, price, "long"):
            return None
        direction = "🟢 LONG"
        return fr, oi8, long_pct, mv1h, price, direction

    return None

def main():
    hits = []

    for s in SYMBOLS:
        sig = build_signal(s)
        if sig:
            fr, oi8, long_pct, mv1h, price, direction = sig
            hits.append((s, fr, oi8, long_pct, mv1h, price, direction))

    if not hits:
        if SEND_IF_EMPTY:
            tg_send(f"🧊 A+ yok.\nSaat: {datetime.now().strftime('%H:%M')}")
        return

    # Mesaj
    header = "🚨 A+ SETUP" if ALERT_ONLY else "🕵️ KRİPTO RAPORU"
    msg = f"{header} ({datetime.now().strftime('%H:%M')})\n"
    msg += "Filtre: |funding|≥0.10%, OI8h≥+8%, tek taraf≥70%, 1h move≤1.2%\n"

    for (s, fr, oi8, long_pct, mv1h, price, direction) in hits[:3]:
        msg += "\n" + "—"*28 + "\n"
        msg += f"• {s}  | Mark: {price:.2f}\n"
        msg += f"  Funding: {fr*100:.3f}%\n"
        msg += f"  OI (8h): {oi8*100:.1f}%\n"
        msg += f"  Long%: {long_pct*100:.0f}% (Short%: {(1-long_pct)*100:.0f}%)\n"
        msg += f"  1h move: {mv1h*100:.2f}%\n"
        msg += f"\n{direction}\n"

    tg_send(msg)

if __name__ == "__main__":
    main()
