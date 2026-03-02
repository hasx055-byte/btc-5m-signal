import os
import time
import requests
from collections import deque
from datetime import datetime

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SAMPLE_INTERVAL_SEC = int(os.getenv("SAMPLE_INTERVAL_SEC", "60"))
WINDOW_SEC = int(os.getenv("WINDOW_SEC", "600"))  # 10 دقائق للقرار
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "4"))

# شروط القرار (تقدر تعدلها لاحقاً)
MIN_SCORE_TRADE = int(os.getenv("MIN_SCORE_TRADE", "80"))
TRAP_MOVE_PCT = float(os.getenv("TRAP_MOVE_PCT", "0.35"))  # إذا الحركة انفجارية نوقف
ENTRY_WINDOW_SEC = int(os.getenv("ENTRY_WINDOW_SEC", "90"))  # آخر 90 ثانية فقط

session = requests.Session()
session.headers.update({"User-Agent": "poly-decision-bot/1.0"})

prices = deque(maxlen=15)  # نخزن ~15 دقيقة (لو كل دقيقة)
trade_count = 0
last_day = None

def tg_send(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN/CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = session.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=15)
    if r.status_code != 200:
        print("Telegram failed:", r.status_code, r.text[:200])

def fetch_btc_price():
    # Coinbase أكثر استقرار من CoinGecko
    url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    return float(r.json()["data"]["amount"])

def calc_fixed_5m_remaining_seconds():
    # الجولة Fixed 5m: نحسب كم باقي على نهاية الخمس دقائق الحالية
    now = datetime.utcnow()
    sec_into_block = (now.minute % 5) * 60 + now.second
    return 300 - sec_into_block

def score_trend(current, avg10):
    # Trend score (40)
    return 40 if current > avg10 else 0

def score_momentum():
    # Momentum score (25) إذا آخر 3 قراءات صاعدة/هابطة
    if len(prices) < 4:
        return 0, "FLAT"
    a, b, c = prices[-3], prices[-2], prices[-1]
    if c > b > a:
        return 25, "UP"
    if c < b < a:
        return 25, "DOWN"
    return 0, "CHOP"

def trap_filter(move_pct):
    # Trap filter (20) — لو الحركة انفجارية نعتبرها فخ
    return 0 if abs(move_pct) > TRAP_MOVE_PCT else 20

def main():
    global trade_count, last_day

    tg_send("🧠 POLY DECISION BOT started")

    while True:
        # Reset daily counter
        today = datetime.utcnow().date()
        if last_day != today:
            trade_count = 0
            last_day = today

        if trade_count >= MAX_TRADES_PER_DAY:
            time.sleep(120)
            continue

        try:
            p = fetch_btc_price()
        except Exception as e:
            print("Price error:", e)
            time.sleep(20)
            continue

        prices.append(p)

        if len(prices) < 10:
            time.sleep(SAMPLE_INTERVAL_SEC)
            continue

        # متوسط آخر 10 دقائق تقريبًا
        last10 = list(prices)[-10:]
        avg10 = sum(last10) / len(last10)

        # حركة داخل WINDOW (10 دقائق)
        oldest = last10[0]
        move_pct = ((p - oldest) / oldest) * 100

        # Timing
        remaining = calc_fixed_5m_remaining_seconds()
        in_entry_window = remaining <= ENTRY_WINDOW_SEC

        # Scoring
        score = 0
        score += score_trend(p, avg10)
        mom_score, mom_dir = score_momentum()
        score += mom_score
        score += trap_filter(move_pct)
        if in_entry_window:
            score += 15

        # Direction decision
        if mom_dir == "CHOP":
            direction = "NO TRADE"
        else:
            direction = "UP" if p > avg10 else "DOWN"

        print(f"BTC={p:.2f} avg10={avg10:.2f} move={move_pct:.3f}% rem={remaining}s score={score} dir={direction}")

        # Send decision only if strong
        if score >= MIN_SCORE_TRADE and direction != "NO TRADE":
            tg_send(
                f"🧠 POLY DECISION\n"
                f"Direction: {direction}\n"
                f"Confidence: {score}%\n"
                f"Time Left: {remaining}s\n"
                f"Move(10m): {move_pct:.2f}%\n"
                f"✅ TRADE ALLOWED"
            )
            trade_count += 1

        time.sleep(SAMPLE_INTERVAL_SEC)

if __name__ == "__main__":
    main()
