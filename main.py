import os
import time
import math
import requests
from collections import deque
from datetime import datetime, timezone

# ----------------------------
# Config (from Railway Variables)
# ----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SYMBOL = os.getenv("SYMBOL", "bitcoin").strip().lower()  # for CoinGecko: "bitcoin"
VS = os.getenv("VS", "usd").strip().lower()

SAMPLE_INTERVAL_SEC = int(os.getenv("SAMPLE_INTERVAL_SEC", "10"))   # pull price every N seconds
WINDOW_SEC = int(os.getenv("WINDOW_SEC", "300"))                    # 5 minutes = 300 sec
MIN_MOVE_PCT = float(os.getenv("MIN_MOVE_PCT", "0.10"))             # minimum % move to trigger signal
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "60"))                 # avoid spam: min time between alerts

# Optional: require that move direction is "consistent" (reduces noise)
MAX_ZIGZAG_RATIO = float(os.getenv("MAX_ZIGZAG_RATIO", "0.65"))      # 0..1 ; lower = stricter

# API endpoint (CoinGecko - generally stable, no Binance 451)
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

# ----------------------------
# Helpers
# ----------------------------
def utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def tg_send(text: str):
    """Send Telegram message if token & chat id set; otherwise just print."""
    print(text, flush=True)
    if not BOT_TOKEN or not CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        # Don't crash because of telegram
        print(f"[{utc_now_str()}] Telegram send failed: {e}", flush=True)

def fetch_btc_price_usd() -> float:
    """
    Fetch BTC price from CoinGecko (simple/price).
    Returns float price.
    """
    params = {"ids": SYMBOL, "vs_currencies": VS}
    r = requests.get(COINGECKO_URL, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return float(data[SYMBOL][VS])

def pct_change(a: float, b: float) -> float:
    """Percent change from a to b."""
    if a == 0:
        return 0.0
    return (b - a) / a * 100.0

def zigzag_ratio(prices: list[float]) -> float:
    """
    Measures how 'choppy' the path is:
    total absolute step distance / absolute net move.
    If very choppy, ratio is high. We convert to a normalized 'zigzag ratio' in [0,1]-ish.
    """
    if len(prices) < 3:
        return 0.0

    steps = 0.0
    for i in range(1, len(prices)):
        steps += abs(prices[i] - prices[i - 1])

    net = abs(prices[-1] - prices[0])
    if net == 0:
        return 1.0  # totally flat -> consider noisy

    raw = steps / net  # >= 1
    # map 1..5+ into 0..1 (rough)
    return min(1.0, max(0.0, (raw - 1.0) / 4.0))

def decide_signal(prices: list[float]) -> tuple[str | None, float, float]:
    """
    Decide UP/DOWN based on net move over window.
    Returns: (signal or None, move_pct, zigzag)
    """
    if len(prices) < 2:
        return None, 0.0, 0.0

    start = prices[0]
    end = prices[-1]
    move = pct_change(start, end)
    zz = zigzag_ratio(prices)

    # require minimum move
    if abs(move) < MIN_MOVE_PCT:
        return None, move, zz

    # require not too choppy
    if zz > MAX_ZIGZAG_RATIO:
        return None, move, zz

    return ("UP" if move > 0 else "DOWN"), move, zz

# ----------------------------
# Main loop
# ----------------------------
def main():
    tg_send(f"🟢 BTC Signal Bot started\nTime: {utc_now_str()}\nWindow: {WINDOW_SEC}s | Sample: {SAMPLE_INTERVAL_SEC}s\nMinMove: {MIN_MOVE_PCT}% | Cooldown: {COOLDOWN_SEC}s")

    points = deque()  # (timestamp, price)
    last_alert_ts = 0.0
    last_signal = None

    while True:
        try:
            price = fetch_btc_price_usd()
            now = time.time()
            points.append((now, price))

            # keep only WINDOW_SEC
            while points and (now - points[0][0]) > WINDOW_SEC:
                points.popleft()

            prices = [p for _, p in points]
            signal, move_pct, zz = decide_signal(prices)

            # status line every loop (خففها لو تبي)
            print(f"[{utc_now_str()}] BTC={price:.2f} | points={len(prices)} | move={move_pct:+.3f}% | zigzag={zz:.2f}", flush=True)

            # send alert if new signal and cooldown passed
            if signal:
                cooldown_ok = (now - last_alert_ts) >= COOLDOWN_SEC
                changed = (signal != last_signal)

                if cooldown_ok and changed:
                    msg = (
                        f"🚨 SIGNAL: {signal}\n"
                        f"BTC: {price:.2f} {VS.upper()}\n"
                        f"Move({WINDOW_SEC}s): {move_pct:+.3f}%\n"
                        f"Zigzag: {zz:.2f}\n"
                        f"Time: {utc_now_str()}"
                    )
                    tg_send(msg)
                    last_alert_ts = now
                    last_signal = signal

            time.sleep(SAMPLE_INTERVAL_SEC)

        except requests.HTTPError as e:
            # API responded but error; wait then retry
            tg_send(f"⚠️ HTTP error while fetching price: {e}\nTime: {utc_now_str()}\nRetrying in 20s...")
            time.sleep(20)

        except Exception as e:
            tg_send(f"⚠️ Unexpected error: {e}\nTime: {utc_now_str()}\nRetrying in 20s...")
            time.sleep(20)

if __name__ == "__main__":
    main()
