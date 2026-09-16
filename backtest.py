import time
import traceback
import numpy as np
import pandas as pd
from config import *
from market import fetch, add_features
from ai_engine import GoldAI
from decision import build_setup


class GoldBacktest:
    """Chronological walk-forward test matching the live signal rules."""
    def __init__(self):
        self.last = {}

    def _completed(self, df):
        return df.iloc[:-1].copy() if len(df) > 1 else df

    def _enrich(self, tf, x):
        x = x.copy()
        for htf, prefix in [('15m', 'ctx15'), ('1h', 'ctx1h')]:
            needed = (tf == '5m' and htf in ('15m', '1h')) or (tf == '15m' and htf == '1h')
            if not needed:
                x[prefix+'_trend'] = 0; x[prefix+'_rsi'] = 50; x[prefix+'_macd_hist'] = 0; x[prefix+'_dist_ema20'] = 0
                continue
            try:
                h = self._completed(add_features(fetch(GOLD_DATA_SYMBOL, htf)).dropna())
                h = h[['ema20','ema50','ema200','rsi','macd_hist','dist_ema20']].copy(); h['trend'] = 0
                h.loc[(h.ema20 > h.ema50) & (h.ema50 > h.ema200), 'trend'] = 1
                h.loc[(h.ema20 < h.ema50) & (h.ema50 < h.ema200), 'trend'] = -1
                h = h.rename(columns={'trend':prefix+'_trend','rsi':prefix+'_rsi','macd_hist':prefix+'_macd_hist','dist_ema20':prefix+'_dist_ema20'}).drop(columns=['ema20','ema50','ema200'])
                x = pd.merge_asof(x.sort_index(), h.sort_index(), left_index=True, right_index=True, direction='backward')
            except Exception:
                x[prefix+'_trend'] = 0; x[prefix+'_rsi'] = 50; x[prefix+'_macd_hist'] = 0; x[prefix+'_dist_ema20'] = 0
        return x

    def _context(self, tf, row_time, x):
        r = x.loc[x.index <= row_time].iloc[-1]
        if tf == '5m':
            a, b = int(r.get('ctx15_trend', 0)), int(r.get('ctx1h_trend', 0))
            return {'trend': 1 if a > 0 and b > 0 else (-1 if a < 0 and b < 0 else 0)}
        if tf == '15m': return {'trend': int(r.get('ctx1h_trend', 0))}
        return {'trend': 0}

    def _resolve(self, df, start, setup):
        d = setup['direction']; entry = float(setup['entry']); sl = float(setup['sl']); tp = float(setup['tp']); active = False
        for j in range(start, len(df)):
            hi, lo = float(df['High'].iloc[j]), float(df['Low'].iloc[j])
            if not active:
                if not (lo <= entry <= hi): continue
                active = True
            hit_sl, hit_tp = ((lo <= sl, hi >= tp) if d == 'BUY' else (hi >= sl, lo <= tp))
            if hit_sl and hit_tp: return 'SL', j, j
            if hit_tp: return 'TP', j, j
            if hit_sl: return 'SL', j, j
        return None, None, None

    @staticmethod
    def _score_band(v):
        return '65-69' if float(v) < 70 else ('70-74' if float(v) < 75 else ('75-79' if float(v) < 80 else '80+'))

    @staticmethod
    def _ai_band(v):
        p = float(v) * 100
        return 'أقل من 30%' if p < 30 else ('30-39%' if p < 40 else ('40-49%' if p < 50 else ('50-59%' if p < 60 else '60%+')))

    @staticmethod
    def _groups(trades, key):
        groups = {}
        for t in trades: groups.setdefault(t[key], []).append(t)
        out = []
        for k, a in groups.items():
            n = len(a); w = sum(t['result'] == 'TP' for t in a); net = sum(t['r'] for t in a)
            gp = sum(max(t['r'], 0) for t in a); gl = abs(sum(min(t['r'], 0) for t in a))
            out.append({'key':k, 'n':n, 'wr':100*w/n, 'net':net, 'pf':gp/gl if gl else (float('inf') if gp else 0), 'e':net/n})
        return out

    @staticmethod
    def _dd(trades):
        equity = peak = drawdown = 0.0
        for t in trades:
            equity += t['r']; peak = max(peak, equity); drawdown = max(drawdown, peak-equity)
        return drawdown

    def run(self, tf, bars=None, retrain_every=None):
        started = time.time()
        requested_bars = int(bars if bars is not None else {
            '5m': BACKTEST_BARS_5M,
            '15m': BACKTEST_BARS_15M,
            '1h': BACKTEST_BARS_1H,
        }.get(tf, BACKTEST_BARS))
        retrain_every = int(retrain_every if retrain_every is not None else BACKTEST_RETRAIN_EVERY)
        raw = fetch(GOLD_DATA_SYMBOL, tf, period=requested_bars)
        received_bars = len(raw)
        if received_bars < requested_bars:
            return {'timeframe': tf, 'error': f'history_incomplete:requested={requested_bars},received={received_bars}', 'requested_bars': requested_bars, 'raw_bars': received_bars}
        if len(raw) > requested_bars:
            raw = raw.iloc[-requested_bars:].copy()
        raw = self._completed(raw)
        x = self._enrich(tf, add_features(raw).dropna())
        if len(x) < 1200: return {'timeframe':tf, 'error':f'insufficient_bars:{len(x)}', 'requested_bars':requested_bars, 'raw_bars':len(raw), 'usable_bars':len(x)}
        warmup = 700; step = max(200, retrain_every); trades = []; model = GoldAI(); last_fit = -1; i = warmup
        while i < len(x)-1:
            if last_fit < 0 or i-last_fit >= step:
                model = GoldAI(); ok = model.fit(tf, x.iloc[:i], RR)
                if not ok: i += 1; continue
                last_fit = i
            row = x.iloc[i]
            probs = {'BUY':model.probability(tf,row,'BUY'), 'SELL':model.probability(tf,row,'SELL')}
            prefix = pd.concat([x.iloc[:i+1].copy(), row.to_frame().T], axis=0)
            setup = build_setup(prefix, probs, RR, DIVISOR, SL_ATR_MULT, MIN_SCORE, MIN_AI_PROB, self._context(tf,row.name,x))
            if setup:
                start = raw.index.searchsorted(row.name, side='right'); result, end_j, act_j = self._resolve(raw, start, setup)
                if result:
                    r = 2.0 if result == 'TP' else -1.0
                    trades.append({'time':row.name.isoformat(),'direction':setup['direction'],'score':float(setup['score']),'score_band':self._score_band(setup['score']),'ai':float(setup['ai_prob']),'ai_band':self._ai_band(setup['ai_prob']),'result':result,'r':r,'activation_time':raw.index[act_j].isoformat()})
                    i = max(i+1, end_j+1); continue
            i += 1
        n = len(trades); w = sum(t['result']=='TP' for t in trades); net = sum(t['r'] for t in trades); gp = sum(max(t['r'],0) for t in trades); gl = abs(sum(min(t['r'],0) for t in trades))
        res = {'timeframe':tf,'requested_bars':requested_bars,'raw_bars':len(raw),'usable_bars':len(x),'bars':len(x),'trades':n,'wins':w,'losses':n-w,'win_rate':100*w/n if n else 0,'net_r':net,'avg_score':sum(t['score'] for t in trades)/n if n else 0,'profit_factor':gp/gl if gl else (float('inf') if gp else 0),'max_drawdown_r':self._dd(trades),'expectancy_r':net/n if n else 0,'direction':self._groups(trades,'direction'),'score_bands':self._groups(trades,'score_band'),'ai_bands':self._groups(trades,'ai_band'),'elapsed_s':round(time.time()-started,1),'trades_detail':trades[-100:]}
        self.last[tf] = res; return res

    def run_all(self):
        out = {}
        bars_by_tf = {'5m': BACKTEST_BARS_5M, '15m': BACKTEST_BARS_15M, '1h': BACKTEST_BARS_1H}
        for tf in TIMEFRAMES:
            try: out[tf] = self.run(tf, bars=bars_by_tf.get(tf, BACKTEST_BARS), retrain_every=BACKTEST_RETRAIN_EVERY)
            except Exception as exc: traceback.print_exc(); out[tf] = {'timeframe':tf,'error':f'{type(exc).__name__}: {exc}'}
        return out

    @staticmethod
    def _fmt_groups(rows, order):
        rank = {v:i for i,v in enumerate(order)}; rows = sorted(rows, key=lambda z:rank.get(z['key'],999)); out = []
        for z in rows:
            if z['key'] not in rank: continue
            pf = '∞' if np.isinf(z['pf']) else f"{z['pf']:.2f}"
            out.append(f"   {z['key']}: {z['n']} ص | {z['wr']:.1f}% | {z['net']:+.1f}R | PF {pf} | E {z['e']:+.2f}R")
        return out or ['   —']

    @staticmethod
    def format_ar(results):
        lines = ['🧪 <b>Backtest حقيقي — Walk-Forward</b>','','اختبار زمني بدون استخدام شموع المستقبل في تدريب النموذج.','الدخول لا يُحسب صفقة إلا بعد لمس Entry فعليًا.','عند لمس SL وTP داخل نفس الشمعة تُحسب SL بشكل محافظ.','']; total = wins = 0; net = 0.0
        for tf in TIMEFRAMES:
            r = results.get(tf,{})
            if r.get('error'):
                lines += [f'⏱️ <b>{tf}</b>: ❌ {r["error"]}','']; continue
            n,w,l = r.get('trades',0),r.get('wins',0),r.get('losses',0); total += n; wins += w; net += r.get('net_r',0); pf = r.get('profit_factor',0); pf = '∞' if np.isinf(pf) else f'{pf:.2f}'
            lines += [f'⏱️ <b>{tf}</b>',f'📚 الشموع الخام: {r.get("raw_bars",0)} / المطلوب: {r.get("requested_bars",0)}',f'📚 الشموع القابلة للاستخدام: {r.get("usable_bars",0)}',f'📌 الصفقات: {n}',f'✅ ربح: {w} | ❌ خسارة: {l}',f'🎯 الفوز: {r.get("win_rate",0):.1f}%',f'⚖️ صافي: {r.get("net_r",0):+.1f}R',f'📊 متوسط Score: {r.get("avg_score",0):.1f}',f'💰 Profit Factor: {pf}',f'📉 Max Drawdown: {r.get("max_drawdown_r",0):.1f}R',f'📐 Expectancy: {r.get("expectancy_r",0):+.2f}R/صفقة','↔️ <b>شراء/بيع:</b>']
            lines += GoldBacktest._fmt_groups(r.get('direction',[]),['BUY','SELL'])
            lines += ['📊 <b>حسب Score:</b>'] + GoldBacktest._fmt_groups(r.get('score_bands',[]),['65-69','70-74','75-79','80+'])
            lines += ['🧠 <b>حسب تقدير AI:</b>'] + GoldBacktest._fmt_groups(r.get('ai_bands',[]),['أقل من 30%','30-39%','40-49%','50-59%','60%+']) + ['']
        gross_profit = sum(sum(max(t.get('r', 0), 0) for t in results.get(tf, {}).get('trades_detail', [])) for tf in TIMEFRAMES)
        gross_loss = abs(sum(min(t.get('r', 0), 0) for t in results.get(tf, {}).get('trades_detail', [])) for tf in TIMEFRAMES)
        pf = gross_profit / gross_loss if gross_loss else (float('inf') if gross_profit else 0); pf = '∞' if np.isinf(pf) else f'{pf:.2f}'
        lines += [f'📌 <b>الإجمالي:</b> {total} صفقة',f'🎯 <b>Win Rate:</b> {(100*wins/total if total else 0):.1f}%',f'⚖️ <b>Net:</b> {net:+.1f}R',f'💰 <b>Profit Factor:</b> {pf}','','⚠️ هذا اختبار تاريخي وليس ضمانًا للنتائج المستقبلية.','⚠️ الإحصاءات لا تعني أن الـAI يتنبأ بالربح يقينًا.']
        return '\n'.join(lines)
