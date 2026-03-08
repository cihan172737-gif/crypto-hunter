from __future__ import annotations
from datetime import datetime, timezone
from bot.exchange.binance_client import BinanceFuturesClient
from bot.strategies.hunter_v6 import HunterV6Strategy
from bot.utils.state import load_state, save_state
from bot.utils.telegram import send_telegram

def _minutes_since_iso(iso_str: str) -> float:
    dt = datetime.fromisoformat(iso_str)
    now = datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 60.0

def btc_guard_ok(config: dict, client: BinanceFuturesClient) -> bool:
    guard = config.get("btc_guard", {})
    if not guard.get("enabled", False):
        return True

    symbol = guard.get("symbol", "BTCUSDT")
    tf = guard.get("tf", "5m")
    df = client.get_klines(symbol, tf, 30)
    if df.empty or len(df) < 3:
        return True

    last = float(df["close"].iloc[-1])
    prev = float(df["close"].iloc[-2])
    change_pct = ((last - prev) / prev) * 100.0

    if change_pct <= float(guard.get("max_dump_pct", -0.9)):
        print(f"[btc_guard] blocked, btc change={change_pct:.2f}%")
        return False

    return True

def format_signal_message(sig) -> str:
    emoji = "🟢" if sig.direction == "LONG" else "🔴"
    return (
        f"{emoji} <b>HUNTER V6 SIGNAL</b>\n\n"
        f"<b>Coin:</b> {sig.symbol}\n"
        f"<b>Direction:</b> {sig.direction}\n"
        f"<b>Score:</b> {sig.score}\n\n"
        f"<b>Entry:</b> {sig.entry}\n"
        f"<b>Stop:</b> {sig.stop}\n"
        f"<b>TP1:</b> {sig.tp1}\n"
        f"<b>TP2:</b> {sig.tp2}\n\n"
        f"<b>Reason:</b> {sig.reason}"
    )

def run_once(config: dict) -> None:
    state = load_state()

    daily_max = int(config["strategy"]["daily_max_signals"])
    if state["signals_today"] >= daily_max:
        print("[runner] günlük sinyal limiti doldu")
        return

    client = BinanceFuturesClient()

    if not btc_guard_ok(config, client):
        return

    strategy = HunterV6Strategy(config, client)
    symbols = config["symbols"]
    cooldown_minutes = int(config["strategy"]["cooldown_minutes"])

    best_signal = None

    for symbol in symbols:
        last_time = state["last_signal_times"].get(symbol)
        if last_time:
            mins = _minutes_since_iso(last_time)
            if mins < cooldown_minutes:
                print(f"[runner] cooldown {symbol}: {mins:.1f} dk")
                continue

        try:
            sig = strategy.analyze(symbol)
            if sig is None:
                print(f"[runner] no signal: {symbol}")
                continue

            if best_signal is None or sig.score > best_signal.score:
                best_signal = sig

        except Exception as e:
            print(f"[runner] {symbol} error: {e}")

    if best_signal is None:
        print("[runner] uygun sinyal yok")
        return

    msg = format_signal_message(best_signal)
    send_telegram(msg)

    now_iso = datetime.now(timezone.utc).isoformat()
    state["signals_today"] += 1
    state["last_signal_times"][best_signal.symbol] = now_iso
    save_state(state)

    print(f"[runner] sent: {best_signal.symbol} {best_signal.direction} score={best_signal.score}")
