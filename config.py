import os
from dotenv import load_dotenv
load_dotenv()
TELEGRAM_BOT_TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
GOLD_DATA_SYMBOL='TVC:GOLD'; GOLD_TV_SYMBOL='GOLD'; GOLD_TV_EXCHANGE='TVC'; TV_SYMBOL='TVC:GOLD'
# All three timeframes remain monitored; signal eligibility is controlled below.
TIMEFRAMES=['5m','15m','1h']
SCAN_SECONDS=int(os.getenv('SCAN_SECONDS','60')); MONITOR_SECONDS=int(os.getenv('MONITOR_SECONDS','15'))
RR=float(os.getenv('RR','2.0')); PIVOT_LEN=int(os.getenv('PIVOT_LEN','5')); DIVISOR=float(os.getenv('DIVISOR','3.6'))
ATR_PERIOD=int(os.getenv('ATR_PERIOD','14')); SL_ATR_MULT=float(os.getenv('SL_ATR_MULT','1.15'))
# Provisional thresholds are candidates for out-of-sample validation, not proof of profitability.
MIN_SCORE=float(os.getenv('MIN_SCORE','65')); MIN_AI_PROB=float(os.getenv('MIN_AI_PROB','0.50'))
MIN_SCORE_5M=float(os.getenv('MIN_SCORE_5M','60')); MIN_AI_PROB_5M=float(os.getenv('MIN_AI_PROB_5M','0.60'))
MIN_SCORE_15M=float(os.getenv('MIN_SCORE_15M','65')); MIN_AI_PROB_15M=float(os.getenv('MIN_AI_PROB_15M','0.45'))
MIN_SCORE_1H=float(os.getenv('MIN_SCORE_1H','60')); MIN_AI_PROB_1H=float(os.getenv('MIN_AI_PROB_1H','0.45'))
# Backtest-guided protective gates. Environment variables permit controlled rollback.
DISABLE_15M_SIGNALS=os.getenv('DISABLE_15M_SIGNALS','true').strip().lower() in ('1','true','yes','on')
DISABLE_1H_SELL_SIGNALS=os.getenv('DISABLE_1H_SELL_SIGNALS','true').strip().lower() in ('1','true','yes','on')
def signal_direction_allowed(tf, direction):
    """Shared live/backtest gate; keeps weak segments out pending more evidence."""
    if tf == '15m' and DISABLE_15M_SIGNALS:
        return False
    if tf == '1h' and direction == 'SELL' and DISABLE_1H_SELL_SIGNALS:
        return False
    return True

MODEL_REFRESH_HOURS=float(os.getenv('MODEL_REFRESH_HOURS','6')); DB_PATH=os.getenv('DB_PATH','data/gold_bot.sqlite3')
MODEL_HISTORY_BARS=int(os.getenv('MODEL_HISTORY_BARS','10000'))
BACKTEST_BARS=int(os.getenv('BACKTEST_BARS','5000'))
BACKTEST_BARS_1M=int(os.getenv('BACKTEST_BARS_1M',str(BACKTEST_BARS)))
BACKTEST_BARS_5M=int(os.getenv('BACKTEST_BARS_5M','10000'))
BACKTEST_BARS_15M=int(os.getenv('BACKTEST_BARS_15M',str(BACKTEST_BARS)))
BACKTEST_BARS_30M=int(os.getenv('BACKTEST_BARS_30M',str(BACKTEST_BARS)))
BACKTEST_BARS_1H=int(os.getenv('BACKTEST_BARS_1H','10000'))
BACKTEST_RETRAIN_EVERY=int(os.getenv('BACKTEST_RETRAIN_EVERY','500'))
def thresholds_for(tf):
    """Return the single source of truth for live and backtest acceptance thresholds."""
    if tf == '5m':
        return MIN_SCORE_5M, MIN_AI_PROB_5M
    if tf == '15m':
        return MIN_SCORE_15M, MIN_AI_PROB_15M
    if tf == '1h':
        return MIN_SCORE_1H, MIN_AI_PROB_1H
    return MIN_SCORE, MIN_AI_PROB

GRID_SCORES=(60,65,70,75)
GRID_AI_PROBS=(0.40,0.45,0.50,0.55,0.60)
SIGNAL_PENDING_TTL_BARS=int(os.getenv('SIGNAL_PENDING_TTL_BARS','12'))
SIGNAL_MAX_LIFETIME_BARS=int(os.getenv('SIGNAL_MAX_LIFETIME_BARS','48'))
