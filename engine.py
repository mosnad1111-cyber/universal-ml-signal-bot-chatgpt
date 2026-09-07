import time,threading,traceback
import pandas as pd
from config import *
from market import fetch,add_features,confirmed_pivots,PERIODS
from ai_engine import GoldAI
from decision import build_setup
from db import DB

class Engine:
    def __init__(self,send): self.send=send; self.db=DB(DB_PATH); self.ai=GoldAI(); self.stop_event=threading.Event(); self.last_model=0; self.last_context={}
    def fmt(self,n): return f'{float(n):,.2f}'
    def _completed(self,df): return df.iloc[:-1].copy() if len(df)>1 else df
    def _context(self,tf, target_index):
        ctx={}
        sources={'5m':['15m','1h'],'15m':['1h'],'1h':[]}
        for htf in sources[tf]:
            try:
                d=add_features(fetch(GOLD_DATA_SYMBOL,htf)).dropna(); d=self._completed(d)
                if len(d):
                    r=d.iloc[-1]
                    trend=1 if r['ema20']>r['ema50']>r['ema200'] else (-1 if r['ema20']<r['ema50']<r['ema200'] else 0)
                    ctx[htf]={'trend':trend,'rsi':float(r['rsi']),'macd_hist':float(r['macd_hist']),'dist_ema20':float(r['dist_ema20'])}
            except Exception: ctx[htf]={'trend':0,'rsi':50.0,'macd_hist':0.0,'dist_ema20':0.0}
        return ctx
    def _enrich(self,tf,x):
        x=x.copy()
        for htf,prefix in [('15m','ctx15'),('1h','ctx1h')]:
            if (tf=='5m' and htf in ('15m','1h')) or (tf=='15m' and htf=='1h'):
                try:
                    h=add_features(fetch(GOLD_DATA_SYMBOL,htf)).dropna(); h=self._completed(h)
                    h=h[['ema20','ema50','ema200','rsi','macd_hist','dist_ema20']].copy()
                    h['trend']=0; h.loc[(h.ema20>h.ema50)&(h.ema50>h.ema200),'trend']=1; h.loc[(h.ema20<h.ema50)&(h.ema50<h.ema200),'trend']=-1
                    h=h.rename(columns={'trend':prefix+'_trend','rsi':prefix+'_rsi','macd_hist':prefix+'_macd_hist','dist_ema20':prefix+'_dist_ema20'}).drop(columns=['ema20','ema50','ema200'])
                    x=pd.merge_asof(x.sort_index(),h.sort_index(),left_index=True,right_index=True,direction='backward')
                except Exception:
                    x[prefix+'_trend']=0; x[prefix+'_rsi']=50; x[prefix+'_macd_hist']=0; x[prefix+'_dist_ema20']=0
            else:
                x[prefix+'_trend']=0; x[prefix+'_rsi']=50; x[prefix+'_macd_hist']=0; x[prefix+'_dist_ema20']=0
        return x
    def signal_text(self,s):
        side='🟢 شراء' if s['direction']=='BUY' else '🔴 بيع'; p=s['ai_prob']*100
        return (f'🚨 <b>إشارة ذهب جديدة</b>\n\n🥇 <b>الذهب:</b> {TV_SYMBOL}\n⏱️ <b>الفريم:</b> {s["timeframe"]}\n📌 <b>الاتجاه:</b> {side}\n\n🎯 <b>الدخول:</b> {self.fmt(s["entry"])}\n🛑 <b>وقف الخسارة:</b> {self.fmt(s["sl"])}\n💰 <b>الهدف:</b> {self.fmt(s["tp"])}\n⚖️ <b>RR:</b> 1:{RR:g}\n🤖 <b>درجة الجودة:</b> {s["score"]:.1f}/100\n🧠 <b>احتمال نجاح الصفقة:</b> {p:.1f}%\n🟡 <b>الحالة:</b> انتظار التفعيل\n\n⚠️ للتحليل والتنفيذ اليدوي فقط.')
    def train_models(self):
        for tf in TIMEFRAMES:
            try:
                raw=fetch(GOLD_DATA_SYMBOL,tf); x=add_features(raw).dropna(); x=self._enrich(tf,x); self.ai.fit(tf,x,RR)
            except Exception: traceback.print_exc()
        self.last_model=time.time()
    def scan(self,tf):
        df=fetch(GOLD_DATA_SYMBOL,tf); x=add_features(df).dropna(); x=self._enrich(tf,x)
        if len(x)<300:return None
        row=x.iloc[-2]
        probs={'BUY':self.ai.probability(tf,row,'BUY'),'SELL':self.ai.probability(tf,row,'SELL')}
        context={'trend':0}
        if tf=='5m':
            context['trend']=1 if self._context(tf,row.name).get('15m',{}).get('trend',0)>0 and self._context(tf,row.name).get('1h',{}).get('trend',0)>0 else (-1 if self._context(tf,row.name).get('15m',{}).get('trend',0)<0 and self._context(tf,row.name).get('1h',{}).get('trend',0)<0 else 0)
        elif tf=='15m': context['trend']=self._context(tf,row.name).get('1h',{}).get('trend',0)
        setup=build_setup(x,probs,RR,DIVISOR,SL_ATR_MULT,MIN_SCORE,MIN_AI_PROB,context)
        if not setup:return None
        highs,lows=confirmed_pivots(df,PIVOT_LEN); rh=highs[-1][0] if highs else 0; rl=lows[-1][0] if lows else 0
        setup.update({'symbol':GOLD_DATA_SYMBOL,'timeframe':tf,'wave_key':f'{tf}:{rh}:{rl}:{setup["candle_time"]}'})
        if self.db.open_for(GOLD_DATA_SYMBOL,tf):return None
        sid=self.db.create(setup)
        if sid: setup['id']=sid; self.send(self.signal_text(setup)); return setup
    def monitor(self):
        while not self.stop_event.is_set():
            for s in self.db.all_open():
                try:
                    df=fetch(GOLD_DATA_SYMBOL,s['timeframe']); high=float(df['High'].iloc[-1]); low=float(df['Low'].iloc[-1])
                    if s['status']=='WAITING':
                        if low<=s['entry']<=high:
                            self.db.activate(s['id'],s['entry']); self.send(f'🟢 <b>تم تفعيل صفقة الذهب</b>\n\n⏱️ {s["timeframe"]}\n📍 الدخول: {self.fmt(s["entry"])}\n🛑 SL: {self.fmt(s["sl"])}\n🎯 TP: {self.fmt(s["tp"])}')
                    else:
                        sl=low<=s['sl'] if s['direction']=='BUY' else high>=s['sl']; tp=high>=s['tp'] if s['direction']=='BUY' else low<=s['tp']
                        if sl and tp: self.db.close(s['id'],'SL',s['sl']); self.send(f'❌ <b>إغلاق محافظ: SL</b>\n⏱️ {s["timeframe"]}\nالذهب لمس SL وTP في نفس الشمعة؛ احتُسبت SL.')
                        elif sl: self.db.close(s['id'],'SL',s['sl']); self.send(f'❌ <b>ضرب وقف الخسارة</b>\n⏱️ {s["timeframe"]}\n💵 السعر: {self.fmt(s["sl"])}')
                        elif tp: self.db.close(s['id'],'TP',s['tp']); self.send(f'✅ <b>تحقق الهدف TP</b>\n⏱️ {s["timeframe"]}\n💵 السعر: {self.fmt(s["tp"])}\n🎉 نتيجة: +{RR:g}R')
                except Exception: traceback.print_exc()
            time.sleep(MONITOR_SECONDS)
    def scanner(self):
        while not self.stop_event.is_set():
            try:
                if time.time()-self.last_model>MODEL_REFRESH_HOURS*3600 or not self.ai.models:self.train_models()
                for tf in TIMEFRAMES:
                    try:self.scan(tf)
                    except Exception:traceback.print_exc()
            except Exception:traceback.print_exc()
            time.sleep(SCAN_SECONDS)
    def start(self): threading.Thread(target=self.scanner,daemon=True).start(); threading.Thread(target=self.monitor,daemon=True).start()
    def stats_text(self):
        total,w,l=self.db.closed_stats(); wr=100*w/total if total else 0; r=w*RR-l
        m=self.ai.info(); lines=[f'📊 <b>إحصائيات الذهب</b>','',f'الصفقات المغلقة: <b>{total}</b>',f'✅ رابحة: <b>{w}</b>',f'❌ خاسرة: <b>{l}</b>',f'📈 الفوز: <b>{wr:.1f}%</b>',f'⚖️ صافي النتيجة النظرية: <b>{r:+.1f}R</b>','', '🧠 <b>النموذج</b>']
        for tf,v in m.items(): lines.append(f'{tf}: {v.get("engine","-")} | AUC {v.get("auc",0):.3f} | Accuracy {v.get("accuracy",0):.3f}')
        return '\n'.join(lines)
