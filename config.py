import os
from dotenv import load_dotenv
load_dotenv()
TELEGRAM_BOT_TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
GOLD_DATA_SYMBOL='TVC:GOLD'; GOLD_TV_SYMBOL='GOLD'; GOLD_TV_EXCHANGE='TVC'; TV_SYMBOL='TVC:GOLD'
TIMEFRAMES=[x.strip() for x in os.getenv('TIMEFRAMES','5m,15m,1h').split(',') if x.strip()]
SCAN_SECONDS=int(os.getenv('SCAN_SECONDS','60')); MONITOR_SECONDS=int(os.getenv('MONITOR_SECONDS','15'))
RR=float(os.getenv('RR','2.0')); PIVOT_LEN=int(os.getenv('PIVOT_LEN','5')); DIVISOR=float(os.getenv('DIVISOR','3.6'))
ATR_PERIOD=int(os.getenv('ATR_PERIOD','14')); SL_ATR_MULT=float(os.getenv('SL_ATR_MULT','1.15'))
MIN_SCORE=float(os.getenv('MIN_SCORE','65')); MIN_AI_PROB=float(os.getenv('MIN_AI_PROB','0.58'))
MODEL_REFRESH_HOURS=float(os.getenv('MODEL_REFRESH_HOURS','6')); DB_PATH=os.getenv('DB_PATH','data/gold_bot.sqlite3')
MODEL_HISTORY_BARS=int(os.getenv('MODEL_HISTORY_BARS','5000'))
