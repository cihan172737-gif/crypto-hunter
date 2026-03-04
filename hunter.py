import requests
import os

SYMBOLS = ["BTCUSDT","ETHUSDT","BNBUSDT"]

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

def get_funding(symbol):

    url = "https://api.bybit.com/v5/market/tickers"

    params = {
        "category":"linear",
        "symbol":symbol
    }

    r = requests.get(url,params=params)
    data = r.json()

    funding = data["result"]["list"][0]["fundingRate"]

    return float(funding)


def send_telegram(msg):

    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

    payload={
        "chat_id":TG_CHAT,
        "text":msg
    }

    requests.post(url,json=payload)


def main():

    msg="Bybit Funding Scan\n\n"

    for s in SYMBOLS:

        fr=get_funding(s)

        msg+=f"{s} funding: {fr}\n"

    send_telegram(msg)


main()
