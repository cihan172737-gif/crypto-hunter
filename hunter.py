import os
import requests
from datetime import datetime

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

URL = "https://api.coingecko.com/api/v3/derivatives"

THRESH = 0.0005


def send(msg):

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

    requests.post(url, json={
        "chat_id": TG_CHAT,
        "text": msg
    })


def main():

    r = requests.get(URL)

    data = r.json()

    alerts = []

    for x in data:

        if x["market"] == "Binance Futures":

            fr = x["funding_rate"]

            if fr and abs(fr) > THRESH:

                alerts.append(
                    f"🚨 {x['symbol']} funding {(fr*100):.3f}%"
                )

    msg = "Funding Scan\n\n"

    if alerts:

        msg += "\n".join(alerts)

    else:

        msg += "Scan OK ✅\nNo alerts"

    msg += "\n\n" + datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    send(msg)


if __name__ == "__main__":
    main()
