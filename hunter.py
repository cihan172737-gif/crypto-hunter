import os
import json
import time
import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
import websockets

# =========================================================
# ENV
# =========================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================================================
# CONFIG
# =========================================================
COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT",
    "XRPUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT",
    "MATICUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT",
    "SUIUSDT", "ATOMUSDT", "FILUSDT", "NEARUSDT", "RUNEUSDT"
]

REQUEST_TIMEOUT = 15
STATE_FILE = "state.json"

# --- Scan cadence
SCAN_INTERVAL_SEC = 30
STARTUP_WARMUP_SEC = 45

# --- Alert controls
MIN_TRUST_SCORE = 8
MIN_SETUP_SCORE = 7
ALERT_COOLDOWN_SEC = 30 * 60  # aynı coin için 30 dk

# --- Core thresholds
MIN_PRICE_MOVE_15M = 0.8      # %
MIN_VOLUME_SPIKE = 1.5        # x
MIN_OI_CHANGE_1H = 1.2        # %
MIN_FUNDING_ABS = 0.005       # %
MIN_LIQ_TOTAL_USD = 150_000   # son pencerede
MIN_LIQ_EVENTS = 2
MIN_LIQ_DOMINANCE = 1.25
MAX_SPREAD_BPS = 20           # bookTicker spread guard
MIN_BOOK_IMBALANCE = 1.15     # bid/ask size ratio

# --- Data sanity
MAX_REASONABLE_FUNDING_PCT = 0.20
MAX_REASONABLE_MOVE_15M = 8.0
MAX_REASONABLE_VOL_SPIKE = 12.0
MAX_REASONABLE_OI_CHANGE = 20.0
MAX_PRICE_MARK_DIFF_PCT = 0.30
FUNDING_CONFIRM_DIFF_PCT = 0.03

# --- Liquidation lookback
LIQ_LOOKBACK_SEC = 180

# --- WebSocket endpoints
BINANCE_LIQ_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"
BINANCE_BOOK_WS = "wss://fstream.binance.com/ws/!bookTicker"

# =========================================================
# HTTP
# =========================================================
session = requests.Session()
session.headers.update({"User-Agent": "futures-hunter-v4/1.0"})


def safe_get(url: str, params=None):
    r = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


# =========================================================
# TELEGRAM
# =========================================================
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
        print("Telegram:", r.status_code, r.text[:180])
    except Exception as e:
        print("Telegram error:", e)


# =========================================================
# STATE
# =========================================================
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


# =========================================================
# HELPERS
# =========================================================
def now_ts() -> float:
    return time.time()


def pct_change(old: float, new: float):
    if old == 0:
        return None
    return ((new - old) / old) * 100.0


def normalize_funding_pct(raw_decimal: float):
    # örn 0.0001 => 0.01%
    return raw_decimal * 100.0


# =========================================================
# REST MARKET DATA
# =========================================================
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
    return safe_get(
        "https://fapi.binance.com/fapi/v1/klines",
        {"symbol": symbol, "interval": interval, "limit": limit}
    )


def get_binance_open_interest(symbol: str):
    data = safe_get(
        "https://fapi.binance.com/fapi/v1/openInterest",
        {"symbol": symbol}
    )
    return float(data["openInterest"])


def get_binance_open_interest_hist(symbol: str, period="15m", limit=5):
    return safe_get(
        "https://fapi.binance.com/futures/data/openInterestHist",
        {"symbol": symbol, "period": period, "limit": limit}
    )


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


# =========================================================
# CLOSED-CANDLE CALCS
# =========================================================
def calc_closed_candle_move_15m(symbol: str):
    klines = get_binance_klines(symbol, "15m", 4)
    if len(klines) < 4:
        return None

    prev_closed = klines[-3]
    last_closed = klines[-2]

    prev_close = float(prev_closed[4])
    last_close = float(last_closed[4])

    return pct_change(prev_close, last_close)


def calc_closed_candle_volume_spike_15m(symbol: str):
    klines = get_binance_klines(symbol, "15m", 6)
    if len(klines) < 6:
        return None

    last_closed_volume = float(klines[-2][5])
    ref_volumes = [float(k[5]) for k in klines[-5:-2]]

    if not ref_volumes:
        return None

    avg_prev = sum(ref_volumes) / len(ref_volumes)
    if avg_prev == 0:
        return None

    return last_closed_volume / avg_prev


def calc_oi_change_1h(symbol: str):
    hist = get_binance_open_interest_hist(symbol, "15m", 5)
    if not hist or len(hist) < 5:
        return None

    first_val = float(hist[0]["sumOpenInterest"])
    last_val = float(hist[-1]["sumOpenInterest"])

    return pct_change(first_val, last_val)


# =========================================================
# REAL-TIME CACHE
# =========================================================
liq_lock = threading.Lock()
book_lock = threading.Lock()

liq_events = {}   # symbol -> list[{"ts","side","usd"}]
book_cache = {}   # symbol -> {"bid_price","bid_qty","ask_price","ask_qty","ts"}


def add_liq_event(symbol: str, side: str, usd_value: float):
    if symbol not in COINS:
        return
    evt = {"ts": now_ts(), "side": side, "usd": float(usd_value)}
    with liq_lock:
        liq_events.setdefault(symbol, []).append(evt)


def prune_liq_events():
    cutoff = now_ts() - LIQ_LOOKBACK_SEC
    with liq_lock:
        for symbol in list(liq_events.keys()):
            liq_events[symbol] = [e for e in liq_events[symbol] if e["ts"] >= cutoff]
            if not liq_events[symbol]:
                del liq_events[symbol]


def update_book(symbol: str, bid_price: float, bid_qty: float, ask_price: float, ask_qty: float):
    if symbol not in COINS:
        return
    with book_lock:
        book_cache[symbol] = {
            "bid_price": bid_price,
            "bid_qty": bid_qty,
            "ask_price": ask_price,
            "ask_qty": ask_qty,
            "ts": now_ts(),
        }


def get_book_snapshot(symbol: str):
    with book_lock:
        return dict(book_cache.get(symbol, {}))


def get_liq_snapshot(symbol: str):
    prune_liq_events()
    with liq_lock:
        events = list(liq_events.get(symbol, []))

    long_liq_usd = 0.0
    short_liq_usd = 0.0
    long_events = 0
    short_events = 0

    # Binance force liquidation:
    # SELL liquidation order => long liquidation
    # BUY liquidation order => short liquidation
    for e in events:
        if e["side"] == "SELL":
            long_liq_usd += e["usd"]
            long_events += 1
        elif e["side"] == "BUY":
            short_liq_usd += e["usd"]
            short_events += 1

    total_usd = long_liq_usd + short_liq_usd
    total_events = long_events + short_events

    dominant_side = None
    dominant_usd = 0.0
    opposite_usd = 0.0

    if long_liq_usd > short_liq_usd:
        dominant_side = "LONG_LIQUIDATIONS"
        dominant_usd = long_liq_usd
        opposite_usd = short_liq_usd
    elif short_liq_usd > long_liq_usd:
        dominant_side = "SHORT_LIQUIDATIONS"
        dominant_usd = short_liq_usd
        opposite_usd = long_liq_usd

    dominance_ratio = dominant_usd / max(opposite_usd, 1.0) if dominant_side else 1.0

    return {
        "total_usd": total_usd,
        "total_events": total_events,
        "long_liq_usd": long_liq_usd,
        "short_liq_usd": short_liq_usd,
        "long_events": long_events,
        "short_events": short_events,
        "dominant_side": dominant_side,
        "dominance_ratio": dominance_ratio,
    }


# =========================================================
# QUALITY CHECKS
# =========================================================
def funding_quality(binance_pct, bybit_pct):
    if binance_pct is None:
        return False, "Funding missing"

    if abs(binance_pct) > MAX_REASONABLE_FUNDING_PCT:
        return False, "Funding outlier"

    if bybit_pct is None:
        return False, "Bybit confirm missing"

    if abs(bybit_pct) > MAX_REASONABLE_FUNDING_PCT:
        return False, "Bybit funding outlier"

    sign_ok = (
        (binance_pct >= 0 and bybit_pct >= 0) or
        (binance_pct <= 0 and bybit_pct <= 0)
    )
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


def metrics_quality(move_15m_pct, volume_spike, oi_change_pct):
    if move_15m_pct is None or volume_spike is None or oi_change_pct is None:
        return False, "Missing metrics"

    if abs(move_15m_pct) > MAX_REASONABLE_MOVE_15M:
        return False, "Price move outlier"

    if volume_spike <= 0 or volume_spike > MAX_REASONABLE_VOL_SPIKE:
        return False, "Volume spike outlier"

    if abs(oi_change_pct) > MAX_REASONABLE_OI_CHANGE:
        return False, "OI change outlier"

    return True, "Metrics sane"


def liquidation_quality(liq):
    if liq["total_usd"] < MIN_LIQ_TOTAL_USD:
        return False, "Liquidation weak"
    if liq["total_events"] < MIN_LIQ_EVENTS:
        return False, "Liquidation sparse"
    if liq["dominant_side"] is None:
        return False, "Liquidation mixed"
    if liq["dominance_ratio"] < MIN_LIQ_DOMINANCE:
        return False, "Liquidation not dominant"
    return True, "Liquidation confirmed"


def book_quality(book):
    if not book:
        return False, "Book missing"

    bid_price = book.get("bid_price", 0.0)
    ask_price = book.get("ask_price", 0.0)
    bid_qty = book.get("bid_qty", 0.0)
    ask_qty = book.get("ask_qty", 0.0)

    if min(bid_price, ask_price, bid_qty, ask_qty) <= 0:
        return False, "Book invalid"

    mid = (bid_price + ask_price) / 2
    spread_bps = ((ask_price - bid_price) / mid) * 10000

    if spread_bps > MAX_SPREAD_BPS:
        return False, f"Spread wide ({spread_bps:.1f} bps)"

    return True, "Book confirmed"


def book_imbalance(book):
    bid_qty = book.get("bid_qty", 0.0)
    ask_qty = book.get("ask_qty", 0.0)
    if bid_qty <= 0 or ask_qty <= 0:
        return None, None

    ratio = bid_qty / ask_qty
    if ratio >= MIN_BOOK_IMBALANCE:
        return "BID_HEAVY", ratio
    if ratio <= 1 / MIN_BOOK_IMBALANCE:
        return "ASK_HEAVY", ratio
    return "BALANCED", ratio


def data_trust_score(funding_ok, price_ok, metrics_ok, liq_ok, book_ok):
    score = 0
    if funding_ok:
        score += 3
    if price_ok:
        score += 2
    if metrics_ok:
        score += 2
    if liq_ok:
        score += 2
    if book_ok:
        score += 1
    return score


# =========================================================
# SIGNAL LOGIC
# =========================================================
def detect_bias(funding_pct, oi_change_pct, move_15m_pct, liq, book_bias):
    if (
        funding_pct > 0 and
        oi_change_pct > 0 and
        move_15m_pct > 0 and
        liq["dominant_side"] == "SHORT_LIQUIDATIONS" and
        book_bias in ("ASK_HEAVY", "BALANCED")
    ):
        return "SHORT SETUP"

    if (
        funding_pct < 0 and
        oi_change_pct > 0 and
        move_15m_pct < 0 and
        liq["dominant_side"] == "LONG_LIQUIDATIONS" and
        book_bias in ("BID_HEAVY", "BALANCED")
    ):
        return "LONG SETUP"

    return "WATCHLIST"


def calculate_setup_score(funding_pct, oi_change_pct, volume_spike, move_15m_pct, liq, book_bias):
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
        score += 1
    if volume_spike >= 3.0:
        score += 1

    amv = abs(move_15m_pct)
    if amv >= 0.8:
        score += 1
    if amv >= 1.5:
        score += 1
    if amv >= 2.5:
        score += 1

    if liq["total_usd"] >= MIN_LIQ_TOTAL_USD:
        score += 1
    if liq["total_usd"] >= 400_000:
        score += 1
    if liq["dominance_ratio"] >= 1.5:
        score += 1
    if liq["dominance_ratio"] >= 2.0:
        score += 1

    if book_bias in ("BID_HEAVY", "ASK_HEAVY"):
        score += 1

    return min(score, 10)


def should_alert(trust_score, setup_score, bias):
    if trust_score < MIN_TRUST_SCORE:
        return False
    if setup_score < MIN_SETUP_SCORE:
        return False
    if bias == "WATCHLIST":
        return False
    return True


def build_reasons(funding_pct, oi_change_pct, volume_spike, move_15m_pct, liq, book_bias, book_ratio):
    reasons = []

    if funding_pct > 0:
        reasons.append("Positive funding crowded long")
    elif funding_pct < 0:
        reasons.append("Negative funding crowded short")

    if oi_change_pct > 0 and abs(move_15m_pct) >= 0.8:
        reasons.append("OI rising with directional move")

    if volume_spike >= MIN_VOLUME_SPIKE:
        reasons.append("Closed-candle volume expanded")

    if liq["dominant_side"] == "SHORT_LIQUIDATIONS":
        reasons.append(f"Short liquidations clustered (${liq['short_liq_usd']:,.0f})")
    elif liq["dominant_side"] == "LONG_LIQUIDATIONS":
        reasons.append(f"Long liquidations clustered (${liq['long_liq_usd']:,.0f})")

    if book_bias == "BID_HEAVY":
        reasons.append(f"Top book bid-heavy ({book_ratio:.2f}x)")
    elif book_bias == "ASK_HEAVY":
        reasons.append(f"Top book ask-heavy ({book_ratio:.2f}x)")

    return reasons


def action_from_bias(bias):
    if bias == "SHORT SETUP":
        return "WATCH FOR REVERSAL / SHORT CONFIRMATION"
    if bias == "LONG SETUP":
        return "WATCH FOR REVERSAL / LONG CONFIRMATION"
    return "WAIT"


def make_alert_key(symbol, bias):
    return f"{symbol}|{bias}"


# =========================================================
# FORMAT
# =========================================================
def format_alert(symbol, last_price, mark_price, funding_pct, bybit_funding_pct,
                 oi_now, oi_change_pct, volume_spike, move_15m_pct,
                 liq, book_bias, book_ratio, trust_score, setup_score, reasons, bias):
    reason_text = "\n".join(f"- {r}" for r in reasons)
    action = action_from_bias(bias)

    ratio_text = "N/A" if book_ratio is None else f"{book_ratio:.2f}"

    return (
        f"🚨 FUTURES HUNTER V4\n\n"
        f"Coin: {symbol.replace('USDT', '')}\n"
        f"Bias: {bias}\n"
        f"Last Price: {last_price:.4f}\n"
        f"Mark Price: {mark_price:.4f}\n"
        f"Funding (Binance): {funding_pct:+.3f}%\n"
        f"Funding (Bybit): {bybit_funding_pct:+.3f}%\n"
        f"OI Now: {oi_now:,.0f}\n"
        f"OI Change (1h): {oi_change_pct:+.1f}%\n"
        f"Volume Spike (15m): {volume_spike:.1f}x\n"
        f"15m Move (closed): {move_15m_pct:+.1f}%\n"
        f"Liq 3m Total: ${liq['total_usd']:,.0f}\n"
        f"Liq 3m Longs: ${liq['long_liq_usd']:,.0f}\n"
        f"Liq 3m Shorts: ${liq['short_liq_usd']:,.0f}\n"
        f"Book Bias: {book_bias}\n"
        f"Book Ratio: {ratio_text}\n\n"
        f"Reason:\n{reason_text}\n\n"
        f"Data Trust: {trust_score}/10\n"
        f"Setup Score: {setup_score}/10\n"
        f"Action: {action}"
    )


def format_debug(symbol, funding_pct, bybit_funding_pct, oi_change_pct, volume_spike,
                 move_15m_pct, liq, book_bias, book_ratio, trust_score, setup_score, bias):
    ratio_text = "N/A" if book_ratio is None else f"{book_ratio:.2f}"
    return (
        f"[DEBUG] {symbol} | "
        f"fund_bin={funding_pct:+.3f}% | fund_byb={bybit_funding_pct:+.3f}% | "
        f"oi1h={oi_change_pct:+.2f}% | vol15={volume_spike:.2f}x | move15={move_15m_pct:+.2f}% | "
        f"liq=${liq['total_usd']:,.0f} (L={liq['long_liq_usd']:,.0f}/S={liq['short_liq_usd']:,.0f}) | "
        f"book={book_bias}/{ratio_text} | trust={trust_score} | setup={setup_score} | bias={bias}"
    )


# =========================================================
# ANALYZE
# =========================================================
def analyze_symbol(symbol: str):
    mark_info = get_binance_mark_info(symbol)
    last_price = get_binance_last_price(symbol)
    oi_now = get_binance_open_interest(symbol)

    mark_price = mark_info["mark_price"]
    funding_pct = normalize_funding_pct(mark_info["funding_raw"])

    bybit = get_bybit_funding(symbol)
    if not bybit:
        return None, None

    bybit_funding_pct = normalize_funding_pct(bybit["funding_raw"])

    move_15m_pct = calc_closed_candle_move_15m(symbol)
    volume_spike = calc_closed_candle_volume_spike_15m(symbol)
    oi_change_pct = calc_oi_change_1h(symbol)

    liq = get_liq_snapshot(symbol)
    book = get_book_snapshot(symbol)

    funding_ok, _ = funding_quality(funding_pct, bybit_funding_pct)
    price_ok, _ = price_quality(last_price, mark_price)
    metrics_ok, _ = metrics_quality(move_15m_pct, volume_spike, oi_change_pct)
    liq_ok, _ = liquidation_quality(liq)
    book_ok, _ = book_quality(book)

    book_bias, book_ratio = book_imbalance(book) if book_ok else (None, None)

    trust_score = data_trust_score(
        funding_ok=funding_ok,
        price_ok=price_ok,
        metrics_ok=metrics_ok,
        liq_ok=liq_ok,
        book_ok=book_ok,
    )

    bias = detect_bias(
        funding_pct=funding_pct,
        oi_change_pct=oi_change_pct,
        move_15m_pct=move_15m_pct,
        liq=liq,
        book_bias=book_bias,
    )

    setup_score = calculate_setup_score(
        funding_pct=funding_pct,
        oi_change_pct=oi_change_pct,
        volume_spike=volume_spike,
        move_15m_pct=move_15m_pct,
        liq=liq,
        book_bias=book_bias,
    )

    print(format_debug(
        symbol=symbol,
        funding_pct=funding_pct,
        bybit_funding_pct=bybit_funding_pct,
        oi_change_pct=oi_change_pct,
        volume_spike=volume_spike,
        move_15m_pct=move_15m_pct,
        liq=liq,
        book_bias=book_bias,
        book_ratio=book_ratio,
        trust_score=trust_score,
        setup_score=setup_score,
        bias=bias,
    ))

    if not should_alert(trust_score=trust_score, setup_score=setup_score, bias=bias):
        return None, None

    reasons = build_reasons(
        funding_pct=funding_pct,
        oi_change_pct=oi_change_pct,
        volume_spike=volume_spike,
        move_15m_pct=move_15m_pct,
        liq=liq,
        book_bias=book_bias,
        book_ratio=book_ratio,
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
        liq=liq,
        book_bias=book_bias,
        book_ratio=book_ratio,
        trust_score=trust_score,
        setup_score=setup_score,
        reasons=reasons,
        bias=bias,
    )

    return msg, make_alert_key(symbol, bias)


# =========================================================
# WEBSOCKETS
# =========================================================
async def binance_liq_listener():
    while True:
        try:
            async with websockets.connect(BINANCE_LIQ_WS, ping_interval=20, ping_timeout=20) as ws:
                print("Binance liquidation WS connected")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        data = msg.get("data", msg)
                        items = data if isinstance(data, list) else [data]

                        for item in items:
                            order = item.get("o", {})
                            symbol = order.get("s")
                            side = order.get("S")
                            price = float(order.get("ap", order.get("p", 0)) or 0)
                            qty = float(order.get("q", 0) or 0)
                            usd_value = price * qty
                            if symbol and side and usd_value > 0:
                                add_liq_event(symbol, side, usd_value)
                    except Exception as e:
                        print("Binance liq parse error:", e)
        except Exception as e:
            print("Binance liq reconnect:", e)
            await asyncio.sleep(3)


async def binance_book_listener():
    while True:
        try:
            async with websockets.connect(BINANCE_BOOK_WS, ping_interval=20, ping_timeout=20) as ws:
                print("Binance bookTicker WS connected")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        data = msg.get("data", msg)

                        symbol = data.get("s")
                        bid_price = float(data.get("b", 0) or 0)
                        bid_qty = float(data.get("B", 0) or 0)
                        ask_price = float(data.get("a", 0) or 0)
                        ask_qty = float(data.get("A", 0) or 0)

                        if symbol and min(bid_price, bid_qty, ask_price, ask_qty) > 0:
                            update_book(symbol, bid_price, bid_qty, ask_price, ask_qty)
                    except Exception as e:
                        print("Binance book parse error:", e)
        except Exception as e:
            print("Binance book reconnect:", e)
            await asyncio.sleep(3)


def start_ws_background():
    def runner():
        async def main_ws():
            await asyncio.gather(
                binance_liq_listener(),
                binance_book_listener(),
            )
        asyncio.run(main_ws())

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread


# =========================================================
# MAIN LOOP
# =========================================================
def main():
    state = load_state()
    last_alert_times = state.get("last_alert_times", {})

    start_ws_background()

    print(f"Warming up streams for {STARTUP_WARMUP_SEC}s...")
    time.sleep(STARTUP_WARMUP_SEC)

    send_telegram(f"✅ Futures Hunter V4 started @ {datetime.now(timezone.utc).isoformat()}")

    while True:
        try:
            for symbol in COINS:
                try:
                    msg, key = analyze_symbol(symbol)
                    if msg and key:
                        last_ts = float(last_alert_times.get(key, 0))
                        if now_ts() - last_ts >= ALERT_COOLDOWN_SEC:
                            send_telegram(msg)
                            last_alert_times[key] = now_ts()
                        else:
                            print(f"Cooldown skip: {key}")
                except Exception as e:
                    print(f"Analyze error {symbol}: {e}")

                time.sleep(0.3)

            state["last_alert_times"] = last_alert_times
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)

            time.sleep(SCAN_INTERVAL_SEC)

        except KeyboardInterrupt:
            print("Stopped by user.")
            break
        except Exception as e:
            print("Main loop error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
