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

# optional: force provider (BITSTAMP / COINBASE / KRAKEN / AUTO)
DATA_SOURCE = os.getenv("DATA_SOURCE", "AUTO").strip().upper()

session = requests.Session()
session.headers.update({
    "User-Agent": "poly-decision-bot/2.3",
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
# CONFIDENCE + SUGGESTED RISK
# =============================
def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def compute_confidence_and_risk(direction: str, info: dict):
    """
    Confidence: 55% إلى 92% (تقريبي) بناءً على قوة الشروط
    Suggested Risk: نسبة من رأس المال لكل صفقة (0.5% إلى 2.5%)
    """
    score = 0.0  # 0..100

    # 1) Trend strength (EMA spread)
    ema_fast = info["ema_fast"]
    ema_slow = info["ema_slow"]
    spread_pct = abs(ema_fast - ema_slow) / info["price"] * 100  # %
    # spread صغير = ترند ضعيف، spread أكبر = ترند أقوى
    score += clamp(spread_pct / 0.15 * 18, 0, 18)  # 0..18

    # 2) Momentum (RSI distance from neutral)
    r = info["rsi"]
    if direction == "BUY":
        # أفضل لما RSI أعلى من 55
        score += clamp((r - 50) / 20 * 22, 0, 22)  # 0..22
    else:
        # أفضل لما RSI أقل من 45
        score += clamp((50 - r) / 20 * 22, 0, 22)  # 0..22

    # 3) Move magnitude vs threshold
    move = abs(info["move_pct"])
    score += clamp((move - MIN_MOVE_PCT) / (MIN_MOVE_PCT) * 20, 0, 20)  # 0..20

    # 4) Volume spike strength
    volx = info["vol_ratio"]
    # نعتبر > VOL_SPIKE جيد، وكل ما ارتفع أفضل
    score += clamp((volx / VOL_SPIKE) * 22, 0, 22)  # 0..22

    # 5) Breakout confirmation
    if info["break_up"] or info["break_down"]:
        score += 18  # 0 أو 18

    # تحويل score إلى Confidence %
    # baseline 55 + (score scaled)
    confidence = 55 + (score / 100.0) * 37  # 55..92 تقريباً
    confidence = clamp(confidence, 55, 92)

    # Risk level + suggested position sizing (% of bankroll)
    # كل ما زادت الثقة، نرفع المخاطرة بشكل بسيط فقط
    if confidence >= 80:
        risk_level = "LOW 🟢"
        suggested_risk = 1.5  # % of bankroll
    elif confidence >= 70:
        risk_level = "MEDIUM 🟡"
        suggested_risk = 1.0
    else:
        risk_level = "HIGH 🔴"
        suggested_risk = 0.5

    return round(confidence), risk_level, suggested_risk

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
        "break_down": break_down
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

    tg_send("🟣 Poly Decision Bot started ✅ (AUTO providers + Confidence)")

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
                time.sleep(20)
                continue

            direction, info = decision_signal(closes, vols)

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

            now = time.time()
            if direction and (now - last_signal_time >= COOLDOWN_SEC) and (signals_today < MAX_SIGNALS_PER_DAY):
                confidence, risk_level, suggested_risk = compute_confidence_and_risk(direction, info)

                emoji = "📈" if direction == "BUY" else "📉"
                msg = (
                    f"{emoji} {direction} (Decision)\n"
                    f"BTC-USD\n"
                    f"Price: {info['price']:.2f}\n"
                    f"Move({SIGNAL_WINDOW_MIN}m): {info['move_pct']:.2f}%\n"
                    f"RSI({RSI_PERIOD}): {info['rsi']:.1f}\n"
                    f"EMA{EMA_FAST}/{EMA_SLOW}: {info['ema_fast']:.2f} / {info['ema_slow']:.2f}\n"
                    f"Vol Spike: x{info['vol_ratio']:.2f}\n"
                    f"Breakout: {'UP ✅' if info['break_up'] else ('DOWN ✅' if info['break_down'] else 'NO')}\n"
                    f"\n"
                    f"Confidence: {confidence}%\n"
                    f"Risk Level: {risk_level}\n"
                    f"Suggested Risk: {suggested_risk}%\n"
                    f"\n"
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
