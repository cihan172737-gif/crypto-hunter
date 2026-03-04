import os
import requests
from datetime import datetime

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = [
"BTC","ETH","SOL","XRP","BNB",
"DOGE","ADA","AVAX","LINK","TRX"
]

URL = "https://open-api.coinglass.com/public/v2/funding"

def send(msg):

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

    requests.post(url,json={
        "chat_id": TG_CHAT,
        "text": msg
    })


def get_funding(symbol):

    r = requests.get(URL)

    data = r.json()

    for x in data["data"]:

        if x["symbol"] == symbol:

            return float(x["fundingRate"])

    return None


def main():

    alerts = []

    for s in SYMBOLS:

        fr = get_funding(s)

        if fr is None:
            continue

        if abs(fr) > 0.0005:

            alerts.append(
                f"🚨 {s} funding {(fr*100):.3f}%"
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
