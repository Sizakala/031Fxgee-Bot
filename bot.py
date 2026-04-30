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

SYMBOLS = [
    "R_10", "1HZ10V", "R_25", "1HZ25V", "R_50", "1HZ50V", "R_75", "1HZ75V",
    "R_100", "1HZ100V", "STP", "frxXAUUSD", "frxUS30", "frxNAS100",
    "frxEURUSD", "frxBTCUSD"
]

DISPLAY_NAME = {
    "R_10": "Volatility 10 Index", "1HZ10V": "Volatility 10 (1s) Index",
    "R_25": "Volatility 25 Index", "1HZ25V": "Volatility 25 (1s) Index",
    "R_50": "Volatility 50 Index", "1HZ50V": "Volatility 50 (1s) Index",
    "R_75": "Volatility 75 Index", "1HZ75V": "Volatility 75 (1s) Index",
    "R_100": "Volatility 100 Index", "1HZ100V": "Volatility 100 (1s) Index",
    "STP": "Step Index", "frxXAUUSD": "XAUUSD", "frxUS30": "US30",
    "frxNAS100": "NAS100", "frxEURUSD": "EURUSD", "frxBTCUSD": "BTCUSD"
}

TIMEFRAMES = ["M15", "M30", "H1"]
TIMEFRAME_MINUTES = {"M15": 15, "M30": 30, "H1": 60}
HEARTBEAT_INTERVAL = 3600   # 1 hour

candles = {}
current_bar = {}

app = Flask(__name__)
@app.route('/ping')
def ping():
    return 'pong', 200

def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def send_heartbeat():
    send_telegram(f"💓 031FxGee bot heartbeat — {datetime.now().strftime('%H:%M:%S')}")

def init_candle_store():
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            candles[(sym, tf)] = deque(maxlen=500)
            current_bar[(sym, tf)] = None

def update_bar(sym, tf, time_sec, price):
    key = (sym, tf)
    minutes = TIMEFRAME_MINUTES[tf]
    bar_start = (time_sec // (minutes * 60)) * (minutes * 60)
    bar_dt = datetime.fromtimestamp(bar_start)
    cur = current_bar[key]
    if cur is None or cur["time"] != bar_dt:
        if cur is not None:
            candles[key].append(cur)
            detect_patterns(sym, tf, cur)
        current_bar[key] = {"time": bar_dt, "open": price, "high": price, "low": price, "close": price, "volume": 1}
    else:
        cur["high"] = max(cur["high"], price)
        cur["low"] = min(cur["low"], price)
        cur["close"] = price
        cur["volume"] += 1

def get_recent_swings(bars, lookback=50):
    if len(bars) < 5:
        return [], []
    highs, lows = [], []
    for i in range(2, len(bars)-2):
        if bars[i]["high"] > bars[i-1]["high"] and bars[i]["high"] > bars[i+1]["high"] and bars[i]["high"] > bars[i-2]["high"] and bars[i]["high"] > bars[i+2]["high"]:
            highs.append({"price": bars[i]["high"], "time": bars[i]["time"], "type": "high"})
        if bars[i]["low"] < bars[i-1]["low"] and bars[i]["low"] < bars[i+1]["low"] and bars[i]["low"] < bars[i-2]["low"] and bars[i]["low"] < bars[i+2]["low"]:
            lows.append({"price": bars[i]["low"], "time": bars[i]["time"], "type": "low"})
    return highs, lows

def detect_icc(sym, tf):
    key = (sym, tf)
    bars_list = list(candles[key])
    if len(bars_list) < 20:
        return None
    swings_high, swings_low = get_recent_swings(bars_list)
    if len(swings_high) < 2 or len(swings_low) < 2:
        return None
    sh1, sh2 = swings_high[-2], swings_high[-1]
    sl1, sl2 = swings_low[-2], swings_low[-1]
    # Bearish ICC
    if sh2["price"] > sh1["price"] and sl2["price"] > sl1["price"]:
        for i in range(len(bars_list)-1, -1, -1):
            if bars_list[i]["close"] < sl2["price"]:
                tolerance = 2 * get_symbol_tick(sym)
                for j in range(i+1, len(bars_list)):
                    if abs(bars_list[j]["close"] - sl2["price"]) <= tolerance:
                        correction_low = min(bars_list[k]["low"] for k in range(i, j+1))
                        for m in range(j+1, len(bars_list)):
                            if bars_list[m]["close"] < correction_low:
                                return {"direction": "SELL", "tf": tf}
                        break
                break
    # Bullish ICC
    if sh2["price"] < sh1["price"] and sl2["price"] < sl1["price"]:
        for i in range(len(bars_list)-1, -1, -1):
            if bars_list[i]["close"] > sh2["price"]:
                tolerance = 2 * get_symbol_tick(sym)
                for j in range(i+1, len(bars_list)):
                    if abs(bars_list[j]["close"] - sh2["price"]) <= tolerance:
                        correction_high = max(bars_list[k]["high"] for k in range(i, j+1))
                        for m in range(j+1, len(bars_list)):
                            if bars_list[m]["close"] > correction_high:
                                return {"direction": "BUY", "tf": tf}
                        break
                break
    return None

def detect_double_top_bottom(sym, tf):
    key = (sym, tf)
    bars = list(candles[key])
    if len(bars) < 20:
        return None

    tolerance_pct = 0.005   # 0.5% tolerance
    min_separation = 2      # at least 2 bars between peaks
    max_lookback = 30       # search up to 30 bars back

    # --- Swing highs method ---
    swings_high, swings_low = get_recent_swings(bars, lookback=50)
    if len(swings_high) >= 2:
        last2 = swings_high[-2:]
        if len(last2) == 2:
            h1, h2 = last2
            if abs(h1["price"] - h2["price"]) / max(h1["price"], 1) < tolerance_pct:
                idx1 = next(i for i, b in enumerate(bars) if b["time"] == h1["time"])
                idx2 = next(i for i, b in enumerate(bars) if b["time"] == h2["time"])
                if abs(idx2 - idx1) >= min_separation:
                    return "RESISTANCE"

    # --- Simple equal highs (any two bars, 2‑30 bars apart) ---
    highs_only = [(b["high"], i) for i, b in enumerate(bars)]
    for i in range(len(highs_only)-1, 0, -1):
        for j in range(i-1, max(i-max_lookback, -1), -1):
            if abs(highs_only[i][0] - highs_only[j][0]) / max(highs_only[i][0], 1) < tolerance_pct:
                if abs(highs_only[i][1] - highs_only[j][1]) >= min_separation:
                    return "RESISTANCE"

    # --- Swing lows method ---
    if len(swings_low) >= 2:
        last2 = swings_low[-2:]
        if len(last2) == 2:
            l1, l2 = last2
            if abs(l1["price"] - l2["price"]) / max(l1["price"], 1) < tolerance_pct:
                idx1 = next(i for i, b in enumerate(bars) if b["time"] == l1["time"])
                idx2 = next(i for i, b in enumerate(bars) if b["time"] == l2["time"])
                if abs(idx2 - idx1) >= min_separation:
                    return "SUPPORT"

    # --- Simple equal lows (any two bars, 2‑30 bars apart) ---
    lows_only = [(b["low"], i) for i, b in enumerate(bars)]
    for i in range(len(lows_only)-1, 0, -1):
        for j in range(i-1, max(i-max_lookback, -1), -1):
            if abs(lows_only[i][0] - lows_only[j][0]) / max(lows_only[i][0], 1) < tolerance_pct:
                if abs(lows_only[i][1] - lows_only[j][1]) >= min_separation:
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
        send_telegram(f"🛡️ Strong Resistance\n{display} {tf} – Equal Highs / Double Top")
    elif dtb == "SUPPORT":
        send_telegram(f"🛡️ Strong Support\n{display} {tf} – Equal Lows / Double Bottom")

# --- Deriv WebSocket tick listener ---
def on_message(ws, message):
    data = json.loads(message)
    if "tick" in data:
        tick = data["tick"]
        sym = tick["symbol"]
        price = tick["quote"]
        epoch = tick["epoch"]
        print(f"TICK: {sym} @ {price}")
        for tf in TIMEFRAMES:
            update_bar(sym, tf, epoch, price)

def on_error(ws, error): print(f"WebSocket error: {error}")
def on_close(ws, *args):
    print("WebSocket closed, reconnecting in 5s...")
    time.sleep(5)
    start_websocket()
def on_open(ws):
    print("WebSocket connected. Subscribing to symbols...")
    for sym in SYMBOLS:
        ws.send(json.dumps({"ticks": sym, "subscribe": 1}))
        print(f"Subscribed to {sym}")

def start_websocket():
    ws = websocket.WebSocketApp("wss://ws.derivws.com/websockets/v3?app_id=1089",
        on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
    ws.run_forever(ping_interval=30)

def heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        send_heartbeat()

def start_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    init_candle_store()
    threading.Thread(target=start_flask, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    time.sleep(2)
    print("DEBUG: Launching WebSocket...")
    start_websocket()
