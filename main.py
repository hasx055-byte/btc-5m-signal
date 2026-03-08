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
MIN_MOVE_PCT = float(os.getenv("MIN_MOVE_PCT", "0.35"))

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "1800"))
MAX_SIGNALS_PER_DAY = int(os.getenv("MAX_SIGNALS_PER_DAY", "4"))

EMA_FAST = int(os.getenv("EMA_FAST", "20"))
EMA_SLOW = int(os.getenv("EMA_SLOW", "50"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
VOL_SPIKE = float(os.getenv("VOL_SPIKE", "1.8"))

MIN_CONF_SEND = int(os.getenv("MIN_CONF_SEND", "75"))

VOL_TRAP_ENABLED = os.getenv("VOL_TRAP_ENABLED", "1").strip()
VOL_TRAP_LOOKBACK = int(os.getenv("VOL_TRAP_LOOKBACK", "30"))
VOL_TRAP_MULT = float(os.getenv("VOL_TRAP_MULT", "3.0"))
VOL_TRAP_MIN_PCT = float(os.getenv("VOL_TRAP_MIN_PCT", "0.25"))

ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
MIN_ATR_PCT = float(os.getenv("MIN_ATR_PCT", "0.10"))
MAX_ATR_PCT = float(os.getenv("MAX_ATR_PCT", "0.80"))

# NEW: Early setup / Strong momentum
EARLY_SETUP_ENABLED = os.getenv("EARLY_SETUP_ENABLED", "1").strip()
EARLY_COOLDOWN_SEC = int(os.getenv("EARLY_COOLDOWN_SEC", "900"))   # 15m
STRONG_MOMENTUM_MIN_CONF = int(os.getenv("STRONG_MOMENTUM_MIN_CONF", "88"))

DATA_SOURCE = os.getenv("DATA_SOURCE", "AUTO").strip().upper()

session = requests.Session()
session.headers.update({
    "User-Agent": "poly-decision-bot/5.0",
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
        r = session.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=15)
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

def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        trs.append(tr)
    return sum(trs) / len(trs)

# =============================
# DATA PROVIDERS
# returns opens, highs, lows, closes, vols, last_close
# =============================
def fetch_bitstamp(limit=250):
    url = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
    params = {"step": 60, "limit": limit}
    r = session.get(url, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()["data"]["ohlc"]
    opens = [float(x["open"]) for x in data]
    highs = [float(x["high"]) for x in data]
    lows = [float(x["low"]) for x in data]
    closes = [float(x["close"]) for x in data]
    vols = [float(x["volume"]) for x in data]
    return opens, highs, lows, closes, vols, closes[-1]

def fetch_coinbase(limit=250):
    url = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
    params = {"granularity": 60}
    r = session.get(url, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()
    data = data[:limit]
    data = list(reversed(data))
    opens = [float(x[3]) for x in data]
    highs = [float(x[2]) for x in data]
    lows = [float(x[1]) for x in data]
    closes = [float(x[4]) for x in data]
    vols = [float(x[5]) for x in data]
    return opens, highs, lows, closes, vols, closes[-1]

def fetch_kraken(limit=250):
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": "XBTUSD", "interval": 1}
    r = session.get(url, params=params, timeout=12)
    r.raise_for_status()
    j = r.json()
    key = next(iter(set(j["result"].keys()) - {"last"}))
    data = j["result"][key][-limit:]
    opens = [float(x[1]) for x in data]
    highs = [float(x[2]) for x in data]
    lows = [float(x[3]) for x in data]
    closes = [float(x[4]) for x in data]
    vols = [float(x[6]) for x in data]
    return opens, highs, lows, closes, vols, closes[-1]

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
            opens, highs, lows, closes, vols, last = fn(limit=limit)
            if closes and len(closes) >= 60:
                return opens, highs, lows, closes, vols, last
        except Exception as e:
            last_err = e
            print(f"{fn.__name__} error:", e)

    print("All providers failed:", last_err)
    return None, None, None, None, None, None

# =============================
# HELPERS
# =============================
def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def candle_strength(direction, o, h, l, c):
    candle_range = max(h - l, 1e-9)
    body = abs(c - o)
    body_ratio = body / candle_range

    close_pos = (c - l) / candle_range
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    score = 0

    if body_ratio >= 0.70:
        score += 40
    elif body_ratio >= 0.50:
        score += 30
    elif body_ratio >= 0.30:
        score += 20
    else:
        score += 10

    if direction == "BUY":
        if close_pos >= 0.80:
            score += 35
        elif close_pos >= 0.65:
            score += 25
        elif close_pos >= 0.50:
            score += 15
        if lower_wick <= candle_range * 0.15:
            score += 15
        elif lower_wick <= candle_range * 0.25:
            score += 10
        if upper_wick > candle_range * 0.35:
            score -= 15
    else:
        if close_pos <= 0.20:
            score += 35
        elif close_pos <= 0.35:
            score += 25
        elif close_pos <= 0.50:
            score += 15
        if upper_wick <= candle_range * 0.15:
            score += 15
        elif upper_wick <= candle_range * 0.25:
            score += 10
        if lower_wick > candle_range * 0.35:
            score -= 15

    score = clamp(score, 0, 100)

    if score >= 80:
        label = "Very Strong"
    elif score >= 65:
        label = "Strong"
    elif score >= 50:
        label = "Medium"
    else:
        label = "Weak"

    return int(score), label

def wick_rejection(direction, o, h, l, c):
    candle_range = max(h - l, 1e-9)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    if direction == "BUY":
        return upper_wick / candle_range >= 0.45
    else:
        return lower_wick / candle_range >= 0.45

def volatility_trap(closes):
    if VOL_TRAP_ENABLED != "1":
        return False, {"spike_pct": 0.0, "avg_pct": 0.0}

    lb = VOL_TRAP_LOOKBACK
    if len(closes) < lb + 3:
        return False, {"spike_pct": 0.0, "avg_pct": 0.0}

    prev = closes[-2]
    last = closes[-1]
    if prev <= 0:
        return False, {"spike_pct": 0.0, "avg_pct": 0.0}

    spike_pct = abs((last - prev) / prev) * 100

    abs_rets = []
    for i in range(-(lb + 1), -1):
        p0 = closes[i]
        p1 = closes[i + 1]
        if p0 <= 0:
            continue
        abs_rets.append(abs((p1 - p0) / p0) * 100)

    if not abs_rets:
        return False, {"spike_pct": spike_pct, "avg_pct": 0.0}

    avg_pct = sum(abs_rets) / len(abs_rets)
    if avg_pct < 0.0001:
        return False, {"spike_pct": spike_pct, "avg_pct": avg_pct}

    is_trap = (spike_pct >= VOL_TRAP_MIN_PCT) and (spike_pct >= VOL_TRAP_MULT * avg_pct)
    return is_trap, {"spike_pct": spike_pct, "avg_pct": avg_pct}

def compute_confidence_and_risk(direction: str, info: dict):
    score = 0.0

    spread_pct = abs(info["ema_fast"] - info["ema_slow"]) / info["price"] * 100
    score += clamp(spread_pct / 0.15 * 16, 0, 16)

    r = info["rsi"]
    if direction == "BUY":
        score += clamp((r - 50) / 20 * 18, 0, 18)
    else:
        score += clamp((50 - r) / 20 * 18, 0, 18)

    move = abs(info["move_pct"])
    score += clamp((move - MIN_MOVE_PCT) / MIN_MOVE_PCT * 16, 0, 16)

    volx = info["vol_ratio"]
    score += clamp((volx / VOL_SPIKE) * 18, 0, 18)

    if info["break_up"] or info["break_down"]:
        score += 12

    score += clamp(info["candle_strength_score"] / 100 * 12, 0, 12)

    atr_pct_val = info["atr_pct"]
    if MIN_ATR_PCT <= atr_pct_val <= MAX_ATR_PCT:
        score += 10

    confidence = 55 + (score / 100.0) * 37
    confidence = clamp(confidence, 55, 92)

    if confidence >= 80:
        risk_level = "LOW 🟢"
        suggested_risk = 1.5
    elif confidence >= 70:
        risk_level = "MEDIUM 🟡"
        suggested_risk = 1.0
    else:
        risk_level = "HIGH 🔴"
        suggested_risk = 0.5

    return int(round(confidence)), risk_level, suggested_risk

def entry_timing_label(direction, info):
    if info["wick_rejection"]:
        return "SKIP", "Wick rejection"

    if info["atr_pct"] < MIN_ATR_PCT:
        return "SKIP", "Low volatility"

    if info["atr_pct"] > MAX_ATR_PCT:
        return "WAIT 1 CANDLE", "ATR too hot"

    if direction == "BUY":
        if info["rsi"] >= 75:
            return "WAIT 1 CANDLE", "RSI high"
        if info["candle_strength_score"] >= 75 and info["vol_ratio"] >= 2.0:
            return "ENTER NOW", "Strong bullish candle"
        return "WAIT 1 CANDLE", "Need extra confirmation"

    if direction == "SELL":
        if info["rsi"] <= 25:
            return "WAIT 1 CANDLE", "RSI too low"
        if info["candle_strength_score"] >= 75 and info["vol_ratio"] >= 2.0:
            return "ENTER NOW", "Strong bearish candle"
        return "WAIT 1 CANDLE", "Need extra confirmation"

    return "SKIP", "No valid direction"

# =============================
# EARLY SETUP + STRONG MOMENTUM
# =============================
def early_setup_signal(closes, vols):
    """
    تنبيه مبكر قبل الإشارة النهائية.
    لا يعني دخول، فقط Prepare.
    """
    if len(closes) < max(EMA_SLOW + 10, RSI_PERIOD + 10, SIGNAL_WINDOW_MIN + 5):
        return None

    last = closes[-1]
    first = closes[-(SIGNAL_WINDOW_MIN + 1)]
    move_pct = ((last - first) / first) * 100

    ema_fast = ema(closes[-(EMA_FAST * 3):], EMA_FAST)
    ema_slow = ema(closes[-(EMA_SLOW * 3):], EMA_SLOW)
    r = rsi(closes, RSI_PERIOD)

    if ema_fast is None or ema_slow is None or r is None:
        return None

    lookback = 20
    if len(vols) < lookback + 2:
        return None

    avg_vol = sum(vols[-lookback:]) / lookback
    vol_ratio = 1.0 if avg_vol < 0.0001 else (vols[-1] / avg_vol)

    recent_high = max(closes[-(SIGNAL_WINDOW_MIN+1):-1])
    recent_low = min(closes[-(SIGNAL_WINDOW_MIN+1):-1])

    near_high = last >= recent_high * 0.9992
    near_low = last <= recent_low * 1.0008

    # BUY early
    if (
        ema_fast > ema_slow
        and 52 <= r <= 68
        and move_pct >= MIN_MOVE_PCT * 0.55
        and vol_ratio >= 1.20
        and near_high
    ):
        return {
            "direction": "BUY",
            "price": last,
            "move_pct": move_pct,
            "rsi": r,
            "vol_ratio": vol_ratio,
            "reason": "RSI rising + volume building + near breakout"
        }

    # SELL early
    if (
        ema_fast < ema_slow
        and 32 <= r <= 48
        and move_pct <= -(MIN_MOVE_PCT * 0.55)
        and vol_ratio >= 1.20
        and near_low
    ):
        return {
            "direction": "SELL",
            "price": last,
            "move_pct": move_pct,
            "rsi": r,
            "vol_ratio": vol_ratio,
            "reason": "RSI falling + volume building + near breakdown"
        }

    return None

def strong_momentum_label(direction, info, confidence):
    """
    إشارة ذهبية نادرة
    """
    if direction == "BUY":
        if (
            confidence >= STRONG_MOMENTUM_MIN_CONF
            and info["break_up"]
            and info["vol_ratio"] >= 2.5
            and info["candle_strength_score"] >= 80
            and info["atr_pct"] >= 0.15
            and info["rsi"] < 78
        ):
            return True
    else:
        if (
            confidence >= STRONG_MOMENTUM_MIN_CONF
            and info["break_down"]
            and info["vol_ratio"] >= 2.5
            and info["candle_strength_score"] >= 80
            and info["atr_pct"] >= 0.15
            and info["rsi"] > 22
        ):
            return True
    return False

# =============================
# DECISION ENGINE
# =============================
def decision_signal(opens, highs, lows, closes, vols):
    n = SIGNAL_WINDOW_MIN

    if len(closes) < max(EMA_SLOW + 10, RSI_PERIOD + 10, ATR_PERIOD + 5, n + 5):
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

    lookback = 30
    if len(vols) < lookback + 2:
        return None, None
    avg_vol = sum(vols[-lookback:]) / lookback
    vol_ratio = 1.0 if avg_vol < 0.0001 else (vols[-1] / avg_vol)

    recent_high = max(highs[-(n+1):-1])
    recent_low = min(lows[-(n+1):-1])

    break_up = last > recent_high
    break_down = last < recent_low

    trend_up = ema_fast > ema_slow
    trend_down = ema_fast < ema_slow

    atr_val = atr(highs, lows, closes, ATR_PERIOD)
    if atr_val is None:
        return None, None
    atr_pct_val = (atr_val / last) * 100

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

    direction = None
    if buy_ok:
        direction = "BUY"
    elif sell_ok:
        direction = "SELL"

    latest_o = opens[-1]
    latest_h = highs[-1]
    latest_l = lows[-1]
    latest_c = closes[-1]

    candle_score = 0
    candle_label = "Weak"
    wick_flag = False

    if direction:
        candle_score, candle_label = candle_strength(direction, latest_o, latest_h, latest_l, latest_c)
        wick_flag = wick_rejection(direction, latest_o, latest_h, latest_l, latest_c)

    info = {
        "price": last,
        "move_pct": move_pct,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "rsi": r,
        "vol_ratio": vol_ratio,
        "break_up": break_up,
        "break_down": break_down,
        "atr_pct": atr_pct_val,
        "candle_strength_score": candle_score,
        "candle_strength_label": candle_label,
        "wick_rejection": wick_flag,
    }

    return direction, info

# =============================
# MAIN LOOP
# =============================
def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return

    tg_send("🟣 Poly Decision Bot started ✅ (Early + Strong Momentum)")

    last_signal_time = 0
    signals_today = 0
    current_day = datetime.now(timezone.utc).date()

    last_early_time = 0
    last_early_side = None

    while True:
        try:
            today = datetime.now(timezone.utc).date()
            if today != current_day:
                current_day = today
                signals_today = 0

            opens, highs, lows, closes, vols, last_price = fetch_klines_1m(limit=250)
            if closes is None:
                time.sleep(20)
                continue

            is_trap, trap_info = volatility_trap(closes)

            # ===== EARLY SETUP =====
            if EARLY_SETUP_ENABLED == "1":
                early = early_setup_signal(closes, vols)
                if early:
                    now = time.time()
                    if (
                        now - last_early_time >= EARLY_COOLDOWN_SEC
                        or last_early_side != early["direction"]
                    ):
                        tg_send(
                            f"⚠️ EARLY SETUP\n"
                            f"Direction Bias: {early['direction']}\n"
                            f"BTC-USD\n"
                            f"Price: {early['price']:.2f}\n"
                            f"Move({SIGNAL_WINDOW_MIN}m): {early['move_pct']:.2f}%\n"
                            f"RSI: {early['rsi']:.1f}\n"
                            f"Vol Build: x{early['vol_ratio']:.2f}\n"
                            f"Action: PREPARE\n"
                            f"Reason: {early['reason']}"
                        )
                        last_early_time = now
                        last_early_side = early["direction"]

            # ===== MAIN DECISION =====
            direction, info = decision_signal(opens, highs, lows, closes, vols)

            if info:
                print(
                    f"[{datetime.now(timezone.utc)}] price={last_price} "
                    f"move{SIGNAL_WINDOW_MIN}m={info['move_pct']:.3f}% "
                    f"rsi={info['rsi']:.1f} volx={info['vol_ratio']:.2f} "
                    f"atr={info['atr_pct']:.3f}% "
                    f"candle={info['candle_strength_label']} "
                    f"wick={info['wick_rejection']} "
                    f"trap={is_trap} spike={trap_info['spike_pct']:.3f}% avg={trap_info['avg_pct']:.3f}%"
                )
            else:
                print(
                    f"[{datetime.now(timezone.utc)}] price={last_price} waiting "
                    f"trap={is_trap} spike={trap_info['spike_pct']:.3f}% avg={trap_info['avg_pct']:.3f}%"
                )
                time.sleep(SAMPLE_INTERVAL_SEC)
                continue

            if signals_today >= MAX_SIGNALS_PER_DAY:
                time.sleep(SAMPLE_INTERVAL_SEC)
                continue

            if direction:
                confidence, risk_level, suggested_risk = compute_confidence_and_risk(direction, info)
                timing, timing_reason = entry_timing_label(direction, info)
                is_strong = strong_momentum_label(direction, info, confidence)

                if confidence < MIN_CONF_SEND:
                    time.sleep(SAMPLE_INTERVAL_SEC)
                    continue

                if is_trap:
                    time.sleep(SAMPLE_INTERVAL_SEC)
                    continue

                if timing == "SKIP":
                    time.sleep(SAMPLE_INTERVAL_SEC)
                    continue

                now = time.time()
                if now - last_signal_time >= COOLDOWN_SEC:
                    emoji = "📈" if direction == "BUY" else "📉"

                    header = f"{emoji} {direction} (Decision)"
                    if is_strong:
                        header = f"🚀 STRONG MOMENTUM {direction}"

                    msg = (
                        f"{header}\n"
                        f"BTC-USD\n"
                        f"Price: {info['price']:.2f}\n"
                        f"Move({SIGNAL_WINDOW_MIN}m): {info['move_pct']:.2f}%\n"
                        f"RSI({RSI_PERIOD}): {info['rsi']:.1f}\n"
                        f"EMA{EMA_FAST}/{EMA_SLOW}: {info['ema_fast']:.2f} / {info['ema_slow']:.2f}\n"
                        f"Vol Spike: x{info['vol_ratio']:.2f}\n"
                        f"Breakout: {'UP ✅' if info['break_up'] else ('DOWN ✅' if info['break_down'] else 'NO')}\n"
                        f"ATR%: {info['atr_pct']:.2f}%\n"
                        f"Candle Strength: {info['candle_strength_label']} ({info['candle_strength_score']}/100)\n"
                        f"Wick Rejection: {'YES ⚠️' if info['wick_rejection'] else 'NO ✅'}\n"
                        f"\n"
                        f"Confidence: {confidence}%\n"
                        f"Risk Level: {risk_level}\n"
                        f"Suggested Risk: {suggested_risk}%\n"
                        f"Timing: {timing}\n"
                        f"Reason: {timing_reason}\n"
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
