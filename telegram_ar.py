import time
import threading
import requests
from config import (
    TELEGRAM_BOT_TOKEN, BACKTEST_BARS, BACKTEST_RETRAIN_EVERY,
    TIMEFRAMES, BACKTEST_BARS_5M, BACKTEST_BARS_1H,
)
from backtest import GoldBacktest


class Telegram:
    def __init__(self, db=None):
        self.token=TELEGRAM_BOT_TOKEN
        self.base=f'https://api.telegram.org/bot{self.token}' if self.token else ''
        self.db=db; self.chat_id=None; self.offset=0; self.engine=None; self.backtest_running=False
        if self.db:
            saved=self.db.get_setting('telegram_chat_id')
            if saved:
                try: self.chat_id=int(saved)
                except Exception: self.chat_id=saved

    def ready(self): return bool(self.base and self.chat_id)

    def send(self,text,chat_id=None):
        if not self.base: return False
        cid=chat_id or self.chat_id
        if not cid: return False
        try:
            r=requests.post(self.base+'/sendMessage',json={'chat_id':cid,'text':text,'parse_mode':'HTML'},timeout=20)
            return bool(r.ok and r.json().get('ok'))
        except Exception: return False

    def send_long(self,text,chat_id=None,limit=3900):
        if not text: return True
        parts=[]; current=''
        for line in text.splitlines(True):
            if len(current)+len(line)<=limit: current+=line
            else:
                if current: parts.append(current.rstrip())
                while len(line)>limit: parts.append(line[:limit]); line=line[limit:]
                current=line
        if current: parts.append(current.rstrip())
        ok=True
        for i,part in enumerate(parts,1):
            if len(parts)>1: part=f'📄 <b>تقرير Backtest — جزء {i}/{len(parts)}</b>\n\n'+part
            if not self.send(part,chat_id): ok=False
        return ok

    def keyboard(self):
        return {'inline_keyboard':[
            [{'text':'🥇 تحليل 5 دقائق','callback_data':'scan:5m'}, {'text':'🥇 تحليل 1 ساعة','callback_data':'scan:1h'}],
            [{'text':'📊 إحصائيات الأداء','callback_data':'stats'}, {'text':'📋 آخر الإشارات','callback_data':'recent'}],
            [{'text':'🔎 تشخيص النظام','callback_data':'diagnostics'}, {'text':'🧪 Backtest 5m + 1H','callback_data':'backtest'}],
            [{'text':'🟢 حالة البوت','callback_data':'status'}]
        ]}

    def menu(self): return '🤖 <b>بوت تحليل الذهب بالذكاء الاصطناعي</b>\n\n🥇 الذهب فقط | ⏱️ 5 دقائق + 1 ساعة\n\nاختر العملية من الأزرار بالأسفل 👇'

    def send_menu(self,cid):
        if not self.base: return False
        try:
            r=requests.post(self.base+'/sendMessage',json={'chat_id':cid,'text':self.menu(),'parse_mode':'HTML','reply_markup':self.keyboard()},timeout=20)
            return bool(r.ok and r.json().get('ok'))
        except Exception: return False

    def _remember_chat(self,cid):
        self.chat_id=cid
        if self.db:
            try: self.db.set_setting('telegram_chat_id',cid)
            except Exception: pass

    def _backtest(self,cid):
        if self.backtest_running:
            self.send('⏳ <b>الـBacktest يعمل حاليًا</b>\nانتظر النتيجة الحالية قبل تشغيل اختبار آخر.',cid); return
        self.backtest_running=True
        tf_bars = {
            '5m': BACKTEST_BARS_5M,
            '1h': BACKTEST_BARS_1H,
        }
        test_timeframes = [tf for tf in TIMEFRAMES if tf in tf_bars]
        self.send(
            '🧪 <b>بدأ Backtest تاريخي</b>\n\n'
            'Walk-Forward على TVC:GOLD\n'
            f'الفريمات: {" / ".join(test_timeframes)}\n'
            f'الشموع المستهدفة: {BACKTEST_BARS}\n'
            f'إعادة التدريب كل: {BACKTEST_RETRAIN_EVERY}\n\n'
            '⏳ جاري تجهيز البيانات…', cid
        )
        def work():
            try:
                bt=GoldBacktest(); results={}
                for tf in test_timeframes:
                    bars = tf_bars[tf]
                    self.send(f'⏳ <b>جاري اختبار {tf}</b>\nتدريب Walk-Forward على {bars} شمعة تقريبًا…',cid)
                    try:
                        results[tf]=bt.run(tf,bars=bars,retrain_every=BACKTEST_RETRAIN_EVERY)
                        if results[tf].get('error'):
                            self.send(f'⚠️ <b>{tf}</b> لم يكتمل: {results[tf]["error"]}',cid)
                        else:
                            r=results[tf]
                            self.send(f'✅ <b>انتهى اختبار {tf}</b>\nالشموع: {r.get("bars",0)}\nالصفقات: {r.get("trades",0)}\nWin Rate: {r.get("win_rate",0):.1f}%\nNet: {r.get("net_r",0):+.1f}R',cid)
                    except Exception as exc:
                        results[tf]={'timeframe':tf,'error':f'{type(exc).__name__}: {exc}'}
                        self.send(f'❌ <b>فشل اختبار {tf}</b>\n{type(exc).__name__}: {exc}',cid)
                report=bt.format_ar(results)
                if not self.send_long(report,cid): self.send('⚠️ <b>اكتمل الـBacktest لكن تعذر إرسال أحد أجزاء التقرير.</b>',cid)
            except Exception as e:
                self.send(f'❌ <b>فشل الـBacktest</b>\n{type(e).__name__}: {e}',cid)
            finally: self.backtest_running=False
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        if not self.base: print('ERROR: TELEGRAM_BOT_TOKEN غير موجود'); return
        while True:
            try:
                r=requests.get(self.base+'/getUpdates',params={'timeout':25,'offset':self.offset},timeout=35)
                if not r.ok: time.sleep(5); continue
                for u in r.json().get('result',[]):
                    self.offset=u['update_id']+1
                    msg=u.get('message')
                    if msg:
                        cid=msg['chat']['id']; self._remember_chat(cid); self.send_menu(cid)
                    cb=u.get('callback_query')
                    if cb:
                        cid=cb['message']['chat']['id']; self._remember_chat(cid); data=cb.get('data','')
                        try: requests.post(self.base+'/answerCallbackQuery',json={'callback_query_id':cb['id']},timeout=10)
                        except Exception: pass
                        if data.startswith('scan:'):
                            tf=data.split(':',1)[1]
                            try:
                                s=self.engine.scan(tf)
                                if not s:
                                    d=self.engine.last_diagnostics.get(tf,{})
                                    labels={'score_below_threshold':'الـScore أقل من الحد المطلوب','risk_out_of_range':'المخاطرة خارج النطاق','model_not_ready':'النموذج غير جاهز','open_signal_exists':'توجد إشارة مفتوحة لهذا الفريم','telegram_not_ready':'Telegram غير جاهز لاستقبال الإشارة','telegram_send_failed':'تعذر إرسال الإشارة عبر Telegram','insufficient_feature_bars':'البيانات غير كافية','no_model_probabilities':'لا توجد نتيجة AI'}
                                    reason=labels.get(d.get('reject',''),d.get('reject','غير معروف'))
                                    self.send(f'⚪ <b>لا توجد إشارة قوية الآن</b>\nالفريم: {tf}\n\n🔎 السبب: <b>{reason}</b>',cid)
                            except Exception as e: self.send(f'⚠️ تعذر التحليل الآن: {e}',cid)
                        elif data=='stats': self.send(self.engine.stats_text(),cid)
                        elif data=='diagnostics': self.send(self.engine.diagnostics_text(),cid)
                        elif data=='backtest': self._backtest(cid)
                        elif data=='recent':
                            rows=self.engine.db.recent(8)
                            if not rows: self.send('📋 لا توجد إشارات مسجلة بعد.',cid)
                            else:
                                lines=['📋 <b>آخر الإشارات</b>','']
                                for s in rows:
                                    icon='🟢' if s['direction']=='BUY' else '🔴'; lines.append(f'{icon} {s["timeframe"]} | {s["direction"]} | {s["status"]} | {s["result"] or "—"}')
                                self.send('\n'.join(lines),cid)
                        elif data=='status': self.send('🟢 <b>البوت يعمل</b>\n\n🥇 الذهب فقط\n⏱️ 5m / 1H فقط\n🤖 XGBoost / AI تدريبي\n📡 مراقبة الإشارات مفعلة',cid)
            except Exception: time.sleep(5)

    def start(self,engine):
        self.engine=engine; threading.Thread(target=self.poll,daemon=True).start()
