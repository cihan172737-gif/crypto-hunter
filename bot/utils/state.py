from __future__ import annotations
import json
import os
from datetime import datetime, timezone

STATE_FILE = "state.json"

def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        state = {
            "date": _today_str(),
            "signals_today": 0,
            "last_signal_times": {}
        }
        save_state(state)
        return state

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    if state.get("date") != _today_str():
        state = {
            "date": _today_str(),
            "signals_today": 0,
            "last_signal_times": {}
        }
        save_state(state)

    return state

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
