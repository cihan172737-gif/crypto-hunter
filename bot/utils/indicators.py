from __future__ import annotations
import pandas as pd
import numpy as np

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    cum_pv = pv.cumsum()
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    return cum_pv / cum_vol

def volume_ratio(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    vol_ma = df["volume"].rolling(lookback).mean()
    return df["volume"] / vol_ma.replace(0, np.nan)

def atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    a = atr(df, period)
    return (a / df["close"]) * 100.0

def liquidity_sweep_proxy(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bullish sweep:
      - alt wick gövdeye göre büyük
      - kapanış güçlü
      - hacim artmış
    Bearish sweep:
      - üst wick gövdeye göre büyük
      - kapanış zayıf
      - hacim artmış
    """
    out = df.copy()
    body = (out["close"] - out["open"]).abs()
    lower_wick = (out[["open", "close"]].min(axis=1) - out["low"]).clip(lower=0)
    upper_wick = (out["high"] - out[["open", "close"]].max(axis=1)).clip(lower=0)
    vr = volume_ratio(out, 20).fillna(0)

    out["bull_sweep"] = (
        (lower_wick > body * 1.3) &
        (out["close"] > out["open"]) &
        (vr > 1.2)
    )

    out["bear_sweep"] = (
        (upper_wick > body * 1.3) &
        (out["close"] < out["open"]) &
        (vr > 1.2)
    )
    return out
