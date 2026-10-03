import os
from dotenv import load_dotenv
load_dotenv()
TELEGRAM_BOT_TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
GOLD_DATA_SYMBOL='TVC:GOLD'; GOLD_TV_SYMBOL='GOLD'; GOLD_TV_EXCHANGE='TVC'; TV_SYMBOL='TVC:GOLD'
# Production/backtest timeframes: 5m and 1h only.
TIMEFRAMES=['5m','1h']
SCAN_SECONDS=int(os.getenv('SCAN_SECONDS','60')); MONITOR_SECONDS=int(os.getenv('MONITOR_SECONDS','15'))
RR=float(os.getenv('RR','2.0')); PIVOT_LEN=int(os.getenv('PIVOT_LEN','5')); DIVISOR=float(os.getenv('DIVISOR','3.6'))
ATR_PERIOD=int(os.getenv('ATR_PERIOD','14')); SL_ATR_MULT=float(os.getenv('SL_ATR_MULT','1.15'))
# Per-timeframe test thresholds.
MIN_SCORE=float(os.getenv('MIN_SCORE','65')); MIN_AI_PROB=float(os.getenv('MIN_AI_PROB','0.50'))
MIN_SCORE_5M=float(os.getenv('MIN_SCORE_5M','60')); MIN_AI_PROB_5M=float(os.getenv('MIN_AI_PROB_5M','0.45'))
MIN_SCORE_1H=float(os.getenv('MIN_SCORE_1H','65')); MIN_AI_PROB_1H=float(os.getenv('MIN_AI_PROB_1H','0.40'))
MODEL_REFRESH_HOURS=float(os.getenv('MODEL_REFRESH_HOURS','6')); DB_PATH=os.getenv('DB_PATH','data/gold_bot.sqlite3')
# Keep live model training on the same requested history depth as the extended backtest.
# TradingView may return fewer bars than requested; the bot uses whatever is actually available.
MODEL_HISTORY_BARS=int(os.getenv('MODEL_HISTORY_BARS','10000'))
# Historical validation controls.
BACKTEST_BARS=int(os.getenv('BACKTEST_BARS','5000'))
BACKTEST_BARS_1M=int(os.getenv('BACKTEST_BARS_1M',str(BACKTEST_BARS)))
BACKTEST_BARS_5M=int(os.getenv('BACKTEST_BARS_5M','10000'))
BACKTEST_BARS_15M=int(os.getenv('BACKTEST_BARS_15M',str(BACKTEST_BARS)))
BACKTEST_BARS_30M=int(os.getenv('BACKTEST_BARS_30M',str(BACKTEST_BARS)))
BACKTEST_BARS_1H=int(os.getenv('BACKTEST_BARS_1H','10000'))
BACKTEST_RETRAIN_EVERY=int(os.getenv('BACKTEST_RETRAIN_EVERY','500'))
# Grid search: threshold-only robustness test on the same walk-forward history.
GRID_SCORES=(60,65,70,75)
GRID_AI_PROBS=(0.40,0.45,0.50,0.55,0.60)
