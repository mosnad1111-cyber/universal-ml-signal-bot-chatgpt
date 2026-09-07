import json
import time
import threading
import requests

from config import TELEGRAM_BOT_TOKEN

class Telegram:
    def __init__(self):
        self.token=TELEGRAM_BOT_TOKEN
        self.base=f'https://api.telegram.org/bot{self.token}' if self.token else ''
        self.chat_id=None
        self.offset=0
        self.engine=None

    def send(self,text,chat_id=None):
        if not self.base: return False
        cid=chat_id or self.chat_id
        if not cid: return False
        try:
            r=requests.post(self.base+'/sendMessage',json={'chat_id':cid,'text':text,'parse_mode':'HTML'},timeout=20)
            return r.ok
        except Exception: return False

    def keyboard(self):
        return {'inline_keyboard':[
            [{'text':'🔍 تحليل الذهب 5 دقائق','callback_data':'scan:5m'}, {'text':'🔍 تحليل الذهب 15 دقيقة','callback_data':'scan:15m'}],
            [{'text':'🔍 تحليل الذهب 1 ساعة','callback_data':'scan:1h'}],
            [{'text':'📊 إحصائيات الأداء','callback_data':'stats'}, {'text':'📋 الإشارات الأخيرة','callback_data':'recent'}],
            [{'text':'ℹ️ حالة البوت','callback_data':'status'}]
        ]}

    def menu(self):
        return '🤖 <b>بوت تحليل الذهب بالذكاء الاصطناعي</b>\n\nاختر العملية من الأزرار بالأسفل 👇'

    def send_menu(self,cid):
        if not self.base: return
        try:
            requests.post(self.base+'/sendMessage',json={'chat_id':cid,'text':self.menu(),'parse_mode':'HTML','reply_markup':self.keyboard()},timeout=20)
        except Exception: pass

    def poll(self):
        if not self.base:
            print('ERROR: TELEGRAM_BOT_TOKEN غير موجود')
            return
        while True:
            try:
                r=requests.get(self.base+'/getUpdates',params={'timeout':25,'offset':self.offset},timeout=35)
                if not r.ok: time.sleep(5); continue
                for u in r.json().get('result',[]):
                    self.offset=u['update_id']+1
                    msg=u.get('message')
                    if msg:
                        cid=msg['chat']['id']; self.chat_id=cid
                        text=(msg.get('text') or '').strip().lower()
                        if text in ('/start','/menu','start','القائمة'):
                            self.send_menu(cid)
                        else:
                            self.send_menu(cid)
                    cb=u.get('callback_query')
                    if cb:
                        cid=cb['message']['chat']['id']; self.chat_id=cid
                        data=cb.get('data','')
                        requests.post(self.base+'/answerCallbackQuery',json={'callback_query_id':cb['id']},timeout=10)
                        if data.startswith('scan:'):
                            tf=data.split(':',1)[1]
                            try:
                                s=self.engine.scan(tf)
                                if not s: self.send(f'⚪ <b>لا توجد إشارة قوية الآن</b>\nالفريم: {tf}\n\nتم رفض الإعداد لأن شروط الجودة/الذكاء الاصطناعي لم تكتمل.',cid)
                            except Exception as e: self.send(f'⚠️ تعذر التحليل الآن: {e}',cid)
                        elif data=='stats': self.send(self.engine.stats_text(),cid)
                        elif data=='recent':
                            rows=self.engine.db.recent(8)
                            if not rows: self.send('📋 لا توجد إشارات مسجلة بعد.',cid)
                            else:
                                lines=['📋 <b>آخر الإشارات</b>','']
                                for s in rows:
                                    icon='🟢' if s['direction']=='BUY' else '🔴'
                                    lines.append(f'{icon} {s["timeframe"]} | {s["direction"]} | {s["status"]} | {s["result"] or "—"}')
                                self.send('\n'.join(lines),cid)
                        elif data=='status': self.send('🟢 <b>البوت يعمل</b>\n\n🥇 الذهب فقط\n⏱️ 5m / 15m / 1H\n🤖 نموذج AI تدريبي\n📡 مراقبة الإشارات مفعلة',cid)
            except Exception:
                time.sleep(5)

    def start(self,engine):
        self.engine=engine
        threading.Thread(target=self.poll,daemon=True).start()
