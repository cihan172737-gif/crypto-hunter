import os
import time
import requests
from datetime import datetime, timezone

# ---------------------------
# CONFIG
# ---------------------------
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]  # istersen çoğaltırız
TIMEFRAME_HOURS = 8  # "8 saat daha iyi" dediğin için
MIN_ABS_FUNDING = 0.0005  # 0.05% / 8h eşik (kalite için); istersen ayarlarız

ALERT_ONLY = os.getenv("ALERT_ONLY", "1") == "1"        # sadece sinyal varsa mesaj
SEND_IF_EMPTY = os.getenv("SEND_IF_EMPTY", "0") == "1"  # sinyal yoksa da mesaj at

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

# Binance futures endpoints (GitHub runner bazılarını bloklayabiliyor)
BASE_CANDIDATES = [
    "https://www.binance.com",     # çoğu zaman daha iyi
    "https://fapi.binance.com",    # klasik
    "https://api.binance.com",     # bazı bölgelerde fallback
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CryptoHunter/1.0; +https://github.com/)"
}

TIMEOUT = 20


# ---------------------------
# HELPERS
# ---------------------------
def tg_send(text: str):
    if not TG_TOKEN or not TG_CHAT:
        # secrets eksikse burada düşmesin diye
        print("Telegram secrets missing. Message:\n", text)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=TIMEOUT)
    except Exception as e:
        print("Telegram send error:", e)


def get_json(path: str, params: dict | None = None):
    """
    Aynı endpointi 3 farklı base ile dener.
    451/403 gibi engellerde diğer base'e geçer.
    """
    last_err = None
    for base in BASE_CANDIDATES:
        url = f"{base}{path}"
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            # 451/403 gibi durumlarda raise ile yakalayacağız
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            last_err = e
            # 451/403/418/429 vb. durumlarda diğer base'e geç
            try:
                code = e.response.status_code
            except Exception:
                code = None
            print(f"HTTPError {code} @ {url}")
            continue
        except Exception as e:
            last_err = e
            print(f"Error @ {url}: {e}")
            continue

    raise last_err if last_err else RuntimeError("All endpoints failed")


def fmt_pct(x: float) -> str:
    return f"{x*100:.3f}%"


def now_tr():
    # TR saatine yakın göstermek için (UTC+3 sabit)
    return datetime.now(timezone.utc).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------
# SIGNAL LOGIC (A+ basit ama disiplinli)
# ---------------------------
def funding_rate(symbol: str) -> float:
    # premiumIndex: {"lastFundingRate": "...", "nextFundingTime": ...}
    j = get_json("/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(j["lastFundingRate"])


def choose_direction(fr: float) -> str:
    """
    Funding pozitif ve yüksekse: piyasada long kalabalık -> SHORT bias
    Funding negatif ve yüksekse: short kalabalık -> LONG bias
    """
    if fr > 0:
        return "SHORT"
    if fr < 0:
        return "LONG"
    return "FLAT"


def score_setup(fr: float) -> str:
    """
    Basit A+ kuralı:
    - |funding| >= MIN_ABS_FUNDING ise aday
    """
    if abs(fr) >= MIN_ABS_FUNDING:
        return "A+"
    return "NO"


def build_message(signals: list[dict], note: str = "") -> str:
    ts = now_tr()
    lines = [f"🧠 Crypto Hunter Alert ({ts})"]
    if note:
        lines.append(note)
    lines.append("")

    if not signals:
        lines.append("🧊 A+ yok (şartlar sağlanmadı).")
        return "\n".join(lines)

    lines.append("🚨 A+ SETUP BULUNDU")
    lines.append("")

    # solda olsun dedin: formatı soldan hizalı tutuyorum
    for s in signals:
        lines.append(f"• {s['symbol']}")
        lines.append(f"  Funding: {s['funding_pct']}")
        lines.append(f"  Zaman: {TIMEFRAME_HOURS}h")
        lines.append(f"  Skor: {s['score']}")
        lines.append(f"  ---")
        lines.append("")

    # en altta long/short yazsın dedin:
    # birden fazla sinyal varsa ilkini yazıyoruz (istersen hepsine ayrı yazarız)
    lines.append(f"📌 SONUÇ: {signals[0]['direction']}")
    return "\n".join(lines)


def main():
    signals = []
    errors = []

    for sym in SYMBOLS:
        try:
            fr = funding_rate(sym)
            sc = score_setup(fr)
            if sc == "A+":
                direction = choose_direction(fr)
                signals.append({
                    "symbol": sym,
                    "funding": fr,
                    "funding_pct": fmt_pct(fr),
                    "score": sc,
                    "direction": direction
                })
        except Exception as e:
            errors.append(f"{sym}: {type(e).__name__} - {e}")

        # küçük throttle
        time.sleep(0.3)

    # Eğer hiç sinyal yoksa ve SEND_IF_EMPTY kapalıysa sessiz kal
    if not signals and ALERT_ONLY and not SEND_IF_EMPTY and not errors:
        print("No A+ and SEND_IF_EMPTY=0. Exiting silently.")
        return

    note = ""
    if errors:
        # GitHub runner Binance'ı engelliyorsa burada görürüz (451/403 vb.)
        note = "⚠️ Veri erişim uyarısı:\n" + "\n".join(errors[:6])
        if len(errors) > 6:
            note += f"\n... +{len(errors)-6} hata daha"

    msg = build_message(signals, note=note)
    tg_send(msg)
    print(msg)


if __name__ == "__main__":
    main()
