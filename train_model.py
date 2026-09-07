"""Optional offline model training helper. The production service also trains small rolling models in memory."""
from config import TIMEFRAMES, GOLD_DATA_SYMBOL, RR
from market import fetch, add_features
from ai_engine import GoldAI

ai=GoldAI()
for tf in TIMEFRAMES:
    df=fetch(GOLD_DATA_SYMBOL,tf)
    x=add_features(df).dropna()
    print(tf, 'rows=',len(x), 'trained=',ai.fit(tf,x,RR))
