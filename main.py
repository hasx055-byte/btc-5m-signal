from __future__ import annotations

import os
import time
import requests
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


# =========================================================
# CONFIG
# =========================================================

@dataclass(frozen=True)
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
    CHAT_ID: str = os.getenv("CHAT_ID", "").strip()

    SYMBOL: str = os.getenv("SYMBOL", "BTCUSDT").strip().upper()

    # polling
    SAMPLE_INTERVAL_SEC: int = int(os.getenv("SAMPLE_INTERVAL_SEC", "10"))

    # 15m logic
    SIGNAL_WINDOW_MIN: int = int(os.getenv("SIGNAL_WINDOW_MIN", "15"))

    # thresholds
    MIN_MOVE_PCT: float = float(os.getenv("MIN_MOVE_PCT", "0.18"))
    VOL_SPIKE: float = float(os.getenv("VOL_SPIKE", "1.35"))
    MIN_CONF_SEND: int = int(os.getenv("MIN_CONF_SEND", "68"))
    HIGH_CONF: int = int(os.getenv("HIGH_CONF", "82"))

    EMA_FAST: int = int(os.getenv("EMA_FAST", "20"))
    EMA_SLOW: int = int(os.getenv("EMA_SLOW", "50"))
    RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", "14"))
    ATR_PERIOD: int = int(os.getenv("ATR_PERIOD", "14"))

    DRIFT_1M_PCT: float = float(os.getenv("DRIFT_1M_PCT", "0.08"))
    DRIFT_3M_PCT: float = float(os.getenv("DRIFT_3M_PCT", "0.15"))
    ACCEL_MIN_SCORE: float = float(os.getenv("ACCEL_MIN_SCORE", "0.35"))
    PRESSURE_MIN: float = float(os.getenv("PRESSURE_MIN", "0.08"))

    MIN_ATR_PCT: float = float(os.getenv("MIN_ATR_PCT", "0.10"))
    MAX_ATR_PCT: float = float(os.getenv("MAX_ATR_PCT", "0.90"))

    SIGNAL_COOLDOWN_SEC: int = int(os.getenv("SIGNAL_COOLDOWN_SEC", "900"))
    EARLY_COOLDOWN_SEC: int = int(os.getenv("EARLY_COOLDOWN_SEC", "300"))
    MAX_SIGNALS_PER_DAY: int = int(os.getenv("MAX_SIGNALS_PER_DAY", "12"))

    ORDERBOOK_ENABLED: bool = os.getenv("ORDERBOOK_ENABLED", "1").strip() == "1"

    BINANCE_BASE: str = os.getenv("BINANCE_BASE", "https://api.binance.com")


SETTINGS = Settings()


# =========================================================
# TELEGRAM
# =========================================================

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self.session = requests.Session()

    def send(self, text: str) -> None:
        if not self.token or not self.chat_id:
            print("Missing BOT_TOKEN or CHAT_ID")
            print(text)
            return

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            r = self.session.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if r.status_code != 200:
                print("Telegram failed:", r.status_code, r.text[:300])
        except Exception as e:
            print("Telegram error:", e)


# =========================================================
# HELPERS
# =========================================================

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100.0


def ema(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
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


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> Optional[float]:
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


def candle_strength(direction: str, o: float, h: float, l: float, c: float) -> tuple[int, str]:
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

    score = int(clamp(score, 0, 100))
    if score >= 80:
        label = "Very Strong"
    elif score >= 65:
        label = "Strong"
    elif score >= 50:
        label = "Medium"
    else:
        label = "Weak"

    return score, label


def wick_rejection(direction: str, o: float, h: float, l: float, c: float) -> bool:
    candle_range = max(h - l, 1e-9)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    if direction == "BUY":
        return upper_wick / candle_range >= 0.45
    return lower_wick / candle_range >= 0.45


# =========================================================
# MARKET DATA
# =========================================================

class MultiSourceData:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "btc-15m-flow-bot/1.1"})

    # ---------- Binance ----------
    def fetch_binance_1m_klines(self, symbol: str, limit: int = 250):
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol, "interval": "1m", "limit": limit}
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        opens = [float(x[1]) for x in data]
        highs = [float(x[2]) for x in data]
        lows = [float(x[3]) for x in data]
        closes = [float(x[4]) for x in data]
        vols = [float(x[5]) for x in data]
        return opens, highs, lows, closes, vols, "BINANCE"

    def fetch_binance_orderbook(self, symbol: str, levels: int = 20):
        url = "https://api.binance.com/api/v3/depth"
        params = {"symbol": symbol, "limit": levels}
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        j = r.json()

        bids = j.get("bids", [])[:levels]
        asks = j.get("asks", [])[:levels]

        bid_vol = sum(float(qty) for _, qty in bids)
        ask_vol = sum(float(qty) for _, qty in asks)

        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0, "Neutral"

        imbalance = (bid_vol - ask_vol) / total
        if imbalance > SETTINGS.PRESSURE_MIN:
            label = "Bullish"
        elif imbalance < -SETTINGS.PRESSURE_MIN:
            label = "Bearish"
        else:
            label = "Neutral"

        return imbalance, label

    # ---------- Coinbase ----------
    def fetch_coinbase_1m_klines(self, limit: int = 250):
        url = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
        params = {"granularity": 60}
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        data = data[:limit]
        data = list(reversed(data))  # oldest -> newest

        opens = [float(x[3]) for x in data]
        highs = [float(x[2]) for x in data]
        lows = [float(x[1]) for x in data]
        closes = [float(x[4]) for x in data]
        vols = [float(x[5]) for x in data]
        return opens, highs, lows, closes, vols, "COINBASE"

    # ---------- Kraken ----------
    def fetch_kraken_1m_klines(self, limit: int = 250):
        url = "https://api.kraken.com/0/public/OHLC"
        params = {"pair": "XBTUSD", "interval": 1}
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        j = r.json()

        key = next(iter(set(j["result"].keys()) - {"last"}))
        data = j["result"][key][-limit:]

        opens = [float(x[1]) for x in data]
        highs = [float(x[2]) for x in data]
        lows = [float(x[3]) for x in data]
        closes = [float(x[4]) for x in data]
        vols = [float(x[6]) for x in data]
        return opens, highs, lows, closes, vols, "KRAKEN"

    # ---------- Bitstamp ----------
    def fetch_bitstamp_1m_klines(self, limit: int = 250):
        url = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
        params = {"step": 60, "limit": limit}
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()["data"]["ohlc"]

        opens = [float(x["open"]) for x in data]
        highs = [float(x["high"]) for x in data]
        lows = [float(x["low"]) for x in data]
        closes = [float(x["close"]) for x in data]
        vols = [float(x["volume"]) for x in data]
        return opens, highs, lows, closes, vols, "BITSTAMP"

    # ---------- Unified fetch ----------
    def fetch_1m_klines(self, symbol: str, limit: int = 250):
        providers = [
            lambda: self.fetch_binance_1m_klines(symbol, limit),
            lambda: self.fetch_coinbase_1m_klines(limit),
            lambda: self.fetch_kraken_1m_klines(limit),
            lambda: self.fetch_bitstamp_1m_klines(limit),
        ]

        last_err = None
        for fn in providers:
            try:
                return fn()
            except Exception as e:
                last_err = e
                print("Provider failed:", e)

        raise RuntimeError(f"All market data providers failed: {last_err}")

    def fetch_orderbook_pressure(self, symbol: str, levels: int = 20):
        if not SETTINGS.ORDERBOOK_ENABLED:
            return 0.0, "OFF"

        try:
            return self.fetch_binance_orderbook(symbol, levels)
        except Exception as e:
            print("Orderbook disabled/fallback due to error:", e)
            return 0.0, "OFF"

# =========================================================
# FLOW FEATURES
# =========================================================

def compute_drift_features(closes: list[float]) -> dict:
    if len(closes) < 5:
        return {"drift_1m": 0.0, "drift_3m": 0.0, "steam": False}

    drift_1m = pct_change(closes[-2], closes[-1])
    drift_3m = pct_change(closes[-4], closes[-1])
    steam = abs(drift_1m) >= SETTINGS.DRIFT_1M_PCT or abs(drift_3m) >= SETTINGS.DRIFT_3M_PCT

    return {"drift_1m": drift_1m, "drift_3m": drift_3m, "steam": steam}


def compute_volume_expansion(vols: list[float]) -> dict:
    if len(vols) < 35:
        return {"vol_ratio": 1.0, "expanding": False}

    avg_vol = sum(vols[-31:-1]) / 30.0
    vol_ratio = vols[-1] / avg_vol if avg_vol > 1e-9 else 1.0

    return {"vol_ratio": vol_ratio, "expanding": vol_ratio >= SETTINGS.VOL_SPIKE}


def compute_buy_acceleration(closes: list[float], ema_fast_now: float, ema_slow_now: float, rsi_now: float) -> tuple[float, str]:
    if len(closes) < 12:
        return 0.0, "No data"

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
    rsi_old = rsi(closes[:-3], SETTINGS.RSI_PERIOD)
    rsi_delta = 0.0 if rsi_old is None else (rsi_now - rsi_old)

    spread_pct = abs(ema_fast_now - ema_slow_now) / closes[-1] * 100.0

    score = 0.0
    if accel_raw > 0:
        score += 0.25
    if recent_mean > 0:
        score += 0.20
    if rsi_delta > 1.5:
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


def compute_sell_acceleration(closes: list[float], ema_fast_now: float, ema_slow_now: float, rsi_now: float) -> tuple[float, str]:
    if len(closes) < 12:
        return 0.0, "No data"

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
    rsi_old = rsi(closes[:-3], SETTINGS.RSI_PERIOD)
    rsi_delta = 0.0 if rsi_old is None else (rsi_now - rsi_old)

    spread_pct = abs(ema_fast_now - ema_slow_now) / closes[-1] * 100.0

    score = 0.0
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


def retest_reclaim_buy(highs: list[float], closes: list[float], window: int) -> tuple[bool, str]:
    if len(closes) < window + 5:
        return False, "No data"

    recent_high = max(highs[-(window + 1):-1])
    last_close = closes[-1]
    prev_close = closes[-2]

    if prev_close <= recent_high and last_close > recent_high:
        return True, "Breakout reclaim"
    if last_close >= recent_high * 0.999:
        return True, "Near reclaim"
    return False, "No reclaim"


def retest_reclaim_sell(lows: list[float], closes: list[float], window: int) -> tuple[bool, str]:
    if len(closes) < window + 5:
        return False, "No data"

    recent_low = min(lows[-(window + 1):-1])
    last_close = closes[-1]
    prev_close = closes[-2]

    if prev_close >= recent_low and last_close < recent_low:
        return True, "Breakdown reclaim"
    if last_close <= recent_low * 1.001:
        return True, "Near breakdown"
    return False, "No reclaim"


# =========================================================
# DECISION ENGINE
# =========================================================

def build_buy_signal(opens, highs, lows, closes, vols, orderbook_imbalance, orderbook_label):
    n = SETTINGS.SIGNAL_WINDOW_MIN
    if len(closes) < max(SETTINGS.EMA_SLOW + 10, SETTINGS.RSI_PERIOD + 10, SETTINGS.ATR_PERIOD + 5, n + 5):
        return None

    price = closes[-1]
    first = closes[-(n + 1)]
    move_pct = pct_change(first, price)

    ema_fast_now = ema(closes[-(SETTINGS.EMA_FAST * 3):], SETTINGS.EMA_FAST)
    ema_slow_now = ema(closes[-(SETTINGS.EMA_SLOW * 3):], SETTINGS.EMA_SLOW)
    if ema_fast_now is None or ema_slow_now is None:
        return None

    r = rsi(closes, SETTINGS.RSI_PERIOD)
    if r is None:
        return None

    atr_val = atr(highs, lows, closes, SETTINGS.ATR_PERIOD)
    if atr_val is None:
        return None
    atr_pct = (atr_val / price) * 100.0

    drift = compute_drift_features(closes)
    volume_info = compute_volume_expansion(vols)
    accel_score, accel_he label = compute_buy_acceleration(closes, ema_fast_now, ema_slow_now, r)
    reclaim_ok, reclaim_note = retest_reclaim_buy(highs, closes, n)

    trend_up = ema_fast_now > ema_slow_now
    recent_high = max(highs[-(n + 1):-1])
    break_up = price > recent_high

    latest_o, latest_h, latest_l, latest_c = opens[-1], highs[-1], lows[-1], closes[-1]
    candle_score, candle_label = candle_strength("BUY", latest_o, latest_h, latest_l, latest_c)
    wick_flag = wick_rejection("BUY", latest_o, latest_h, latest_l, latest_c)

    flow_stack = []
    score = 0.0

    if trend_up:
        flow_stack.append("Trend up")
        score += 12
    if move_pct >= SETTINGS.MIN_MOVE_PCT:
        flow_stack.append("15m move")
        score += 12
    if volume_info["expanding"]:
        flow_stack.append("Volume expansion")
        score += 14
    if drift["steam"]:
        flow_stack.append("Steam move")
        score += 10
    if drift["drift_1m"] > 0:
        flow_stack.append("1m drift")
        score += 8
    if drift["drift_3m"] > 0:
        flow_stack.append("3m drift")
        score += 8
    if accel_score >= SETTINGS.ACCEL_MIN_SCORE:
        flow_stack.append("Acceleration")
        score += accel_score * 16
    if reclaim_ok:
        flow_stack.append("Reclaim")
        score += 10
    if break_up:
        flow_stack.append("Breakout")
        score += 8
    if candle_score >= 65:
        flow_stack.append("Strong candle")
        score += 8
    if SETTINGS.MIN_ATR_PCT <= atr_pct <= SETTINGS.MAX_ATR_PCT:
        flow_stack.append("ATR valid")
        score += 6
    if orderbook_imbalance >= SETTINGS.PRESSURE_MIN:
        flow_stack.append("Buy pressure")
        score += 6

    if wick_flag:
        score -= 10
    if r >= 78:
        score -= 8

    confidence = int(round(clamp(48 + score, 0, 95)))
    if confidence < SETTINGS.MIN_CONF_SEND:
        return None

    if confidence >= SETTINGS.HIGH_CONF:
        level = "HIGH CONVICTION"
        timing = "ENTER NOW" if not wick_flag and accel_score >= 0.60 else "WAIT 1 CANDLE"
    elif confidence >= 75:
        level = "ACTIONABLE+"
        timing = "PREPARE / ENTER ON HOLD"
    else:
        level = "ACTIONABLE"
        timing = "PREPARE"

    return {
        "direction": "BUY",
        "level": level,
        "timing": timing,
        "price": price,
        "move_pct": move_pct,
        "rsi": r,
        "ema_fast": ema_fast_now,
        "ema_slow": ema_slow_now,
        "atr_pct": atr_pct,
        "vol_ratio": volume_info["vol_ratio"],
        "drift_1m": drift["drift_1m"],
        "drift_3m": drift["drift_3m"],
        "steam": drift["steam"],
        "accel_score": accel_score,
        "accel_label": accel_label,
        "candle_score": candle_score,
        "candle_label": candle_label,
        "wick_rejection": wick_flag,
        "breakout": break_up,
        "reclaim_note": reclaim_note,
        "orderbook_imbalance": orderbook_imbalance,
        "orderbook_label": orderbook_label,
        "confidence": confidence,
        "flow_stack": flow_stack,
    }


def build_sell_signal(opens, highs, lows, closes, vols, orderbook_imbalance, orderbook_label):
    n = SETTINGS.SIGNAL_WINDOW_MIN
    if len(closes) < max(SETTINGS.EMA_SLOW + 10, SETTINGS.RSI_PERIOD + 10, SETTINGS.ATR_PERIOD + 5, n + 5):
        return None

    price = closes[-1]
    first = closes[-(n + 1)]
    move_pct = pct_change(first, price)

    ema_fast_now = ema(closes[-(SETTINGS.EMA_FAST * 3):], SETTINGS.EMA_FAST)
    ema_slow_now = ema(closes[-(SETTINGS.EMA_SLOW * 3):], SETTINGS.EMA_SLOW)
    if ema_fast_now is None or ema_slow_now is None:
        return None

    r = rsi(closes, SETTINGS.RSI_PERIOD)
    if r is None:
        return None

    atr_val = atr(highs, lows, closes, SETTINGS.ATR_PERIOD)
    if atr_val is None:
        return None
    atr_pct = (atr_val / price) * 100.0

    drift = compute_drift_features(closes)
    volume_info = compute_volume_expansion(vols)
    accel_score, accel_label = compute_sell_acceleration(closes, ema_fast_now, ema_slow_now, r)
    reclaim_ok, reclaim_note = retest_reclaim_sell(lows, closes, n)

    trend_down = ema_fast_now < ema_slow_now
    recent_low = min(lows[-(n + 1):-1])
    break_down = price < recent_low

    latest_o, latest_h, latest_l, latest_c = opens[-1], highs[-1], lows[-1], closes[-1]
    candle_score, candle_label = candle_strength("SELL", latest_o, latest_h, latest_l, latest_c)
    wick_flag = wick_rejection("SELL", latest_o, latest_h, latest_l, latest_c)

    flow_stack = []
    score = 0.0

    if trend_down:
        flow_stack.append("Trend down")
        score += 12
    if move_pct <= -SETTINGS.MIN_MOVE_PCT:
        flow_stack.append("15m move")
        score += 12
    if volume_info["expanding"]:
        flow_stack.append("Volume expansion")
        score += 14
    if drift["steam"]:
        flow_stack.append("Steam move")
        score += 10
    if drift["drift_1m"] < 0:
        flow_stack.append("1m drift")
        score += 8
    if drift["drift_3m"] < 0:
        flow_stack.append("3m drift")
        score += 8
    if accel_score >= SETTINGS.ACCEL_MIN_SCORE:
        flow_stack.append("Acceleration")
        score += accel_score * 16
    if reclaim_ok:
        flow_stack.append("Reclaim")
        score += 10
    if break_down:
        flow_stack.append("Breakdown")
        score += 8
    if candle_score >= 65:
        flow_stack.append("Strong candle")
        score += 8
    if SETTINGS.MIN_ATR_PCT <= atr_pct <= SETTINGS.MAX_ATR_PCT:
        flow_stack.append("ATR valid")
        score += 6
    if orderbook_imbalance <= -SETTINGS.PRESSURE_MIN:
        flow_stack.append("Sell pressure")
        score += 6

    if wick_flag:
        score -= 10
    if r <= 22:
        score -= 8

    confidence = int(round(clamp(48 + score, 0, 95)))
    if confidence < SETTINGS.MIN_CONF_SEND:
        return None

    if confidence >= SETTINGS.HIGH_CONF:
        level = "HIGH CONVICTION"
        timing = "ENTER NOW" if not wick_flag and accel_score >= 0.60 else "WAIT 1 CANDLE"
    elif confidence >= 75:
        level = "ACTIONABLE+"
        timing = "PREPARE / ENTER ON HOLD"
    else:
        level = "ACTIONABLE"
        timing = "PREPARE"

    return {
        "direction": "SELL",
        "level": level,
        "timing": timing,
        "price": price,
        "move_pct": move_pct,
        "rsi": r,
        "ema_fast": ema_fast_now,
        "ema_slow": ema_slow_now,
        "atr_pct": atr_pct,
        "vol_ratio": volume_info["vol_ratio"],
        "drift_1m": drift["drift_1m"],
        "drift_3m": drift["drift_3m"],
        "steam": drift["steam"],
        "accel_score": accel_score,
        "accel_label": accel_label,
        "candle_score": candle_score,
        "candle_label": candle_label,
        "wick_rejection": wick_flag,
        "breakdown": break_down,
        "reclaim_note": reclaim_note,
        "orderbook_imbalance": orderbook_imbalance,
        "orderbook_label": orderbook_label,
        "confidence": confidence,
        "flow_stack": flow_stack,
    }


def build_early_signal(opens, highs, lows, closes, vols):
    n = SETTINGS.SIGNAL_WINDOW_MIN
    if len(closes) < max(SETTINGS.EMA_SLOW + 10, SETTINGS.RSI_PERIOD + 10, n + 5):
        return None

    price = closes[-1]
    first = closes[-(n + 1)]
    move_pct = pct_change(first, price)

    ema_fast_now = ema(closes[-(SETTINGS.EMA_FAST * 3):], SETTINGS.EMA_FAST)
    ema_slow_now = ema(closes[-(SETTINGS.EMA_SLOW * 3):], SETTINGS.EMA_SLOW)
    if ema_fast_now is None or ema_slow_now is None:
        return None

    r = rsi(closes, SETTINGS.RSI_PERIOD)
    if r is None:
        return None

    drift = compute_drift_features(closes)
    volume_info = compute_volume_expansion(vols)

    if (
        ema_fast_now > ema_slow_now
        and move_pct >= SETTINGS.MIN_MOVE_PCT * 0.55
        and volume_info["vol_ratio"] >= 1.15
        and drift["drift_1m"] > 0
        and 52 <= r <= 72
    ):
        return {
            "direction": "BUY",
            "price": price,
            "move_pct": move_pct,
            "rsi": r,
            "vol_ratio": volume_info["vol_ratio"],
            "drift_1m": drift["drift_1m"],
            "reason": "flow building + volume expansion + positive drift",
        }

    if (
        ema_fast_now < ema_slow_now
        and move_pct <= -(SETTINGS.MIN_MOVE_PCT * 0.55)
        and volume_info["vol_ratio"] >= 1.15
        and drift["drift_1m"] < 0
        and 28 <= r <= 48
    ):
        return {
            "direction": "SELL",
            "price": price,
            "move_pct": move_pct,
            "rsi": r,
            "vol_ratio": volume_info["vol_ratio"],
            "drift_1m": drift["drift_1m"],
            "reason": "flow building + volume expansion + negative drift",
        }

    return None


# =========================================================
# MESSAGE FORMAT
# =========================================================

def format_early_signal(sig: dict) -> str:
    return (
        f"⚠️ BTC FLOW WATCHLIST\n"
        f"Direction Bias: {sig['direction']}\n"
        f"Symbol: {SETTINGS.SYMBOL}\n"
        f"Price: {sig['price']:.2f}\n"
        f"Move({SETTINGS.SIGNAL_WINDOW_MIN}m): {sig['move_pct']:.2f}%\n"
        f"RSI({SETTINGS.RSI_PERIOD}): {sig['rsi']:.1f}\n"
        f"Vol Expansion: x{sig['vol_ratio']:.2f}\n"
        f"1m Drift: {sig['drift_1m']:.2f}%\n"
        f"Action: WATCH / PREPARE\n"
        f"Reason: {sig['reason']}"
    )


def format_main_signal(sig: dict) -> str:
    breakout_text = "UP ✅" if sig["direction"] == "BUY" and sig.get("breakout") else (
        "DOWN ✅" if sig["direction"] == "SELL" and sig.get("breakdown") else "NO"
    )

    icon = "📈" if sig["direction"] == "BUY" else "📉"
    if sig["level"] == "HIGH CONVICTION":
        icon = "🚀" if sig["direction"] == "BUY" else "🧨"

    flow_lines = "\n".join(f"- {x}" for x in sig["flow_stack"]) if sig["flow_stack"] else "- None"

    return (
        f"{icon} BTC FLOW SIGNAL\n"
        f"Mode: {sig['level']}\n"
        f"Symbol: {SETTINGS.SYMBOL}\n"
        f"Direction: {sig['direction']}\n"
        f"Price: {sig['price']:.2f}\n"
        f"Move({SETTINGS.SIGNAL_WINDOW_MIN}m): {sig['move_pct']:.2f}%\n"
        f"RSI({SETTINGS.RSI_PERIOD}): {sig['rsi']:.1f}\n"
        f"EMA{SETTINGS.EMA_FAST}/{SETTINGS.EMA_SLOW}: {sig['ema_fast']:.2f} / {sig['ema_slow']:.2f}\n"
        f"ATR%: {sig['atr_pct']:.2f}%\n"
        f"Vol Expansion: x{sig['vol_ratio']:.2f}\n"
        f"1m Drift: {sig['drift_1m']:.2f}%\n"
        f"3m Drift: {sig['drift_3m']:.2f}%\n"
        f"Steam Move: {'YES' if sig['steam'] else 'NO'}\n"
        f"Acceleration: {sig['accel_label']} ({sig['accel_score']:.2f})\n"
        f"Candle Strength: {sig['candle_label']} ({sig['candle_score']}/100)\n"
        f"Wick Rejection: {'YES ⚠️' if sig['wick_rejection'] else 'NO ✅'}\n"
        f"Breakout/Breakdown: {breakout_text}\n"
        f"Retest/Reclaim: {sig['reclaim_note']}\n"
        f"Pressure: {sig['orderbook_label']} ({sig['orderbook_imbalance']:.2f})\n"
        f"\n"
        f"Flow Stack:\n{flow_lines}\n\n"
        f"Confidence: {sig['confidence']}%\n"
        f"Timing: {sig['timing']}"
    )


# =========================================================
# MAIN
# =========================================================

def main():
    if not SETTINGS.BOT_TOKEN or not SETTINGS.CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return

    data = MultiSourceData()
    tg = TelegramNotifier(SETTINGS.BOT_TOKEN, SETTINGS.CHAT_ID)

    tg.send(
        f"🟣 BTC 15m Flow Bot started\n"
        f"Symbol: {SETTINGS.SYMBOL}\n"
        f"Mode: Telegram decision bot\n"
        f"Style: stacked flow + early momentum + micro-edge"
    )

    last_signal_time = 0.0
    last_early_time = 0.0
    last_early_side = None
    signals_today = 0
    current_day = datetime.now(timezone.utc).date()

    while True:
        try:
            today = datetime.now(timezone.utc).date()
            if today != current_day:
                current_day = today
                signals_today = 0

            opens, highs, lows, closes, vols, source_name = data.fetch_1m_klines(SETTINGS.SYMBOL, 250)
            orderbook_imbalance, orderbook_label = data.fetch_orderbook_pressure(SETTINGS.SYMBOL, 20)

            early = build_early_signal(opens, highs, lows, closes, vols)
            if early:
                now = time.time()
                if (
                    now - last_early_time >= SETTINGS.EARLY_COOLDOWN_SEC
                    or last_early_side != early["direction"]
                ):
                    tg.send(format_early_signal(early))
                    last_early_time = now
                    last_early_side = early["direction"]

            buy_sig = build_buy_signal(opens, highs, lows, closes, vols, orderbook_imbalance, orderbook_label)
            sell_sig = build_sell_signal(opens, highs, lows, closes, vols, orderbook_imbalance, orderbook_label)

            best = None
            if buy_sig and sell_sig:
                best = buy_sig if buy_sig["confidence"] >= sell_sig["confidence"] else sell_sig
            else:
                best = buy_sig or sell_sig

            if best:
                print(
                    f"[{datetime.now(timezone.utc).isoformat()}] "
                    f"price={best['price']:.2f} dir={best['direction']} conf={best['confidence']} "
                    f"move15={best['move_pct']:.2f}% volx={best['vol_ratio']:.2f} "
                    f"drift1={best['drift_1m']:.2f}% drift3={best['drift_3m']:.2f}% "
                    f"accel={best['accel_score']:.2f} pressure={best['orderbook_imbalance']:.2f}"
                )
            else:
                print(f"[{datetime.now(timezone.utc).isoformat()}] no actionable flow signal")

            if signals_today >= SETTINGS.MAX_SIGNALS_PER_DAY:
                time.sleep(SETTINGS.SAMPLE_INTERVAL_SEC)
                continue

            if best:
                now = time.time()
                if now - last_signal_time >= SETTINGS.SIGNAL_COOLDOWN_SEC:
                    tg.send(format_main_signal(best))
                    last_signal_time = now
                    signals_today += 1

            time.sleep(SETTINGS.SAMPLE_INTERVAL_SEC)

        except KeyboardInterrupt:
            print("Stopped by user.")
            break
        except Exception as e:
            print("Loop error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
