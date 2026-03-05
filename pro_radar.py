import os
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

symbols = [
"BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","DOGEUSDT",
"WIFUSDT","PEPEUSDT","INJUSDT","APTUSDT","ARBUSDT"
]

def send(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url,json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg
    })

def check(symbol):

    url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
    r = requests.get(url).json()

    data = r["result"]["list"][0]

    funding = float(data["fundingRate"])
    volume = float(data["volume24h"])

    if abs(funding) > 0.004:

        msg = f"""
🚨 PRO RADAR

Coin: {symbol}

Funding: {funding*100:.2f} %

Volume 24h: {volume}

Check:
OI
Heatmap
CVD
"""

        send(msg)

for s in symbols:
    check(s)
