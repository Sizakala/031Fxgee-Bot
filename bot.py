import websocket
import json
import threading
import time
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from collections import deque
from flask import Flask

# ================= CONFIG =================
TELEGRAM_BOT_TOKEN = "8691377500:AAE0PxlrLsG4yO5oLUV-HD38JvjW0HJUHDk"
TELEGRAM_CHAT_ID = "6661868112"  # your numeric chat ID
BOT_NAME = "031FxGee bot"   # used in signature

# All Deriv symbols – USE REAL TICK CODES
# (Display names shown in comments for your reference)
SYMBOLS = [
    "R_10",    # Volatility 10 Index
    "1HZ10V",  # Volatility 10 (1s) Index
    "R_25",    # Volatility 25 Index
    "1HZ25V",  # Volatility 25 (1s) Index
    "R_50",    # Volatility 50 Index
    "1HZ50V",  # Volatility 50 (1s) Index
    "R_75",    # Volatility 75 Index
    "1HZ75V",  # Volatility 75 (1s) Index
    "R_100",   # Volatility 100 Index
    "1HZ100V", # Volatility 100 (1s) Index
    "STP",     # Step Index
    "frxXAUUSD",  # Gold
    "frxUS30",    # US30
    "frxNAS100",  # NAS100
    "frxEURUSD",  # EURUSD
    "frxBTCUSD",  # BTCUSD
]

# Map tick codes → pretty names for alerts
DISPLAY_NAME = {
    "R_10": "Volatility 10 Index",
    "1HZ10V": "Volatility 10 (1s) Index",
    "R_25": "Volatility 25 Index",
    "1HZ25V": "Volatility 25 (1s) Index",
    "R_50": "Volatility 50 Index",
    "1HZ50V": "Volatility 50 (1s) Index",
    "R_75": "Volatility 75 Index",
    "1HZ75V": "Volatility 75 (1s) Index",
    "R_100": "Volatility 100 Index",
    "1HZ100V": "Volatility 100 (1s) Index",
    "STP": "Step Index",
    "frxXAUUSD": "XAUUSD",
    "frxUS30": "US30",
    "frxNAS100": "NAS100",
    "frxEURUSD": "EURUSD",
    "frxBTCUSD": "BTCUSD",
}

TIMEFRAMES = ["M15", "M30", "H1"]
TIMEFRAME_MINUTES = {"M15": 15, "M30": 30, "H1": 60}

# Heartbeat interval (seconds)
HEARTBEAT_INTERVAL = 300   # 5 minutes

# ===========================================

# Candlestick storage per symbol and timeframe
candles = {}  # key: (symbol_code, tf) -> deque of bars
current_bar = {}  # (symbol_code, tf) -> current open bar

# Flask app for Render's required web service & keep‑alive ping
app = Flask(__name__)

@app.route('/ping')
def ping():
    return 'pong', 200

# --- Telegram helpers (using urllib, synchronous) ---
def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def send_heartbeat():
    msg = f"💓 031FxGee bot heartbeat — {datetime.now().strftime('%H:%M:%S')}"
    send_telegram(msg)

# --- Candlestick management ---
def init_candle_store():
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            key = (sym, tf)
            candles[key] = deque(maxlen=500)
            current_bar[key] = None

def update_bar(sym, tf, time_sec, price):
    """
    Update current open bar with the tick price.
    If bar time boundary crossed, finalise old bar and start new.
    """
    key = (sym, tf)
    minutes = TIMEFRAME_MINUTES[tf]
    # round time to candle start
    bar_start = (time_sec // (minutes * 60)) * (minutes * 60)
    bar_dt = datetime.fromtimestamp(bar_start)

    cur = current_bar[key]
    if cur is None or cur["time"] != bar_dt:
        # finalise old bar if exists
        if cur is not None:
            candles[key].append(cur)
            detect_patterns(sym, tf, cur)
        # start new bar
        current_bar[key] = {
            "time": bar_dt,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1
        }
    else:
        # update current bar
        cur["high"] = max(cur["high"], price)
        cur["low"] = min(cur["low"], price)
        cur["close"] = price
        cur["volume"] += 1

# --- Pattern detection ---
def get_recent_swings(bars, lookback=50):
    if len(bars) < 5:
        return [], []
    highs = []
    lows = []
    for i in range(2, len(bars)-2):
        high_i = bars[i]["high"]
        low_i = bars[i]["low"]
        if high_i > bars[i-1]["high"] and high_i > bars[i+1]["high"] and high_i > bars[i-2]["high"] and high_i > bars[i+2]["high"]:
            highs.append({"price": high_i, "time": bars[i]["time"], "type": "high"})
        if low_i < bars[i-1]["low"] and low_i < bars[i+1]["low"] and low_i < bars[i-2]["low"] and low_i < bars[i+2]["low"]:
            lows.append({"price": low_i, "time": bars[i]["time"], "type": "low"})
    return highs, lows

def detect_icc(sym, tf):
    key = (sym, tf)
    bars_list = list(candles[key])
    if len(bars_list) < 20:
        return None
    swings_high, swings_low = get_recent_swings(bars_list)
    if len(swings_high) < 2 or len(swings_low) < 2:
        return None

    # bearish ICC
    sh1 = swings_high[-2]
    sh2 = swings_high[-1]
    sl1 = swings_low[-2]
    sl2 = swings_low[-1]

    if sh2["price"] > sh1["price"] and sl2["price"] > sl1["price"]:
        for i in range(len(bars_list)-1, -1, -1):
            if bars_list[i]["close"] < sl2["price"]:
                tolerance = 2 * get_symbol_tick(sym)
                for j in range(i+1, len(bars_list)):
                    if abs(bars_list[j]["close"] - sl2["price"]) <= tolerance:
                        correction_low = min(bars_list[k]["low"] for k in range(i, j+1))
                        for m in range(j+1, len(bars_list)):
                            if bars_list[m]["close"] < correction_low:
                                return {"direction": "SELL", "tf": tf, "detail": f"Bearish ICC"}
                        break
                break

    # bullish ICC
    if sh2["price"] < sh1["price"] and sl2["price"] < sl1["price"]:
        for i in range(len(bars_list)-1, -1, -1):
            if bars_list[i]["close"] > sh2["price"]:
                tolerance = 2 * get_symbol_tick(sym)
                for j in range(i+1, len(bars_list)):
                    if abs(bars_list[j]["close"] - sh2["price"]) <= tolerance:
                        correction_high = max(bars_list[k]["high"] for k in range(i, j+1))
                        for m in range(j+1, len(bars_list)):
                            if bars_list[m]["close"] > correction_high:
                                return {"direction": "BUY", "tf": tf, "detail": f"Bullish ICC"}
                        break
                break
    return None

def detect_double_top_bottom(sym, tf):
    key = (sym, tf)
    bars = list(candles[key])
    if len(bars) < 20:
        return None
    swings_high, swings_low = get_recent_swings(bars, lookback=50)
    if len(swings_high) >= 2:
        last2 = swings_high[-2:]
        if len(last2) == 2:
            h1, h2 = last2
            if abs(h1["price"] - h2["price"]) / max(h1["price"], 1) < 0.002:
                idx1 = next(i for i, b in enumerate(bars) if b["time"] == h1["time"])
                idx2 = next(i for i, b in enumerate(bars) if b["time"] == h2["time"])
                if abs(idx2 - idx1) >= 3:
                    return "RESISTANCE"
    if len(swings_low) >= 2:
        last2 = swings_low[-2:]
        if len(last2) == 2:
            l1, l2 = last2
            if abs(l1["price"] - l2["price"]) / max(l1["price"], 1) < 0.002:
                idx1 = next(i for i, b in enumerate(bars) if b["time"] == l1["time"])
                idx2 = next(i for i, b in enumerate(bars) if b["time"] == l2["time"])
                if abs(idx2 - idx1) >= 3:
                    return "SUPPORT"
    return None

def get_symbol_tick(sym):
    if "R_" in sym or "1HZ" in sym or sym == "STP":
        return 0.001
    elif sym in ["frxXAUUSD", "frxBTCUSD"]:
        return 0.1
    elif sym in ["frxUS30", "frxNAS100"]:
        return 1.0
    else:
        return 0.0001

last_icc_signal = {}

def detect_patterns(sym, tf, closed_bar):
    # Translate tick code to display name for alerts
    display = DISPLAY_NAME.get(sym, sym)
    icc = detect_icc(sym, tf)
    if icc:
        now = datetime.now()
        sig_key = (sym, icc["direction"])
        if sig_key not in last_icc_signal or (now - last_icc_signal[sig_key]).seconds > 600:
            last_icc_signal[sig_key] = now
            msg = f"🔥 {'STRONG BUY' if icc['direction'] == 'BUY' else 'STRONG SELL'}\n{display} {tf}"
            send_telegram(msg)

    dtb = detect_double_top_bottom(sym, tf)
    if dtb == "RESISTANCE":
        send_telegram(f"🛡️ Strong Resistance\n{display} {tf} – Double Top")
    elif dtb == "SUPPORT":
        send_telegram(f"🛡️ Strong Support\n{display} {tf} – Double Bottom")

# --- Deriv WebSocket tick listener ---
def on_message(ws, message):
    data = json.loads(message)
    if "tick" in data:
        tick = data["tick"]
        sym = tick["symbol"]
        price = tick["quote"]
        epoch = tick["epoch"]
        # DEBUG: Print every tick to confirm data flow
        print(f"TICK: {sym} @ {price}")
        for tf in TIMEFRAMES:
            update_bar(sym, tf, epoch, price)

def on_error(ws, error):
    print(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed, reconnecting in 5s...")
    time.sleep(5)
    start_websocket()

def on_open(ws):
    print("WebSocket connected. Subscribing to symbols...")
    for sym in SYMBOLS:
        ws.send(json.dumps({"ticks": sym, "subscribe": 1}))
        print(f"Subscribed to {sym}")

def start_websocket():
    ws = websocket.WebSocketApp(
        "wss://ws.derivws.com/websockets/v3?app_id=1089",
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    ws.run_forever(ping_interval=30)

# --- Heartbeat thread ---
def heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        send_heartbeat()

# --- Flask thread ---
def start_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    init_candle_store()
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    time.sleep(2)
    start_websocket()
