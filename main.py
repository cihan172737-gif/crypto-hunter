import time
import yaml
from bot.core.runner import run_once
from bot.utils.state import load_state

def load_config():
    with open("config/settings.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def main():
    config = load_config()
    _ = load_state()

    if config["runtime"].get("once", True):
        run_once(config)
        return

    loop_seconds = int(config["runtime"].get("loop_seconds", 300))
    while True:
        try:
            run_once(config)
        except Exception as e:
            print(f"[main] error: {e}")
        time.sleep(loop_seconds)

if __name__ == "__main__":
    main()
