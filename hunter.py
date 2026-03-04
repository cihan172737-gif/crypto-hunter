# hunter.py — Funding Alerts (Binance, GitHub Actions uyumlu)

import os
import time
import requests
from datetime import datetime

# -------- CONFIG --------

DEFAULT_SYMBOLS = [
"BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","TRXUSDT",
"MATICUSDT","DOTUSDT","LTCUSDT","ATOMUSDT","OPUSDT","ARBUSDT","APTUSDT","INJUSDT","NEARUSDT","UNIUSDT"
]

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID","")

SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY","1") == "1"

THRESH_LOW  = float(os.getenv("THRESH_LOW","0.0003"))
THRESH_MID  = float(os.getenv("THRESH_MID","0.0005"))
THRESH_HIGH = float(os.getenv("THRESH_HIGH","0.0008"))

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT","20"))

BINANCE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

# -------- HELPERS --------

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def send_telegram(text):

    if not TG_TOKEN or not TG_CHAT:
        print("Telegram config missing")
        print(text)
        return

    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

    try:
        requests.post(url,json={
            "chat_id":TG_CHAT,
            "text":text
        },timeout=HTTP_TIMEOUT)
    except Exception as e:
        print("Telegram error:",e)

def funding(symbol):

    try:
        r=requests.get(BINANCE_URL,params={"symbol":symbol},timeout=HTTP_TIMEOUT)

        if r.status_code!=200:
            return None,f"{r.status_code} error"

        data=r.json()

        fr=float(data["lastFundingRate"])

        return fr,None

    except Exception as e:
        return None,str(e)

def level(fr):

    fr=abs(fr)

    if fr>=THRESH_HIGH:
        return "HIGH"

    if fr>=THRESH_MID:
        return "MID"

    if fr>=THRESH_LOW:
        return "LOW"

    return None

# -------- MAIN --------

def main():

    alerts=[]
    errors=[]

    for sym in DEFAULT_SYMBOLS:

        fr,err=funding(sym)

        if err:
            errors.append(f"{sym} | {err}")
            continue

        lvl=level(fr)

        if lvl:

            side="POS" if fr>0 else "NEG"

            alerts.append(f"🚨 {lvl} | {sym} | funding {(fr*100):.3f}% ({side})")

        time.sleep(0.1)

    msg="Funding Alerts\n\n"

    if errors:
        msg+="Errors:\n"
        msg+="\n".join(errors[:5])
        msg+="\n\n"

    if alerts:

        msg+="Alerts:\n"
        msg+="\n".join(alerts)

    else:

        msg+="Scan OK ✅\nNo alerts."

    msg+=f"\n\n{now()}"

    send_telegram(msg)

    print(msg)

if __name__=="__main__":
    main()
