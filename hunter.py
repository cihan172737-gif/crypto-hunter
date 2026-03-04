import requests
import os
import json
import time
from datetime import datetime, timedelta

BYBIT_API = "https://api.bybit.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

MAX_ALERTS = 3
COOLDOWN_MINUTES = 180

THRESHOLDS = [
    0.0010,
    0.0015,
    0.0020
]

COOLDOWN_FILE = "cooldown.json"


def load_symbols():
    if os.path.exists("symbols.txt"):
        with open("symbols.txt") as f:
            return [s.strip() for s in f.readlines() if s.strip()]
    return ["BTCUSDT", "ETHUSDT"]


def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg
        }
    )


def get_funding(symbol):
    url = f"{BYBIT_API}/v5/market/tickers"

    r = requests.get(
        url,
        params={
            "category": "linear",
            "symbol": symbol
        }
    ).json()

    return float(r["result"]["list"][0]["fundingRate"])


def get_open_interest(symbol):
    url = f"{BYBIT_API}/v5/market/open-interest"

    r = requests.get(
        url,
        params={
            "category": "linear",
            "symbol": symbol,
            "intervalTime": "5min"
        }
    ).json()

    data = r["result"]["list"]

    if len(data) < 2:
        return 0

    oi_now = float(data[-1]["openInterest"])
    oi_prev = float(data[-2]["openInterest"])

    return oi_now - oi_prev


def load_cooldown():
    if os.path.exists(COOLDOWN_FILE):
        with open(COOLDOWN_FILE) as f:
            return json.load(f)
    return {}


def save_cooldown(data):
    with open(COOLDOWN_FILE, "w") as f:
        json.dump(data, f)


def check_tier(value):

    abs_val = abs(value)

    for t in reversed(THRESHOLDS):
        if abs_val >= t:
            return t

    return None


def main():

    symbols = load_symbols()

    cooldown = load_cooldown()

    alerts = []

    for symbol in symbols:

        try:

            funding = get_funding(symbol)

            tier = check_tier(funding)

            if not tier:
                continue

            if symbol in cooldown:

                expire = datetime.fromisoformat(cooldown[symbol])

                if datetime.utcnow() < expire:
                    continue

            oi_change = get_open_interest(symbol)

            if oi_change <= 0:
                continue

            if funding > 0:
                side = "SHORT candidate"
            else:
                side = "LONG candidate"

            alerts.append((symbol, funding, tier, side))

            cooldown[symbol] = (
                datetime.utcnow() + timedelta(minutes=COOLDOWN_MINUTES)
            ).isoformat()

            if len(alerts) >= MAX_ALERTS:
                break

            time.sleep(0.2)

        except Exception:
            pass

    save_cooldown(cooldown)

    for symbol, funding, tier, side in alerts:

        msg = f"""
FUNDING ALERT

Coin: {symbol}
Funding: {funding*100:.3f}%

Tier: {tier*100:.2f}%

Setup: {side}
"""

        send_telegram(msg)


if __name__ == "__main__":
    main()
