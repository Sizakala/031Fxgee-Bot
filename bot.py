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
    now_sast = datetime.utcnow() + timedelta(hours=2)
    time_str = now_sast.strftime("%H:%M:%S %d/%m/%Y")
    send_telegram(f"💓 031FxGee bot heartbeat — ⏱️ {time_str}")

def init_candle_store():
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            candles[(sym, tf)] = deque(maxlen=500)
            current_bar[(sym, tf)] = None

def update_bar(sym, tf, time_sec, price):
    key = (sym, tf)
    minutes = TIMEFRAME_MINUTES[tf]
    bar_start = (time_sec // (minutes * 60)) * (minutes * 60)
    bar_dt = datetime.utcfromtimestamp(bar_start)
    cur = current_bar[key]
    if cur is None or cur["time"] != bar_dt:
        if cur is not None:
            candles[key].append(cur)
            detect_patterns(sym, tf, cur)
        current_bar[key] = {"time": bar_dt, "open": price, "high": price, "low": price, "close": price, "volume": 1, "range": 0}
    else:
        cur["high"] = max(cur["high"], price)
        cur["low"] = min(cur["low"], price)
        cur["close"] = price
        cur["volume"] += 1
        cur["range"] = cur["high"] - cur["low"]

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

def is_synthetic(sym):
    return sym.startswith("R_") or sym.startswith("1HZ") or sym == "STP"

def check_rejection_strength(bar):
    open_p = bar["open"]
    close_p = bar["close"]
    high_p = bar["high"]
    low_p = bar["low"]
    body = abs(close_p - open_p)
    total_range = high_p - low_p
    if total_range == 0:
        return False, None
    upper_wick = high_p - max(open_p, close_p)
    lower_wick = min(open_p, close_p) - low_p
    if upper_wick >= 3 * body and body > 0 and lower_wick < body:
        return True, "Strong Bearish Spike 💀"
    if lower_wick >= 3 * body and body > 0 and upper_wick < body:
        return True, "Strong Bullish Spike 💀"
    return False, None

# --- REVISED MOMENTUM DIVERGENCE ---
def check_momentum_divergence(bar1, bar2):
    """
    bar1 = first touch bar, bar2 = second touch bar.
    Returns True if momentum is unchanged or diverging (second bar range <= first bar range * 1.005).
    This allows slightly higher but still effectively equal or reduced momentum.
    """
    range1 = bar1.get("range", 0)
    range2 = bar2.get("range", 0)
    if range1 == 0:
        return True
    # Allow up to 0.5% larger range on the second touch (effectively equal)
    return range2 <= range1 * 1.005

def detect_double_pattern_with_confluence(sym, tf, closed_bar):
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

    # ---- GOLD: instant on zone touch ----
    if sym == "frxXAUUSD":
        for j in range(last_idx - min_separation, max(last_idx - max_lookback - 1, -1), -1):
            if abs(last_high - bars[j]["high"]) / max(last_high, 1) < tolerance_pct:
                return "RESISTANCE", "Zone touched 🥇"
            if abs(last_low - bars[j]["low"]) / max(last_low, 1) < tolerance_pct:
                return "SUPPORT", "Zone touched 🥇"
        return None, None

    # ---- SYNTHETICS ----
    if is_synthetic(sym):
        for j in range(last_idx - min_separation, max(last_idx - max_lookback - 1, -1), -1):
            if abs(last_high - bars[j]["high"]) / max(last_high, 1) < tolerance_pct:
                strong, rejection_type = check_rejection_strength(last_bar)
                if not strong:
                    return None, None
                # Revised momentum: second range ≤ first * 1.005
                if not check_momentum_divergence(bars[j], last_bar):
                    return None, None
                if tf in ("M15", "M30"):
                    if not check_h1_confluence(sym, last_high, "RESISTANCE"):
                        return None, None
                return "RESISTANCE", rejection_type

        for j in range(last_idx - min_separation, max(last_idx - max_lookback - 1, -1), -1):
            if abs(last_low - bars[j]["low"]) / max(last_low, 1) < tolerance_pct:
                strong, rejection_type = check_rejection_strength(last_bar)
                if not strong:
                    return None, None
                if not check_momentum_divergence(bars[j], last_bar):
                    return None, None
                if tf in ("M15", "M30"):
                    if not check_h1_confluence(sym, last_low, "SUPPORT"):
                        return None, None
                return "SUPPORT", rejection_type
        return None, None

    # ---- Non-synthetic non-gold ----
    prev_bar = bars[last_idx - 1] if last_idx >= 1 else None
    for j in range(last_idx - min_separation, max(last_idx - max_lookback - 1, -1), -1):
        if abs(last_high - bars[j]["high"]) / max(last_high, 1) < tolerance_pct:
            rejection = check_rejection_standard(last_bar, prev_bar)
            if rejection:
                return "RESISTANCE", rejection
            return None, None
        if abs(last_low - bars[j]["low"]) / max(last_low, 1) < tolerance_pct:
            rejection = check_rejection_standard(last_bar, prev_bar)
            if rejection:
                return "SUPPORT", rejection
            return None, None
    return None, None

def check_rejection_standard(bar, prev_bar):
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

def check_h1_confluence(sym, zone_price, pattern_type):
    h1_bars = list(candles.get((sym, "H1"), []))
    if len(h1_bars) < 5:
        return False
    zone_tolerance = 0.003
    if pattern_type == "RESISTANCE":
        for i in range(len(h1_bars)-1, max(len(h1_bars)-150, -1), -1):
            if abs(h1_bars[i]["high"] - zone_price) / max(zone_price, 1) < zone_tolerance:
                for j in range(i-2, max(i-150, -1), -1):
                    if abs(h1_bars[j]["high"] - h1_bars[i]["high"]) / max(h1_bars[i]["high"], 1) < 0.005:
                        return True
    else:
        for i in range(len(h1_bars)-1, max(len(h1_bars)-150, -1), -1):
            if abs(h1_bars[i]["low"] - zone_price) / max(zone_price, 1) < zone_tolerance:
                for j in range(i-2, max(i-150, -1), -1):
                    if abs(h1_bars[j]["low"] - h1_bars[i]["low"]) / max(h1_bars[i]["low"], 1) < 0.005:
                        return True
    return False

def get_symbol_tick(sym):
    if "R_" in sym or "1HZ" in sym or sym == "STP":
        return 0.001
    elif sym in ["frxXAUUSD", "frxBTCUSD"]:
        return 0.1
    elif sym in ["frxUS30", "frxNAS100"]:
        return 1.0
    else:
        return 0.0001

icc_lock = threading.Lock()
double_lock = threading.Lock()
last_icc_signal = {}
last_double_signal = {}

def detect_patterns(sym, tf, closed_bar):
    display = DISPLAY_NAME.get(sym, sym)
    bar_time_sast = closed_bar["time"] + timedelta(hours=2)
    candle_str = bar_time_sast.strftime("%H:%M:%S %d/%m/%Y")
    now_sast = datetime.utcnow() + timedelta(hours=2)
    sent_str = now_sast.strftime("%H:%M:%S")

    # --- ICC ---
    icc = detect_icc(sym, tf)
    if icc:
        with icc_lock:
            sig_key = (sym, icc["direction"])
            now = datetime.utcnow()
            if sig_key not in last_icc_signal or (now - last_icc_signal[sig_key]).seconds > 600:
                last_icc_signal[sig_key] = now
        if icc["direction"] == "BUY":
            direction_text = "STRONG BUY"
            emoji = "📈"
        else:
            direction_text = "STRONG SELL"
            emoji = "📉"
        msg = (
            f"🔥 {direction_text}{emoji}\n"
            f"{display} {tf} – ICC Correction\n"
            f"Level: {icc['level']:.2f}\n"
            f"🕯️ Candle close (SAST): {candle_str}\n"
            f"⏱️ Signal sent (SAST): {sent_str}"
        )
        print(f"ALERT: {msg}")
        send_telegram(msg)

    # --- Double tops/bottoms ---
    dt_type, rejection_desc = detect_double_pattern_with_confluence(sym, tf, closed_bar)
    if dt_type and rejection_desc:
        with double_lock:
            cool_key = (sym, tf, dt_type)
            now = datetime.utcnow()
            if cool_key not in last_double_signal or (now - last_double_signal[cool_key]).seconds > 600:
                last_double_signal[cool_key] = now
        if dt_type == "RESISTANCE":
            msg = (
                f"🛡️ Strong Resistance📉\n"
                f"{display} {tf} – Equal Highs / Double Top\n"
                f"Rejection: {rejection_desc}\n"
                f"🕯️ Candle close (SAST): {candle_str}\n"
                f"⏱️ Signal sent (SAST): {sent_str}"
            )
        else:
            msg = (
                f"🛡️ Strong Support📈\n"
                f"{display} {tf} – Equal Lows / Double Bottom\n"
                f"Rejection: {rejection_desc}\n"
                f"🕯️ Candle close (SAST): {candle_str}\n"
                f"⏱️ Signal sent (SAST): {sent_str}"
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

def websocket_keepalive(ws):
    while True:
        try:
            ws.send(json.dumps({"ping": 1}))
        except:
            pass
        time.sleep(120)

def start_websocket():
    ws = websocket.WebSocketApp("wss://ws.derivws.com/websockets/v3?app_id=1089",
        on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
    def on_open_with_keepalive(ws):
        on_open(ws)
        threading.Thread(target=websocket_keepalive, args=(ws,), daemon=True).start()
    ws.on_open = on_open_with_keepalive
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
