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
    close = x['Close'].to_numpy(); high = x['High'].to_numpy(); low = x['Low'].to_numpy(); atr = x['atr'].to_numpy()
    for i in range(max(0, len(x) - horizon - 1)):
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
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
                outcome = 0; break
            if hit_target:
                outcome = 1; break
            if hit_stop:
                outcome = 0; break
        if outcome is not None:
            y[i] = outcome
    return pd.Series(y, index=x.index)


class GoldAI:
    def __init__(self):
        self.models = {}; self.metrics = {}; self.trained_at = {}; self.errors = {}

    def fit(self, timeframe, raw_df, rr=2.0, feedback=None):
        x = raw_df.copy()
        horizons = {'5m': 36, '15m': 24, '1h': 18}
        labels = {d: _label_direction(x, d, horizons.get(timeframe, 24), rr) for d in (1, 0)}
        base = x[FEATURES[:-1]].copy()
        valid = base.notna().all(axis=1) & labels[1].notna() & labels[0].notna()
        base = base.loc[valid]
        if len(base) < 300:
            self.errors[timeframe] = f'insufficient_training_rows:{len(base)}'; return False
        split = int(len(base) * 0.80)
        train_idx, val_idx = base.index[:split], base.index[split:]
        if len(train_idx) < 300 or len(val_idx) < 40:
            self.errors[timeframe] = f'insufficient_split:{len(train_idx)}/{len(val_idx)}'; return False
        train_parts=[]; val_parts=[]
        for direction in (1, 0):
            d=base.loc[train_idx].copy(); d['direction']=direction; d['_label']=labels[direction].loc[train_idx].astype(int).values; train_parts.append(d)
            d=base.loc[val_idx].copy(); d['direction']=direction; d['_label']=labels[direction].loc[val_idx].astype(int).values; val_parts.append(d)
        train=pd.concat(train_parts).sort_index(kind='stable'); val=pd.concat(val_parts).sort_index(kind='stable')
        Xtr=train[FEATURES]; ytr=train['_label']; Xv=val[FEATURES]; yv=val['_label']
        if len(Xtr)<600 or ytr.nunique()<2 or yv.nunique()<2:
            self.errors[timeframe]='invalid_class_distribution'; return False
        try:
            from xgboost import XGBClassifier
            model=XGBClassifier(n_estimators=350,max_depth=4,learning_rate=0.035,subsample=0.85,colsample_bytree=0.85,min_child_weight=6,reg_alpha=0.15,reg_lambda=2.0,objective='binary:logistic',eval_metric='logloss',tree_method='hist',random_state=42,n_jobs=1)
            model.fit(Xtr,ytr,eval_set=[(Xv,yv)],verbose=False); engine='XGBoost'
        except Exception as exc:
            self.errors[timeframe]=f'XGBoost: {type(exc).__name__}: {exc}'
            model=HistGradientBoostingClassifier(max_iter=250,learning_rate=0.045,max_leaf_nodes=15,l2_regularization=1.0,random_state=42)
            model.fit(Xtr,ytr); engine='HistGradientBoosting'
        pv=model.predict_proba(Xv)[:,1]
        self.models[timeframe]=model; self.metrics[timeframe]={
            'engine':engine,'training_rows':int(len(Xtr)),'validation_rows':int(len(Xv)),
            'training_candles':int(len(train_idx)),'validation_candles':int(len(val_idx)),
            'auc':float(roc_auc_score(yv,pv)) if yv.nunique()==2 else 0.5,
            'accuracy':float(accuracy_score(yv,(pv>=0.5).astype(int))),
            'positive_rate':float(ytr.mean()),'validation_positive_rate':float(yv.mean())}
        self.trained_at[timeframe]=time.time(); self.errors.pop(timeframe,None); return True

    def probability(self, timeframe, row, direction):
        model=self.models.get(timeframe)
        if model is None: return None
        vals={k:float(row.get(k,np.nan)) for k in FEATURES[:-1]}; vals['direction']=1 if direction=='BUY' else 0
        X=pd.DataFrame([vals],columns=FEATURES)
        if X.isna().any().any(): return None
        try: return float(model.predict_proba(X)[0,1])
        except Exception: return None

    def info(self): return self.metrics.copy()
