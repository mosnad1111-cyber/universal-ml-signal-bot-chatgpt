import time
import warnings
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, accuracy_score
from config import SIGNAL_LABEL_HORIZON_BARS

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


def _label_direction(x, direction, horizon, rr=2.0, divisor=3.6, sl_atr_mult=1.15, strategy='baseline', timeframe=None):
    """Label the outcome of the same entry/SL/TP geometry used by live build_setup.

    The label is attached to the signal candle. Entry activation and exits are
    evaluated only on subsequent candles, matching the live monitor/backtest.
    """
    y = np.full(len(x), np.nan)
    close = x['Close'].to_numpy(dtype=float)
    high = x['High'].to_numpy(dtype=float)
    low = x['Low'].to_numpy(dtype=float)
    atr = x['atr'].to_numpy(dtype=float)
    high20 = x['high20'].to_numpy(dtype=float)
    low20 = x['low20'].to_numpy(dtype=float)

    if not np.isfinite(divisor) or divisor <= 0 or not np.isfinite(sl_atr_mult) or sl_atr_mult <= 0:
        raise ValueError("divisor and sl_atr_mult must be positive finite numbers")

    for i in range(max(0, len(x) - horizon)):
        if not all(np.isfinite(v) for v in (close[i], atr[i], high20[i], low20[i])) or atr[i] <= 0:
            continue

        span = max(high20[i] - low20[i], atr[i])
        # Baseline geometry does not depend on these indicators; default them only
        # for lightweight callers/tests that provide OHLC plus ATR/range columns.
        ema20 = float(x['ema20'].iloc[i]) if 'ema20' in x else float(close[i])
        ema50 = float(x['ema50'].iloc[i]) if 'ema50' in x else float(close[i])
        ema200 = float(x['ema200'].iloc[i]) if 'ema200' in x else float(close[i])
        rsi = float(x['rsi'].iloc[i]) if 'rsi' in x else 50.0
        macd_hist = float(x['macd_hist'].iloc[i]) if 'macd_hist' in x else 0.0
        is_buy = direction == 1
        # Match the higher-timeframe direction filter used by live/backtest setup selection.
        ctx15 = float(x['ctx15_trend'].iloc[i]) if 'ctx15_trend' in x else 0.0
        ctx1h = float(x['ctx1h_trend'].iloc[i]) if 'ctx1h_trend' in x else 0.0
        context_trend = 1 if timeframe == '5m' and ctx15 > 0 and ctx1h > 0 else (
            -1 if timeframe == '5m' and ctx15 < 0 and ctx1h < 0 else (
                ctx1h if timeframe == '15m' else 0
            )
        )
        # Preserve baseline training exactly for a fair comparison with the live model.
        # The new strategy hypotheses incorporate this filter in both labels and setup selection.
        if strategy != 'baseline':
            if context_trend > 0 and not is_buy:
                continue
            if context_trend < 0 and is_buy:
                continue
        if strategy == 'baseline':
            if is_buy:
                entry = max(close[i], low20[i] + span / divisor)
                stop = min(low20[i] - 0.10 * atr[i], entry - sl_atr_mult * atr[i])
            else:
                entry = min(close[i], high20[i] - span / divisor)
                stop = max(high20[i] + 0.10 * atr[i], entry + sl_atr_mult * atr[i])
        elif strategy == 'trend_pullback':
            aligned = (ema20 > ema50 > ema200) if is_buy else (ema20 < ema50 < ema200)
            rsi_ok = (45 <= rsi <= 62) if is_buy else (38 <= rsi <= 55)
            if not aligned or not rsi_ok or abs(close[i] - ema20) > 0.75 * atr[i]:
                continue
            entry = ema20
            stop = min(low20[i] - 0.10 * atr[i], entry - sl_atr_mult * atr[i]) if is_buy else max(high20[i] + 0.10 * atr[i], entry + sl_atr_mult * atr[i])
        elif strategy == 'breakout_retest':
            is_breakout = close[i] > high20[i] if is_buy else close[i] < low20[i]
            if not is_breakout:
                continue
            entry = high20[i] if is_buy else low20[i]
            # Keep the label geometry identical to decision.build_setup and
            # within the 0.55–3.0 ATR tradable-risk bounds.
            stop = entry - sl_atr_mult * atr[i] if is_buy else entry + sl_atr_mult * atr[i]
        elif strategy == 'momentum_continuation':
            aligned = (ema20 > ema50 > ema200) if is_buy else (ema20 < ema50 < ema200)
            momentum_ok = (rsi >= 55 and macd_hist > 0) if is_buy else (rsi <= 45 and macd_hist < 0)
            if not aligned or not momentum_ok:
                continue
            entry = close[i]
            stop = entry - sl_atr_mult * atr[i] if is_buy else entry + sl_atr_mult * atr[i]
        else:
            raise ValueError(f'Unknown strategy: {strategy}')
        risk = entry - stop if is_buy else stop - entry
        target = entry + rr * risk if is_buy else entry - rr * risk

        risk_atr = risk / atr[i]
        # Match the live setup's risk sanity filter; non-tradable geometries
        # must not become positive training examples.
        if not np.isfinite(risk_atr) or risk_atr < 0.55 or risk_atr > 3.0:
            continue

        active = False
        activation_j = None
        for j in range(i + 1, min(len(x), i + horizon + 1)):
            if not active:
                if not (low[j] <= entry <= high[j]):
                    # Pending setups do not expire in live trading. Keep looking
                    # within the label observation window for a real Entry touch.
                    continue
                active = True
                activation_j = j

            hit_target = high[j] >= target if direction == 1 else low[j] <= target
            hit_stop = low[j] <= stop if direction == 1 else high[j] >= stop
            # If both levels are inside one OHLC candle, conservatively count SL.
            if hit_target and hit_stop:
                y[i] = 0
                break
            if hit_stop:
                y[i] = 0
                break
            if hit_target:
                # On the activation candle, OHLC cannot prove that Entry was
                # reached before TP (price may have gapped across Entry). Label
                # this ambiguous case as a loss rather than leak optimistic
                # target outcomes into training.
                y[i] = 0 if j == activation_j else 1
                break
    return pd.Series(y, index=x.index)

class GoldAI:
    def __init__(self):
        self.models = {}; self.metrics = {}; self.trained_at = {}; self.errors = {}

    def fit(self, timeframe, raw_df, rr=2.0, feedback=None, divisor=3.6, sl_atr_mult=1.15, fast_mode=False, strategy='baseline'):
        x = raw_df.copy()
        horizons = {'5m': 36, '15m': 24, '1h': 18}
        # This finite horizon is only the training-label observation window.
        # Unresolved examples stay unlabeled; live trades do not expire by time.
        horizon = max(horizons.get(timeframe, 24), max(1, int(SIGNAL_LABEL_HORIZON_BARS)))
        labels = {d: _label_direction(x, d, horizon, rr, divisor, sl_atr_mult, strategy, timeframe) for d in (1, 0)}
        base = x[FEATURES[:-1]].copy()
        base_valid = base.notna().all(axis=1)

        # Important: do NOT require both BUY and SELL labels to exist on the
        # same candle. That unnecessarily discards 1H samples and can bias the
        # model toward candles where both hypothetical trades resolved.
        valid_by_direction = {d: base_valid & labels[d].notna() for d in (1, 0)}
        all_valid_idx = base.index[valid_by_direction[1] | valid_by_direction[0]]
        if len(all_valid_idx) < 300:
            self.errors[timeframe] = f'insufficient_training_rows:{len(all_valid_idx)}'; return False

        # Split by unique candle timestamp first. No future timestamp can enter
        # training, and BUY/SELL copies of one candle stay in the same split.
        split_pos = int(len(all_valid_idx) * 0.80)
        if split_pos <= 0 or split_pos >= len(all_valid_idx):
            self.errors[timeframe] = 'invalid_temporal_split'; return False
        split_time = all_valid_idx[split_pos]

        # Purge training samples whose forward outcome window could overlap the
        # validation period. Use the full candle index (not only labeled rows).
        split_loc = int(x.index.get_indexer([split_time])[0])
        purge_loc = max(0, split_loc - horizon)
        purge_time = x.index[purge_loc]
        purged_candles = max(0, split_loc - purge_loc)

        train_parts=[]; val_parts=[]
        for direction in (1, 0):
            idx = base.index[valid_by_direction[direction]]
            train_idx = idx[idx < purge_time]
            val_idx = idx[idx >= split_time]
            if len(train_idx):
                d=base.loc[train_idx].copy(); d['direction']=direction; d['_label']=labels[direction].loc[train_idx].astype(int).values; train_parts.append(d)
            if len(val_idx):
                d=base.loc[val_idx].copy(); d['direction']=direction; d['_label']=labels[direction].loc[val_idx].astype(int).values; val_parts.append(d)

        if not train_parts or not val_parts:
            self.errors[timeframe]='insufficient_temporal_split'; return False
        train=pd.concat(train_parts).sort_index(kind='stable'); val=pd.concat(val_parts).sort_index(kind='stable')
        Xtr=train[FEATURES]; ytr=train['_label']; Xv=val[FEATURES]; yv=val['_label']
        if len(Xtr)<600 or len(Xv)<40 or ytr.nunique()<2:
            self.errors[timeframe] = (
                f'invalid_training_distribution:train_rows={len(Xtr)} '
                f'train_classes={ytr.nunique()} val_rows={len(Xv)} '
                f'val_classes={yv.nunique()}'
            )
            return False
        # A chronological validation fold can legitimately contain one class,
        # especially for strategy-specific labels and short history windows.
        # The model only needs both classes in the training set. Validation
        # metrics below already skip AUC when the validation fold is single-class.

        # Walk-forward backtests only need the final deployment model for each
        # fold. Skip the separate validation fit/metrics fit and train once on
        # the exact same purged train+validation rows used by the normal final
        # refit below. This roughly halves model-fitting work without reducing
        # tree count or changing the feature/label/risk definitions.
        if fast_mode:
            all_labeled = pd.concat([train, val]).sort_index(kind='stable')
            Xall = all_labeled[FEATURES]
            yall = all_labeled['_label']
            try:
                from xgboost import XGBClassifier
                model = XGBClassifier(n_estimators=350,max_depth=4,learning_rate=0.035,subsample=0.85,colsample_bytree=0.85,min_child_weight=6,reg_alpha=0.15,reg_lambda=2.0,objective='binary:logistic',eval_metric='logloss',tree_method='hist',random_state=42,n_jobs=1)
                model.fit(Xall, yall)
                engine = 'XGBoost'
            except Exception as exc:
                self.errors[timeframe]=f'XGBoost: {type(exc).__name__}: {exc}'
                model=HistGradientBoostingClassifier(max_iter=250,learning_rate=0.045,max_leaf_nodes=15,l2_regularization=1.0,random_state=42)
                model.fit(Xall, yall)
                engine='HistGradientBoosting'
            metrics = {
                'engine': engine, 'training_rows': int(len(Xall)),
                'deployment_training_rows': int(len(Xall)),
                'training_candles': int(len(set(all_labeled.index))),
                'deployment_training_candles': int(len(set(all_labeled.index))),
                'fast_mode': True,
            }
            if hasattr(model, 'feature_importances_'):
                imp=np.asarray(model.feature_importances_,dtype=float)
                metrics['top_features']=[{'feature':k,'importance':float(v)} for k,v in sorted(zip(FEATURES,imp),key=lambda z:z[1],reverse=True)[:10]]
            self.models[timeframe]=model; self.metrics[timeframe]=metrics
            self.trained_at[timeframe]=time.time(); self.errors.pop(timeframe,None); return True

        try:
            from xgboost import XGBClassifier
            model=XGBClassifier(n_estimators=350,max_depth=4,learning_rate=0.035,subsample=0.85,colsample_bytree=0.85,min_child_weight=6,reg_alpha=0.15,reg_lambda=2.0,objective='binary:logistic',eval_metric='logloss',tree_method='hist',random_state=42,n_jobs=1)
            model.fit(Xtr,ytr,eval_set=[(Xv,yv)],verbose=False); engine='XGBoost'
        except Exception as exc:
            self.errors[timeframe]=f'XGBoost: {type(exc).__name__}: {exc}'
            model=HistGradientBoostingClassifier(max_iter=250,learning_rate=0.045,max_leaf_nodes=15,l2_regularization=1.0,random_state=42)
            model.fit(Xtr,ytr); engine='HistGradientBoosting'

        pv=model.predict_proba(Xv)[:,1]
        val_direction=val['direction'].to_numpy()
        metrics={
            'engine':engine,'training_rows':int(len(Xtr)),'validation_rows':int(len(Xv)),
            'training_candles':int(len(set(train.index))),'validation_candles':int(len(set(val.index))),'purged_candles':int(purged_candles),
            'auc':float(roc_auc_score(yv,pv)) if yv.nunique()==2 else 0.5,
            'accuracy':float(accuracy_score(yv,(pv>=0.5).astype(int))),
            'positive_rate':float(ytr.mean()),'validation_positive_rate':float(yv.mean()),
            'validation_baseline_accuracy':float(max(yv.mean(),1-yv.mean())),
            'validation_mean_probability':float(np.mean(pv)),
            'validation_brier':float(np.mean((pv-yv.to_numpy())**2)),
        }
        # Direction-specific AUC is essential: a combined AUC can hide that
        # BUY and SELL behave very differently, especially on 1H.
        for d, name in ((1,'buy'),(0,'sell')):
            mask=val_direction==d
            if mask.sum() >= 10 and yv.iloc[mask].nunique()==2:
                metrics[f'{name}_auc']=float(roc_auc_score(yv.iloc[mask],pv[mask]))
            metrics[f'{name}_validation_rows']=int(mask.sum())
            metrics[f'{name}_positive_rate']=float(yv.iloc[mask].mean()) if mask.sum() else 0.0

        # Keep the chronological holdout metrics above untouched, but refit the
        # deployment model on every label that is already fully observable at this
        # training timestamp. _label_direction leaves the most recent horizon
        # candles unlabeled, so these outcomes have matured and do not leak future
        # information. Previously the live/WF model discarded the newest 20% of
        # available history after using it only for validation.
        all_labeled = pd.concat([train, val]).sort_index(kind='stable')
        Xall = all_labeled[FEATURES]
        yall = all_labeled['_label']
        model.fit(Xall, yall)
        metrics['deployment_training_rows'] = int(len(Xall))
        metrics['deployment_training_candles'] = int(len(set(all_labeled.index)))

        if hasattr(model, 'feature_importances_'):
            imp=np.asarray(model.feature_importances_,dtype=float)
            pairs=sorted(zip(FEATURES,imp),key=lambda z:z[1],reverse=True)[:10]
            metrics['top_features']=[{'feature':k,'importance':float(v)} for k,v in pairs]

        self.models[timeframe]=model; self.metrics[timeframe]=metrics
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
