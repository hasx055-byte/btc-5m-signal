import os
import time
import requests
from datetime import datetime, timezone

# =============================
# CONFIG (Railway Variables)
# =============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SYMBOL = os.getenv("SYMBOL", "BTCUSDT").upper()

SAMPLE_INTERVAL_SEC = int(os.getenv("SAMPLE_INTERVAL_SEC", "60"))

SIGNAL_WINDOW_MIN = int(os.getenv("SIGNAL_WINDOW_MIN", "15"))
MIN_MOVE_PCT = float(os.getenv("MIN_MOVE_PCT", "0.20"))

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "1800"))
MAX_SIGNALS_PER_DAY = int(os.getenv("MAX_SIGNALS_PER_DAY", "4"))

EMA_FAST = int(os.getenv("EMA_FAST", "20"))
EMA_SLOW = int(os.getenv("EMA_SLOW", "50"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
VOL_SPIKE = float(os.getenv("VOL_SPIKE", "1.5"))

# ✅ BYBIT API (بديل Binance)
BYBIT_KLINES = "https://api.bybit.com/v5/market/kline"


# =============================
# TELEGRAM
# =============================
def tg_send(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)


# =============================
# INDICATORS
# =============================
def ema(values, period):
    if len(values) < period:
        return None

    k = 2 / (period + 1)
    e = values[0]

    for v in values[1:]:
        e = v * k + e * (1 - k)

    return e


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None

    gains = 0
    losses = 0

    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += abs(diff)

    if losses == 0:
        return 100

    rs = (gains / period) / (losses / period)
    return 100 - (100 / (1 + rs))


# =============================
# FETCH DATA (BYBIT)
# =============================
def fetch_klines_1m(limit=200):

    params = {
        "category": "linear",
        "symbol": SYMBOL,
        "interval": "1",
        "limit": limit
    }

    try:
        r = requests.get(BYBIT_KLINES, params=params, timeout=10)
        r.raise_for_status()

        data = r.json()["result"]["list"]

        closes = [float(x[4]) for x in reversed(data)]
        vols = [float(x[5]) for x in reversed(data)]

        return closes, vols, closes[-1]

    except Exception as e:
        print("Klines fetch error:", e)
        return None, None, None


# =============================
# DECISION ENGINE
# =============================
def decision_signal(closes, vols):

    n = SIGNAL_WINDOW_MIN

    if len(closes) < EMA_SLOW + 10:
        return None, None

    last = closes[-1]
    first = closes[-(n + 1)]

    move_pct = ((last - first) / first) * 100

    ema_fast = ema(closes[-EMA_FAST * 3:], EMA_FAST)
    ema_slow = ema(closes[-EMA_SLOW * 3:], EMA_SLOW)

    r = rsi(closes, RSI_PERIOD)

    avg_vol = sum(vols[-30:]) / 30
    vol_ratio = vols[-1] / avg_vol if avg_vol else 0

    trend_up = ema_fast > ema_slow
    trend_down = ema_fast < ema_slow

    buy_ok = (
        trend_up and
        r >= 52 and
        move_pct >= MIN_MOVE_PCT and
        vol_ratio >= VOL_SPIKE
    )

    sell_ok = (
        trend_down and
        r <= 48 and
        move_pct <= -MIN_MOVE_PCT and
        vol_ratio >= VOL_SPIKE
    )

    info = {
        "price": last,
        "move": move_pct,
        "rsi": r,
        "vol": vol_ratio,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow
    }

    if buy_ok:
        return "BUY", info

    if sell_ok:
        return "SELL", info

    return None, info


# =============================
# MAIN LOOP
# =============================
def main():

    tg_send("🟣 Poly Decision Bot started ✅")

    last_signal = 0
    signals_today = 0
    day = datetime.now(timezone.utc).date()

    while True:

        today = datetime.now(timezone.utc).date()
        if today != day:
            signals_today = 0
            day = today

        closes, vols, price = fetch_klines_1m(250)

        if closes is None:
            time.sleep(20)
            continue

        direction, info = decision_signal(closes, vols)

        print(
            f"{price} | move={info['move']:.3f}% "
            f"RSI={info['rsi']:.1f} Vol={info['vol']:.2f}"
        )

        now = time.time()

        if (
            direction
            and now - last_signal > COOLDOWN_SEC
            and signals_today < MAX_SIGNALS_PER_DAY
        ):

            emoji = "📈" if direction == "BUY" else "📉"

            msg = (
                f"{emoji} {direction} SIGNAL\n"
                f"{SYMBOL}\n"
                f"Price: {info['price']:.2f}\n"
                f"Move(15m): {info['move']:.2f}%\n"
                f"RSI: {info['rsi']:.1f}\n"
                f"Volume Spike: x{info['vol']:.2f}\n"
                f"Signals Today: {signals_today+1}/{MAX_SIGNALS_PER_DAY}"
            )

            tg_send(msg)

            last_signal = now
            signals_today += 1

        time.sleep(SAMPLE_INTERVAL_SEC)


if __name__ == "__main__":
    main()
