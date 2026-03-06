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
STATE_FILE = "state.json"

# =========================
# SIGNAL FILTERS
# =========================
MIN_SCORE = 7
MIN_TRUST_SCORE = 8

MIN_PRICE_MOVE_15M = 0.8      # %
MIN_VOLUME_SPIKE = 1.5        # x
MIN_OI_CHANGE_1H = 1.2        # %
MIN_FUNDING_ABS = 0.005       # %   (örn: 0.005 = %0.005)

# =========================
# DATA SANITY LIMITS
# =========================
MAX_REASONABLE_FUNDING_PCT = 0.20   # funding % olarak bu üstü şüpheli
MAX_REASONABLE_MOVE_15M = 8.0       # 15m move üstü outlier guard
MAX_REASONABLE_VOL_SPIKE = 12.0     # hacim spike guard
MAX_REASONABLE_OI_CHANGE = 20.0     # 1h OI change guard
MAX_PRICE_MARK_DIFF_PCT = 0.30      # last vs mark farkı %

# Funding cross-check
FUNDING_CONFIRM_DIFF_PCT = 0.03     # Binance vs Bybit yüzde puan farkı

# =========================
# HTTP SESSION
# =========================
session = requests.Session()
session.headers.update({
    "User-Agent": "futures-hunter-v3.1/1.0"
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
    Path(STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# =========================
# HELPERS
# =========================
def pct_change(old: float, new: float):
    if old == 0:
        return None
    return ((new - old) / old) * 100.0


def normalize_funding_pct(raw_decimal: float):
    # örn: 0.0001 => 0.01%
    return raw_decimal * 100.0


# =========================
# BINANCE - PRIMARY SOURCE
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
        "funding_raw": float(data["lastFundingRate"]),
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
# =========================
def get_bybit_funding(symbol: str):
    data = safe_get(
        "https://api.bybit.com/v5/market/tickers",
        {"category": "linear", "symbol": symbol}
    )
    items = data.get("result", {}).get("list", [])
    if not items:
        return None

    item = items[0]
    return {
        "funding_raw": float(item["fundingRate"]),
        "last_price": float(item["lastPrice"]),
    }


# =========================
# CALCULATIONS
# =========================
def calc_closed_candle_move_15m(symbol: str):
    """
    Sadece kapanmış mumlar:
    bir önceki kapanmış mum close -> son kapanmış mum close
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
    Son kapanan 15m mum hacmi / önceki 3 kapanmış mum ortalaması
    """
    klines = get_binance_klines(symbol, interval="15m", limit=6)
    if len(klines) < 6:
        return None

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
    15m periyotlu OI hist üzerinden yaklaşık 1 saatlik değişim
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
    if binance_pct is None:
        return False, "Funding missing"

    if abs(binance_pct) > MAX_REASONABLE_FUNDING_PCT:
        return False, "Funding outlier"

    if bybit_pct is None:
        return False, "Bybit confirm missing"

    if abs(bybit_pct) > MAX_REASONABLE_FUNDING_PCT:
        return False, "Bybit funding outlier"

    # yön aynı mı
    if binance_pct == 0 and bybit_pct == 0:
        sign_ok = True
    else:
        sign_ok = (binance_pct >= 0 and bybit_pct >= 0) or (binance_pct <= 0 and bybit_pct <= 0)

    if not sign_ok:
        return False, "Funding sign mismatch"

    diff = abs(binance_pct - bybit_pct)
    if diff > FUNDING_CONFIRM_DIFF_PCT:
        return False, f"Funding mismatch ({diff:.3f}%)"

    return True, "Funding confirmed"


def price_quality(last_price, mark_price):
    if last_price is None or mark_price is None:
        return False, "Price missing"

    if last_price <= 0 or mark_price <= 0:
        return False, "Invalid price"

    diff_pct = abs(last_price - mark_price) / mark_price * 100.0
    if diff_pct > MAX_PRICE_MARK_DIFF_PCT:
        return False, f"Last/Mark mismatch ({diff_pct:.3f}%)"

    return True, "Price confirmed"


def metric_sanity(move_15m_pct, volume_spike, oi_change_pct):
    if move_15m_pct is None or volume_spike is None or oi_change_pct is None:
        return False, "Missing metrics"

    if abs(move_15m_pct) > MAX_REASONABLE_MOVE_15M:
        return False, "Price move outlier"

    if volume_spike <= 0 or volume_spike > MAX_REASONABLE_VOL_SPIKE:
        return False, "Volume spike outlier"

    if abs(oi_change_pct) > MAX_REASONABLE_OI_CHANGE:
        return False, "OI change outlier"

    return True, "Metrics sane"


def data_trust_score(funding_ok, price_ok, metrics_ok, bybit_exists):
    score = 0
    if funding_ok:
        score += 4
    if price_ok:
        score += 3
    if metrics_ok:
        score += 2
    if bybit_exists:
        score += 1
    return score


# =========================
# SIGNAL LOGIC
# =========================
def detect_bias(funding_pct, oi_change_pct, move_15m_pct):
    if funding_pct > 0 and oi_change_pct > 0 and move_15m_pct > 0:
        return "SHORT SETUP"
    if funding_pct < 0 and oi_change_pct > 0 and move_15m_pct < 0:
        return "LONG SETUP"
    return "WATCHLIST"


def calculate_setup_score(funding_pct, oi_change_pct, volume_spike, move_15m_pct, trust_score):
    score = 0

    af = abs(funding_pct)
    if af >= 0.005:
        score += 1
    if af >= 0.010:
        score += 1
    if af >= 0.020:
        score += 2

    aoi = abs(oi_change_pct)
    if aoi >= 1.2:
        score += 1
    if aoi >= 2.5:
        score += 2
    if aoi >= 4.0:
        score += 2

    if volume_spike >= 1.5:
        score += 1
    if volume_spike >= 2.0:
        score += 2
    if volume_spike >= 3.0:
        score += 1

    amv = abs(move_15m_pct)
    if amv >= 0.8:
        score += 1
    if amv >= 1.5:
        score += 1
    if amv >= 2.5:
        score += 1

    if trust_score >= 9:
        score += 1

    return min(score, 10)


def build_reasons(funding_pct, oi_change_pct, volume_spike, move_15m_pct, bias, funding_note, price_note):
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
        reasons.append("Closed-candle volume expanded")

    reasons.append(funding_note)
    reasons.append(price_note)

    if bias == "SHORT SETUP":
        reasons.append("Potential long squeeze zone")
    elif bias == "LONG SETUP":
        reasons.append("Potential short squeeze zone")

    return reasons


def should_alert(funding_pct, oi_change_pct, volume_spike, move_15m_pct, trust_score, setup_score):
    if trust_score < MIN_TRUST_SCORE:
        return False

    if abs(funding_pct) < MIN_FUNDING_ABS:
        return False

    if abs(oi_change_pct) < MIN_OI_CHANGE_1H:
        return False

    if volume_spike < MIN_VOLUME_SPIKE:
        return False

    if abs(move_15m_pct) < MIN_PRICE_MOVE_15M:
        return False

    if setup_score < MIN_SCORE:
        return False

    return True


def duplicate_guard_key(symbol, bias, funding_pct, oi_change_pct, move_15m_pct, setup_score):
    return (
        f"{symbol}|{bias}|"
        f"{round(funding_pct, 3)}|"
        f"{round(oi_change_pct, 1)}|"
        f"{round(move_15m_pct, 1)}|"
        f"{setup_score}"
    )


# =========================
# FORMATTING
# =========================
def format_alert(
    symbol,
    last_price,
    mark_price,
    funding_pct,
    bybit_funding_pct,
    oi_now,
    oi_change_pct,
    volume_spike,
    move_15m_pct,
    bias,
    trust_score,
    setup_score,
    reasons
):
    if bias == "SHORT SETUP":
        action = "WATCH FOR REVERSAL / SHORT CONFIRMATION"
    elif bias == "LONG SETUP":
        action = "WATCH FOR REVERSAL / LONG CONFIRMATION"
    else:
        action = "WATCH CLOSELY / WAIT CONFIRMATION"

    reason_text = "\n".join(f"- {r}" for r in reasons)

    return (
        f"🚨 FUTURES HUNTER V3.1\n\n"
        f"Coin: {symbol.replace('USDT', '')}\n"
        f"Bias: {bias}\n"
        f"Last Price: {last_price:.4f}\n"
        f"Mark Price: {mark_price:.4f}\n"
        f"Funding (Binance): {funding_pct:+.3f}%\n"
        f"Funding (Bybit): {bybit_funding_pct:+.3f}%\n"
        f"OI Now: {oi_now:,.0f}\n"
        f"OI Change (1h): {oi_change_pct:+.1f}%\n"
        f"Volume Spike (15m): {volume_spike:.1f}x\n"
        f"15m Move (closed): {move_15m_pct:+.1f}%\n\n"
        f"Reason:\n{reason_text}\n\n"
        f"Data Trust: {trust_score}/10\n"
        f"Setup Score: {setup_score}/10\n"
        f"Action: {action}"
    )


def format_debug(
    symbol,
    last_price,
    mark_price,
    funding_pct,
    bybit_funding_pct,
    oi_now,
    oi_change_pct,
    volume_spike,
    move_15m_pct,
    funding_status,
    price_status,
    metrics_status,
    trust_score,
    setup_score
):
    return (
        f"[DEBUG] {symbol} | "
        f"last={last_price:.4f} | "
        f"mark={mark_price:.4f} | "
        f"fund_bin={funding_pct:+.3f}% | "
        f"fund_byb={bybit_funding_pct:+.3f}% | "
        f"oi={oi_now:.0f} | "
        f"oi1h={oi_change_pct:+.2f}% | "
        f"vol15={volume_spike:.2f}x | "
        f"move15={move_15m_pct:+.2f}% | "
        f"funding={funding_status} | "
        f"price={price_status} | "
        f"metrics={metrics_status} | "
        f"trust={trust_score} | "
        f"setup={setup_score}"
    )


# =========================
# ANALYZE
# =========================
def analyze_symbol(symbol: str):
    try:
        mark_info = get_binance_mark_info(symbol)
        last_price = get_binance_last_price(symbol)
        oi_now = get_binance_open_interest(symbol)

        mark_price = mark_info["mark_price"]
        funding_pct = normalize_funding_pct(mark_info["funding_raw"])

        bybit = get_bybit_funding(symbol)
        if not bybit:
            print(f"{symbol}: Bybit funding yok")
            return None, None

        bybit_funding_pct = normalize_funding_pct(bybit["funding_raw"])

        move_15m_pct = calc_closed_candle_move_15m(symbol)
        volume_spike = calc_closed_candle_volume_spike_15m(symbol)
        oi_change_pct = calc_oi_change_1h(symbol)

        funding_ok, funding_status = funding_quality(funding_pct, bybit_funding_pct)
        price_ok, price_status = price_quality(last_price, mark_price)
        metrics_ok, metrics_status = metric_sanity(move_15m_pct, volume_spike, oi_change_pct)

        trust_score = data_trust_score(
            funding_ok=funding_ok,
            price_ok=price_ok,
            metrics_ok=metrics_ok,
            bybit_exists=True
        )

        if move_15m_pct is None or volume_spike is None or oi_change_pct is None:
            print(f"{symbol}: eksik metrik")
            return None, None

        bias = detect_bias(funding_pct, oi_change_pct, move_15m_pct)

        setup_score = calculate_setup_score(
            funding_pct=funding_pct,
            oi_change_pct=oi_change_pct,
            volume_spike=volume_spike,
            move_15m_pct=move_15m_pct,
            trust_score=trust_score
        )

        print(format_debug(
            symbol=symbol,
            last_price=last_price,
            mark_price=mark_price,
            funding_pct=funding_pct,
            bybit_funding_pct=bybit_funding_pct,
            oi_now=oi_now,
            oi_change_pct=oi_change_pct,
            volume_spike=volume_spike,
            move_15m_pct=move_15m_pct,
            funding_status=funding_status,
            price_status=price_status,
            metrics_status=metrics_status,
            trust_score=trust_score,
            setup_score=setup_score
        ))

        if not funding_ok or not price_ok or not metrics_ok:
            return None, None

        if not should_alert(
            funding_pct=funding_pct,
            oi_change_pct=oi_change_pct,
            volume_spike=volume_spike,
            move_15m_pct=move_15m_pct,
            trust_score=trust_score,
            setup_score=setup_score
        ):
            return None, None

        reasons = build_reasons(
            funding_pct=funding_pct,
            oi_change_pct=oi_change_pct,
            volume_spike=volume_spike,
            move_15m_pct=move_15m_pct,
            bias=bias,
            funding_note=funding_status,
            price_note=price_status
        )

        msg = format_alert(
            symbol=symbol,
            last_price=last_price,
            mark_price=mark_price,
            funding_pct=funding_pct,
            bybit_funding_pct=bybit_funding_pct,
            oi_now=oi_now,
            oi_change_pct=oi_change_pct,
            volume_spike=volume_spike,
            move_15m_pct=move_15m_pct,
            bias=bias,
            trust_score=trust_score,
            setup_score=setup_score,
            reasons=reasons
        )

        key = duplicate_guard_key(
            symbol=symbol,
            bias=bias,
            funding_pct=funding_pct,
            oi_change_pct=oi_change_pct,
            move_15m_pct=move_15m_pct,
            setup_score=setup_score
        )

        return msg, key

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
    send_telegram(f"✅ Futures Hunter V3.1 started @ {now_utc}")

    alerts_to_send = []
    new_sent_keys = set(sent_keys)

    for symbol in COINS:
        msg, key = analyze_symbol(symbol)
        if msg and key:
            if key not in sent_keys:
                alerts_to_send.append((msg, key))
            else:
                print(f"Duplicate skipped: {key}")
        time.sleep(0.4)

    if not alerts_to_send:
        send_telegram("ℹ️ Futures Hunter V3.1: doğrulanmış ve güvenilir güçlü setup bulunamadı.")
    else:
        for msg, key in alerts_to_send:
            send_telegram(msg)
            new_sent_keys.add(key)
            time.sleep(1.0)

    state["sent_keys"] = list(new_sent_keys)[-300:]
    state["updated_at"] = now_utc
    save_state(state)


if __name__ == "__main__":
    main()
