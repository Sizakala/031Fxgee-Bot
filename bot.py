import websocket
import json
import threading
import time
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
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

    if sh2["price"] > sh1["price"] and sl2["price"] > sl1["price"]:
        for i in range(len(bars_list)-1, -1, -1):
            if bars_list[i]["close"] < sl2["price"]:
                tolerance = 2 * get_symbol_tick(sym)
                for j in range(i+1, len(bars_list)):
                    if abs(bars_list[j]["close"] - sl2["price"]) <= tolerance:
                        return {"direction": "SELL", "tf": tf, "level": sl2["price"]}
                break

    if sh2["price"] < sh1["price"] and sl2["price"] < sl1["price"]:
        for i in range(len(bars_list)-1, -1, -1):
            if bars_list[i]["close"] > sh2["price"]:
                tolerance = 2 * get_symbol_tick(sym)
                for j in range(i+1, len(bars_list)):
                    if abs(bars_list[j]["close"] - sh2["price"]) <= tolerance:
                        return {"direction": "BUY", "tf": tf, "level": sh2["price"]}
                break
    return None

# --- Rejection candle detection ---
def check_rejection(bar, prev_bar):
    open_p = bar["open"]
    close_p = bar["close"]
    high_p = bar["high"]
    low_p = bar["low"]
    body = abs(close_p - open_p)
    total_range = high_p - low_p
    if total_range == 0:
        return None
    upper_wick = high_p - max(open_p, close_p)
    lower_wick = min(open_p, close_p) - low_p
    if upper_wick >= 2 * body and body > 0 and lower_wick < body:
        return "Bearish Spike 🕯️"
    if lower_wick >= 2 * body and body > 0 and upper_wick < body:
        return "Bullish Spike 🕯️"
    if total_range > 0 and body < 0.1 * total_range:
        return "Doji ⚖️"
    if prev_bar:
        prev_open = prev_bar["open"]
        prev_close = prev_bar["close"]
        prev_body = prev_close - prev_open
        if prev_body > 0 and close_p < open_p and abs(close_p - open_p) > abs(prev_body):
            if close_p < prev_open and open_p > prev_close:
                return "Bearish Engulfing 🔴"
        if prev_body < 0 and close_p > open_p and abs(close_p - open_p) > abs(prev_body):
            if close_p > prev_open and open_p < prev_close:
                return "Bullish Engulfing 🟢"
    return None

def detect_double_top_bottom_with_rejection(sym, tf, closed_bar):
    key = (sym, tf)
    bars = list(candles[key])
    if len(bars) < 5:
        return None, None
    tolerance_pct = 0.005
    min_separation = 2
    max_lookback = 150
    last_idx = len(bars) - 1
    last_bar = bars[last_idx]
    last_high = last_bar["high"]
    last_low = last_bar["low"]
    prev_bar = bars[last_idx - 1] if last_idx >= 1 else None

    # Check equal highs
    for j in range(last_idx - min_separation, max(last_idx - max_lookback - 1, -1), -1):
        if abs(last_high - bars[j]["high"]) / max(last_high, 1) < tolerance_pct:
            # GOLD exception: no rejection needed
            if sym == "frxXAUUSD":
                return "RESISTANCE", "Zone touched"
            rejection = check_rejection(last_bar, prev_bar)
            if rejection:
                return "RESISTANCE", rejection
            return None, None

    # Check equal lows
    for j in range(last_idx - min_separation, max(last_idx - max_lookback - 1, -1), -1):
        if abs(last_low - bars[j]["low"]) / max(last_low, 1) < tolerance_pct:
            # GOLD exception: no rejection needed
            if sym == "frxXAUUSD":
                return "SUPPORT", "Zone touched"
            rejection = check_rejection(last_bar, prev_bar)
            if rejection:
                return "SUPPORT", rejection
            return None, None

    return None, None

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
last_double_signal = {}

def detect_patterns(sym, tf, closed_bar):
    display = DISPLAY_NAME.get(sym, sym)
    bar_time_utc = closed_bar["time"]
    bar_time_sast = bar_time_utc + timedelta(hours=2)
    time_str = bar_time_sast.strftime("%H:%M %d/%m/%Y")

    # --- ICC ---
    icc = detect_icc(sym, tf)
    if icc:
        now = datetime.now()
        sig_key = (sym, icc["direction"])
        if sig_key not in last_icc_signal or (now - last_icc_signal[sig_key]).seconds > 600:
            last_icc_signal[sig_key] = now
            if icc["direction"] == "BUY":
                emoji = "📈"
                direction_text = "STRONG BUY"
            else:
                emoji = "📉"
                direction_text = "STRONG SELL"
            msg = (
                f"🔥 {direction_text}{emoji}\n"
                f"{display} {tf} – ICC Correction\n"
                f"Level: {icc['level']:.2f}\n"
                f"🕯️ Candle close (SAST): ⌚ {time_str}"
            )
            print(f"ALERT: {msg}")
            send_telegram(msg)

    # --- Double tops/bottoms ---
    dt_type, rejection_desc = detect_double_top_bottom_with_rejection(sym, tf, closed_bar)
    if dt_type and rejection_desc:
        now = datetime.now()
        cool_key = (sym, tf, dt_type)
        if cool_key not in last_double_signal or (now - last_double_signal[cool_key]).seconds > 600:
            last_double_signal[cool_key] = now
            if dt_type == "RESISTANCE":
                msg = (
                    f"🛡️ Strong Resistance📉\n"
                    f"{display} {tf} – Equal Highs / Double Top\n"
                    f"Rejection: {rejection_desc}\n"
                    f"🕯️ Candle close (SAST): ⌚ {time_str}"
                )
            else:
                msg = (
                    f"🛡️ Strong Support📈\n"
                    f"{display} {tf} – Equal Lows / Double Bottom\n"
                    f"Rejection: {rejection_desc}\n"
                    f"🕯️ Candle close (SAST): ⌚ {time_str}"
                )
            print(f"ALERT: {msg}")
            send_telegram(msg)

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
