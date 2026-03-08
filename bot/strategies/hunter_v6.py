from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd

from bot.utils.indicators import (
    ema,
    rsi,
    atr,
    vwap,
    volume_ratio,
    atr_pct,
    liquidity_sweep_proxy,
)

@dataclass
class Signal:
    symbol: str
    direction: str
    score: float
    entry: float
    stop: float
    tp1: float
    tp2: float
    reason: str

class HunterV6Strategy:
    def __init__(self, config: dict, client):
        self.config = config
        self.client = client
        self.filters = config["filters"]
        self.risk = config["risk"]

    def enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema_fast"] = ema(df["close"], self.filters["ema_fast"])
        df["ema_slow"] = ema(df["close"], self.filters["ema_slow"])
        df["rsi"] = rsi(df["close"], self.filters["rsi_period"])
        df["atr"] = atr(df, 14)
        df["vwap"] = vwap(df)
        df["vol_ratio"] = volume_ratio(df, 20)
        df["atr_pct"] = atr_pct(df, 14)
        df = liquidity_sweep_proxy(df)
        return df

    def _oi_change_pct(self, symbol: str) -> float:
        oi = self.client.get_open_interest_hist(symbol, period="5m", limit=12)
        if oi.empty or len(oi) < 2:
            return 0.0
        first = float(oi["sumOpenInterest"].iloc[-2])
        last = float(oi["sumOpenInterest"].iloc[-1])
        if first == 0:
            return 0.0
        return ((last - first) / first) * 100.0

    def _score_long(
        self,
        row_5m: pd.Series,
        row_15m: pd.Series,
        row_1h: pd.Series,
        funding: float,
        oi_change_pct: float,
        taker_imbalance_pct: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons = []

        if row_1h["close"] > row_1h["ema_slow"]:
            score += 1.5
            reasons.append("1h trend up")

        if row_15m["ema_fast"] > row_15m["ema_slow"]:
            score += 1.5
            reasons.append("15m confirm")

        if row_5m["ema_fast"] > row_5m["ema_slow"]:
            score += 1.5
            reasons.append("5m trigger")

        if row_5m["close"] > row_5m["vwap"]:
            score += 1.0
            reasons.append("above VWAP")

        if row_5m["rsi"] >= self.filters["rsi_long_min"]:
            score += 0.8
            reasons.append("RSI ok")

        if row_5m["vol_ratio"] >= self.filters["min_volume_ratio"]:
            score += 1.0
            reasons.append("volume spike")

        if row_5m["bull_sweep"]:
            score += 1.2
            reasons.append("bull sweep")

        if abs(funding) <= self.filters["max_funding_abs"]:
            score += 0.7
            reasons.append("funding healthy")
        elif funding < 0:
            score += 0.9
            reasons.append("negative funding supportive")

        if oi_change_pct >= self.filters["min_oi_change_pct"]:
            score += 1.0
            reasons.append("OI rising")

        if taker_imbalance_pct >= self.filters["min_taker_imbalance_pct"]:
            score += 1.1
            reasons.append("taker buy pressure")

        if row_5m["atr_pct"] >= self.filters["min_atr_pct"]:
            score += 0.7
            reasons.append("enough volatility")

        return score, reasons

    def _score_short(
        self,
        row_5m: pd.Series,
        row_15m: pd.Series,
        row_1h: pd.Series,
        funding: float,
        oi_change_pct: float,
        taker_imbalance_pct: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons = []

        if row_1h["close"] < row_1h["ema_slow"]:
            score += 1.5
            reasons.append("1h trend down")

        if row_15m["ema_fast"] < row_15m["ema_slow"]:
            score += 1.5
            reasons.append("15m confirm")

        if row_5m["ema_fast"] < row_5m["ema_slow"]:
            score += 1.5
            reasons.append("5m trigger")

        if row_5m["close"] < row_5m["vwap"]:
            score += 1.0
            reasons.append("below VWAP")

        if row_5m["rsi"] <= self.filters["rsi_short_max"]:
            score += 0.8
            reasons.append("RSI ok")

        if row_5m["vol_ratio"] >= self.filters["min_volume_ratio"]:
            score += 1.0
            reasons.append("volume spike")

        if row_5m["bear_sweep"]:
            score += 1.2
            reasons.append("bear sweep")

        if abs(funding) <= self.filters["max_funding_abs"]:
            score += 0.7
            reasons.append("funding healthy")
        elif funding > 0:
            score += 0.9
            reasons.append("positive funding supportive")

        if oi_change_pct >= self.filters["min_oi_change_pct"]:
            score += 1.0
            reasons.append("OI rising")

        if taker_imbalance_pct <= -self.filters["min_taker_imbalance_pct"]:
            score += 1.1
            reasons.append("taker sell pressure")

        if row_5m["atr_pct"] >= self.filters["min_atr_pct"]:
            score += 0.7
            reasons.append("enough volatility")

        return score, reasons

    def analyze(self, symbol: str) -> Optional[Signal]:
        tf_entry = self.config["timeframes"]["entry"]
        tf_confirm = self.config["timeframes"]["confirm"]
        tf_trend = self.config["timeframes"]["trend"]

        df_5m = self.enrich(self.client.get_klines(symbol, tf_entry, 220))
        df_15m = self.enrich(self.client.get_klines(symbol, tf_confirm, 220))
        df_1h = self.enrich(self.client.get_klines(symbol, tf_trend, 220))

        if df_5m.empty or df_15m.empty or df_1h.empty:
            return None

        r5 = df_5m.iloc[-1]
        r15 = df_15m.iloc[-1]
        r1h = df_1h.iloc[-1]

        funding = self.client.get_funding_rate(symbol)
        oi_change_pct = self._oi_change_pct(symbol)
        taker_imbalance_pct = self.client.get_taker_imbalance_pct(symbol, "5m", 30)

        long_score, long_reasons = self._score_long(r5, r15, r1h, funding, oi_change_pct, taker_imbalance_pct)
        short_score, short_reasons = self._score_short(r5, r15, r1h, funding, oi_change_pct, taker_imbalance_pct)

        direction = "LONG" if long_score >= short_score else "SHORT"
        best_score = max(long_score, short_score)
        reasons = long_reasons if direction == "LONG" else short_reasons

        min_score = float(self.config["strategy"]["min_score"])
        if best_score < min_score:
            return None

        price = float(r5["close"])
        atr_val = float(r5["atr"])

        if direction == "LONG":
            stop = price - atr_val * self.risk["atr_sl_mult"]
            tp1 = price + atr_val * self.risk["atr_tp1_mult"]
            tp2 = price + atr_val * self.risk["atr_tp2_mult"]
        else:
            stop = price + atr_val * self.risk["atr_sl_mult"]
            tp1 = price - atr_val * self.risk["atr_tp1_mult"]
            tp2 = price - atr_val * self.risk["atr_tp2_mult"]

        return Signal(
            symbol=symbol,
            direction=direction,
            score=round(best_score, 2),
            entry=round(price, 6),
            stop=round(stop, 6),
            tp1=round(tp1, 6),
            tp2=round(tp2, 6),
            reason=", ".join(reasons[:6]),
        )
