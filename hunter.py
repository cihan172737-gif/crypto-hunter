import os
import requests
from datetime import datetime

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

URL = "https://open-api.coinglass.com/public/v2/funding"

SYMBOLS = [
"BTC","ETH","SOL","XRP","BNB",
"DOGE","ADA","AVAX","LINK","TRX"
]


def send(msg):

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

    requests.post(url, json={
        "chat_id": TG_CHAT,
        "text": msg
    })


def get_all():

    try:

        r = requests.get(URL, timeout=20)

        j = r.json()

        if "data" not in j:
            return []

        return j["data"]

    except:
        return []


def main():

    data = get_all()

    alerts = []

    for coin in SYMBOLS:

        for x in data:

            if x.get("symbol") == coin:

                fr = float(x.get("fundingRate",0))

                if abs(fr) > 0.0005:

                    alerts.append(
                        f"🚨 {coin} funding {(fr*100):.3f}%"
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
