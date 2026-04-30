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

# All Deriv symbols you need (exactly as in MT5 screenshots)
SYMBOLS = [
    "Volatility 10 Index",
    "Volatility 10 (1s) Index",
    "Volatility 25 Index",
    "Volatility 25 (1s) Index",
    "Volatility 50 Index",
    "Volatility 50 (1s) Index",
    "Volatility 75 Index",
    "Volatility 75 (1s) Index",
    "Volatility 100 Index",
    "Volatility 100 (1s) Index",
    "Step Index",
    "XAUUSD",
    "US30",
    "NAS100",
    "EURUSD",
    "BTCUSD",
]

TIMEFRAMES = ["M15", "M30", "H1"]
TIMEFRAME_MINUTES = {"M15": 15, "M30": 30, "H1": 60}

# Heartbeat interval (seconds)
HEARTBEAT_INTERVAL = 300   # 5 minutes

# ===========================================

# Candlestick storage per symbol and timeframe
candles = {}  # key: (symbol, tf) -> deque of {'time', 'open','high','low','close'}
current_bar = {}  # (symbol, tf) -> dict of current open bar

# Flask app for Render's required web service & keep‑alive ping
app = Flask(__name__)

@app.route('/ping')
def ping():
    return 'pong', 200

# --- Telegram helpers (now using urllib directly, synchronous) ---
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
    # round time to candle start (floor to multiple of minutes)
    bar_start = (time_sec // (minutes * 60)) * (minutes * 60)
    bar_dt = datetime.fromtimestamp(bar_start)

    cur = current_bar[key]
    # new bar needed?
    if cur is None or cur["time"] != bar_dt:
        # finalise old bar if exists
        if cur is not None:
            candles[key].append(cur)
            # after finalising, run pattern detection
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
    """
    Return list of swing highs and swing lows (fractals) from bars.
    Each swing: {'price': float, 'time': datetime, 'type': 'high'/'low'}
    """
    if len(bars) < 5:
        return [], []

    highs = []
    lows = []
    # use index from latest to older
    for i in range(2, len(bars)-2):
        high_i = bars[i]["high"]
        low_i = bars[i]["low"]
        high_prev1 = bars[i-1]["high"]
        high_next1 = bars[i+1]["high"]
        high_prev2 = bars[i-2]["high"]
        high_next2 = bars[i+2]["high"]
        low_prev1 = bars[i-1]["low"]
        low_next1 = bars[i+1]["low"]
        low_prev2 = bars[i-2]["low"]
        low_next2 = bars[i+2]["low"]

        # swing high fractal
        if high_i > high_prev1 and high_i > high_next1 and high_i > high_prev2 and high_i > high_next2:
            highs.append({"price": high_i, "time": bars[i]["time"], "type": "high"})
        # swing low fractal
        if low_i < low_prev1 and low_i < low_next1 and low_i < low_prev2 and low_i < low_next2:
            lows.append({"price": low_i, "time": bars[i]["time"], "type": "low"})

    return highs, lows

def detect_icc(sym, tf):
    """
    Detect ICC (Indication,Correction,Continuation) for given symbol/timeframe.
    Returns None or dict with direction ('BUY'/'SELL') and details.
    """
    key = (sym, tf)
    bars_list = list(candles[key])
    if len(bars_list) < 20:
        return None
    swings_high, swings_low = get_recent_swings(bars_list)
    if len(swings_high) < 2 or len(swings_low) < 2:
        return None

    if len(swings_high) >= 2 and len(swings_low) >= 2:
        # bearish ICC check
        sh1 = swings_high[-2]
        sh2 = swings_high[-1]
        sl1 = swings_low[-2]
        sl2 = swings_low[-1]

        # uptrend: SH2 > SH1 and SL2 > SL1
        if sh2["price"] > sh1["price"] and sl2["price"] > sl1["price"]:
            for i in range(len(bars_list)-1, -1, -1):
                if bars_list[i]["close"] < sl2["price"]:
                    tolerance = 2 * get_symbol_tick(sym)
                    for j in range(i+1, len(bars_list)):
                        if abs(bars_list[j]["close"] - sl2["price"]) <= tolerance:
                            correction_low = min(bars_list[k]["low"] for k in range(i, j+1))
                            for m in range(j+1, len(bars_list)):
                                if bars_list[m]["close"] < correction_low:
                                    return {"direction": "SELL", "tf": tf, "detail": f"Bearish ICC: break of HL {sl2['price']:.2f}, retest, continuation down"}
                            break
                    break

        # bullish ICC check (downtrend: lower highs, lower lows)
        if sh2["price"] < sh1["price"] and sl2["price"] < sl1["price"]:
            for i in range(len(bars_list)-1, -1, -1):
                if bars_list[i]["close"] > sh2["price"]:
                    tolerance = 2 * get_symbol_tick(sym)
                    for j in range(i+1, len(bars_list)):
                        if abs(bars_list[j]["close"] - sh2["price"]) <= tolerance:
                            correction_high = max(bars_list[k]["high"] for k in range(i, j+1))
                            for m in range(j+1, len(bars_list)):
                                if bars_list[m]["close"] > correction_high:
                                    return {"direction": "BUY", "tf": tf, "detail": f"Bullish ICC: break of LH {sh2['price']:.2f}, retest, continuation up"}
                            break
                    break
    return None

def detect_double_top_bottom(sym, tf):
    """
    Simple double top/bottom detection on recent bars.
    Returns "RESISTANCE" or "SUPPORT" or None.
    """
    key = (sym, tf)
    bars = list(candles[key])
    if len(bars) < 20:
        return None
    swings_high, swings_low = get_recent_swings(bars, lookback=50)
    if len(swings_high) >= 2:
        last2_highs = swings_high[-2:]
        if len(last2_highs) == 2:
            h1, h2 = last2_highs
            if abs(h1["price"] - h2["price"]) / max(h1["price"], 1) < 0.002:
                idx1 = next(i for i, b in enumerate(bars) if b["time"] == h1["time"])
                idx2 = next(i for i, b in enumerate(bars) if b["time"] == h2["time"])
                if abs(idx2 - idx1) >= 3:
                    return "RESISTANCE"
    if len(swings_low) >= 2:
        last2_lows = swings_low[-2:]
        if len(last2_lows) == 2:
            l1, l2 = last2_lows
            if abs(l1["price"] - l2["price"]) / max(l1["price"], 1) < 0.002:
                idx1 = next(i for i, b in enumerate(bars) if b["time"] == l1["time"])
                idx2 = next(i for i, b in enumerate(bars) if b["time"] == l2["time"])
                if abs(idx2 - idx1) >= 3:
                    return "SUPPORT"
    return None

def get_symbol_tick(sym):
    if "Volatility" in sym:
        return 0.001
    elif sym in ["XAUUSD", "BTCUSD"]:
        return 0.1
    elif sym in ["US30", "NAS100"]:
        return 1.0
    else:
        return 0.0001

last_icc_signal = {}  # (sym, direction) -> datetime

def detect_patterns(sym, tf, closed_bar):
    icc = detect_icc(sym, tf)
    if icc:
        now = datetime.now()
        sig_key = (sym, icc["direction"])
        if sig_key not in last_icc_signal or (now - last_icc_signal[sig_key]).seconds > 600:
            last_icc_signal[sig_key] = now
            msg = f"🔥 {'STRONG BUY' if icc['direction'] == 'BUY' else 'STRONG SELL'}\n{sym} {tf}"
            send_telegram(msg)

    dtb = detect_double_top_bottom(sym, tf)
    if dtb == "RESISTANCE":
        send_telegram(f"🛡️ Strong Resistance\n{sym} {tf} – Double Top")
    elif dtb == "SUPPORT":
        send_telegram(f"🛡️ Strong Support\n{sym} {tf} – Double Bottom")

# --- Deriv WebSocket tick listener ---
def on_message(ws, message):
    data = json.loads(message)
    if "tick" in data:
        tick = data["tick"]
        sym = tick["symbol"]
        price = tick["quote"]
        epoch = tick["epoch"]
        for tf in TIMEFRAMES:
            update_bar(sym, tf, epoch, price)

def on_error(ws, error):
    print(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed, reconnecting in 5s...")
    time.sleep(5)
    start_websocket()

def on_open(ws):
    for sym in SYMBOLS:
        ws.send(json.dumps({"ticks": sym, "subscribe": 1}))

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
