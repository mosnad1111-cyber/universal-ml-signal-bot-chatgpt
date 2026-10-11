import time
import threading
import requests
import subprocess
import sys
import json
from pathlib import Path
from config import TELEGRAM_BOT_TOKEN, BACKTEST_BARS_5M, BACKTEST_BARS_1H, BACKTEST_RETRAIN_EVERY, GRID_SCORES, GRID_AI_PROBS
from backtest import GoldBacktest

class Telegram:
    def __init__(self, db=None):
        self.token=TELEGRAM_BOT_TOKEN
        self.base=f'https://api.telegram.org/bot{self.token}' if self.token else ''
        self.db=db; self.chat_id=None; self.offset=0; self.engine=None; self.backtest_running=False; self.research_backtest_running=False
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
            [{'text':'🔍 تحليل الذهب 5 دقائق','callback_data':'scan:5m'}, {'text':'🔍 تحليل الذهب 1 ساعة','callback_data':'scan:1h'}],
            [{'text':'📊 إحصائيات الأداء','callback_data':'stats'}, {'text':'📋 الإشارات الأخيرة','callback_data':'recent'}],
            [{'text':'🔎 تشخيص الإشارات','callback_data':'diagnostics'}, {'text':'🧪 Backtest تاريخي','callback_data':'backtest'}],
            [{'text':'🔬 اختبار العتبات AI/Score','callback_data':'grid'}],
            [{'text':'📈 اختبار قمة/قاع اليوم السابق (مستقل)','callback_data':'pdhpdl_menu'}],
            [{'text':'ℹ️ حالة البوت','callback_data':'status'}]
        ]}

    def menu(self): return '🤖 <b>بوت تحليل الذهب بالذكاء الاصطناعي</b>\n\nاختر العملية من الأزرار بالأسفل 👇'

    def research_keyboard(self):
        return {'inline_keyboard':[
            [{'text':'▶️ تشغيل اختبار قمة/قاع اليوم السابق','callback_data':'pdhpdl_run'}],
            [{'text':'📘 قواعد الاختبار','callback_data':'pdhpdl_rules'}],
            [{'text':'⬅️ القائمة الرئيسية','callback_data':'home'}]
        ]}

    def send_research_menu(self, cid):
        text_msg=('📈 <b>اختبار استراتيجية قمة/قاع اليوم السابق</b>\n\n'
                  'اختبار بحثي مستقل عن Backtest الأصلي وعن إشارات التداول الحية.\n'
                  'البيانات: TVC:GOLD | مستويات اليوم السابق من فريم الساعة | الدخول من 5 دقائق.\n\n'
                  'اختر تشغيل الاختبار أو عرض القواعد:')
        try:
            r=requests.post(self.base+'/sendMessage',json={'chat_id':cid,'text':text_msg,'parse_mode':'HTML','reply_markup':self.research_keyboard()},timeout=20)
            return bool(r.ok and r.json().get('ok'))
        except Exception: return False

    def _pdhpdl_rules(self, cid):
        self.send('📘 <b>قواعد اختبار قمة/قاع اليوم السابق</b>\n\n'
          '• مصدر القمة والقاع: شموع 1H المكتملة، ويوم التداول من 17:00 إلى 17:00 بتوقيت نيويورك مع التوقيت الصيفي.\n'
          '• الدخول على 5m فقط بين 09:30 و16:00 بتوقيت نيويورك.\n'
          '• الاختراق الناجح: إغلاق خارج المستوى، ثم إعادة اختبار خلال 6 شموع، ثم شمعة تأكيد.\n'
          '• الاختراق الكاذب: إغلاق خارج المستوى ثم عودة للداخل خلال 3 شموع، ثم إعادة اختبار وتأكيد عكسي.\n'
          '• إذا كانت شمعة التأكيد ابتلاعية: دخول عند إغلاقها مباشرة.\n'
          '• إذا كانت شمعة رفض أو نموذجًا آخر: ننتظر إغلاق الشمعة التالية خارج قمة/قاع شمعة الرفض في اتجاه الصفقة.\n'
          '• وقف الخسارة: خلف المستوى بمقدار 0.10 × ATR(14)، والهدف 2R.\n'
          '• إذا لمس السعر الوقف والهدف في الشمعة نفسها، تُحتسب خسارة تحفظيًا.\n'
          '• تكلفة افتراضية للاختبار: 0.5 نقطة سعرية لكل صفقة ذهابًا وإيابًا؛ ليست سعر سبريد مؤكدًا.\n\n'
          '⚠️ هذه نتائج بحثية وليست إشارات تداول، ولا تغيّر الاختبار الأصلي أو منطق البوت.',cid)

    def _send_document(self, path, cid, caption):
        try:
            with open(path,'rb') as fh:
                r=requests.post(self.base+'/sendDocument',data={'chat_id':cid,'caption':caption},files={'document':(Path(path).name,fh)},timeout=60)
            return bool(r.ok and r.json().get('ok'))
        except Exception as exc:
            print(f'تعذر إرسال ملف الاختبار {path}: {exc}')
            return False

    def _pdhpdl_backtest(self, cid):
        if self.research_backtest_running:
            self.send('⏳ اختبار قمة/قاع اليوم السابق يعمل حاليًا. انتظر التقرير قبل تشغيله مرة أخرى.',cid); return
        self.research_backtest_running=True
        self.send('📈 <b>بدأ الاختبار البحثي المستقل</b>\n\n'
          'سيجلب شموع TVC:GOLD لفريمَي 5m و1H، ثم يحسب الاختراق الناجح والكاذب كلًا على حدة.\n'
          'الإعدادات: 5000 شمعة مطلوبة لكل فريم، هدف 2R، وتكلفة مفترضة 0.5 نقطة سعرية.\n\n'
          '⏳ قد يستغرق عدة دقائق. الاختبار الأصلي وإشارات التداول الحية منفصلان عن هذه العملية.',cid)
        def work():
            try:
                script=Path('research/pdh_pdl_breakout_backtest.py')
                if not script.exists(): raise FileNotFoundError(f'ملف الاختبار غير موجود: {script}')
                proc=subprocess.run([sys.executable,str(script),'--bars','5000','--cost-points','0.5'],
                  capture_output=True,text=True,timeout=900)
                if proc.returncode != 0:
                    detail=(proc.stderr or proc.stdout or 'لا توجد تفاصيل إضافية')[-2800:]
                    self.send('❌ <b>تعذر إكمال الاختبار البحثي</b>\n<pre>'+detail.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')+'</pre>',cid)
                    return
                report_path=Path('research/results/summary.json')
                if not report_path.exists(): raise FileNotFoundError('انتهى السكربت دون إنشاء summary.json')
                report=json.loads(report_path.read_text(encoding='utf-8'))
                data=report.get('data',{}); results=report.get('results',{})
                def block(title,key):
                    r=results.get(key,{})
                    return (f'<b>{title}</b>\nعدد الصفقات: {r.get("trades",0)} | الفوز: {r.get("wins",0)} | الخسارة: {r.get("losses",0)}\n'
                      f'نسبة الفوز: {r.get("win_rate_pct",0):.2f}%\nصافي العائد: {r.get("net_R",0):+.3f}R | متوسط الصفقة: {r.get("expectancy_R",0):+.4f}R\n'
                      f'Profit Factor: {r.get("profit_factor") if r.get("profit_factor") is not None else "غير متاح"}')
                msg=('✅ <b>اكتمل اختبار قمة/قاع اليوم السابق</b>\n\n'
                  +block('🟢 الاختراق الناجح','successful')+'\n\n'
                  +block('🔴 الاختراق الكاذب','false')+'\n\n'
                  +f'شموع 5m المستخدمة: {data.get("5m_rows",0)}\nشموع 1H المستخدمة: {data.get("1h_rows",0)}\n'
                  +f'الفترة المتاحة 5m (UTC): {data.get("5m_first_utc") or "—"} إلى {data.get("5m_last_utc") or "—"}\n'
                  +'التكلفة: 0.5 نقطة سعرية مفترضة لكل صفقة.\n'
                  +'⚠️ اختبار تاريخي افتراضي؛ لا يضمن نتائج مستقبلية ولا يرسل أوامر تداول.')
                self.send(msg,cid)
                for path,caption in [
                  ('research/results/summary.json','📊 ملخص نتائج الاختبار'),
                  ('research/results/successful_breakouts.csv','🟢 صفقات الاختراق الناجح'),
                  ('research/results/false_breakouts.csv','🔴 صفقات الاختراق الكاذب')]:
                    if Path(path).exists(): self._send_document(path,cid,caption)
            except subprocess.TimeoutExpired:
                self.send('⌛ انتهت مهلة الاختبار (15 دقيقة). لم يتغير الاختبار الأصلي أو إشارات البوت.',cid)
            except Exception as exc:
                self.send(f'❌ تعذر تشغيل الاختبار المستقل: {type(exc).__name__}: {exc}',cid)
            finally: self.research_backtest_running=False
        threading.Thread(target=work,daemon=True).start()

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
        self.send(f'🧪 <b>بدأ Backtest تاريخي موسّع</b>\n\nWalk-Forward على TVC:GOLD\nالفريمات: 5m / 1H\nالشموع المستهدفة: 10,000 لكل فريم\nإعادة التدريب كل: {BACKTEST_RETRAIN_EVERY}\n\n⏳ جاري تجهيز البيانات…',cid)
        def work():
            try:
                bt=GoldBacktest(); results={}
                for tf, bars in (('5m', BACKTEST_BARS_5M), ('1h', BACKTEST_BARS_1H)):
                    self.send(f'⏳ <b>جاري اختبار {tf}</b>\nتدريب Walk-Forward على {bars:,} شمعة تقريبًا…',cid)
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


    def _grid_search(self,cid):
        if self.backtest_running:
            self.send('⏳ <b>يوجد اختبار يعمل حاليًا</b>\nانتظر انتهاء الاختبار الحالي قبل تشغيل Grid Search.',cid); return
        self.backtest_running=True
        total=len(GRID_SCORES)*len(GRID_AI_PROBS)*2
        self.send(f'🔬 <b>بدأ Grid Search</b>\n\n5m + 1H\nScore: {" / ".join(str(int(x)) for x in GRID_SCORES)}\nAI: {" / ".join(str(int(x*100))+"%" for x in GRID_AI_PROBS)}\nإجمالي الاختبارات: {total}\n\n⏳ قد يستغرق وقتًا لأن كل تركيبة تعيد Walk-Forward كامل.',cid)
        def work():
            try:
                bt=GoldBacktest(); grids={}
                def progress(tf,done,total_tf,score,ai):
                    self.send(f'🔬 <b>{tf}</b> — {done}/{total_tf}\nScore {score:.0f} | AI {ai*100:.0f}%',cid)
                for tf,bars in (('5m',BACKTEST_BARS_5M),('1h',BACKTEST_BARS_1H)):
                    grids[tf]=bt.run_grid(tf,bars=bars,retrain_every=BACKTEST_RETRAIN_EVERY,progress=progress)
                self.send_long(bt.format_grid_ar(grids),cid)
            except Exception as e:
                self.send(f'❌ <b>فشل Grid Search</b>\n{type(e).__name__}: {e}',cid)
            finally:
                self.backtest_running=False
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
                        if data=='pdhpdl_menu': self.send_research_menu(cid)
                        elif data=='pdhpdl_run': self._pdhpdl_backtest(cid)
                        elif data=='pdhpdl_rules': self._pdhpdl_rules(cid)
                        elif data=='home': self.send_menu(cid)
                        elif data.startswith('scan:'):
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
                        elif data=='grid': self._grid_search(cid)
                        elif data=='recent':
                            rows=self.engine.db.recent(8)
                            if not rows: self.send('📋 لا توجد إشارات مسجلة بعد.',cid)
                            else:
                                lines=['📋 <b>آخر الإشارات</b>','']
                                for s in rows:
                                    icon='🟢' if s['direction']=='BUY' else '🔴'; lines.append(f'{icon} {s["timeframe"]} | {s["direction"]} | {s["status"]} | {s["result"] or "—"}')
                                self.send('\n'.join(lines),cid)
                        elif data=='status': self.send('🟢 <b>البوت يعمل</b>\n\n🥇 الذهب فقط\n⏱️ 5m / 1H\n🤖 XGBoost / AI تدريبي\n📡 مراقبة الإشارات مفعلة',cid)
            except Exception: time.sleep(5)

    def start(self,engine):
        self.engine=engine; threading.Thread(target=self.poll,daemon=True).start()
