import os, time, requests
from collections import deque

BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SAMPLE_INTERVAL = float(os.getenv("SAMPLE_INTERVAL", "2"))
WINDOW_SEC = int(os.getenv("WINDOW_SEC", "60"))
MIN_MOMENTUM_PCT = float(os.getenv("MIN_MOMENTUM_PCT", "0.06"))
MAX_NOISE_PCT = float(os.getenv("MAX_NOISE_PCT", "0.18"))
MIN_MARKET_EDGE = float(os.getenv("MIN_MARKET_EDGE", "58"))

UP_PROB = float(os.getenv("UP_PROB", "60"))
DOWN_PROB = 100.0 - UP_PROB

def tg_send(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)

def fetch_price():
    r = requests.get(BINANCE_PRICE_URL, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])

def pct(a, b): return (b - a) / a * 100.0
def vol(ps): return (max(ps) - min(ps)) / ps[0] * 100.0

def decide(ps):
    m = pct(ps[0], ps[-1])
    v = vol(ps)
    if abs(m) < MIN_MOMENTUM_PCT: return None, m, v
    if v > MAX_NOISE_PCT: return None, m, v
    return ("UP" if m > 0 else "DOWN"), m, v

def main():
    best_side = "UP" if UP_PROB >= DOWN_PROB else "DOWN"
    best_prob = max(UP_PROB, DOWN_PROB)
    if best_prob < MIN_MARKET_EDGE:
        tg_send(f"❌ No trade: edge {best_prob:.1f}% < {MIN_MARKET_EDGE:.1f}%")
        return

    tg_send(f"🟢 Started | Market={best_side}({best_prob:.1f}%) | Window={WINDOW_SEC}s")

    prices = deque(maxlen=int(WINDOW_SEC / SAMPLE_INTERVAL))
    last = None
    while True:
        p = fetch_price()
        prices.append(p)
        if len(prices) == prices.maxlen:
            out = decide(list(prices))
            if out[0] is None:
                last = None
            else:
                direction, m, v = out
                aligned = (direction == best_side)
                sig = "ENTER" if aligned else "SKIP"
                if sig != last:
                    tg_send(f"{'✅ ENTER' if aligned else '❌ SKIP'} | "
                            f"Market={best_side}({best_prob:.1f}%) | "
                            f"BTC={direction} mom={m:.3f}% vol={v:.3f}%")
                    last = sig
        time.sleep(SAMPLE_INTERVAL)

if __name__ == "__main__":
    main()
