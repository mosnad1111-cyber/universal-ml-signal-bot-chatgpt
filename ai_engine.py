import json
import os
import time
import warnings
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, accuracy_score

warnings.filterwarnings('ignore')

BASE_FEATURES = [
    'ema9','ema20','ema50','ema200','atr_pct','rsi','macd','macd_signal','macd_hist',
    'ret1','ret3','ret6','range','body','upper_wick','lower_wick','vol_ratio',
    'dist_ema20','dist_ema50','dist_ema200','atr_rank','trend_strength',
    'breakout_up','breakout_dn','dist_high20_atr','dist_low20_atr'
]
CONTEXT_FEATURES = [
    'ctx15_trend','ctx15_rsi','ctx15_macd_hist','ctx15_dist_ema20',
    'ctx1h_trend','ctx1h_rsi','ctx1h_macd_hist','ctx1h_dist_ema20'
]
FEATURES = BASE_FEATURES + CONTEXT_FEATURES + ['direction']


def _label_direction(x, direction, horizon, rr=2.0):
    y = np.full(len(x), np.nan)
    close = x['Close'].to_numpy(); high=x['High'].to_numpy(); low=x['Low'].to_numpy(); atr=x['atr'].to_numpy()
    for i in range(max(0, len(x)-horizon-1)):
        if not np.isfinite(atr[i]) or atr[i] <= 0: continue
        risk = float(atr[i])
        if direction == 1:
            target, stop = close[i] + rr*risk, close[i] - risk
        else:
            target, stop = close[i] - rr*risk, close[i] + risk
        outcome = None
        for j in range(i+1, min(len(x), i+horizon+1)):
            hit_target = high[j] >= target if direction == 1 else low[j] <= target
            hit_stop = low[j] <= stop if direction == 1 else high[j] >= stop
            if hit_target and hit_stop:
                outcome = 0
                break
            if hit_target: outcome = 1; break
            if hit_stop: outcome = 0; break
        if outcome is not None: y[i] = outcome
    return pd.Series(y, index=x.index)


class GoldAI:
    def __init__(self):
        self.models = {}
        self.metrics = {}
        self.trained_at = {}

    def _fit_model(self, X, y):
        split = int(len(X) * 0.80)
        if split < 300 or len(X)-split < 80:
            return None, {}
        Xtr, Xv = X.iloc[:split], X.iloc[split:]
        ytr, yv = y.iloc[:split], y.iloc[split:]
        if ytr.nunique() < 2 or yv.nunique() < 2:
            return None, {}
        try:
            from xgboost import XGBClassifier
            model = XGBClassifier(
                n_estimators=350, max_depth=4, learning_rate=0.035,
                subsample=0.85, colsample_bytree=0.85,
                min_child_weight=6, reg_alpha=0.15, reg_lambda=2.0,
                objective='binary:logistic', eval_metric='logloss',
                tree_method='hist', random_state=42, n_jobs=1
            )
            model.fit(Xtr, ytr, eval_set=[(Xv, yv)], verbose=False)
            engine = 'XGBoost'
        except Exception:
            model = HistGradientBoostingClassifier(
                max_iter=250, learning_rate=0.045, max_leaf_nodes=15,
                l2_regularization=1.0, random_state=42
            )
            model.fit(Xtr, ytr)
            engine = 'HistGradientBoosting'
        pv = model.predict_proba(Xv)[:,1]
        auc = float(roc_auc_score(yv, pv)) if len(np.unique(yv)) == 2 else 0.5
        acc = float(accuracy_score(yv, (pv >= 0.5).astype(int)))
        return model, {'engine':engine,'validation_rows':len(yv),'auc':auc,'accuracy':acc,'positive_rate':float(y.mean())}

    def fit(self, timeframe, raw_df, rr=2.0, feedback=None):
        x = raw_df.copy()
        frames=[]
        for direction in (1,0):
            y = _label_direction(x, direction, horizon=36 if timeframe=='5m' else (24 if timeframe=='15m' else 18), rr=rr)
            d = x[FEATURES[:-1]].copy()
            d['direction'] = direction
            mask = y.notna() & d.notna().all(axis=1)
            d = d.loc[mask]; target=y.loc[mask].astype(int)
            frames.append((d,target))
        if not frames: return False
        parts=[]
        for a,b in frames:
            z=a.copy(); z['_label']=b.values; parts.append(z)
        combo=pd.concat(parts,axis=0).sort_index()
        combo=combo.dropna(subset=['_label'])
        X=combo[FEATURES].copy(); y=combo['_label'].astype(int)
        if len(X) < 600 or y.nunique() < 2: return False
        X,y = X.tail(10000),y.tail(10000)
        model, metrics = self._fit_model(X,y)
        if model is None: return False
        self.models[timeframe]=model
        self.metrics[timeframe]=metrics
        self.trained_at[timeframe]=time.time()
        return True

    def probability(self, timeframe, row, direction):
        model=self.models.get(timeframe)
        if model is None: return None
        vals={k: float(row.get(k, np.nan)) for k in FEATURES[:-1]}
        vals['direction']=1 if direction=='BUY' else 0
        X=pd.DataFrame([vals], columns=FEATURES)
        if X.isna().any().any(): return None
        try: return float(model.predict_proba(X)[0,1])
        except Exception: return None

    def info(self):
        return self.metrics.copy()
