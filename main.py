import os
import time
import requests
from collections import deque
from datetime import datetime, timezone

# =============================
# CONFIG (من Railway Variables)
# =============================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SYMBOL = os.getenv("SYMBOL", "bitcoin").strip().lower()
VS = os.getenv("VS", "usd").strip().lower()

SAMPLE_INTERVAL_SEC = int(os.getenv("SAMPLE_INTERVAL_SEC", "60"))
WINDOW_SEC = int(os.getenv("WINDOW_SEC", "300"))
MIN_MOVE_PCT = float(os.getenv("MIN_MOVE_PCT", "0.2"))

COOLDOWN_SEC = 300

PRICE_URL = f"https://api.coingecko.com/api/v3/simple/price?ids={SYMBOL}&vs_currencies={VS}"

# =============================
# TELEGRAM SEND
# =============================

def tg_send(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("BOT_TOKEN or CHAT_ID missing")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }

    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

# =============================
# FETCH PRICE
# =============================

def fetch_price():
    try:
        r = requests.get(PRICE_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        return float(data[SYMBOL][VS])
    except Exception as e:
        print("Price fetch error:", e)
        return None

# =============================
# MAIN LOOP
# =============================

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return

    tg_send("🟢 BTC Signal Bot started")

    prices = deque()
    last_signal_time = 0

    while True:
        price = fetch_price()

        if price is None:
            time.sleep(20)
            continue

        now = time.time()
        prices.append((now, price))

        # احذف البيانات القديمة خارج النافذة
        while prices and now - prices[0][0] > WINDOW_SEC:
            prices.popleft()

        if len(prices) >= 2:
            first_price = prices[0][1]
            move_pct = ((price - first_price) / first_price) * 100

            print(f"[{datetime.now(timezone.utc)}] BTC={price} move={move_pct:.3f}%")

            if abs(move_pct) >= MIN_MOVE_PCT:
                if now - last_signal_time > COOLDOWN_SEC:
                    direction = "📈 BUY" if move_pct > 0 else "📉 SELL"

                    message = (
                        f"{direction} SIGNAL\n"
                        f"Price: {price:.2f} USD\n"
                        f"Move: {move_pct:.2f}% in {WINDOW_SEC//60}m"
                    )

                    tg_send(message)
                    last_signal_time = now

        time.sleep(SAMPLE_INTERVAL_SEC)


if __name__ == "__main__":
    main()
