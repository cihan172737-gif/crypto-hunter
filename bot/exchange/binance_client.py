from __future__ import annotations
import requests
import pandas as pd
from typing import Optional

BASE_FAPI = "https://fapi.binance.com"

class BinanceFuturesClient:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def _get(self, path: str, params: Optional[dict] = None):
        url = f"{BASE_FAPI}{path}"
        r = requests.get(url, params=params or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        data = self._get("/fapi/v1/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })
        cols = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ]
        df = pd.DataFrame(data, columns=cols)
        numeric_cols = [
            "open", "high", "low", "close", "volume",
            "quote_asset_volume", "taker_buy_base", "taker_buy_quote"
        ]
        for c in numeric_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def get_funding_rate(self, symbol: str) -> float:
        data = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(data["lastFundingRate"])

    def get_open_interest_hist(self, symbol: str, period: str = "5m", limit: int = 30) -> pd.DataFrame:
        data = self._get("/futures/data/openInterestHist", {
            "symbol": symbol,
            "period": period,
            "limit": limit,
        })
        df = pd.DataFrame(data)
        if df.empty:
            return df
        if "sumOpenInterest" in df.columns:
            df["sumOpenInterest"] = pd.to_numeric(df["sumOpenInterest"], errors="coerce")
        if "sumOpenInterestValue" in df.columns:
            df["sumOpenInterestValue"] = pd.to_numeric(df["sumOpenInterestValue"], errors="coerce")
        return df

    def get_taker_imbalance_pct(self, symbol: str, interval: str = "5m", limit: int = 30) -> float:
        df = self.get_klines(symbol, interval, limit)
        if df.empty:
            return 0.0
        taker_buy = df["taker_buy_base"].tail(10).sum()
        total = df["volume"].tail(10).sum()
        if total <= 0:
            return 0.0
        taker_sell = max(total - taker_buy, 0)
        return ((taker_buy - taker_sell) / total) * 100.0
