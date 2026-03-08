import requests
import pandas as pd


class BinanceClient:

    def get_klines(self, symbol, interval, limit=100):

        # CoinGecko fallback fiyat
        url = "https://api.coingecko.com/api/v3/simple/price"

        coin = symbol.replace("USDT", "").lower()

        r = requests.get(url, params={
            "ids": coin,
            "vs_currencies": "usd"
        })

        data = r.json()

        price = data.get(coin, {}).get("usd", 0)

        df = pd.DataFrame({
            "close": [price] * limit,
            "open": [price] * limit,
            "high": [price] * limit,
            "low": [price] * limit,
            "volume": [0] * limit,
            "taker_buy_base": [0] * limit
        })

        return df


    def get_funding_rate(self, symbol):
        return 0


    def get_open_interest_hist(self, symbol, period="5m", limit=30):
        return pd.DataFrame()


    def get_taker_imbalance_pct(self, symbol, interval="5m", limit=30):
        return 0
