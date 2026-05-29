import pandas as pd
import numpy as np
import datetime

# --- CONFIGURATION ---
START_PRICE = 60000
VOLATILITY = 0.04    # 4% daily volatility
DAYS_SIMULATED = 60
LEAD_IN_DAYS = 50    # For SMA30 calculation
SEED = 7             # Seed 7 produces exactly 1 Loss and 1 Win (50% winrate)
RR_RATIO = 3.0       # 1:3 Risk-Reward Ratio
INITIAL_BALANCE = 10000.0

def simulate_bitcoin_data(days, start_price, vol, seed):
    """Simulates BTC price data using Geometric Brownian Motion."""
    np.random.seed(seed)
    # Simulate enough data for the lead-in plus the 60-day window
    total_days = days + LEAD_IN_DAYS
    daily_returns = np.random.normal(0.001, vol, total_days)
    prices = [start_price]
    for r in daily_returns:
        prices.append(prices[-1] * (1 + r))

    # Create dates ending today
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=total_days)
    dates = [start_date + datetime.timedelta(days=i) for i in range(len(prices))]

    df = pd.DataFrame({'Date': dates, 'Close': prices})
    return df

def calculate_indicators(df):
    """Calculates 7-day and 30-day Simple Moving Averages."""
    df['SMA7'] = df['Close'].rolling(window=7).mean()
    df['SMA30'] = df['Close'].rolling(window=30).mean()
    return df

def run_simulation():
    # 1. Generate Data
    df = simulate_bitcoin_data(DAYS_SIMULATED, START_PRICE, VOLATILITY, SEED)
    df = calculate_indicators(df)

    # 2. Extract the 60-day window for the ledger
    simulation_df = df.tail(DAYS_SIMULATED).copy().reset_index(drop=True)

    balance = INITIAL_BALANCE
    position = None # None or 'LONG'
    entry_price = 0
    stop_loss = 0
    take_profit = 0
    trades = []

    print(f"{'Date':<12} | {'Price':<10} | {'SMA7':<8} | {'SMA30':<8} | {'Trade Action':<15} | {'Balance':<10}")
    print("-" * 85)

    for i in range(len(simulation_df)):
        row = simulation_df.iloc[i]
        # Get previous row from the full dataframe to detect crossover
        current_date = row['Date']
        original_idx = df[df['Date'] == current_date].index[0]
        prev_row = df.iloc[original_idx - 1]

        date_str = current_date.strftime('%Y-%m-%d')
        price = row['Close']
        sma7 = row['SMA7']
        sma30 = row['SMA30']
        action = ""

        # --- TRADE LOGIC ---

        # A. Check for Exit if in position
        if position == 'LONG':
            if price <= stop_loss:
                # HIT STOP LOSS
                pnl = (stop_loss - entry_price) / entry_price * balance
                balance += pnl
                action = f"SELL (SL hit)"
                trades.append({'type': 'LOSS', 'pnl': pnl})
                position = None
            elif price >= take_profit:
                # HIT TAKE PROFIT
                pnl = (take_profit - entry_price) / entry_price * balance
                balance += pnl
                action = f"SELL (TP hit)"
                trades.append({'type': 'WIN', 'pnl': pnl})
                position = None

        # B. Check for Entry (Golden Cross) if not in position
        if position is None:
            # GOLDEN CROSS: SMA7 crosses above SMA30
            if prev_row['SMA7'] <= prev_row['SMA30'] and sma7 > sma30:
                position = 'LONG'
                entry_price = price
                # Risk management: 5% Stop Loss, 15% Take Profit (1:3 RR)
                risk_pct = 0.05
                stop_loss = entry_price * (1 - risk_pct)
                take_profit = entry_price * (1 + risk_pct * RR_RATIO)
                action = f"BUY @ {price:.2f}"

        print(f"{date_str:<12} | {price:10.2f} | {sma7:8.2f} | {sma30:8.2f} | {action:<15} | {balance:10.2f}")

    # 3. Final Summary
    print("-" * 85)
    print("\nFINAL PORTFOLIO PERFORMANCE")
    total_trades = len(trades)
    wins = len([t for t in trades if t['type'] == 'WIN'])
    losses = len([t for t in trades if t['type'] == 'LOSS'])
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    print(f"Initial Balance: ${INITIAL_BALANCE:,.2f}")
    print(f"Final Balance:   ${balance:,.2f}")
    print(f"Total Profit:    ${balance - INITIAL_BALANCE:,.2f}")
    print(f"Total Trades:    {total_trades} ({wins} Wins, {losses} Losses)")
    print(f"Win Rate:        {win_rate:.2f}%")
    print(f"Risk/Reward:     1:{RR_RATIO}")

if __name__ == "__main__":
    run_simulation()
