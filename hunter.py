import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# =========================
# ENV
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================
# CONFIG
# =========================
COINS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "ADAUSDT",
]

REQUEST_TIMEOUT = 15

# --- Signal filters ---
MIN_SCORE = 7
MIN_PRICE_MOVE_15M = 1.0       # %
MIN_VOLUME_SPIKE = 1.6         # x
MIN_OI_CHANGE_1H = 1.5         # %
MIN_FUNDING_ABS = 0.008        # %   örn 0.008 = %0.008

# --- Sanity / data quality ---
MAX_REASONABLE_FUNDING_PCT = 1.0    # funding yüzdesi bunun üstündeyse veri şüpheli
MAX_REASONABLE_MOVE_15M = 12.0      # 15dk hareket bunun üstündeyse veri outlier olabilir
MAX_REASONABLE_VOL_SPIKE = 25.0     # abartı spike outlier guard
MAX_REASONABLE_OI_CHANGE = 40.0     # 1s OI değişimi çok uçuksa ignore

# Funding doğrulama
FUNDING_CONFIRM_DIFF_PCT = 0.08     # Binance vs Bybit farkı yüzde puan bazında

STATE_FILE = "state.json"

# =========================
# HTTP
# =========================
session = requests.Session()
session.headers.update({
    "User-Agent": "futures-hunter-v3/1.0"
})


def safe_get(url: str, params=None):
    r = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


# =========================
# TELEGRAM
# =========================
def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram env eksik.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }
    try:
        r = session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        print("Telegram:", r.status_code, r.text[:250])
    except Exception as e:
        print("Telegram error:", e)


# =========================
# STATE
# =========================
def load_state():
    path = Path(STATE_FILE)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict):
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================
# HELPERS
# =========================
def pct_change(old: float, new: float):
    if old == 0:
        return None
    return ((new - old) / old) * 100.0


def is_number(x):
    return isinstance(x, (int, float))


def round_safe(x, digits=3):
    if x is None:
        return None
    return round(float(x), digits)


# =========================
# BINANCE - PRIMARY SOURCE
# Official docs:
# premiumIndex -> lastFundingRate
# openInterest / openInterestHist
# =========================
def get_binance_mark_info(symbol: str):
    data = safe_get(
        "https://fapi.binance.com/fapi/v1/premiumIndex",
        {"symbol": symbol}
    )
    return {
        "symbol": data["symbol"],
        "mark_price": float(data["markPrice"]),
        "index_price": float(data["indexPrice"]),
        "funding_raw": float(data["lastFundingRate"]),  # decimal
        "next_funding_time": int(data["nextFundingTime"]),
        "server_time": int(data["time"]),
    }


def get_binance_last_price(symbol: str):
    data = safe_get(
        "https://fapi.binance.com/fapi/v1/ticker/price",
        {"symbol": symbol}
    )
    return float(data["price"])


def get_binance_klines(symbol: str, interval="15m", limit=6):
    data = safe_get(
        "https://fapi.binance.com/fapi/v1/klines",
        {"symbol": symbol, "interval": interval, "limit": limit}
    )
    return data


def get_binance_open_interest(symbol: str):
    data = safe_get(
        "https://fapi.binance.com/fapi/v1/openInterest",
        {"symbol": symbol}
    )
    return float(data["openInterest"])


def get_binance_open_interest_hist(symbol: str, period="15m", limit=5):
    data = safe_get(
        "https://fapi.binance.com/futures/data/openInterestHist",
        {"symbol": symbol, "period": period, "limit": limit}
    )
    return data


# =========================
# BYBIT - FUNDING CONFIRMATION
# Official docs:
# /v5/market/tickers -> fundingRate
# =========================
def get_bybit_funding(symbol: str):
    data = safe_get(
        "https://api.bybit.com/v5/market/tickers",
        {"category": "linear", "symbol": symbol}
    )
    items = data.get("result", {}).get("list", [])
    if not items:
        return None
    funding_raw = float(items[0]["fundingRate"])  # decimal
    last_price = float(items[0]["lastPrice"])
    return {
        "funding_raw": funding_raw,
        "last_price": last_price,
    }


# =========================
# DATA NORMALIZATION
# =========================
def normalize_funding_pct(raw_decimal: float):
    # 0.0001 => 0.01%
    return raw_decimal * 100.0


def calc_closed_candle_move_15m(symbol: str):
    """
    Sadece kapalı mumlar:
    son kapanan mum close vs bir önceki kapanan mum close
    """
    klines = get_binance_klines(symbol, interval="15m", limit=4)
    if len(klines) < 4:
        return None

    prev_closed = klines[-3]
    last_closed = klines[-2]

    prev_close = float(prev_closed[4])
    last_close = float(last_closed[4])

    return pct_change(prev_close, last_close)


def calc_closed_candle_volume_spike_15m(symbol: str):
    """
    Son kapanan 15m mum hacmi / önceki 3 kapanan mum ortalaması
    Sadece kapalı mumlar kullanılır.
    """
    klines = get_binance_klines(symbol, interval="15m", limit=6)
    if len(klines) < 6:
        return None

    # açık mum = son eleman olabilir, onu kullanmıyoruz
    last_closed_volume = float(klines[-2][5])
    reference_volumes = [float(k[5]) for k in klines[-5:-2]]

    if not reference_volumes:
        return None

    avg_prev = sum(reference_volumes) / len(reference_volumes)
    if avg_prev == 0:
        return None

    return last_closed_volume / avg_prev


def calc_oi_change_1h(symbol: str):
    """
    Binance openInterestHist ile 15m periyot, son 5 veri.
    İlk ve son veri arası yaklaşık 1 saatlik değişim.
    """
    hist = get_binance_open_interest_hist(symbol, period="15m", limit=5)
    if not hist or len(hist) < 5:
        return None

    first_val = float(hist[0]["sumOpenInterest"])
    last_val = float(hist[-1]["sumOpenInterest"])

    return pct_change(first_val, last_val)


# =========================
# DATA QUALITY CHECKS
# =========================
def funding_quality(binance_pct, bybit_pct):
    """
    Funding iki kaynak arasında makul yakın mı?
    """
    if binance_pct is None:
        return False, "Funding yok"

    if abs(binance_pct) > MAX_REASONABLE_FUNDING_PCT:
        return False, "Funding outlier"

    if bybit_pct is None:
        # bybit yoksa tamamen iptal etmiyoruz; confidence düşürürüz
        return True, "Bybit confirm yok"

    if abs(bybit_pct) > MAX_REASONABLE_FUNDING_PCT:
        return False, "Bybit funding outlier"

    diff = abs(binance_pct - bybit_pct)
    if diff > FUNDING_CONFIRM_DIFF_PCT:
        return False, f"Funding mismatch ({diff:.3f}%)"

    return True, "Funding confirmed"


def metric_sanity(move_15m_pct, volume_spike, oi_change_pct):
    if move_15m_pct is None or volume_spike is None or oi_change_pct is None:
        return False, "Eksik metrik"

    if abs(move_15m_pct) > MAX_REASONABLE_MOVE_15M:
        return False, "Price move outlier"

    if volume_spike <= 0 or volume_spike > MAX_REASONABLE_VOL_SPIKE:
        return False, "Volume spike outlier"

    if abs(oi_change_pct) > MAX_REASONABLE_OI_CHANGE:
        return False, "OI change outlier"

    return True, "Metrics sane"


# =========================
# LOGIC
# =========================
def detect_bias(funding_pct, oi_change_pct, move_15m_pct):
    if funding_pct > 0 and oi_change_pct > 0 and move_15m_pct > 0:
        return "SHORT SETUP"
    if funding_pct < 0 and oi_change_pct > 0 and move_15m_pct < 0:
        return "LONG SETUP"
    return "WATCHLIST"


def calculate_score(funding_pct, oi_change_pct, volume_spike, move_15m_pct, funding_confirmed: bool):
    score = 0

    af = abs(funding_pct)
    if af >= 0.008:
        score += 1
    if af >= 0.015:
        score += 1
    if af >= 0.03:
        score += 2

    aoi = abs(oi_change_pct)
    if aoi >= 1.5:
        score += 1
    if aoi >= 3:
        score += 2
    if aoi >= 5:
        score += 2

    if volume_spike >= 1.6:
        score += 1
    if volume_spike >= 2.0:
        score += 2
    if volume_spike >= 3.0:
        score += 1

    amv = abs(move_15m_pct)
    if amv >= 1.0:
        score += 1
    if amv >= 1.8:
        score += 1
    if amv >= 3.0:
        score += 1

    if funding_confirmed:
        score += 1

    return min(score, 10)


def build_reasons(funding_pct, oi_change_pct, volume_spike, move_15m_pct, bias, funding_note):
    reasons = []

    if funding_pct > 0:
        reasons.append("Positive funding crowded long")
    elif funding_pct < 0:
        reasons.append("Negative funding crowded short")

    if oi_change_pct > 0 and abs(move_15m_pct) >= 0.8:
        reasons.append("OI rising with directional move")
    elif oi_change_pct > 0:
        reasons.append("OI expanding")

    if volume_spike >= MIN_VOLUME_SPIKE:
        reasons.append("Volume expanded on closed candle")

    if funding_note:
        reasons.append(funding_note)

    if bias == "SHORT SETUP":
        reasons.append("Potential long squeeze zone")
    elif bias == "LONG SETUP":
        reasons.append("Potential short squeeze zone")

    return reasons


def should_alert(funding_pct, oi_change_pct, volume_spike, move_15m_pct, score, funding_ok, metrics_ok):
    if not funding_ok or not metrics_ok:
        return False

    if abs(funding_pct) < MIN_FUNDING_ABS:
        return False

    if abs(oi_change_pct) < MIN_OI_CHANGE_1H:
        return False

    if volume_spike < MIN_VOLUME_SPIKE:
        return False

    if abs(move_15m_pct) < MIN_PRICE_MOVE_15M:
        return False

    if score < MIN_SCORE:
        return False

    return True


def duplicate_guard_key(symbol, bias, score, funding_pct, oi_change_pct, move_15m_pct):
    # Benzer alarmı tekrar tekrar atmasın
    return f"{symbol}|{bias}|{round(funding_pct, 3)}|{round(oi_change_pct, 1)}|{round(move_15m_pct, 1)}|{score}"


def format_alert(symbol, price, mark_price, funding_pct, bybit_funding_pct, oi_now,
                 oi_change_pct, volume_spike, move_15m_pct, bias, score, reasons):
    action = "WATCH FOR REVERSAL / SHORT CONFIRMATION"
    if bias == "LONG SETUP":
        action = "WATCH FOR REVERSAL / LONG CONFIRMATION"
    elif bias == "WATCHLIST":
        action = "WATCH CLOSELY / WAIT CONFIRMATION"

    reason_text = "\n".join([f"- {r}" for r in reasons])

    bybit_text = "N/A" if bybit_funding_pct is None else f"{bybit_funding_pct:+.3f}%"

    return (
        f"🚨 FUTURES HUNTER V3\n\n"
        f"Coin: {symbol.replace('USDT', '')}\n"
        f"Bias: {bias}\n"
        f"Last Price: {price:.4f}\n"
        f"Mark Price: {mark_price:.4f}\n"
        f"Funding (Binance): {funding_pct:+.3f}%\n"
        f"Funding (Bybit): {bybit_text}\n"
        f"OI Now: {oi_now:,.0f}\n"
        f"OI Change (1h): {oi_change_pct:+.1f}%\n"
        f"Volume Spike (15m): {volume_spike:.1f}x\n"
        f"15m Move (closed): {move_15m_pct:+.1f}%\n\n"
        f"Reason:\n{reason_text}\n\n"
        f"Score: {score}/10\n"
        f"Action: {action}"
    )


def format_debug(symbol, price, mark_price, funding_pct, bybit_funding_pct, oi_now,
                 oi_change_pct, volume_spike, move_15m_pct, funding_status, metrics_status, score):
    bybit_text = "N/A" if bybit_funding_pct is None else f"{bybit_funding_pct:+.3f}%"
    return (
        f"[DEBUG] {symbol} | "
        f"last={price:.4f} | mark={mark_price:.4f} | "
        f"fund_bin={funding_pct:+.3f}% | fund_byb={bybit_text} | "
        f"oi={oi_now:.0f} | oi1h={oi_change_pct:+.2f}% | "
        f"vol15={volume_spike:.2f}x | move15={move_15m_pct:+.2f}% | "
        f"funding={funding_status} | metrics={metrics_status} | score={score}"
    )


# =========================
# ANALYZE
# =========================
def analyze_symbol(symbol: str):
    try:
        # Primary source snapshot
        mark_info = get_binance_mark_info(symbol)
        last_price = get_binance_last_price(symbol)
        oi_now = get_binance_open_interest(symbol)

        funding_pct = normalize_funding_pct(mark_info["funding_raw"])
        mark_price = mark_info["mark_price"]

        # Secondary funding confirmation
        bybit = get_bybit_funding(symbol)
        bybit_funding_pct = None
        if bybit:
            bybit_funding_pct = normalize_funding_pct(bybit["funding_raw"])

        # Closed candle / hist metrics
        move_15m_pct = calc_closed_candle_move_15m(symbol)
        volume_spike = calc_closed_candle_volume_spike_15m(symbol)
        oi_change_pct = calc_oi_change_1h(symbol)

        funding_ok, funding_status = funding_quality(funding_pct, bybit_funding_pct)
        metrics_ok, metrics_status = metric_sanity(move_15m_pct, volume_spike, oi_change_pct)

        if move_15m_pct is None or volume_spike is None or oi_change_pct is None:
            print(f"{symbol}: eksik veri")
            return None, None

        bias = detect_bias(funding_pct, oi_change_pct, move_15m_pct)
        score = calculate_score(
            funding_pct=funding_pct,
            oi_change_pct=oi_change_pct,
            volume_spike=volume_spike,
            move_15m_pct=move_15m_pct,
            funding_confirmed=funding_ok
        )

        print(format_debug(
            symbol=symbol,
            price=last_price,
            mark_price=mark_price,
            funding_pct=funding_pct,
            bybit_funding_pct=bybit_funding_pct,
            oi_now=oi_now,
            oi_change_pct=oi_change_pct,
            volume_spike=volume_spike,
            move_15m_pct=move_15m_pct,
            funding_status=funding_status,
            metrics_status=metrics_status,
            score=score
        ))

        if not should_alert(
            funding_pct=funding_pct,
            oi_change_pct=oi_change_pct,
            volume_spike=volume_spike,
            move_15m_pct=move_15m_pct,
            score=score,
            funding_ok=funding_ok,
            metrics_ok=metrics_ok
        ):
            return None, None

        reasons = build_reasons(
            funding_pct=funding_pct,
            oi_change_pct=oi_change_pct,
            volume_spike=volume_spike,
            move_15m_pct=move_15m_pct,
            bias=bias,
            funding_note=funding_status
        )

        alert_message = format_alert(
            symbol=symbol,
            price=last_price,
            mark_price=mark_price,
            funding_pct=funding_pct,
            bybit_funding_pct=bybit_funding_pct,
            oi_now=oi_now,
            oi_change_pct=oi_change_pct,
            volume_spike=volume_spike,
            move_15m_pct=move_15m_pct,
            bias=bias,
            score=score,
            reasons=reasons
        )

        dedup_key = duplicate_guard_key(
            symbol=symbol,
            bias=bias,
            score=score,
            funding_pct=funding_pct,
            oi_change_pct=oi_change_pct,
            move_15m_pct=move_15m_pct
        )

        return alert_message, dedup_key

    except Exception as e:
        print(f"Analyze error {symbol}: {e}")
        return None, None


# =========================
# MAIN
# =========================
def main():
    state = load_state()
    sent_keys = set(state.get("sent_keys", []))

    now_utc = datetime.now(timezone.utc).isoformat()
    send_telegram(f"✅ Futures Hunter V3 started @ {now_utc}")

    new_sent_keys = set(sent_keys)
    alerts_to_send = []

    for symbol in COINS:
        msg, key = analyze_symbol(symbol)
        if msg and key:
            if key not in sent_keys:
                alerts_to_send.append((msg, key))
            else:
                print(f"Duplicate skipped: {key}")
        time.sleep(0.4)

    if not alerts_to_send:
        send_telegram("ℹ️ Futures Hunter V3: doğrulanmış güçlü setup bulunamadı.")
    else:
        for msg, key in alerts_to_send:
            send_telegram(msg)
            new_sent_keys.add(key)
            time.sleep(1.0)

    # sent key sayısı büyümesin
    trimmed = list(new_sent_keys)[-300:]
    state["sent_keys"] = trimmed
    state["updated_at"] = now_utc
    save_state(state)


if __name__ == "__main__":
    main()
