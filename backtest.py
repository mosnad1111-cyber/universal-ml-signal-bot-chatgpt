import time
import traceback
import numpy as np
import pandas as pd

from config import *
from market import fetch, add_features
from ai_engine import GoldAI
from decision import build_setup


class GoldBacktest:
    """Strict chronological walk-forward test of the live signal rules.

    A model is trained only on candles strictly before the test window. Signals
    are generated from completed candles and then resolved on later candles.
    This is deliberately separate from live DB results so it cannot contaminate
    the training set.
    """

    def __init__(self):
        self.last = {}

    def _completed(self, df):
        return df.iloc[:-1].copy() if len(df) > 1 else df

    def _enrich(self, tf, x):
        x = x.copy()
        sources = [('15m', 'ctx15'), ('1h', 'ctx1h')]
        for htf, prefix in sources:
            needed = (tf == '5m' and htf in ('15m', '1h')) or (tf == '15m' and htf == '1h')
            if needed:
                try:
                    h = self._completed(add_features(fetch(GOLD_DATA_SYMBOL, htf)).dropna())
                    h = h[['ema20','ema50','ema200','rsi','macd_hist','dist_ema20']].copy()
                    h['trend'] = 0
                    h.loc[(h.ema20 > h.ema50) & (h.ema50 > h.ema200), 'trend'] = 1
                    h.loc[(h.ema20 < h.ema50) & (h.ema50 < h.ema200), 'trend'] = -1
                    h = h.rename(columns={
                        'trend': prefix+'_trend', 'rsi': prefix+'_rsi',
                        'macd_hist': prefix+'_macd_hist', 'dist_ema20': prefix+'_dist_ema20'
                    }).drop(columns=['ema20','ema50','ema200'])
                    x = pd.merge_asof(x.sort_index(), h.sort_index(), left_index=True, right_index=True, direction='backward')
                except Exception:
                    x[prefix+'_trend']=0; x[prefix+'_rsi']=50; x[prefix+'_macd_hist']=0; x[prefix+'_dist_ema20']=0
            else:
                x[prefix+'_trend']=0; x[prefix+'_rsi']=50; x[prefix+'_macd_hist']=0; x[prefix+'_dist_ema20']=0
        return x

    def _context(self, tf, row_time, x):
        r = x.loc[x.index <= row_time].iloc[-1]
        if tf == '5m':
            a, b = int(r.get('ctx15_trend', 0)), int(r.get('ctx1h_trend', 0))
            return {'trend': 1 if a > 0 and b > 0 else (-1 if a < 0 and b < 0 else 0)}
        if tf == '15m':
            return {'trend': int(r.get('ctx1h_trend', 0))}
        return {'trend': 0}

    def _resolve(self, df, start, setup):
        direction = setup['direction']; sl = float(setup['sl']); tp = float(setup['tp'])
        for j in range(start, len(df)):
            hi, lo = float(df['High'].iloc[j]), float(df['Low'].iloc[j])
            if direction == 'BUY':
                hit_sl, hit_tp = lo <= sl, hi >= tp
            else:
                hit_sl, hit_tp = hi >= sl, lo <= tp
            if hit_sl and hit_tp:
                return 'SL', j
            if hit_tp:
                return 'TP', j
            if hit_sl:
                return 'SL', j
        return None, None

    def run(self, tf, bars=None, retrain_every=500):
        started = time.time()
        raw = fetch(GOLD_DATA_SYMBOL, tf)
        if bars and len(raw) > bars:
            raw = raw.iloc[-bars:].copy()
        x = self._enrich(tf, add_features(raw).dropna())
        if len(x) < 1200:
            return {'timeframe': tf, 'error': f'insufficient_bars:{len(x)}'}

        # Keep a clean completed-candle sequence. Each test point uses a model
        # trained before that point; no future candle enters model fitting.
        warmup = 700
        step = max(200, int(retrain_every))
        trades = []
        model = GoldAI()
        last_fit = -1
        i = warmup
        while i < len(x) - 2:
            if last_fit < 0 or i - last_fit >= step:
                train_end = i - 1
                train_x = x.iloc[:train_end + 1].copy()
                model = GoldAI()
                ok = model.fit(tf, train_x, RR)
                if not ok:
                    i += 1
                    continue
                last_fit = i

            row = x.iloc[i]
            probs = {'BUY': model.probability(tf, row, 'BUY'), 'SELL': model.probability(tf, row, 'SELL')}
            # build_setup intentionally reads -2 (the same completed-candle
            # convention as live). Duplicate only the test row so -2 == row.
            prefix = x.iloc[:i+1].copy()
            prefix = pd.concat([prefix, row.to_frame().T], axis=0)
            setup = build_setup(prefix, probs, RR, DIVISOR, SL_ATR_MULT, MIN_SCORE, MIN_AI_PROB, self._context(tf, row.name, x))
            if setup:
                # One trade at a time per timeframe, exactly as live.
                result, end_j = self._resolve(raw, raw.index.searchsorted(row.name, side='right'), setup)
                if result:
                    r = 2.0 if result == 'TP' else -1.0
                    trades.append({
                        'time': row.name.isoformat(), 'direction': setup['direction'],
                        'score': float(setup['score']), 'ai': float(setup['ai_prob']),
                        'result': result, 'r': r
                    })
                    i = max(i + 1, end_j + 1)
                    continue
            i += 1

        total = len(trades); wins = sum(t['result'] == 'TP' for t in trades); losses = total - wins
        result = {
            'timeframe': tf, 'bars': int(len(x)), 'trades': total, 'wins': wins,
            'losses': losses, 'win_rate': (100.0*wins/total if total else 0.0),
            'net_r': sum(t['r'] for t in trades),
            'avg_score': (sum(t['score'] for t in trades)/total if total else 0.0),
            'elapsed_s': round(time.time()-started, 1), 'trades_detail': trades[-50:]
        }
        self.last[tf] = result
        return result

    def run_all(self):
        out = {}
        for tf in TIMEFRAMES:
            try:
                out[tf] = self.run(tf)
            except Exception as exc:
                traceback.print_exc(); out[tf] = {'timeframe': tf, 'error': f'{type(exc).__name__}: {exc}'}
        return out

    @staticmethod
    def format_ar(results):
        lines = ['🧪 <b>Backtest حقيقي — Walk-Forward</b>', '', 'اختبار زمني بدون استخدام شموع المستقبل في تدريب النموذج.']
        total=wins=0; net=0.0
        for tf in TIMEFRAMES:
            r = results.get(tf, {})
            if r.get('error'):
                lines += [f'⏱️ <b>{tf}</b>: ❌ {r["error"]}', '']
                continue
            n,w,l = r.get('trades',0),r.get('wins',0),r.get('losses',0)
            total += n; wins += w; net += r.get('net_r',0.0)
            lines += [f'⏱️ <b>{tf}</b>', f'📚 الشموع: {r.get("bars",0)}', f'📌 الصفقات: {n}', f'✅ ربح: {w} | ❌ خسارة: {l}', f'🎯 الفوز: {r.get("win_rate",0):.1f}%', f'⚖️ صافي: {r.get("net_r",0):+.1f}R', f'📊 متوسط Score: {r.get("avg_score",0):.1f}', '']
        lines += [f'📌 <b>الإجمالي:</b> {total} صفقة', f'🎯 <b>Win Rate:</b> {(100*wins/total if total else 0):.1f}%', f'⚖️ <b>Net:</b> {net:+.1f}R', '', '⚠️ هذا اختبار تاريخي وليس ضمانًا للنتائج المستقبلية.']
        return '\n'.join(lines)
