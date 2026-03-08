import os
import time
import math
import requests
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BYBIT_BASE = "https://api.bybit.com"

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "DOTUSDT",
    "TRXUSDT",
    "APTUSDT",
    "ARBUSDT",
    "OPUSDT",
    "PEPEUSDT",
    "1000BONKUSDT",
]

# Filtreler
MIN_24H_TURNOVER_USD = 30_000_000
MIN_ABS_24H_MOVE_PCT = 2.0
MIN_ABS_FUNDING_PCT = 0.01     # %0.01
MIN_OI_CHANGE_PCT = 1.0

# Puan eşikleri
MIN_FINAL_SCORE = 4
TOP_N = 3

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "futures-hunter-v5/1.0"})


def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secret eksik.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = SESSION.post(url, data=payload, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print("Telegram gönderim hatası:", e)


def http_get(url: str, params=None, retries=3, sleep_s=2):
    last_err = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=25)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(sleep_s)
    raise last_err


def bybit_ok(data):
    return isinstance(data, dict) and data.get("retCode") == 0


def get_tickers():
    data = http_get(
        f"{BYBIT_BASE}/v5/market/tickers",
        params={"category": "linear"}
    )
    if not bybit_ok(data):
        raise RuntimeError(f"Bybit tickers hatası: {data}")

    result = {}
    for item in data["result"]["list"]:
        symbol = item.get("symbol")
        try:
            result[symbol] = {
                "lastPrice": float(item.get("lastPrice", 0) or 0),
                "markPrice": float(item.get("markPrice", 0) or 0),
                "price24hPcnt": float(item.get("price24hPcnt", 0) or 0) * 100.0,
                "turnover24h": float(item.get("turnover24h", 0) or 0),
                "volume24h": float(item.get("volume24h", 0) or 0),
            }
        except Exception:
            continue
    return result


def get_latest_funding(symbol: str):
    data = http_get(
        f"{BYBIT_BASE}/v5/market/funding/history",
        params={
            "category": "linear",
            "symbol": symbol,
            "limit": 1,
        }
    )
    if not bybit_ok(data):
        return None

    rows = data["result"].get("list", [])
    if not rows:
        return None

    try:
        return float(rows[0]["fundingRate"]) * 100.0
    except Exception:
        return None


def get_open_interest_now(symbol: str):
    data = http_get(
        f"{BYBIT_BASE}/v5/market/open-interest",
        params={
            "category": "linear",
            "symbol": symbol,
            "intervalTime": "5min",
            "limit": 1,
        }
    )
    if not bybit_ok(data):
        return None

    rows = data["result"].get("list", [])
    if not rows:
        return None

    try:
        return float(rows[0]["openInterest"])
    except Exception:
        return None


def get_open_interest_prev(symbol: str):
    data = http_get(
        f"{BYBIT_BASE}/v5/market/open-interest",
        params={
            "category": "linear",
            "symbol": symbol,
            "intervalTime": "5min",
            "limit": 2,
        }
    )
    if not bybit_ok(data):
        return None

    rows = data["result"].get("list", [])
    if len(rows) < 2:
        return None

    try:
        return float(rows[1]["openInterest"])
    except Exception:
        return None


def calc_oi_change_pct(current_oi, prev_oi):
    if current_oi is None or prev_oi is None or prev_oi == 0:
        return None
    return ((current_oi - prev_oi) / prev_oi) * 100.0


def score_signal(symbol, tickers):
    t = tickers.get(symbol)
    if not t:
        return None

    turnover = t["turnover24h"]
    move_pct = t["price24hPcnt"]
    last_price = t["lastPrice"]
    mark_price = t["markPrice"]

    if turnover < MIN_24H_TURNOVER_USD:
        return None

    funding_pct = get_latest_funding(symbol)
    oi_now = get_open_interest_now(symbol)
    oi_prev = get_open_interest_prev(symbol)
    oi_change_pct = calc_oi_change_pct(oi_now, oi_prev)

    score = 0
    reasons = []

    # Likidite / hacim
    if turnover >= 200_000_000:
        score += 2
        reasons.append("çok yüksek hacim")
    elif turnover >= 80_000_000:
        score += 1
        reasons.append("yüksek hacim")

    # Momentum
    if abs(move_pct) >= 5:
        score += 2
        reasons.append(f"sert hareket {move_pct:.2f}%")
    elif abs(move_pct) >= MIN_ABS_24H_MOVE_PCT:
        score += 1
        reasons.append(f"hareketli piyasa {move_pct:.2f}%")

    # Funding
    if funding_pct is not None:
        if abs(funding_pct) >= 0.03:
            score += 2
            reasons.append(f"uç funding {funding_pct:.4f}%")
        elif abs(funding_pct) >= MIN_ABS_FUNDING_PCT:
            score += 1
            reasons.append(f"anlamlı funding {funding_pct:.4f}%")

    # OI değişimi
    if oi_change_pct is not None:
        if abs(oi_change_pct) >= 3:
            score += 2
            reasons.append(f"OI spike {oi_change_pct:.2f}%")
        elif abs(oi_change_pct) >= MIN_OI_CHANGE_PCT:
            score += 1
            reasons.append(f"OI artışı/azalışı {oi_change_pct:.2f}%")

    # Mark vs last sapması
    if mark_price > 0 and last_price > 0:
        dev_pct = ((last_price - mark_price) / mark_price) * 100.0
        if abs(dev_pct) >= 0.15:
            score += 1
            reasons.append(f"mark sapması {dev_pct:.3f}%")
    else:
        dev_pct = None

    if score < MIN_FINAL_SCORE:
        return None

    # Basit bias
    if funding_pct is not None and oi_change_pct is not None:
        if funding_pct > 0 and move_pct > 0 and oi_change_pct > 0:
            bias = "LONG kalabalığı / ters short fırsatı takip"
        elif funding_pct < 0 and move_pct < 0 and oi_change_pct > 0:
            bias = "SHORT kalabalığı / squeeze long fırsatı takip"
        elif funding_pct > 0 and move_pct < 0:
            bias = "LONG'lar eziliyor / satış devamı izlenebilir"
        elif funding_pct < 0 and move_pct > 0:
            bias = "SHORT squeeze devam edebilir"
        else:
            bias = "yön karışık, teyit beklenmeli"
    else:
        bias = "veri kısmi, teyit beklenmeli"

    return {
        "symbol": symbol,
        "score": score,
        "turnover24h": turnover,
        "move_pct": move_pct,
        "funding_pct": funding_pct,
        "oi_now": oi_now,
        "oi_prev": oi_prev,
        "oi_change_pct": oi_change_pct,
        "last_price": last_price,
        "mark_price": mark_price,
        "bias": bias,
        "reasons": reasons,
    }


def format_signal(sig):
    funding_txt = f"{sig['funding_pct']:.4f}%" if sig["funding_pct"] is not None else "n/a"
    oi_change_txt = f"{sig['oi_change_pct']:.2f}%" if sig["oi_change_pct"] is not None else "n/a"

    return (
        f"🚨 <b>Futures Hunter V5 PRO</b>\n"
        f"• Coin: <b>{sig['symbol']}</b>\n"
        f"• Skor: <b>{sig['score']}</b>\n"
        f"• 24s hareket: <b>{sig['move_pct']:.2f}%</b>\n"
        f"• Funding: <b>{funding_txt}</b>\n"
        f"• OI değişim: <b>{oi_change_txt}</b>\n"
        f"• 24s turnover: <b>{sig['turnover24h']:,.0f} USD</b>\n"
        f"• Son fiyat: <b>{sig['last_price']}</b>\n"
        f"• Yorum: <b>{sig['bias']}</b>\n\n"
        f"• Nedenler:\n- " + "\n- ".join(sig["reasons"])
    )


def main():
    now_utc = datetime.now(timezone.utc).isoformat()
    tg_send(f"✅ <b>Futures Hunter V5 PRO</b> başladı @ {now_utc}")

    try:
        tickers = get_tickers()

        scanned = 0
        signals = []
        volume_pass = 0

        for symbol in SYMBOLS:
            scanned += 1

            t = tickers.get(symbol)
            if t and t["turnover24h"] >= MIN_24H_TURNOVER_USD:
                volume_pass += 1

            try:
                sig = score_signal(symbol, tickers)
                if sig:
                    signals.append(sig)
            except Exception as e:
                print(f"{symbol} hata:", e)

            time.sleep(0.2)

        debug = (
            f"📊 <b>Futures Hunter V5 PRO Debug</b>\n"
            f"• Taranan coin: <b>{scanned}</b>\n"
            f"• Hacim filtresini geçen: <b>{volume_pass}</b>\n"
            f"• Final setup: <b>{len(signals)}</b>"
        )
        tg_send(debug)

        if not signals:
            tg_send("ℹ️ <b>Futures Hunter V5 PRO:</b> uygun setup bulunamadı.")
            return

        signals.sort(
            key=lambda x: (
                x["score"],
                abs(x["move_pct"]),
                x["turnover24h"],
                abs(x["oi_change_pct"]) if x["oi_change_pct"] is not None else 0,
                abs(x["funding_pct"]) if x["funding_pct"] is not None else 0,
            ),
            reverse=True
        )

        for sig in signals[:TOP_N]:
            tg_send(format_signal(sig))

    except Exception as e:
        tg_send(f"❌ <b>Futures Hunter V5 PRO hata:</b>\n<code>{str(e)}</code>")
        raise


if __name__ == "__main__":
    main()
