import time
import traceback
import threading
import numpy as np
import pandas as pd
from config import *
from market import fetch, add_features
from ai_engine import GoldAI
from decision import build_setup


# Share an immutable market snapshot and its out-of-sample predictions between
# separate Telegram Backtest/Grid Search requests in the same worker process.
_SNAPSHOT_CACHE = {}
_PREDICTION_CACHE = {}
_CACHE_LOCK = threading.RLock()
_CACHE_TTL_SECONDS = 30 * 60


class GoldBacktest:
    """Chronological walk-forward test matching the live signal rules."""
    def __init__(self):
        self.last = {}

    def _completed(self, df):
        return df.iloc[:-1].copy() if len(df) > 1 else df

    def _enrich(self, tf, x):
        x = x.copy()
        # Map each tested timeframe to the higher-timeframe context it needs.
        # 1m -> 5m/15m/1h, 5m -> 15m/1h, 15m -> 1h, 30m -> 1h.
        context_map = {
            '1m': {'5m': 'ctx5', '15m': 'ctx15', '1h': 'ctx1h'},
            '5m': {'15m': 'ctx15', '1h': 'ctx1h'},
            '15m': {'1h': 'ctx1h'},
            '30m': {'1h': 'ctx1h'},
        }
        required = context_map.get(tf, {})
        for htf, prefix in [('5m', 'ctx5'), ('15m', 'ctx15'), ('1h', 'ctx1h')]:
            if htf not in required:
                x[prefix+'_trend'] = 0
                x[prefix+'_rsi'] = 50
                x[prefix+'_macd_hist'] = 0
                x[prefix+'_dist_ema20'] = 0
                continue
            try:
                h = self._completed(add_features(fetch(GOLD_DATA_SYMBOL, htf)).dropna())
                h = h[['ema20','ema50','ema200','rsi','macd_hist','dist_ema20']].copy()
                h['trend'] = 0
                h.loc[(h.ema20 > h.ema50) & (h.ema50 > h.ema200), 'trend'] = 1
                h.loc[(h.ema20 < h.ema50) & (h.ema50 < h.ema200), 'trend'] = -1
                h = h.rename(columns={
                    'trend': prefix+'_trend',
                    'rsi': prefix+'_rsi',
                    'macd_hist': prefix+'_macd_hist',
                    'dist_ema20': prefix+'_dist_ema20',
                }).drop(columns=['ema20','ema50','ema200'])
                # OHLC timestamps mark candle OPEN. Higher-timeframe features are
                # only knowable at candle CLOSE; shift availability forward to
                # prevent historical lower-timeframe rows seeing future HTF data.
                h.index = h.index + pd.Timedelta({'5m': '5min', '15m': '15min', '1h': '1h'}[htf])
                x = pd.merge_asof(
                    x.sort_index(), h.sort_index(),
                    left_index=True, right_index=True, direction='backward'
                )
            except Exception:
                x[prefix+'_trend'] = 0
                x[prefix+'_rsi'] = 50
                x[prefix+'_macd_hist'] = 0
                x[prefix+'_dist_ema20'] = 0
        return x

    def _context(self, tf, row_time, x):
        r = x.loc[x.index <= row_time].iloc[-1]
        if tf == '1m':
            trends = [int(r.get('ctx5_trend', 0)), int(r.get('ctx15_trend', 0)), int(r.get('ctx1h_trend', 0))]
            nonzero = [v for v in trends if v != 0]
            return {'trend': nonzero[0] if nonzero and all(v > 0 for v in nonzero) else (-1 if nonzero and all(v < 0 for v in nonzero) else 0)}
        if tf == '5m':
            a, b = int(r.get('ctx15_trend', 0)), int(r.get('ctx1h_trend', 0))
            return {'trend': 1 if a > 0 and b > 0 else (-1 if a < 0 and b < 0 else 0)}
        if tf in ('15m', '30m'):
            return {'trend': int(r.get('ctx1h_trend', 0))}
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
    def _period_groups(trades, start_time, end_time):
        """Split trades by the midpoint of the actual tested time window."""
        if not trades or start_time is None or end_time is None:
            return []
        midpoint = start_time + (end_time - start_time) / 2
        buckets = {'النصف الأول': [], 'النصف الثاني': []}
        for t in trades:
            ts = pd.Timestamp(t['time'])
            buckets['النصف الأول' if ts <= midpoint else 'النصف الثاني'].append(t)
        out = []
        for k in ('النصف الأول', 'النصف الثاني'):
            a = buckets[k]
            if not a:
                continue
            n = len(a)
            w = sum(t['result'] == 'TP' for t in a)
            net = sum(t['r'] for t in a)
            gp = sum(max(t['r'], 0) for t in a)
            gl = abs(sum(min(t['r'], 0) for t in a))
            out.append({'key': k, 'n': n, 'wr': 100*w/n, 'net': net,
                        'pf': gp/gl if gl else (float('inf') if gp else 0),
                        'e': net/n})
        return out

    @staticmethod
    def _sequence_stats(trades):
        if not trades:
            return {'max_win_streak': 0, 'max_loss_streak': 0, 'max_dd_r': 0.0,
                    'dd_trough_trade': 0, 'dd_peak_trade': 0}
        equity = peak = 0.0
        max_dd = 0.0
        peak_trade = trough_trade = 0
        current_win = current_loss = 0
        max_win = max_loss = 0
        for idx, t in enumerate(trades, 1):
            equity += t['r']
            if equity > peak:
                peak = equity
                peak_trade = idx
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
                trough_trade = idx
            if t['result'] == 'TP':
                current_win += 1
                current_loss = 0
                max_win = max(max_win, current_win)
            else:
                current_loss += 1
                current_win = 0
                max_loss = max(max_loss, current_loss)
        return {'max_win_streak': max_win, 'max_loss_streak': max_loss,
                'max_dd_r': max_dd, 'dd_peak_trade': peak_trade,
                'dd_trough_trade': trough_trade}

    @staticmethod
    def _quarter_groups(trades, start_time, end_time):
        if not trades or start_time is None or end_time is None:
            return []
        span = end_time - start_time
        if span.total_seconds() <= 0:
            return []
        buckets = {1: [], 2: [], 3: [], 4: []}
        for t in trades:
            ratio = (pd.Timestamp(t['time']) - start_time) / span
            q = min(4, max(1, int(ratio * 4) + 1))
            buckets[q].append(t)
        out = []
        for q in range(1, 5):
            a = buckets[q]
            if not a:
                continue
            n = len(a)
            w = sum(t['result'] == 'TP' for t in a)
            net = sum(t['r'] for t in a)
            gp = sum(max(t['r'], 0) for t in a)
            gl = abs(sum(min(t['r'], 0) for t in a))
            out.append({'key': f'الربع {q}', 'n': n, 'wr': 100*w/n, 'net': net,
                        'pf': gp/gl if gl else (float('inf') if gp else 0),
                        'e': net/n})
        return out

    @staticmethod
    def _dd(trades):
        equity = peak = drawdown = 0.0
        for t in trades:
            equity += t['r']; peak = max(peak, equity); drawdown = max(drawdown, peak-equity)
        return drawdown

    @staticmethod
    def _requested_bars(tf, bars=None):
        return int(bars if bars is not None else {
            '1m': BACKTEST_BARS_1M,
            '5m': BACKTEST_BARS_5M,
            '15m': BACKTEST_BARS_15M,
            '30m': BACKTEST_BARS_30M,
            '1h': BACKTEST_BARS_1H,
        }.get(tf, BACKTEST_BARS))

    def _prepare(self, tf, requested_bars):
        """Fetch and prepare one cached, immutable market snapshot per timeframe.

        Backtest and Grid Search actions often run in separate Telegram requests.
        Reusing the same snapshot for 30 minutes makes their results directly
        comparable instead of silently comparing different latest candles.
        """
        cache_key = (tf, int(requested_bars))
        now = time.time()
        with _CACHE_LOCK:
            cached = _SNAPSHOT_CACHE.get(cache_key)
            if cached and now - cached[0] < _CACHE_TTL_SECONDS:
                prepared = cached[1]
                return {
                    **prepared,
                    'raw': prepared['raw'].copy(),
                    'features': prepared['features'].copy(),
                    'snapshot_reused': True,
                }
            _SNAPSHOT_CACHE.pop(cache_key, None)

        raw = fetch(GOLD_DATA_SYMBOL, tf, period=requested_bars)
        received_bars = len(raw)
        history_partial = received_bars < requested_bars
        if len(raw) > requested_bars:
            raw = raw.iloc[-requested_bars:].copy()
            received_bars = len(raw)
        raw = self._completed(raw)
        x = self._enrich(tf, add_features(raw).dropna())
        start = x.index[0].isoformat() if len(x) else None
        end = x.index[-1].isoformat() if len(x) else None
        snapshot_id = f'{tf}:{requested_bars}:{len(raw)}:{start}:{end}'
        prepared = {
            'raw': raw.copy(),
            'features': x.copy(),
            'requested_bars': requested_bars,
            'received_bars': received_bars,
            'history_partial': history_partial,
            'snapshot_id': snapshot_id,
            'snapshot_start': start,
            'snapshot_end': end,
            'snapshot_reused': False,
        }
        with _CACHE_LOCK:
            _SNAPSHOT_CACHE[cache_key] = (time.time(), prepared)
        return {**prepared, 'raw': raw.copy(), 'features': x.copy()}

    def _walk_forward_predictions(self, tf, x, retrain_every):
        """Generate/cache deterministic out-of-sample probabilities for a snapshot.

        Predictions are generated chronologically using only candles strictly
        before each predicted candle. Identical snapshots reuse the same stream.
        """
        start = x.index[0].isoformat() if len(x) else None
        end = x.index[-1].isoformat() if len(x) else None
        cache_key = (tf, int(retrain_every), len(x), start, end)
        now = time.time()
        with _CACHE_LOCK:
            cached = _PREDICTION_CACHE.get(cache_key)
            if cached and now - cached[0] < _CACHE_TTL_SECONDS:
                return {i: dict(p) for i, p in cached[1].items()}
            _PREDICTION_CACHE.pop(cache_key, None)

        warmup = 700
        step = max(200, int(retrain_every))
        predictions = {}
        model = GoldAI()
        last_fit = -1
        for i in range(warmup, len(x) - 1):
            if last_fit < 0 or i - last_fit >= step:
                model = GoldAI()
                ok = model.fit(tf, x.iloc[:i], RR, divisor=DIVISOR,
                               sl_atr_mult=SL_ATR_MULT)
                if not ok:
                    continue
                last_fit = i
            row = x.iloc[i]
            predictions[i] = {
                'BUY': model.probability(tf, row, 'BUY'),
                'SELL': model.probability(tf, row, 'SELL'),
            }
        with _CACHE_LOCK:
            _PREDICTION_CACHE[cache_key] = (
                time.time(), {i: dict(p) for i, p in predictions.items()}
            )
        return predictions

    def run(self, tf, bars=None, retrain_every=None, min_score_override=None,
            min_ai_override=None, _prepared=None, _predictions=None):
        started = time.time()
        requested_bars = self._requested_bars(tf, bars)
        retrain_every = int(retrain_every if retrain_every is not None else BACKTEST_RETRAIN_EVERY)
        prepared = _prepared if _prepared is not None else self._prepare(tf, requested_bars)
        raw = prepared['raw']
        x = prepared['features']
        history_partial = prepared['history_partial']
        if len(x) < 1200:
            return {'timeframe':tf, 'error':f'insufficient_bars:{len(x)}',
                    'requested_bars':requested_bars,
                    'raw_bars':prepared.get('received_bars', len(raw)),
                    'completed_bars':len(raw), 'usable_bars':len(x),
                    'history_partial':history_partial,
                    'snapshot_id':prepared.get('snapshot_id'),
                    'snapshot_start':x.index[0].isoformat() if len(x) else None,
                    'snapshot_end':x.index[-1].isoformat() if len(x) else None,
                    'snapshot_reused':prepared.get('snapshot_reused', False)}
        predictions = (_predictions if _predictions is not None else
                       self._walk_forward_predictions(tf, x, retrain_every))
        min_score, min_ai = thresholds_for(tf)
        if min_score_override is not None:
            min_score = float(min_score_override)
        if min_ai_override is not None:
            min_ai = float(min_ai_override)

        warmup = 700
        trades = []
        i = warmup
        while i < len(x) - 1:
            probs = predictions.get(i)
            if probs is None:
                i += 1
                continue
            row = x.iloc[i]
            prefix = pd.concat([x.iloc[:i+1].copy(), row.to_frame().T], axis=0)
            setup = build_setup(prefix, probs, RR, DIVISOR, SL_ATR_MULT,
                                min_score, min_ai, self._context(tf, row.name, x))
            if setup:
                start = raw.index.searchsorted(row.name, side='right')
                result, end_j, act_j = self._resolve(raw, start, setup)
                if result:
                    r = 2.0 if result == 'TP' else -1.0
                    trades.append({
                        'time': row.name.isoformat(),
                        'direction': setup['direction'],
                        'score': float(setup['score']),
                        'score_band': self._score_band(setup['score']),
                        'ai': float(setup['ai_prob']),
                        'ai_band': self._ai_band(setup['ai_prob']),
                        'result': result,
                        'r': r,
                        'activation_time': raw.index[act_j].isoformat(),
                    })
                    i = max(i + 1, end_j + 1)
                    continue
            i += 1

        n = len(trades)
        w = sum(t['result'] == 'TP' for t in trades)
        net = sum(t['r'] for t in trades)
        gp = sum(max(t['r'], 0) for t in trades)
        gl = abs(sum(min(t['r'], 0) for t in trades))
        res = {
            'timeframe': tf, 'requested_bars': requested_bars,
            'raw_bars': prepared.get('received_bars', len(raw)),
            'completed_bars': len(raw), 'usable_bars': len(x), 'bars': len(x),
            'history_partial': history_partial,
            'snapshot_id': prepared.get('snapshot_id'),
            'snapshot_start': x.index[0].isoformat() if len(x) else None,
            'snapshot_end': x.index[-1].isoformat() if len(x) else None,
            'snapshot_reused': prepared.get('snapshot_reused', False),
            'trades': n, 'wins': w,
            'losses': n-w, 'win_rate': 100*w/n if n else 0, 'net_r': net,
            'avg_score': sum(t['score'] for t in trades)/n if n else 0,
            'profit_factor': gp/gl if gl else (float('inf') if gp else 0),
            'max_drawdown_r': self._dd(trades),
            'expectancy_r': net/n if n else 0,
            'direction': self._groups(trades, 'direction'),
            'score_bands': self._groups(trades, 'score_band'),
            'ai_bands': self._groups(trades, 'ai_band'),
            'periods': self._period_groups(trades, x.index[0] if len(x) else None,
                                           x.index[-1] if len(x) else None),
            'quarters': self._quarter_groups(trades, x.index[0] if len(x) else None,
                                             x.index[-1] if len(x) else None),
            'sequence': self._sequence_stats(trades),
            'elapsed_s': round(time.time()-started, 1),
            'trades_detail': trades[-100:],
        }
        self.last[tf] = res
        return res

    def run_grid(self, tf, bars=None, retrain_every=None, scores=None, ai_probs=None, progress=None):
        """Run a threshold-only grid on identical walk-forward data.

        The XGBoost/model/risk logic is unchanged. Only the acceptance thresholds
        are varied, so the resulting table measures threshold sensitivity rather
        than silently changing the strategy.
        """
        scores = tuple(scores or GRID_SCORES)
        ai_probs = tuple(ai_probs or GRID_AI_PROBS)
        rows = []
        total = len(scores) * len(ai_probs)
        done = 0
        # Fetch, feature-engineer, and align higher-timeframe context once.
        # Every threshold combination reuses this exact snapshot. Model training
        # is deterministic (fixed random_state) so thresholds are the only
        # variable affecting trade acceptance.
        requested_bars = self._requested_bars(tf, bars)
        prepared = self._prepare(tf, requested_bars)
        predictions = None
        if len(prepared['features']) >= 1200:
            predictions = self._walk_forward_predictions(
                tf, prepared['features'],
                int(retrain_every if retrain_every is not None else BACKTEST_RETRAIN_EVERY)
            )
        for score in scores:
            for ai in ai_probs:
                done += 1
                if progress:
                    progress(tf, done, total, float(score), float(ai))
                r = self.run(tf, bars=requested_bars, retrain_every=retrain_every,
                             min_score_override=score, min_ai_override=ai,
                             _prepared=prepared, _predictions=predictions)
                if r.get('error'):
                    rows.append({'score':float(score), 'ai':float(ai), 'error':r.get('error')})
                    continue
                rows.append({
                    'score': float(score), 'ai': float(ai),
                    'trades': int(r.get('trades',0)),
                    'win_rate': float(r.get('win_rate',0)),
                    'net_r': float(r.get('net_r',0)),
                    'pf': float(r.get('profit_factor',0)),
                    'dd': float(r.get('max_drawdown_r',0)),
                    'e': float(r.get('expectancy_r',0)),
                    'raw_bars': int(r.get('raw_bars',0)),
                    'completed_bars': int(r.get('completed_bars',0)),
                    'usable_bars': int(r.get('usable_bars',0)),
                    'partial': bool(r.get('history_partial',False)),
                })
        default_score, default_ai = ((MIN_SCORE_5M, MIN_AI_PROB_5M) if tf == '5m' else
                                     ((MIN_SCORE_1H, MIN_AI_PROB_1H) if tf == '1h' else
                                      (MIN_SCORE, MIN_AI_PROB)))
        baseline = next((row for row in rows
                         if 'error' not in row and row['score'] == float(default_score)
                         and row['ai'] == float(default_ai)), None)
        return {'timeframe':tf, 'rows':rows, 'scores':scores, 'ai_probs':ai_probs,
                'bars':requested_bars, 'raw_bars':prepared.get('received_bars', len(prepared['raw'])),
                'completed_bars':len(prepared['raw']),
                'usable_bars':len(prepared['features']),
                'history_partial':prepared['history_partial'],
                'snapshot_id':prepared.get('snapshot_id'),
                'snapshot_reused':prepared.get('snapshot_reused', False),
                'snapshot_start':prepared['features'].index[0].isoformat() if len(prepared['features']) else None,
                'snapshot_end':prepared['features'].index[-1].isoformat() if len(prepared['features']) else None,
                'default_score':float(default_score), 'default_ai':float(default_ai),
                'baseline':baseline,
                'trials':len(rows)}

    @staticmethod
    def format_grid_ar(grid_results):
        lines = ['🔬 <b>اختبار عتبات Score / AI — Grid Search</b>',
                 '', 'نفس بيانات Walk-Forward ونفس XGBoost ونفس إدارة الصفقة؛ التغيير الوحيد هو عتبات القبول.',
                 '⚠️ ترتيب النتائج داخل العينة لا يعني ضمانًا مستقبليًا.', '']
        for tf, g in grid_results.items():
            lines += [f'⏱️ <b>{tf}</b> — {g.get("trials",0)} تركيبة']
            lines.append(
                f'📚 البيانات الثابتة لهذه التركيبات: {g.get("raw_bars", 0)} شمعة خام | '
                f'{g.get("usable_bars", 0)} قابلة للاستخدام'
            )
            if g.get('history_partial'):
                lines.append('⚠️ البيانات المتاحة أقل من العدد المطلوب.')
            if g.get('snapshot_reused'):
                lines.append('♻️ أُعيد استخدام لقطة البيانات المحفوظة لمقارنة عادلة مع الاختبار السابق.')
            if g.get('snapshot_start') and g.get('snapshot_end'):
                lines.append(f'🗓️ النطاق: {g["snapshot_start"]} → {g["snapshot_end"]}')
            if g.get('snapshot_id'):
                lines.append(f'🧾 معرّف اللقطة: {g["snapshot_id"]}')
            if g.get('baseline'):
                b = g['baseline']
                pf_base = '∞' if np.isinf(b['pf']) else f"{b['pf']:.2f}"
                lines.append(f"⚙️ الإعداد الحالي ({g['default_score']:.0f}/{g['default_ai']*100:.0f}%): {b['trades']} ص | Net {b['net_r']:+.1f}R | PF {pf_base} | E {b['e']:+.2f}R")
            lines.append('')
            rows = [r for r in g.get('rows',[]) if 'error' not in r]
            rows.sort(key=lambda z: (-z['net_r'], -z['pf'], z['dd'], -z['trades']))
            for r in rows:
                pf = '∞' if np.isinf(r['pf']) else f"{r['pf']:.2f}"
                lines.append(f"Score {r['score']:.0f} | AI {r['ai']*100:.0f}% | {r['trades']} ص | WR {r['win_rate']:.1f}% | Net {r['net_r']:+.1f}R | PF {pf} | DD {r['dd']:.1f}R | E {r['e']:+.2f}R")
            errors = [r for r in g.get('rows',[]) if 'error' in r]
            if errors:
                lines += [f'❌ أخطاء: {len(errors)}']
            lines.append('')
        return '\n'.join(lines)

    def run_all(self):
        out = {}
        bars_by_tf = {
            '1m': BACKTEST_BARS_1M,
            '5m': BACKTEST_BARS_5M,
            '15m': BACKTEST_BARS_15M,
            '30m': BACKTEST_BARS_30M,
            '1h': BACKTEST_BARS_1H,
        }
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
            history_note = f'⚠️ التاريخ المتاح أقل من المطلوب: {r.get("raw_bars",0):,} / {r.get("requested_bars",0):,} — تم تشغيل الاختبار على المتاح.' if r.get('history_partial') else ''
            lines += ([history_note] if history_note else []) + [f'⏱️ <b>{tf}</b>',f'📚 الشموع الخام المستلمة: {r.get("raw_bars",0)} / المطلوب: {r.get("requested_bars",0)}',f'📚 الشموع المكتملة بعد استبعاد الحالية: {r.get("completed_bars",r.get("raw_bars",0))}',f'📚 الشموع القابلة للاستخدام: {r.get("usable_bars",0)}']
            if r.get('snapshot_reused'):
                lines.append('♻️ أُعيد استخدام لقطة البيانات المحفوظة.')
            if r.get('snapshot_start') and r.get('snapshot_end'):
                lines.append(f'🗓️ النطاق: {r["snapshot_start"]} → {r["snapshot_end"]}')
            if r.get('snapshot_id'):
                lines.append(f'🧾 معرّف اللقطة: {r["snapshot_id"]}')
            lines += [f'📌 الصفقات: {n}',f'✅ ربح: {w} | ❌ خسارة: {l}',f'🎯 الفوز: {r.get("win_rate",0):.1f}%',f'⚖️ صافي: {r.get("net_r",0):+.1f}R',f'📊 متوسط Score: {r.get("avg_score",0):.1f}',f'💰 Profit Factor: {pf}',f'📉 Max Drawdown: {r.get("max_drawdown_r",0):.1f}R',f'📐 Expectancy: {r.get("expectancy_r",0):+.2f}R/صفقة','↔️ <b>شراء/بيع:</b>']
            lines += GoldBacktest._fmt_groups(r.get('direction',[]),['BUY','SELL'])
            lines += ['📊 <b>حسب Score:</b>'] + GoldBacktest._fmt_groups(r.get('score_bands',[]),['65-69','70-74','75-79','80+'])
            lines += ['🧠 <b>حسب تقدير AI:</b>'] + GoldBacktest._fmt_groups(r.get('ai_bands',[]),['أقل من 30%','30-39%','40-49%','50-59%','60%+'])
            lines += ['🕒 <b>توزيع النتائج زمنيًا:</b>'] + GoldBacktest._fmt_groups(r.get('periods',[]),['النصف الأول','النصف الثاني'])
            lines += ['📅 <b>حسب أرباع الفترة:</b>'] + GoldBacktest._fmt_groups(r.get('quarters',[]),['الربع 1','الربع 2','الربع 3','الربع 4'])
            seq = r.get('sequence', {})
            lines += [f"🔥 أطول سلسلة ربح: {seq.get('max_win_streak',0)}", f"🧊 أطول سلسلة خسارة: {seq.get('max_loss_streak',0)}", f"📉 أقصى سحب محسوب من تسلسل الصفقات: {seq.get('max_dd_r',0):.1f}R", '']
        gross_profit = sum(sum(max(t.get('r', 0), 0) for t in results.get(tf, {}).get('trades_detail', [])) for tf in TIMEFRAMES)
        gross_loss = abs(sum(min(t.get('r', 0), 0) for tf in TIMEFRAMES for t in results.get(tf, {}).get('trades_detail', [])))
        pf = gross_profit / gross_loss if gross_loss else (float('inf') if gross_profit else 0); pf = '∞' if np.isinf(pf) else f'{pf:.2f}'
        lines += [f'📌 <b>الإجمالي:</b> {total} صفقة',f'🎯 <b>Win Rate:</b> {(100*wins/total if total else 0):.1f}%',f'⚖️ <b>Net:</b> {net:+.1f}R',f'💰 <b>Profit Factor:</b> {pf}','','⚠️ هذا اختبار تاريخي وليس ضمانًا للنتائج المستقبلية.','⚠️ الإحصاءات لا تعني أن الـAI يتنبأ بالربح يقينًا.']
        return '\n'.join(lines)
