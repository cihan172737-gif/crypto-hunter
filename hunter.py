import os
import requests
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
BINANCE_TICKER_24H_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
BINANCE_OI_URL = "https://fapi.binance.com/fapi/v1/openInterest"

# Test için daha geniş ve daha hareketli liste
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "1000PEPEUSDT",
]

# TEST EŞİKLERİ — önce sinyal üretebildiğini görelim
MIN_ABS_FUNDING_RATE = 0.0001   # %0.01
MIN_PRICE_CHANGE_24H = 1.5      # %1.5
MIN_VOLUME_USDT = 50_000_000    # 50M USDT
MIN_SCORE = 2                   # 2 koşul yeterli olsun

# Not:
# Binance public endpoint ile gerçek liquidation feed almak doğrudan kolay değil.
# Bu yüzden bu test sürümünde:
# funding + hacim + fiyat hareketi + OI ile "setup" üretiyoruz.
# Sonraki aşamada liquidation kaynağı ayrı bağlanabilir.


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram env eksik.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        requests.post(url, data=payload, timeout=15)
    except Exception as e:
        print("Telegram gönderim hatası:", e)


def safe_get_json(url: str, params=None):
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def get_funding_map():
    data = safe_get_json(BINANCE_FUNDING_URL)
    result = {}
    for item in data:
        symbol = item.get("symbol")
        try:
            result[symbol] = float(item.get("lastFundingRate", 0))
        except Exception:
            result[symbol] = 0.0
    return result


def get_ticker_map():
    data = safe_get_json(BINANCE_TICKER_24H_URL)
    result = {}
    for item in data:
        symbol = item.get("symbol")
        try:
            result[symbol] = {
                "price_change_pct": float(item.get("priceChangePercent", 0)),
                "quote_volume": float(item.get("quoteVolume", 0)),
                "last_price": float(item.get("lastPrice", 0)),
            }
        except Exception:
            result[symbol] = {
                "price_change_pct": 0.0,
                "quote_volume": 0.0,
                "last_price": 0.0,
            }
    return result


def get_open_interest(symbol: str):
    try:
        data = safe_get_json(BINANCE_OI_URL, params={"symbol": symbol})
        return float(data.get("openInterest", 0))
    except Exception:
        return 0.0


def build_signal(symbol, funding_rate, price_change_pct, quote_volume, open_interest):
    score = 0
    reasons = []

    if abs(funding_rate) >= MIN_ABS_FUNDING_RATE:
        score += 1
        reasons.append(f"Funding güçlü: {funding_rate * 100:.4f}%")

    if abs(price_change_pct) >= MIN_PRICE_CHANGE_24H:
        score += 1
        direction = "LONG baskı" if price_change_pct > 0 else "SHORT baskı"
        reasons.append(f"24s fiyat hareketi: {price_change_pct:.2f}% ({direction})")

    if quote_volume >= MIN_VOLUME_USDT:
        score += 1
        reasons.append(f"Hacim güçlü: {quote_volume:,.0f} USDT")

    if open_interest > 0:
        score += 1
        reasons.append(f"OI mevcut: {open_interest:,.0f}")

    if score < MIN_SCORE:
        return None

    # Basit yorum
    if funding_rate > 0 and price_change_pct > 0:
        bias = "Aşırı LONG yoğunluğu olabilir, ters hareket takip edilebilir."
    elif funding_rate < 0 and price_change_pct < 0:
        bias = "Aşırı SHORT yoğunluğu olabilir, short squeeze takip edilebilir."
    elif funding_rate > 0 and price_change_pct < 0:
        bias = "LONG'lar sıkışıyor olabilir, devam baskısı izlenebilir."
    elif funding_rate < 0 and price_change_pct > 0:
        bias = "SHORT'lar sıkışıyor olabilir, squeeze devam edebilir."
    else:
        bias = "Yön nötr, teyit gerekli."

    return {
        "symbol": symbol,
        "score": score,
        "funding_rate": funding_rate,
        "price_change_pct": price_change_pct,
        "quote_volume": quote_volume,
        "open_interest": open_interest,
        "bias": bias,
        "reasons": reasons,
    }


def format_signal(signal):
    return (
        f"🚨 <b>Futures Hunter V4 TEST Sinyal</b>\n"
        f"• Coin: <b>{signal['symbol']}</b>\n"
        f"• Skor: <b>{signal['score']}</b>\n"
        f"• Funding: <b>{signal['funding_rate'] * 100:.4f}%</b>\n"
        f"• 24s Fiyat: <b>{signal['price_change_pct']:.2f}%</b>\n"
        f"• Hacim: <b>{signal['quote_volume']:,.0f} USDT</b>\n"
        f"• OI: <b>{signal['open_interest']:,.0f}</b>\n"
        f"• Yorum: {signal['bias']}\n\n"
        f"• Nedenler:\n- " + "\n- ".join(signal["reasons"])
    )


def main():
    now_utc = datetime.now(timezone.utc).isoformat()

    send_telegram(f"✅ Futures Hunter V4 TEST started @ {now_utc}")

    try:
        funding_map = get_funding_map()
        ticker_map = get_ticker_map()

        scanned = 0
        funding_pass = 0
        price_pass = 0
        volume_pass = 0
        final_signals = []

        for symbol in SYMBOLS:
            scanned += 1

            funding_rate = funding_map.get(symbol, 0.0)
            ticker = ticker_map.get(symbol, {})
            price_change_pct = ticker.get("price_change_pct", 0.0)
            quote_volume = ticker.get("quote_volume", 0.0)
            open_interest = get_open_interest(symbol)

            if abs(funding_rate) >= MIN_ABS_FUNDING_RATE:
                funding_pass += 1
            if abs(price_change_pct) >= MIN_PRICE_CHANGE_24H:
                price_pass += 1
            if quote_volume >= MIN_VOLUME_USDT:
                volume_pass += 1

            signal = build_signal(
                symbol=symbol,
                funding_rate=funding_rate,
                price_change_pct=price_change_pct,
                quote_volume=quote_volume,
                open_interest=open_interest,
            )

            if signal:
                final_signals.append(signal)

        debug_text = (
            f"📊 <b>Futures Hunter V4 TEST Debug</b>\n"
            f"• Taranan coin: <b>{scanned}</b>\n"
            f"• Funding filtresini geçen: <b>{funding_pass}</b>\n"
            f"• Fiyat hareket filtresini geçen: <b>{price_pass}</b>\n"
            f"• Hacim filtresini geçen: <b>{volume_pass}</b>\n"
            f"• Final setup sayısı: <b>{len(final_signals)}</b>"
        )
        send_telegram(debug_text)

        if not final_signals:
            send_telegram("ℹ️ Futures Hunter V4 TEST: setup bulunamadı.")
            return

        # En iyi 3 sinyali yolla
        final_signals = sorted(
            final_signals,
            key=lambda x: (
                x["score"],
                abs(x["funding_rate"]),
                abs(x["price_change_pct"]),
                x["quote_volume"],
            ),
            reverse=True,
        )

        for signal in final_signals[:3]:
            send_telegram(format_signal(signal))

    except Exception as e:
        send_telegram(f"❌ Futures Hunter V4 TEST hata:\n<code>{str(e)}</code>")
        raise


if __name__ == "__main__":
    main()
