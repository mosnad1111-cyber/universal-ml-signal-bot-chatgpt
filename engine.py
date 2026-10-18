import time
import threading
import traceback
import pandas as pd
from config import *
from market import fetch, add_features, confirmed_pivots
from ai_engine import GoldAI
from decision import build_setup
from db import DB

class Engine:
    def __init__(self, send):
        self.send = send
        self.db = DB(DB_PATH)
        self.ai = {a['symbol']: GoldAI() for a in ASSETS.values()}
        self.stop_event = threading.Event()
        self.last_model = 0
        self.last_train_attempt = 0
        self.last_context = {}
        self.last_diagnostics = {f'{s}|{tf}': {} for s in self.ai for tf in TIMEFRAMES}
        self.last_scan_at = {f'{s}|{tf}': 0 for s in self.ai for tf in TIMEFRAMES}
        self.model_training_ok = {}
        self.scan_counts = {f'{s}|{tf}': 0 for s in self.ai for tf in TIMEFRAMES}
        self.reject_counts = {f'{s}|{tf}': {} for s in self.ai for tf in TIMEFRAMES}

    def fmt(self, n): return f'{float(n):,.2f}'
    def _completed(self, df): return df.iloc[:-1].copy() if len(df) > 1 else df

    def _context(self, tf, target_index, symbol):
        ctx = {}
        sources = {'5m': ['15m', '1h'], '15m': ['1h'], '1h': []}
        for htf in sources.get(tf, []):
            try:
                d = self._completed(add_features(fetch(symbol, htf)).dropna())
                d.index = d.index + pd.Timedelta({'15m': '15min', '1h': '1h'}[htf])
                if len(d):
                    before = d.loc[d.index <= target_index]
                    r = before.iloc[-1] if len(before) else d.iloc[0]
                    trend = 1 if r['ema20'] > r['ema50'] > r['ema200'] else (-1 if r['ema20'] < r['ema50'] < r['ema200'] else 0)
                    ctx[htf] = {'trend': trend, 'rsi': float(r['rsi']), 'macd_hist': float(r['macd_hist']), 'dist_ema20': float(r['dist_ema20'])}
            except Exception as exc:
                ctx[htf] = {'trend': 0, 'rsi': 50.0, 'macd_hist': 0.0, 'dist_ema20': 0.0, 'error': str(exc)}
        return ctx

    def _enrich(self, tf, x, symbol):
        x = x.copy()
        for htf, prefix in [('15m', 'ctx15'), ('1h', 'ctx1h')]:
            needed = (tf == '5m' and htf in ('15m', '1h')) or (tf == '15m' and htf == '1h')
            if needed:
                try:
                    h = add_features(self._completed(fetch(symbol, htf))).dropna()
                    h = h[['ema20','ema50','ema200','rsi','macd_hist','dist_ema20']].copy(); h['trend'] = 0
                    h.loc[(h.ema20 > h.ema50) & (h.ema50 > h.ema200), 'trend'] = 1
                    h.loc[(h.ema20 < h.ema50) & (h.ema50 < h.ema200), 'trend'] = -1
                    h = h.rename(columns={'trend':prefix+'_trend','rsi':prefix+'_rsi','macd_hist':prefix+'_macd_hist','dist_ema20':prefix+'_dist_ema20'}).drop(columns=['ema20','ema50','ema200'])
                    h.index = h.index + pd.Timedelta({'15m': '15min', '1h': '1h'}[htf])
                    x = pd.merge_asof(x.sort_index(), h.sort_index(), left_index=True, right_index=True, direction='backward')
                except Exception:
                    x[prefix+'_trend']=0; x[prefix+'_rsi']=50; x[prefix+'_macd_hist']=0; x[prefix+'_dist_ema20']=0
            else:
                x[prefix+'_trend']=0; x[prefix+'_rsi']=50; x[prefix+'_macd_hist']=0; x[prefix+'_dist_ema20']=0
        return x

    def _thresholds(self, tf):
        return thresholds_for(tf)

    def signal_text(self, s):
        side = '🟢 شراء' if s['direction'] == 'BUY' else '🔴 بيع'; p = s['ai_prob'] * 100
        asset = next((a for a in ASSETS.values() if a['symbol'] == s['symbol']), {'label': s['symbol'], 'icon': '📈'})
        return (f'🚨 <b>إشارة {asset["label"]} جديدة</b>\n\n{asset["icon"]} <b>{asset["label"]}:</b> {s["symbol"]}\n⏱️ <b>الفريم:</b> {s["timeframe"]}\n📌 <b>الاتجاه:</b> {side}\n\n🎯 <b>الدخول:</b> {self.fmt(s["entry"])}\n🛑 <b>وقف الخسارة:</b> {self.fmt(s["sl"])}\n💰 <b>الهدف:</b> {self.fmt(s["tp"])}\n⚖️ <b>RR:</b> 1:{RR:g}\n🤖 <b>درجة الجودة:</b> {s["score"]:.1f}/100\n🧠 <b>تقدير AI:</b> {p:.1f}%\n🟡 <b>الحالة:</b> انتظار التفعيل\n\n⚠️ للتحليل والتنفيذ اليدوي فقط.')

    def train_models(self):
        successes=0; expected=len(self.ai)*len(TIMEFRAMES)
        for symbol, ai in self.ai.items():
            for tf in TIMEFRAMES:
                key=f'{symbol}|{tf}'
                try:
                    history=self._completed(fetch(symbol,tf,period=MODEL_HISTORY_BARS))
                    x=self._enrich(tf,add_features(history).dropna(),symbol)
                    ok=ai.fit(tf,x,RR,divisor=DIVISOR,sl_atr_mult=SL_ATR_MULT); self.model_training_ok[key]=bool(ok)
                    if ok: successes+=1
                    else: ai.errors.setdefault(tf,'fit_returned_false')
                except Exception as exc:
                    self.model_training_ok[key]=False; ai.errors[tf]=f'{type(exc).__name__}: {exc}'; traceback.print_exc()
        self.last_model=time.time() if successes == expected else 0
        return successes

    def scan(self, tf, symbol=GOLD_DATA_SYMBOL):
        key=f'{symbol}|{tf}'; ai=self.ai[symbol]
        diag={'symbol':symbol,'timeframe':tf,'scan_time':time.time(),'model_ready':tf in ai.models}
        self.last_scan_at[key]=diag['scan_time']; self.last_diagnostics[key]=diag
        try:
            df=fetch(symbol,tf,period=MODEL_HISTORY_BARS); diag['raw_bars']=len(df)
            x=self._enrich(tf,add_features(df).dropna(),symbol); diag['feature_bars']=len(x)
            if len(x)<300: diag['reject']='insufficient_feature_bars'; return None
            if tf not in ai.models: diag['reject']='model_not_ready'; return None
            row=x.iloc[-2]; diag['candle_time']=row.name.isoformat(); diag['last_raw_time']=df.index[-1].isoformat()
            probs={'BUY':ai.probability(tf,row,'BUY'),'SELL':ai.probability(tf,row,'SELL')}
            diag['buy_ai']=None if probs['BUY'] is None else round(probs['BUY'],4); diag['sell_ai']=None if probs['SELL'] is None else round(probs['SELL'],4)
            ctx=self._context(tf,row.name,symbol); self.last_context[key]=ctx; context={'trend':0}
            if tf=='5m':
                t15=ctx.get('15m',{}).get('trend',0); t1h=ctx.get('1h',{}).get('trend',0); context['trend']=1 if t15>0 and t1h>0 else (-1 if t15<0 and t1h<0 else 0)
            elif tf=='15m': context['trend']=ctx.get('1h',{}).get('trend',0)
            diag['context_trend']=context['trend']
            min_score, min_ai=self._thresholds(tf); diag['min_score_required']=min_score; diag['min_ai_required']=min_ai
            setup=build_setup(x,probs,RR,DIVISOR,SL_ATR_MULT,min_score,min_ai,context,diagnostics=diag)
            if not setup: self.last_diagnostics[key]=diag; return None
            if not signal_direction_allowed(tf,setup['direction']):
                diag['reject']='strategy_filter_'+tf+'_'+setup['direction'].lower(); self.last_diagnostics[key]=diag; return None
            highs,lows=confirmed_pivots(df,PIVOT_LEN); rh=highs[-1][0] if highs else 0; rl=lows[-1][0] if lows else 0
            setup.update({'symbol':symbol,'timeframe':tf,'wave_key':f'{symbol}:{tf}:{rh}:{rl}:{setup["candle_time"]}'})
            if self.db.open_for(symbol,tf):
                diag['reject']='open_signal_exists'; self.last_diagnostics[key]=diag; return None
            if not getattr(self.send,'__self__',None) or not self.send.__self__.ready():
                diag['reject']='telegram_not_ready'; self.last_diagnostics[key]=diag; return None
            sid=self.db.create(setup)
            if not sid: diag['reject']='database_create_failed'; self.last_diagnostics[key]=diag; return None
            setup['id']=sid
            if not self.send(self.signal_text(setup)):
                self.db.cancel(sid,'NOT_SENT'); diag['reject']='telegram_send_failed'; self.last_diagnostics[key]=diag; return None
            diag['accepted']=True; diag['telegram_sent']=True; self.last_diagnostics[key]=diag; return setup
        except Exception as exc:
            diag['reject']=f'exception:{type(exc).__name__}:{exc}'; self.last_diagnostics[key]=diag; raise

    def _record_scan_outcome(self, tf, symbol=GOLD_DATA_SYMBOL):
        key=f'{symbol}|{tf}'
        self.scan_counts[key]=self.scan_counts.get(key,0)+1
        d=self.last_diagnostics.get(key,{})
        reason=d.get('reject')
        if reason:
            counts=self.reject_counts.setdefault(key,{})
            counts[reason]=counts.get(reason,0)+1

    def diagnostics_text(self):
        lines=['🔎 <b>تشخيص محرك الإشارات</b>','']
        labels={'score_below_threshold':'الـScore أقل من الحد المطلوب','ai_below_threshold':'احتمال AI أقل من الحد المطلوب','risk_out_of_range':'المخاطرة خارج النطاق','model_not_ready':'النموذج غير جاهز','open_signal_exists':'توجد إشارة مفتوحة لهذا الفريم','telegram_not_ready':'Telegram غير جاهز لاستقبال الإشارة','telegram_send_failed':'تعذر إرسال الإشارة عبر Telegram','insufficient_feature_bars':'عدد الشموع غير كافٍ','no_model_probabilities':'لا توجد نتيجة من النموذج','direction_ambiguous':'اتجاه الشراء والبيع غير واضح','against_higher_timeframe_trend':'الإشارة تعاكس اتجاه الفريم الأعلى','invalid_atr':'قيمة ATR غير صالحة','invalid_risk':'المخاطرة غير صالحة','insufficient_bars':'عدد الشموع غير كافٍ'}
        for asset in ASSETS.values():
            lines += [f'{asset["icon"]} <b>{asset["label"]}</b>','']
            for tf in TIMEFRAMES:
                key=f'{asset["symbol"]}|{tf}'; d=self.last_diagnostics.get(key,{})
                if not d: lines += [f'⏱️ <b>{tf}</b>: لم يتم الفحص بعد','']; continue
                lines += [f'⏱️ <b>{tf}</b>',f'🤖 النموذج: {"جاهز" if d.get("model_ready") else "غير جاهز"}',f'🔁 مرات الفحص المسجلة: {self.scan_counts.get(key,0)}']
                common=sorted(self.reject_counts.get(key,{}).items(),key=lambda z:z[1],reverse=True)[:3]
                if common: lines.append('📉 أكثر أسباب الرفض: '+ '، '.join(f'{labels.get(k,k)} ({v})' for k,v in common))
                if 'raw_bars' in d: lines.append(f'📚 البيانات: {d["raw_bars"]} شمعة')
                if 'candle_time' in d: lines.append(f'🕐 الشمعة: {d["candle_time"]}')
                if 'buy_ai' in d: lines.append(f'🟢 AI شراء: {d["buy_ai"]*100:.1f}%' if d['buy_ai'] is not None else '🟢 AI شراء: —')
                if 'sell_ai' in d: lines.append(f'🔴 AI بيع: {d["sell_ai"]*100:.1f}%' if d['sell_ai'] is not None else '🔴 AI بيع: —')
                if 'buy_score' in d: lines.append(f'📈 Score شراء: {d["buy_score"]:.1f}')
                if 'sell_score' in d: lines.append(f'📉 Score بيع: {d["sell_score"]:.1f}')
                if 'min_score_required' in d: lines.append(f'🎯 حد Score: {d["min_score_required"]:.0f}')
                if 'min_ai_required' in d: lines.append(f'🧠 حد AI: {d["min_ai_required"]*100:.0f}%')
                if 'context_trend' in d: lines.append(f'🧭 اتجاه الفريمات الأعلى: {d["context_trend"]:+d}')
                if d.get('reject'): lines.append(f'❌ السبب: <b>{labels.get(d["reject"],d["reject"])}</b>')
                elif d.get('accepted'): lines.append('✅ النتيجة: تم قبول الإعداد وإرسال الإشارة')
                lines.append('')
        return '\n'.join(lines)

    @staticmethod
    def _bars_since_signal(df, candle_time):
        """Return bar age using provider candle-open timestamps, timezone-safe."""
        if df is None or df.empty or not candle_time:
            return 0
        try:
            signal_ts = pd.Timestamp(candle_time)
            idx = df.index
            if getattr(idx, 'tz', None) is not None and signal_ts.tzinfo is None:
                signal_ts = signal_ts.tz_localize(idx.tz)
            elif getattr(idx, 'tz', None) is None and signal_ts.tzinfo is not None:
                signal_ts = signal_ts.tz_convert(None)
            signal_pos = int(idx.searchsorted(signal_ts, side='right')) - 1
            if signal_pos < 0:
                return 0
            return max(0, len(df) - 1 - signal_pos)
        except Exception:
            return 0

    def monitor(self):
        while not self.stop_event.is_set():
            for s in self.db.all_open():
                try:
                    symbol=s['symbol']; asset=next((a for a in ASSETS.values() if a['symbol']==symbol), {'label':symbol,'icon':'📈'})
                    df=fetch(symbol,s['timeframe'])
                    if df is None or df.empty: continue
                    high=float(df['High'].iloc[-1]); low=float(df['Low'].iloc[-1]); close=float(df['Close'].iloc[-1])
                    age=self._bars_since_signal(df,s['candle_time'])
                    if s['status']=='WAITING':
                        if low<=s['entry']<=high:
                            self.db.activate(s['id'],s['entry'])
                            self.send(f'🟢 <b>تم تفعيل صفقة {asset["label"]}</b>\n\n⏱️ {s["timeframe"]}\n📍 الدخول: {self.fmt(s["entry"])}\n🛑 SL: {self.fmt(s["sl"])}\n🎯 TP: {self.fmt(s["tp"])}')
                        elif age>=max(1,SIGNAL_PENDING_TTL_BARS):
                            self.db.cancel(s['id'],'EXPIRED_UNFILLED')
                            self.send(f'⌛ <b>انتهت صلاحية إشارة {asset["label"]} دون تفعيل</b>\n⏱️ {s["timeframe"]}\nلم يصل السعر إلى الدخول خلال {SIGNAL_PENDING_TTL_BARS} شمعة؛ أُلغيت الإشارة.')
                    elif s['status']=='ACTIVE':
                        sl=low<=s['sl'] if s['direction']=='BUY' else high>=s['sl']; tp=high>=s['tp'] if s['direction']=='BUY' else low<=s['tp']
                        if sl and tp:
                            self.db.close(s['id'],'SL',s['sl']); self.send(f'❌ <b>إغلاق محافظ: SL — {asset["label"]}</b>\n⏱️ {s["timeframe"]}\n{asset["label"]} لمس SL وTP في نفس الشمعة؛ احتُسبت SL.')
                        elif sl:
                            self.db.close(s['id'],'SL',s['sl']); self.send(f'❌ <b>ضرب وقف الخسارة — {asset["label"]}</b>\n⏱️ {s["timeframe"]}\n💵 السعر: {self.fmt(s["sl"])}')
                        elif tp:
                            self.db.close(s['id'],'TP',s['tp']); self.send(f'✅ <b>تحقق الهدف TP — {asset["label"]}</b>\n⏱️ {s["timeframe"]}\n💵 السعر: {self.fmt(s["tp"])}\n🎉 نتيجة: +{RR:g}R')
                        elif age>=max(SIGNAL_PENDING_TTL_BARS+1,SIGNAL_MAX_LIFETIME_BARS):
                            self.db.close(s['id'],'TIMEOUT',close)
                            self.send(f'⏳ <b>إغلاق صفقة {asset["label"]} لانتهاء المهلة</b>\n⏱️ {s["timeframe"]}\n💵 سعر الإغلاق: {self.fmt(close)}\nأُغلقت بعد {age} شمعة دون وصول إلى TP أو SL.')
                except Exception: traceback.print_exc()
            time.sleep(MONITOR_SECONDS)

    def scanner(self):
        if self.stop_event.wait(15): return
        while not self.stop_event.is_set():
            try:
                now=time.time()
                missing_models=any(tf not in ai.models for ai in self.ai.values() for tf in TIMEFRAMES)
                refresh_due=now-self.last_model>MODEL_REFRESH_HOURS*3600
                retry_ready=now-self.last_train_attempt>=300
                if (refresh_due or missing_models) and retry_ready:
                    self.last_train_attempt=now; self.train_models()
                for symbol in self.ai:
                    for tf in TIMEFRAMES:
                        try:
                            result = self.scan(tf,symbol)
                            self._record_scan_outcome(tf,symbol)
                            d = self.last_diagnostics.get(f'{symbol}|{tf}', {})
                            print(
                                f"[scan] {symbol} {tf}: "
                                f"result={'SENT' if result else 'NO_SIGNAL'} "
                                f"model_ready={d.get('model_ready')} "
                                f"bars={d.get('raw_bars','-')} "
                                f"candle={d.get('candle_time','-')} "
                                f"buy_ai={d.get('buy_ai','-')} sell_ai={d.get('sell_ai','-')} "
                                f"buy_score={d.get('buy_score','-')} sell_score={d.get('sell_score','-')} "
                                f"reject={d.get('reject','-')}"
                            )
                        except Exception as exc:
                            self._record_scan_outcome(tf,symbol)
                            print(f"[scan] ERROR {symbol} {tf}: {type(exc).__name__}: {exc}")
                            traceback.print_exc()
            except Exception:
                traceback.print_exc()
            time.sleep(SCAN_SECONDS)

    def stats_text(self):
        lines = ['📊 <b>إحصائيات الأصول</b>']
        recent = self.db.recent(1000)
        for asset in ASSETS.values():
            symbol = asset['symbol']
            ai = self.ai[symbol]
            rows = [r for r in recent if r['symbol'] == symbol and r['status'] == 'CLOSED']
            targets = sum(r['result'] == 'TP' for r in rows)
            stops = sum(r['result'] == 'SL' for r in rows)
            timeouts = sum(r['result'] == 'TIMEOUT' for r in rows)
            profitable = 0
            losing = 0
            net_r = 0.0
            for row in rows:
                risk = abs(float(row['entry']) - float(row['sl']))
                if risk <= 0:
                    continue
                if row['result'] == 'TP':
                    realized_r = abs(float(row['tp']) - float(row['entry'])) / risk
                elif row['result'] == 'SL':
                    realized_r = -1.0
                elif row['close_price'] is not None:
                    move = float(row['close_price']) - float(row['entry'])
                    if row['direction'] == 'SELL':
                        move = -move
                    realized_r = move / risk
                else:
                    realized_r = 0.0
                net_r += realized_r
                if realized_r > 0:
                    profitable += 1
                elif realized_r < 0:
                    losing += 1
            wr = 100 * profitable / len(rows) if rows else 0
            lines += [
                '',
                f'{asset["icon"]} <b>{asset["label"]}</b>',
                f'الصفقات المغلقة: <b>{len(rows)}</b>',
                f'✅ الهدف: {targets} | ❌ الوقف: {stops} | ⏳ المهلة: {timeouts}',
                f'📈 نسبة الربح الفعلية: <b>{wr:.1f}%</b>',
                f'⚖️ صافي النتيجة: <b>{net_r:+.1f}R</b>',
                '🧠 <b>تحقق النموذج</b>'
            ]
            metrics = ai.info()
            for tf in TIMEFRAMES:
                value = metrics.get(tf, {})
                if value:
                    lines.append(f'{tf}: {value.get("engine", "-")} | AUC {value.get("auc", 0):.3f} | Acc {value.get("accuracy", 0):.3f}')
                else:
                    lines.append(f'{tf}: ❌ {ai.errors.get(tf, "غير متوفر")}')
        return '\\n'.join(lines)

    def start(self):
        """Start the background market scanner and open-trade monitor."""
        if getattr(self, "_started", False):
            return
        self._started = True
        self.stop_event.clear()
        threading.Thread(
            target=self.scanner,
            name="market-scanner",
            daemon=True,
        ).start()
        threading.Thread(
            target=self.monitor,
            name="trade-monitor",
            daemon=True,
        ).start()
