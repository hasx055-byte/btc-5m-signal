import os
import time
import requests
from datetime import datetime, timezone

# =============================
# CONFIG (Railway Variables)
# =============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SYMBOL = os.getenv("SYMBOL", "BTCUSDT").strip().upper()

SAMPLE_INTERVAL_SEC = int(os.getenv("SAMPLE_INTERVAL_SEC", "60"))

SIGNAL_WINDOW_MIN = int(os.getenv("SIGNAL_WINDOW_MIN", "15"))  # decision window
MIN_MOVE_PCT = float(os.getenv("MIN_MOVE_PCT", "0.20"))        # min move in window (%)

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "1800"))          # 30m default
MAX_SIGNALS_PER_DAY = int(os.getenv("MAX_SIGNALS_PER_DAY", "4"))

EMA_FAST = int(os.getenv("EMA_FAST", "20"))
EMA_SLOW = int(os.getenv("EMA_SLOW", "50"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
VOL_SPIKE = float(os.getenv("VOL_SPIKE", "1.5"))               # volume spike ratio

# Binance endpoints (no key needed)
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

# =============================
# TELEGRAM SEND
# =============================
def tg_send(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=12)
        if r.status_code != 200:
            print("Telegram send failed:", r.status_code, r.text[:200])
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
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))

# =============================
# DATA FETCH (1m candles)
# =============================
def fetch_klines_1m(limit=200):
    params = {"symbol": SYMBOL, "interval": "1m", "limit": limit}
    try:
        r = requests.get(BINANCE_KLINES, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        # each kline: [openTime, open, high, low, close, volume, closeTime, ...]
        closes = [float(x[4]) for x in data]
        vols = [float(x[5]) for x in data]
        last_close = closes[-1]
        return closes, vols, last_close
    except Exception as e:
        print("Klines fetch error:", e)
        return None, None, None

# =============================
# SIGNAL LOGIC (15m decision)
# =============================
def decision_signal(closes, vols):
    """
    Returns: (direction_str, info_dict) or (None, info_dict/None)

    Filters:
    1) Trend: EMA_FAST vs EMA_SLOW
    2) Momentum: RSI
    3) Move over last SIGNAL_WINDOW_MIN minutes
    4) Volume spike: last vol vs avg vol
    5) Breakout: last close breaks last n-min range (strong filter)
    """
    n = SIGNAL_WINDOW_MIN
    if len(closes) < max(EMA_SLOW + 5, RSI_PERIOD + 5, n + 2):
        return None, None

    last = closes[-1]
    first = closes[-(n + 1)]
    move_pct = ((last - first) / first) * 100

    # Breakout filter: last close must break the last n minutes range
    recent_high = max(closes[-(n+1):-1])
    recent_low  = min(closes[-(n+1):-1])
    break_up = last > recent_high
    break_down = last < recent_low

    ema_fast = ema(closes[-(EMA_FAST * 3):], EMA_FAST)
    ema_slow = ema(closes[-(EMA_SLOW * 3):], EMA_SLOW)
    if ema_fast is None or ema_slow is None:
        return None, None

    r = rsi(closes, RSI_PERIOD)
    if r is None:
        return None, None

    # Volume spike: compare last minute volume vs avg of last 30 mins
    lookback = 30
    if len(vols) < lookback + 2:
        return None, None

    avg_vol = sum(vols[-lookback:]) / lookback
    vol_ratio = (vols[-1] / avg_vol) if avg_vol > 0 else 0

    trend_up = ema_fast > ema_slow
    trend_down = ema_fast < ema_slow

    # Buy conditions (strong)
    buy_ok = (
        trend_up and
        r >= 52 and
        move_pct >= MIN_MOVE_PCT and
        vol_ratio >= VOL_SPIKE and
        break_up
    )

    # Sell conditions (strong)
    sell_ok = (
        trend_down and
        r <= 48 and
        move_pct <= -MIN_MOVE_PCT and
        vol_ratio >= VOL_SPIKE and
        break_down
    )

    info = {
        "price": last,
        "move_pct": move_pct,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "rsi": r,
        "vol_ratio": vol_ratio,
        "break_up": break_up,
        "break_down": break_down,
        "recent_high": recent_high,
        "recent_low": recent_low
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
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return

    tg_send("🟣 Poly Decision Bot (15m) started ✅")

    last_signal_time = 0
    signals_today = 0
    current_day = datetime.now(timezone.utc).date()

    while True:
        try:
            today = datetime.now(timezone.utc).date()
            if today != current_day:
                current_day = today
                signals_today = 0

            closes, vols, last_price = fetch_klines_1m(limit=250)
            if closes is None:
                time.sleep(15)
                continue

            direction, info = decision_signal(closes, vols)

            # ✅ Prevent crash if info is None
            if info:
                print(
                    f"[{datetime.now(timezone.utc)}] price={last_price} "
                    f"move{SIGNAL_WINDOW_MIN}m={info['move_pct']:.3f}% "
                    f"rsi={info['rsi']:.1f} volx={info['vol_ratio']:.2f} "
                    f"breakUp={info['break_up']} breakDown={info['break_down']}"
                )
            else:
                print(f"[{datetime.now(timezone.utc)}] price={last_price} waiting (insufficient data)")
                time.sleep(SAMPLE_INTERVAL_SEC)
                continue

            now = time.time()
            if (
                direction
                and (now - last_signal_time >= COOLDOWN_SEC)
                and (signals_today < MAX_SIGNALS_PER_DAY)
            ):
                emoji = "📈" if direction == "BUY" else "📉"
                msg = (
                    f"{emoji} {direction} (15m)\n"
                    f"Symbol: {SYMBOL}\n"
                    f"Price: {info['price']:.2f}\n"
                    f"Move({SIGNAL_WINDOW_MIN}m): {info['move_pct']:.2f}%\n"
                    f"RSI({RSI_PERIOD}): {info['rsi']:.1f}\n"
                    f"EMA{EMA_FAST}/{EMA_SLOW}: {info['ema_fast']:.2f} / {info['ema_slow']:.2f}\n"
                    f"Vol Spike: x{info['vol_ratio']:.2f}\n"
                    f"Breakout: {'UP ✅' if info['break_up'] else ('DOWN ✅' if info['break_down'] else 'NO')}\n"
                    f"Signals today: {signals_today+1}/{MAX_SIGNALS_PER_DAY}"
                )
                tg_send(msg)
                last_signal_time = now
                signals_today += 1

            time.sleep(SAMPLE_INTERVAL_SEC)

        except Exception as e:
            print("Loop error:", e)
            time.sleep(10)

if __name__ == "__main__":
    main()
