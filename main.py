import os
import time
import requests
from datetime import datetime, timezone

# =============================
# CONFIG
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

EARLY_SETUP_ENABLED = os.getenv("EARLY_SETUP_ENABLED", "1").strip()
EARLY_COOLDOWN_SEC = int(os.getenv("EARLY_COOLDOWN_SEC", "900"))
STRONG_MOMENTUM_MIN_CONF = int(os.getenv("STRONG_MOMENTUM_MIN_CONF", "88"))

# NEW: Trend Filter (1H)
TREND_FILTER_ENABLED = os.getenv("TREND_FILTER_ENABLED", "1").strip()
TREND_FAST_1H = int(os.getenv("TREND_FAST_1H", "50"))
TREND_SLOW_1H = int(os.getenv("TREND_SLOW_1H", "200"))

# NEW: Momentum Acceleration
ACCEL_ENABLED = os.getenv("ACCEL_ENABLED", "1").strip()
ACCEL_MIN_SCORE = float(os.getenv("ACCEL_MIN_SCORE", "0.55"))

# NEW: Orderbook
ORDERBOOK_ENABLED = os.getenv("ORDERBOOK_ENABLED", "1").strip()
ORDERBOOK_IMBALANCE_MIN = float(os.getenv("ORDERBOOK_IMBALANCE_MIN", "0.08"))
ORDERBOOK_LEVELS = int(os.getenv("ORDERBOOK_LEVELS", "20"))

# NEW: Liquidity Sweep
SWEEP_ENABLED = os.getenv("SWEEP_ENABLED", "1").strip()
SWEEP_LOOKBACK = int(os.getenv("SWEEP_LOOKBACK", "20"))

DATA_SOURCE = os.getenv("DATA_SOURCE", "AUTO").strip().upper()

session = requests.Session()
session.headers.update({
    "User-Agent": "poly-decision-bot/6.0",
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
def clamp(x, lo, hi):
    return max(lo, min(hi, x))

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
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs)

# =============================
# DATA PROVIDERS
# =============================
def fetch_bitstamp(limit=250, step=60):
    url = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
    params = {"step": step, "limit": limit}
    r = session.get(url, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()["data"]["ohlc"]
    opens = [float(x["open"]) for x in data]
    highs = [float(x["high"]) for x in data]
    lows = [float(x["low"]) for x in data]
    closes = [float(x["close"]) for x in data]
    vols = [float(x["volume"]) for x in data]
    return opens, highs, lows, closes, vols, closes[-1]

def fetch_coinbase(limit=250, granularity=60):
    url = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
    params = {"granularity": granularity}
    r = session.get(url, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()
    data = data[:limit]
    data = list(reversed(data))  # oldest -> newest
    opens = [float(x[3]) for x in data]
    highs = [float(x[2]) for x in data]
    lows = [float(x[1]) for x in data]
    closes = [float(x[4]) for x in data]
    vols = [float(x[5]) for x in data]
    return opens, highs, lows, closes, vols, closes[-1]

def fetch_kraken(limit=250, interval=1):
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": "XBTUSD", "interval": interval}
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

def fetch_1m(limit=250):
    providers = []
    if DATA_SOURCE == "BITSTAMP":
        providers = [lambda: fetch_bitstamp(limit=limit, step=60)]
    elif DATA_SOURCE == "COINBASE":
        providers = [lambda: fetch_coinbase(limit=limit, granularity=60)]
    elif DATA_SOURCE == "KRAKEN":
        providers = [lambda: fetch_kraken(limit=limit, interval=1)]
    else:
        providers = [
            lambda: fetch_bitstamp(limit=limit, step=60),
            lambda: fetch_coinbase(limit=limit, granularity=60),
            lambda: fetch_kraken(limit=limit, interval=1),
        ]

    last_err = None
    for fn in providers:
        try:
            return fn()
        except Exception as e:
            last_err = e
            print("fetch_1m provider error:", e)

    print("All 1m providers failed:", last_err)
    return None, None, None, None, None, None

def fetch_1h(limit=250):
    providers = []
    if DATA_SOURCE == "BITSTAMP":
        providers = [lambda: fetch_bitstamp(limit=limit, step=3600)]
    elif DATA_SOURCE == "COINBASE":
        providers = [lambda: fetch_coinbase(limit=limit, granularity=3600)]
    elif DATA_SOURCE == "KRAKEN":
        providers = [lambda: fetch_kraken(limit=limit, interval=60)]
    else:
        providers = [
            lambda: fetch_bitstamp(limit=limit, step=3600),
            lambda: fetch_coinbase(limit=limit, granularity=3600),
            lambda: fetch_kraken(limit=limit, interval=60),
        ]

    last_err = None
    for fn in providers:
        try:
            return fn()
        except Exception as e:
            last_err = e
            print("fetch_1h provider error:", e)

    print("All 1h providers failed:", last_err)
    return None, None, None, None, None, None

# =============================
# ORDERBOOK
# =============================
def fetch_orderbook_bitstamp():
    url = "https://www.bitstamp.net/api/v2/order_book/btcusd/"
    r = session.get(url, timeout=12)
    r.raise_for_status()
    j = r.json()
    bids = j.get("bids", [])[:ORDERBOOK_LEVELS]
    asks = j.get("asks", [])[:ORDERBOOK_LEVELS]
    bid_vol = sum(float(x[1]) for x in bids)
    ask_vol = sum(float(x[1]) for x in asks)
    return bid_vol, ask_vol

def fetch_orderbook_coinbase():
    url = "https://api.exchange.coinbase.com/products/BTC-USD/book"
    params = {"level": 2}
    r = session.get(url, params=params, timeout=12)
    r.raise_for_status()
    j = r.json()
    bids = j.get("bids", [])[:ORDERBOOK_LEVELS]
    asks = j.get("asks", [])[:ORDERBOOK_LEVELS]
    bid_vol = sum(float(x[1]) for x in bids)
    ask_vol = sum(float(x[1]) for x in asks)
    return bid_vol, ask_vol

def fetch_orderbook_kraken():
    url = "https://api.kraken.com/0/public/Depth"
    params = {"pair": "XBTUSD", "count": ORDERBOOK_LEVELS}
    r = session.get(url, params=params, timeout=12)
    r.raise_for_status()
    j = r.json()
    key = next(iter(j["result"].keys()))
    bids = j["result"][key].get("bids", [])
    asks = j["result"][key].get("asks", [])
    bid_vol = sum(float(x[1]) for x in bids)
    ask_vol = sum(float(x[1]) for x in asks)
    return bid_vol, ask_vol

def fetch_orderbook_imbalance():
    if ORDERBOOK_ENABLED != "1":
        return 0.0, "OFF"

    providers = []
    if DATA_SOURCE == "BITSTAMP":
        providers = [fetch_orderbook_bitstamp]
    elif DATA_SOURCE == "COINBASE":
        providers = [fetch_orderbook_coinbase]
    elif DATA_SOURCE == "KRAKEN":
        providers = [fetch_orderbook_kraken]
    else:
        providers = [fetch_orderbook_bitstamp, fetch_orderbook_coinbase, fetch_orderbook_kraken]

    last_err = None
    for fn in providers:
        try:
            bid_vol, ask_vol = fn()
            total = bid_vol + ask_vol
            if total <= 0:
                return 0.0, "Neutral"
            imbalance = (bid_vol - ask_vol) / total
            label = "Bullish" if imbalance > 0 else "Bearish" if imbalance < 0 else "Neutral"
            return imbalance, label
        except Exception as e:
            last_err = e
            print("orderbook provider error:", e)

    print("All orderbook providers failed:", last_err)
    return 0.0, "Neutral"

# =============================
# CANDLE / PRICE STRUCTURE
# =============================
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

def liquidity_sweep_signal(direction, highs, lows, closes):
    if SWEEP_ENABLED != "1":
        return False, "OFF"

    lb = SWEEP_LOOKBACK
    if len(closes) < lb + 4:
        return False, "No data"

    recent_high = max(highs[-(lb+3):-3])
    recent_low = min(lows[-(lb+3):-3])

    # نفحص آخر 3 شموع فقط
    last_close = closes[-1]
    last_high = highs[-1]
    last_low = lows[-1]

    if direction == "BUY":
        # sweep downside then reclaim
        if last_low < recent_low and last_close > recent_low:
            return True, "Downside liquidity sweep reclaimed"
        return False, "No bullish sweep"

    if direction == "SELL":
        # sweep upside then reject
        if last_high > recent_high and last_close < recent_high:
            return True, "Upside liquidity sweep rejected"
        return False, "No bearish sweep"

    return False, "No direction"

def trend_filter_1h(direction, closes_1h):
    if TREND_FILTER_ENABLED != "1":
        return True, {"fast": 0.0, "slow": 0.0, "label": "OFF"}

    fast = ema(closes_1h[-(TREND_FAST_1H * 3):], TREND_FAST_1H)
    slow = ema(closes_1h[-(TREND_SLOW_1H * 2 + 20):], TREND_SLOW_1H)
    if fast is None or slow is None:
        return False, {"fast": 0.0, "slow": 0.0, "label": "No data"}

    if direction == "BUY":
        ok = fast > slow
        label = "Bullish" if ok else "Against trend"
    else:
        ok = fast < slow
        label = "Bearish" if ok else "Against trend"

    return ok, {"fast": fast, "slow": slow, "label": label}

def momentum_acceleration(direction, closes, ema_fast_now, ema_slow_now, rsi_now):
    if ACCEL_ENABLED != "1":
        return 0.0, "OFF"

    if len(closes) < 12:
        return 0.0, "No data"

    # returns
    r1 = (closes[-1] - closes[-2]) / closes[-2]
    r2 = (closes[-2] - closes[-3]) / closes[-3]
    r3 = (closes[-3] - closes[-4]) / closes[-4]

    recent_mean = (r1 + r2 + r3) / 3.0
    older_mean = (
        ((closes[-4] - closes[-5]) / closes[-5]) +
        ((closes[-5] - closes[-6]) / closes[-6]) +
        ((closes[-6] - closes[-7]) / closes[-7])
    ) / 3.0

    accel_raw = recent_mean - older_mean

    # RSI slope
    rsi_old = rsi(closes[:-3], RSI_PERIOD)
    rsi_delta = 0.0 if rsi_old is None else (rsi_now - rsi_old)

    # ema spread
    spread_pct = abs(ema_fast_now - ema_slow_now) / closes[-1] * 100

    score = 0.0

    if direction == "BUY":
        if accel_raw > 0:
            score += 0.25
        if recent_mean > 0:
            score += 0.20
        if rsi_delta > 1.5:
            score += 0.20
    else:
        if accel_raw < 0:
            score += 0.25
        if recent_mean < 0:
            score += 0.20
        if rsi_delta < -1.5:
            score += 0.20

    if spread_pct >= 0.08:
        score += 0.15
    if spread_pct >= 0.15:
        score += 0.10

    score = clamp(score, 0.0, 1.0)

    if score >= 0.80:
        label = "Very Strong"
    elif score >= 0.60:
        label = "Strong"
    elif score >= 0.40:
        label = "Building"
    else:
        label = "Weak"

    return score, label

# =============================
# EARLY / STRONG
# =============================
def early_setup_signal(closes, vols):
    if EARLY_SETUP_ENABLED != "1":
        return None

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
    if direction == "BUY":
        if (
            confidence >= STRONG_MOMENTUM_MIN_CONF
            and info["break_up"]
            and info["vol_ratio"] >= 2.5
            and info["candle_strength_score"] >= 80
            and info["atr_pct"] >= 0.15
            and info["rsi"] < 78
            and info["accel_score"] >= 0.70
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
            and info["accel_score"] >= 0.70
        ):
            return True
    return False

# =============================
# DECISION ENGINE
# =============================
def compute_confidence_and_risk(direction: str, info: dict):
    score = 0.0

    spread_pct = abs(info["ema_fast"] - info["ema_slow"]) / info["price"] * 100
    score += clamp(spread_pct / 0.15 * 13, 0, 13)

    r = info["rsi"]
    if direction == "BUY":
        score += clamp((r - 50) / 20 * 12, 0, 12)
    else:
        score += clamp((50 - r) / 20 * 12, 0, 12)

    move = abs(info["move_pct"])
    score += clamp((move - MIN_MOVE_PCT) / MIN_MOVE_PCT * 12, 0, 12)

    score += clamp((info["vol_ratio"] / VOL_SPIKE) * 12, 0, 12)

    if info["break_up"] or info["break_down"]:
        score += 8

    score += clamp(info["candle_strength_score"] / 100 * 10, 0, 10)

    if MIN_ATR_PCT <= info["atr_pct"] <= MAX_ATR_PCT:
        score += 8

    # New additions
    if info["trend_ok"]:
        score += 8

    if direction == "BUY" and info["orderbook_imbalance"] >= ORDERBOOK_IMBALANCE_MIN:
        score += 6
    elif direction == "SELL" and info["orderbook_imbalance"] <= -ORDERBOOK_IMBALANCE_MIN:
        score += 6

    if info["sweep_ok"]:
        score += 5

    score += clamp(info["accel_score"] * 12, 0, 12)

    confidence = 55 + (score / 100.0) * 37
    confidence = clamp(confidence, 55, 95)

    if confidence >= 82:
        risk_level = "LOW 🟢"
        suggested_risk = 1.5
    elif confidence >= 72:
        risk_level = "MEDIUM 🟡"
        suggested_risk = 1.0
    else:
        risk_level = "HIGH 🔴"
        suggested_risk = 0.5

    return int(round(confidence)), risk_level, suggested_risk

def entry_timing_label(direction, info):
    if info["wick_rejection"]:
        return "SKIP", "Wick rejection"

    if not info["trend_ok"]:
        return "SKIP", "Against 1H trend"

    if info["atr_pct"] < MIN_ATR_PCT:
        return "SKIP", "Low volatility"

    if info["atr_pct"] > MAX_ATR_PCT:
        return "WAIT 1 CANDLE", "ATR too hot"

    if direction == "BUY":
        if info["rsi"] >= 75:
            return "WAIT 1 CANDLE", "RSI high"
        if info["orderbook_imbalance"] < 0:
            return "WAIT 1 CANDLE", "Orderbook not supportive"
        if info["accel_score"] >= 0.70 and info["candle_strength_score"] >= 75:
            return "ENTER NOW", "Acceleration + strong candle"
        return "WAIT 1 CANDLE", "Need extra confirmation"

    if direction == "SELL":
        if info["rsi"] <= 25:
            return "WAIT 1 CANDLE", "RSI too low"
        if info["orderbook_imbalance"] > 0:
            return "WAIT 1 CANDLE", "Orderbook not supportive"
        if info["accel_score"] >= 0.70 and info["candle_strength_score"] >= 75:
            return "ENTER NOW", "Acceleration + strong candle"
        return "WAIT 1 CANDLE", "Need extra confirmation"

    return "SKIP", "No valid direction"

def decision_signal(opens, highs, lows, closes, vols, closes_1h, orderbook_imbalance, orderbook_label):
    n = SIGNAL_WINDOW_MIN

    if len(closes) < max(EMA_SLOW + 10, RSI_PERIOD + 10, ATR_PERIOD + 5, n + 5):
        return None, None

    last = closes[-1]
    first = closes[-(n + 1)]
    move_pct = ((last - first) / first) * 100

    ema_fast_now = ema(closes[-(EMA_FAST * 3):], EMA_FAST)
    ema_slow_now = ema(closes[-(EMA_SLOW * 3):], EMA_SLOW)
    if ema_fast_now is None or ema_slow_now is None:
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

    trend_up = ema_fast_now > ema_slow_now
    trend_down = ema_fast_now < ema_slow_now

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
    trend_ok = False
    trend_info = {"fast": 0.0, "slow": 0.0, "label": "OFF"}
    accel_score = 0.0
    accel_label = "OFF"
    sweep_ok = False
    sweep_reason = "OFF"

    if direction:
        candle_score, candle_label = candle_strength(direction, latest_o, latest_h, latest_l, latest_c)
        wick_flag = wick_rejection(direction, latest_o, latest_h, latest_l, latest_c)
        trend_ok, trend_info = trend_filter_1h(direction, closes_1h)
        accel_score, accel_label = momentum_acceleration(direction, closes, ema_fast_now, ema_slow_now, r)
        sweep_ok, sweep_reason = liquidity_sweep_signal(direction, highs, lows, closes)

    info = {
        "price": last,
        "move_pct": move_pct,
        "ema_fast": ema_fast_now,
        "ema_slow": ema_slow_now,
        "rsi": r,
        "vol_ratio": vol_ratio,
        "break_up": break_up,
        "break_down": break_down,
        "atr_pct": atr_pct_val,
        "candle_strength_score": candle_score,
        "candle_strength_label": candle_label,
        "wick_rejection": wick_flag,
        "trend_ok": trend_ok,
        "trend_label": trend_info["label"],
        "trend_fast_1h": trend_info["fast"],
        "trend_slow_1h": trend_info["slow"],
        "orderbook_imbalance": orderbook_imbalance,
        "orderbook_label": orderbook_label,
        "accel_score": accel_score,
        "accel_label": accel_label,
        "sweep_ok": sweep_ok,
        "sweep_reason": sweep_reason,
    }

    return direction, info

# =============================
# MAIN LOOP
# =============================
def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return

    tg_send("🟣 Poly Decision Bot started ✅ (Trend + OB + Sweep + Accel)")

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

            opens, highs, lows, closes, vols, last_price = fetch_1m(limit=250)
            if closes is None:
                time.sleep(20)
                continue

            _, _, _, _, closes_1h, _ = fetch_1h(limit=max(TREND_SLOW_1H + 20, 220))
            if closes_1h is None:
                time.sleep(20)
                continue

            orderbook_imbalance, orderbook_label = fetch_orderbook_imbalance()
            is_trap, trap_info = volatility_trap(closes)

            # EARLY SETUP
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

            direction, info = decision_signal(
                opens, highs, lows, closes, vols,
                closes_1h,
                orderbook_imbalance, orderbook_label
            )

            if info:
                print(
                    f"[{datetime.now(timezone.utc)}] price={last_price} "
                    f"move{SIGNAL_WINDOW_MIN}m={info['move_pct']:.3f}% "
                    f"rsi={info['rsi']:.1f} volx={info['vol_ratio']:.2f} "
                    f"atr={info['atr_pct']:.3f}% "
                    f"candle={info['candle_strength_label']} "
                    f"wick={info['wick_rejection']} "
                    f"trend1h={info['trend_label']} "
                    f"ob={info['orderbook_imbalance']:.3f} "
                    f"accel={info['accel_score']:.2f} "
                    f"sweep={info['sweep_ok']} "
                    f"trap={is_trap} spike={trap_info['spike_pct']:.3f}% avg={trap_info['avg_pct']:.3f}%"
                )
            else:
                print(
                    f"[{datetime.now(timezone.utc)}] price={last_price} waiting "
                    f"ob={orderbook_imbalance:.3f} trap={is_trap} "
                    f"spike={trap_info['spike_pct']:.3f}% avg={trap_info['avg_pct']:.3f}%"
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
                        f"1H Trend: {info['trend_label']}\n"
                        f"Orderbook: {info['orderbook_label']} ({info['orderbook_imbalance']:.2f})\n"
                        f"Acceleration: {info['accel_label']} ({info['accel_score']:.2f})\n"
                        f"Liquidity Sweep: {'YES ✅' if info['sweep_ok'] else 'NO'}\n"
                        f"Sweep Note: {info['sweep_reason']}\n"
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
