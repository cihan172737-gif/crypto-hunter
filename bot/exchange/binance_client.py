import requests
import pandas as pd

# Binance Futures alternatif endpointleri
BASE_URLS = [
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://fapi.binance.com"
]

class BinanceClient:

    def _get(self, path, params=None):
        last_error = None

        for base in BASE_URLS:
            try:
                url = base + path
                r = requests.get(url, params=params, timeout=10)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_error = e
                continue

        raise last_error


    def get_klines(self, symbol, interval, limit=100):
        data = self._get(
            "/fapi/v1/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
        )

        df = pd.DataFrame(data, columns=[
            "open_time","open","high","low","close","volume",
            "close_time","qav","num_trades","taker_base","taker_quote","ignore"
        ])

        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)

        return df


    def get_funding_rate(self, symbol):
        data = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(data["lastFundingRate"])


    def get_open_interest_hist(self, symbol, period="5m", limit=30):
        data = self._get(
            "/futures/data/openInterestHist",
            {
                "symbol": symbol,
                "period": period,
                "limit": limit
            }
        )

        df = pd.DataFrame(data)
        if not df.empty:
            df["sumOpenInterest"] = df["sumOpenInterest"].astype(float)

        return df


    def get_taker_imbalance_pct(self, symbol, interval="5m", limit=30):
        df = self.get_klines(symbol, interval, limit)

        taker_buy = df["taker_base"].tail(10).sum()
        total = df["volume"].tail(10).sum()

        if total == 0:
            return 0

        taker_sell = total - taker_buy

        imbalance = (taker_buy - taker_sell) / total * 100

        return imbalance
