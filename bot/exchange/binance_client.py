import requests
import pandas as pd

# Kline için spot endpoint kullanıyoruz
BASE_SPOT = "https://api.binance.com"

# Futures verileri için alternatif endpointler
BASE_FUTURES = [
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://fapi.binance.com",
]


class BinanceClient:
    def _get_spot(self, path, params=None):
        url = BASE_SPOT + path
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def _get_futures(self, path, params=None):
        last_error = None

        for base in BASE_FUTURES:
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
        data = self._get_spot(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
        )

        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])

        for col in ["open", "high", "low", "close", "volume", "taker_buy_base", "taker_buy_quote"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def get_funding_rate(self, symbol):
        try:
            data = self._get_futures("/fapi/v1/premiumIndex", {"symbol": symbol})
            return float(data.get("lastFundingRate", 0))
        except Exception:
            return 0.0

    def get_open_interest_hist(self, symbol, period="5m", limit=30):
        try:
            data = self._get_futures(
                "/futures/data/openInterestHist",
                {
                    "symbol": symbol,
                    "period": period,
                    "limit": limit
                }
            )

            df = pd.DataFrame(data)
            if not df.empty and "sumOpenInterest" in df.columns:
                df["sumOpenInterest"] = pd.to_numeric(df["sumOpenInterest"], errors="coerce")

            return df
        except Exception:
            return pd.DataFrame()

    def get_taker_imbalance_pct(self, symbol, interval="5m", limit=30):
        try:
            df = self.get_klines(symbol, interval, limit)

            taker_buy = df["taker_buy_base"].tail(10).sum()
            total = df["volume"].tail(10).sum()

            if total == 0:
                return 0.0

            taker_sell = total - taker_buy
            imbalance = (taker_buy - taker_sell) / total * 100
            return float(imbalance)
        except Exception:
            return 0.0
