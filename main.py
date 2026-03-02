import os
import time
import requests
from datetime import datetime, timezone

# =============================
# CONFIG (Railway Variables)
# =============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SAMPLE_INTERVAL_SEC = int(os.getenv("SAMPLE_INTERVAL_SEC", "60"))

SIGNAL_WINDOW_MIN = int(os.getenv("SIGNAL_WINDOW_MIN", "15"))
MIN_MOVE_PCT = float(os.getenv("MIN_MOVE_PCT", "0.35"))        # صفقات أقل وأقوى

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "1800"))          # 30m
MAX_SIGNALS_PER_DAY = int(os.getenv("MAX_SIGNALS_PER_DAY", "4"))

EMA_FAST = int(os.getenv("EMA_FAST", "20"))
EMA_SLOW = int(os.getenv("EMA_SLOW", "50"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
VOL_SPIKE = float(os.getenv("VOL_SPIKE", "1.8"))               # أقوى

# Retest settings (NEW)
RETEST_ENABLED = os.getenv("RETEST_ENABLED", "1").strip()  # 1=on, 0=off
RETEST_WAIT_SEC = int(os.getenv("RETEST_WAIT_SEC", "60"))  # كم ننتظر قبل "ENTER NOW"
RETEST_TOL_PCT = float(os.getenv("RETEST_TOL_PCT", "0.05")) # سماحية الرجوع ضد الاتجاه (%)

# optional: force provider (BITSTAMP / COINBASE / KRAKEN / AUTO)
DATA_SOURCE = os.getenv("DATA_SOURCE", "AUTO").strip().upper()

session = requests.Session()
session.headers.update({
    "User-Agent": "poly-decision-bot/2.2",
    "Accept": "application/json"
})

# =============================
# TELEGRAM
# =============================
def tg_send(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = session.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=12)
        if r.status_code != 200:
            print("Telegram failed:", r.status_code, r.text[:200])
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
# DATA PROVIDERS (1m candles)
# returns closes(list), vols(list), last_close(float)
# =============================
def fetch_bitstamp(limit=250):
    url = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
    params = {"step": 60, "limit": limit}
    r = session.get(url, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()["data"]["ohlc"]
    closes = [float(x["close"]) for x in data]
    vols = [float(x["volume"]) for x in data]
    return closes, vols, closes[-1]

def fetch_coinbase(limit=250):
    url = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
    params = {"granularity": 60}
    r = session.get(url, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()
    data = data[:limit]
    data = list(reversed(data))  # oldest->newest
    closes = [float(x[4]) for x in data]
    vols = [float(x[5]) for x in data]
    return closes, vols, closes[-1]

def fetch_kraken(limit=250):
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": "XBTUSD", "interval": 1}
    r = session.get(url, params=params, timeout=12)
    r.raise_for_status()
    j = r.json()
    key = next(iter(set(j["result"].keys()) - {"last"}))
    data = j["result"][key]
    data = data[-limit:]
    closes = [float(x[4]) for x in data]
    vols = [float(x[6]) for x in data]
    return closes, vols, closes[-1]

def fetch_klines_1m(limit=250):
    if DATA_SOURCE == "BITSTAMP":
        providers = [fetch_bitstamp]
    elif DATA_SOURCE == "COINBASE":
        providers = [fetch_coinbase]
    elif DATA_SOURCE == "KRAKEN":
        providers = [fetch_kraken]
    else:
        providers = [fetch_bitstamp, fetch_coinbase, fetch_kraken]

    last_err = None
    for fn in providers:
        try:
            closes, vols, last = fn(limit=limit)
            if closes and len(closes) >= 60:
                return closes, vols, last
        except Exception as e:
            last_err = e
            print(f"{fn.__name__} error:", e)

    print("All providers failed:", last_err)
    return None, None, None

# =============================
# DECISION ENGINE
# =============================
def decision_signal(closes, vols):
    n = SIGNAL_WINDOW_MIN

    if len(closes) < max(EMA_SLOW + 10, RSI_PERIOD + 10, n + 5):
        return None, None

    last = closes[-1]
    first = closes[-(n + 1)]
    move_pct = ((last - first) / first) * 100

    ema_fast = ema(closes[-(EMA_FAST * 3):], EMA_FAST)
    ema_slow = ema(closes[-(EMA_SLOW * 3):], EMA_SLOW)
    if ema_fast is None or ema_slow is None:
        return None, None

    r = rsi(closes, RSI_PERIOD)
    if r is None:
        return None, None

    # Volume spike (مع معالجة vol=0)
    lookback = 30
    if len(vols) < lookback + 2:
        return None, None
    avg_vol = sum(vols[-lookback:]) / lookback

    # ✅ إذا الحجم غير موثوق (قريب من صفر) نخليه محايد بدل 0.00
    if avg_vol < 0.0001:
        vol_ratio = 1.0
    else:
        vol_ratio = vols[-1] / avg_vol

    # Breakout filter (قوي)
    recent_high = max(closes[-(n+1):-1])
    recent_low = min(closes[-(n+1):-1])
    break_up = last > recent_high
    break_down = last < recent_low

    trend_up = ema_fast > ema_slow
    trend_down = ema_fast < ema_slow

    buy_ok = (
        trend_up and
        r >= 52 and
        move_pct >= MIN_MOVE_PCT and
        vol_ratio >= VOL_SPIKE and
        break_up
    )

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
# RETEST LOGIC
# =============================
def retest_ok(direction: str, entry_price: float, current_price: float) -> bool:
    """
    هدفها تمنع الدخول المبكر:
    - BUY: نسمح بدخول آمن إذا السعر ما رجع ضدنا أكثر من RETEST_TOL_PCT
           و/أو بدأ يكمل فوق entry_price
    - SELL: نفس الشي بالعكس
    """
    tol = RETEST_TOL_PCT / 100.0
    if direction == "BUY":
        # لو رجع ضدك كثير (نزل أكثر من tol) -> فخ
        if current_price < entry_price * (1 - tol):
            return False
        # دخول آمن: السعر على الأقل رجع يثبت قريب/فوق entry
        return current_price >= entry_price * (1 - tol/2)
    else:  # SELL
        if current_price > entry_price * (1 + tol):
            return False
        return current_price <= entry_price * (1 + tol/2)

# =============================
# MAIN LOOP
# =============================
def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return

    tg_send("🟣 Poly Decision Bot started ✅ (AUTO providers + Retest)")

    last_signal_time = 0
    signals_today = 0
    current_day = datetime.now(timezone.utc).date()

    # Retest state
    pending = None
    # pending = {"dir": "BUY/SELL", "entry": price, "ts": time.time(), "info": info}

    while True:
        try:
            # reset daily counter
            today = datetime.now(timezone.utc).date()
            if today != current_day:
                current_day = today
                signals_today = 0

            closes, vols, last_price = fetch_klines_1m(limit=250)
            if closes is None:
                time.sleep(20)
                continue

            # 1) إذا عندنا صفقة معلقة (Waiting Retest) نقيّمها
            if pending and RETEST_ENABLED == "1":
                if time.time() - pending["ts"] >= RETEST_WAIT_SEC:
                    cur_price = closes[-1]
                    ok = retest_ok(pending["dir"], pending["entry"], cur_price)

                    if ok:
                        emoji = "✅"
                        side = "ENTER NOW (SAFE BUY)" if pending["dir"] == "BUY" else "ENTER NOW (SAFE SELL)"
                        msg = (
                            f"{emoji} {side}\n"
                            f"BTC-USD\n"
                            f"Current Price: {cur_price:.2f}\n"
                            f"From Signal Price: {pending['entry']:.2f}\n"
                            f"Retest: OK\n"
                            f"Signals today: {signals_today}/{MAX_SIGNALS_PER_DAY}"
                        )
                        tg_send(msg)
                    else:
                        tg_send("⚠️ Retest Failed — Skip Trade")

                    pending = None  # ننهي مرحلة الريتست

            # 2) حساب الإشارة الطبيعية
            direction, info = decision_signal(closes, vols)

            # log
            if info:
                print(
                    f"[{datetime.now(timezone.utc)}] price={last_price} "
                    f"move{SIGNAL_WINDOW_MIN}m={info['move_pct']:.3f}% "
                    f"rsi={info['rsi']:.1f} volx={info['vol_ratio']:.2f}"
                )
            else:
                print(f"[{datetime.now(timezone.utc)}] price={last_price} waiting")
                time.sleep(SAMPLE_INTERVAL_SEC)
                continue

            # 3) إذا طلعت إشارة جديدة، لا نرسل دخول مباشر — نرسل Detected ثم نفعّل retest
            now = time.time()
            can_signal = (
                direction
                and (now - last_signal_time >= COOLDOWN_SEC)
                and (signals_today < MAX_SIGNALS_PER_DAY)
                and (pending is None)  # لا نبدأ إشارة ثانية واحنا ننتظر retest
            )

            if can_signal:
                emoji = "📈" if direction == "BUY" else "📉"
                detected_msg = (
                    f"{emoji} {direction} DETECTED\n"
                    f"BTC-USD\n"
                    f"Price: {info['price']:.2f}\n"
                    f"Move({SIGNAL_WINDOW_MIN}m): {info['move_pct']:.2f}%\n"
                    f"RSI({RSI_PERIOD}): {info['rsi']:.1f}\n"
                    f"EMA{EMA_FAST}/{EMA_SLOW}: {info['ema_fast']:.2f} / {info['ema_slow']:.2f}\n"
                    f"Vol Spike: x{info['vol_ratio']:.2f}\n"
                    f"Breakout: {'UP ✅' if info['break_up'] else ('DOWN ✅' if info['break_down'] else 'NO')}\n"
                    f"⏳ Waiting Retest: {RETEST_WAIT_SEC}s\n"
                    f"Signals today: {signals_today+1}/{MAX_SIGNALS_PER_DAY}"
                )
                tg_send(detected_msg)

                # فعّل مرحلة الريتست
                if RETEST_ENABLED == "1":
                    pending = {"dir": direction, "entry": info["price"], "ts": now, "info": info}
                else:
                    # إذا طفيته، يرجع سلوكنا القديم (إرسال مباشر)
                    pass

                last_signal_time = now
                signals_today += 1

            time.sleep(SAMPLE_INTERVAL_SEC)

        except Exception as e:
            print("Loop error:", e)
            time.sleep(10)

if __name__ == "__main__":
    main()
