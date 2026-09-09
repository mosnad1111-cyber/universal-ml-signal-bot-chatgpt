import time
import threading
import requests
from config import TELEGRAM_BOT_TOKEN

class Telegram:
    def __init__(self, db=None):
        self.token=TELEGRAM_BOT_TOKEN; self.base=f'https://api.telegram.org/bot{self.token}' if self.token else ''
        self.db=db; self.chat_id=None; self.offset=0; self.engine=None
        if self.db:
            saved=self.db.get_setting('telegram_chat_id')
            if saved:
                try: self.chat_id=int(saved)
                except Exception: self.chat_id=saved

    def ready(self):
        return bool(self.base and self.chat_id)

    def send(self,text,chat_id=None):
        if not self.base: return False
        cid=chat_id or self.chat_id
        if not cid: return False
        try:
            r=requests.post(self.base+'/sendMessage',json={'chat_id':cid,'text':text,'parse_mode':'HTML'},timeout=20)
            return bool(r.ok and r.json().get('ok'))
        except Exception: return False

    def keyboard(self):
        return {'inline_keyboard':[
            [{'text':'🔍 تحليل الذهب 5 دقائق','callback_data':'scan:5m'}, {'text':'🔍 تحليل الذهب 15 دقيقة','callback_data':'scan:15m'}],
            [{'text':'🔍 تحليل الذهب 1 ساعة','callback_data':'scan:1h'}],
            [{'text':'📊 إحصائيات الأداء','callback_data':'stats'}, {'text':'📋 الإشارات الأخيرة','callback_data':'recent'}],
            [{'text':'🔎 تشخيص الإشارات','callback_data':'diagnostics'}, {'text':'ℹ️ حالة البوت','callback_data':'status'}]
        ]}

    def menu(self): return '🤖 <b>بوت تحليل الذهب بالذكاء الاصطناعي</b>\n\nاختر العملية من الأزرار بالأسفل 👇'

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
                        elif data=='recent':
                            rows=self.engine.db.recent(8)
                            if not rows: self.send('📋 لا توجد إشارات مسجلة بعد.',cid)
                            else:
                                lines=['📋 <b>آخر الإشارات</b>','']
                                for s in rows:
                                    icon='🟢' if s['direction']=='BUY' else '🔴'; lines.append(f'{icon} {s["timeframe"]} | {s["direction"]} | {s["status"]} | {s["result"] or "—"}')
                                self.send('\n'.join(lines),cid)
                        elif data=='status': self.send('🟢 <b>البوت يعمل</b>\n\n🥇 الذهب فقط\n⏱️ 5m / 15m / 1H\n🤖 XGBoost / AI تدريبي\n📡 مراقبة الإشارات مفعلة',cid)
            except Exception: time.sleep(5)

    def start(self,engine):
        self.engine=engine; threading.Thread(target=self.poll,daemon=True).start()
