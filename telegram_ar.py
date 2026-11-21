import time
import threading
import requests
import json
import uuid
from datetime import datetime, timezone
from lean_actions import LeanActionsClient
from config import ASSETS, TELEGRAM_BOT_TOKEN, BACKTEST_BARS_5M, BACKTEST_BARS_15M, BACKTEST_BARS_1H, BACKTEST_RETRAIN_EVERY, TIMEFRAMES, thresholds_for
from market import fetch
from backtest import GoldBacktest

class Telegram:
    def __init__(self, db=None):
        self.token=TELEGRAM_BOT_TOKEN
        self.base=f'https://api.telegram.org/bot{self.token}' if self.token else ''
        self.db=db; self.chat_id=None; self.offset=0; self.engine=None; self.backtest_running=False
        self.lean_client=LeanActionsClient(db)
        if self.db:
            saved=self.db.get_setting('telegram_chat_id')
            if saved:
                try: self.chat_id=int(saved)
                except Exception: self.chat_id=saved

    def ready(self): return bool(self.base and self.chat_id)

    def send(self,text,chat_id=None,reply_markup=None):
        if not self.base: return False
        cid=chat_id or self.chat_id
        if not cid: return False
        try:
            payload={'chat_id':cid,'text':text,'parse_mode':'HTML'}
            if reply_markup is not None: payload['reply_markup']=reply_markup
            r=requests.post(self.base+'/sendMessage',json=payload,timeout=20)
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
            [{'text':'🥇 الذهب 5m','callback_data':'scan:GOLD:5m'}, {'text':'₿ البيتكوين 5m','callback_data':'scan:BTC:5m'}],
            [{'text':'🥇 الذهب 15m','callback_data':'scan:GOLD:15m'}, {'text':'₿ البيتكوين 15m','callback_data':'scan:BTC:15m'}],
            [{'text':'🥇 الذهب 1h','callback_data':'scan:GOLD:1h'}, {'text':'₿ البيتكوين 1h','callback_data':'scan:BTC:1h'}],
            [{'text':'📊 إحصائيات الأداء','callback_data':'stats'}, {'text':'📋 الإشارات الأخيرة','callback_data':'recent'}],
            [{'text':'🔎 تشخيص الإشارات','callback_data':'diagnostics'}, {'text':'🧪 Backtest الذهب','callback_data':'backtest:GOLD'}, {'text':'🧪 Backtest البيتكوين','callback_data':'backtest:BTC'}],
            [{'text':'⚙️ LEAN تاريخي حقيقي — الذهب','callback_data':'lean_menu:GOLD'}, {'text':'⚙️ LEAN تاريخي حقيقي — BTC','callback_data':'lean_menu:BTC'}],
            [{'text':'🧭 مختبر الاستراتيجيات — الذهب','callback_data':'lab:GOLD'}, {'text':'🧭 مختبر الاستراتيجيات — BTC','callback_data':'lab:BTC'}],
            [{'text':'ℹ️ حالة البوت','callback_data':'status'}]
        ]}

    def menu(self): return '🤖 <b>بوت تحليل الذهب والبيتكوين بالذكاء الاصطناعي</b>\n\nاختر الأصل والفريم من الأزرار بالأسفل 👇'

    def send_menu(self,cid):
        if not self.base: return False
        try:
            r=requests.post(self.base+'/sendMessage',json={'chat_id':cid,'text':self.menu(),'parse_mode':'HTML','reply_markup':self.keyboard()},timeout=20)
            return bool(r.ok and r.json().get('ok'))
        except Exception: return False

    def _register_commands(self):
        """Publish only commands implemented by this bot to Telegram's command menu."""
        if not self.base:
            return False
        commands = [
            {'command': 'menu', 'description': 'عرض القائمة الكاملة'},
            {'command': 'signal', 'description': 'تحليل فوري: /signal BTC 15m'},
            {'command': 'watch', 'description': 'حالة المراقبة التلقائية'},
            {'command': 'price', 'description': 'آخر سعر متاح: /price GOLD أو BTC'},
            {'command': 'threshold', 'description': 'عرض حدود Score وAI الفعالة'},
        ]
        try:
            response = requests.post(
                self.base + '/setMyCommands',
                json={'commands': commands},
                timeout=15,
            )
            ok = bool(response.ok and response.json().get('ok'))
            if not ok:
                print(f'[telegram] setMyCommands failed: {response.text[:300]}', flush=True)
            return ok
        except Exception as exc:
            print(f'[telegram] setMyCommands error: {type(exc).__name__}: {exc}', flush=True)
            return False

    @staticmethod
    def _parse_asset(value):
        aliases = {
            'BTC': 'BTC', 'BITCOIN': 'BTC', 'BITCOINUSDT': 'BTC',
            'بيتكوين': 'BTC', 'البيتكوين': 'BTC',
            'GOLD': 'GOLD', 'XAU': 'GOLD', 'XAUUSD': 'GOLD',
            'ذهب': 'GOLD', 'الذهب': 'GOLD',
        }
        return aliases.get(str(value or '').strip().upper(), aliases.get(str(value or '').strip()))

    @staticmethod
    def _parse_timeframe(value):
        aliases = {'5': '5m', '5M': '5m', '15': '15m', '15M': '15m',
                   '1H': '1h', 'H1': '1h', '60': '1h', '60M': '1h'}
        return aliases.get(str(value or '').strip().upper())

    def _command_signal(self, cid, args):
        if self.engine is None:
            self.send('⏳ محرك التحليل لم يبدأ بعد. أعد المحاولة بعد لحظات.', cid)
            return
        # BTC 15m is the default because it is the configured primary reference.
        asset_key = self._parse_asset(args[0]) if args else 'BTC'
        timeframe = self._parse_timeframe(args[1]) if len(args) > 1 else '15m'
        if asset_key not in ASSETS or timeframe not in TIMEFRAMES:
            self.send('صيغة الأمر: /signal BTC 15m\nالأصول: BTC أو GOLD\nالفريمات: 5m أو 15m أو 1h', cid)
            return
        asset = ASSETS[asset_key]
        try:
            signal = self.engine.scan(timeframe, asset['symbol'])
            if signal:
                # Engine.scan already sends the full entry/SL/TP alert to the paired chat.
                self.send(f'✅ تم إنشاء وإرسال إشارة {asset["label"]} على {timeframe}.', cid)
            else:
                diag = self.engine.last_diagnostics.get(f'{asset["symbol"]}|{timeframe}', {})
                labels = {
                    'score_below_threshold': 'Score أقل من الحد المطلوب',
                    'ai_below_threshold': 'احتمال AI أقل من الحد المطلوب',
                    'risk_out_of_range': 'المخاطرة خارج النطاق',
                    'model_not_ready': 'نموذج AI غير جاهز لهذا الفريم',
                    'open_signal_exists': 'توجد إشارة مفتوحة بالفعل لهذا الأصل والفريم',
                    'insufficient_feature_bars': 'بيانات الشموع غير كافية',
                    'no_model_probabilities': 'لم ينتج النموذج احتمالات صالحة',
                    'direction_ambiguous': 'اتجاه السوق غير واضح',
                    'against_higher_timeframe_trend': 'الإعداد يعاكس اتجاه الفريم الأعلى',
                    'telegram_not_ready': 'إعداد Telegram غير مكتمل',
                }
                reason = labels.get(diag.get('reject'), diag.get('reject', 'لا يوجد إعداد مستوفٍ للشروط الآن'))
                self.send(f'⚪ لا توجد إشارة جديدة\nالأصل: {asset["label"]}\nالفريم: {timeframe}\nالسبب: {reason}', cid)
        except Exception as exc:
            self.send(f'❌ تعذر تحليل {asset["label"]} على {timeframe}: {type(exc).__name__}: {exc}', cid)

    def _command_watch(self, cid):
        if self.engine is None:
            self.send('⏳ محرك المراقبة لم يبدأ بعد.', cid)
            return
        open_rows = self.engine.db.all_open()
        waiting = sum(row['status'] == 'WAITING' for row in open_rows)
        active = sum(row['status'] == 'ACTIVE' for row in open_rows)
        self.send(
            '🟢 <b>المراقبة التلقائية تعمل</b>\n'
            'يفحص البوت الذهب والبيتكوين على 5m و15m و1h تلقائيًا كل دقيقة افتراضيًا.\n'
            'ويراقب تفعيل الدخول ووقف الخسارة والهدف للصفقات المفتوحة.\n'
            f'بانتظار الدخول: {waiting}\nمفعّلة: {active}\n'
            'ملاحظة: هذا الأمر يعرض الحالة؛ لا يوقف المراقبة ولا يغيّر إعداداتها.',
            cid,
        )

    def _command_price(self, cid, args):
        asset_key = self._parse_asset(args[0]) if args else 'BTC'
        if asset_key not in ASSETS:
            self.send('صيغة الأمر: /price BTC أو /price GOLD', cid)
            return
        asset = ASSETS[asset_key]
        try:
            frame = fetch(asset['symbol'], '5m', period=300)
            if frame is None or frame.empty:
                raise RuntimeError('لم تصل بيانات سعر')
            candle = frame.iloc[-1]
            stamp = frame.index[-1]
            source = frame.attrs.get('market_data_source', 'مزود بيانات السوق')
            self.send(
                f'💱 <b>{asset["label"]}</b>\n'
                f'آخر إغلاق/سعر شمعة 5m متاح: <b>{float(candle["Close"]):,.4f}</b>\n'
                f'وقت الشمعة: {stamp.isoformat()}\n'
                f'المصدر: {source}\n'
                'ℹ️ السعر المعروض هو آخر قيمة شمعة متاحة، وليس عرض سعر تنفيذ مضمونًا.',
                cid,
            )
        except Exception as exc:
            self.send(f'❌ تعذر جلب سعر {asset["label"]}: {type(exc).__name__}: {exc}', cid)

    def _command_thresholds(self, cid):
        lines = ['🎯 <b>حدود قبول الإشارات الفعالة</b>', '']
        for key, asset in ASSETS.items():
            lines.append(f'{asset["icon"]} <b>{asset["label"]}</b>')
            for timeframe in TIMEFRAMES:
                score, ai_probability = thresholds_for(timeframe, asset['symbol'])
                lines.append(f'{timeframe}: Score ≥ {score:g} | AI ≥ {ai_probability * 100:g}%')
            lines.append('')
        lines.append('هذه الحدود تخص قبول الإشارات الحية، ولا تغيّر إعدادات تجارب Paper أو مختبر LEAN.')
        self.send('\n'.join(lines), cid)

    def handle_command(self, text, cid):
        """Route Telegram slash commands to real bot functionality."""
        parts = (text or '').strip().split()
        if not parts or not parts[0].startswith('/'):
            return False
        command = parts[0].split('@', 1)[0].lower()
        args = parts[1:]
        if command in ('/start', '/menu', '/help'):
            self.send_menu(cid)
        elif command == '/signal':
            self._command_signal(cid, args)
        elif command == '/watch':
            self._command_watch(cid)
        elif command == '/price':
            self._command_price(cid, args)
        elif command == '/threshold':
            self._command_thresholds(cid)
        else:
            self.send('❔ هذا الأمر غير مدعوم. استخدم /menu لعرض وظائف البوت.', cid)
        return True

    def _authorized_chat(self,cid):
        saved = None
        if self.db:
            try:
                saved = self.db.get_setting('telegram_chat_id')
            except Exception:
                return False
        if saved is None:
            saved = self.chat_id
        # First-contact pairing is allowed only when no chat is registered.
        # Once paired, no other chat can silently take over notifications.
        return saved is None or str(saved) == str(cid)

    def _remember_chat(self,cid):
        if not self._authorized_chat(cid):
            return False
        self.chat_id=cid
        if self.db:
            try: self.db.set_setting('telegram_chat_id',cid)
            except Exception: return False
        return True

    def _lean_menu(self, cid, asset_key='GOLD'):
        asset_key = asset_key if asset_key in ASSETS else 'GOLD'
        asset = ASSETS[asset_key]
        buttons = [
            [{'text': 'كل الفريمات (5m + 15m + 1h)', 'callback_data': f'lean:{asset_key}:all'}],
            [
                {'text': '5m', 'callback_data': f'lean:{asset_key}:5m'},
                {'text': '15m', 'callback_data': f'lean:{asset_key}:15m'},
                {'text': '1h', 'callback_data': f'lean:{asset_key}:1h'},
            ],
        ]
        self.send(
            f'🧪 <b>LEAN Backtest تاريخي — {asset["label"]}</b>\n'
            'اختر الفريم. سيُجلب البوت شموع السوق التاريخية، ويعيد تدريب Walk-Forward، '
            'ثم يمرّر الشموع والإشارات إلى LEAN لمحاكاة الأوامر والرسوم.',
            cid,
            reply_markup={'inline_keyboard': buttons},
        )

    def _lean_backtest(self, cid, asset_key='GOLD', timeframe='all'):
        if not self.db:
            self.send('❌ تخزين طلب LEAN غير متاح في هذه الخدمة.', cid)
            return
        if self.backtest_running or self.db.get_setting('lean_active_request'):
            self.send('⏳ يوجد Backtest آخر قيد التشغيل. انتظر التقرير قبل بدء طلب جديد.', cid)
            return
        if asset_key not in ASSETS:
            self.send('❌ الأصل المحدد غير مدعوم.', cid)
            return
        timeframes = ['5m', '15m', '1h'] if timeframe == 'all' else [timeframe]
        request_id = uuid.uuid4().hex
        record = {
            'request_id': request_id,
            'chat_id': str(cid),
            'asset_key': asset_key,
            'timeframes': timeframes,
            'status': 'queued',
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.lean_client.validate_request(asset_key, timeframes)
            self.db.set_setting(f'lean_request:{request_id}', json.dumps(record, ensure_ascii=False))
            self.db.set_setting('lean_active_request', request_id)
            self.backtest_running = True
            asset = ASSETS[asset_key]
            self.send(
                f'🚀 <b>تم إرسال طلب LEAN الحقيقي إلى GitHub Actions</b>\n'
                f'الأصل: {asset["label"]}\nالفريمات: {", ".join(timeframes)}\n'
                'سيتم جلب OHLC تاريخي فعلي من مزوّد السوق، وتشغيل XGBoost Walk-Forward، '
                'ثم تنفيذ المحاكاة داخل LEAN. سيصل التقرير هنا عند اكتماله.\n'
                'هذا المسار لا يستخدم شموع lean_smoke التجريبية.',
                cid,
            )
            self.lean_client.dispatch(request_id, asset_key, timeframes)
            record['status'] = 'dispatched'
            self.db.set_setting(f'lean_request:{request_id}', json.dumps(record, ensure_ascii=False))
        except Exception as exc:
            self.backtest_running = False
            self.db.set_setting(f'lean_request:{request_id}', json.dumps({**record, 'status': 'dispatch_failed'}))
            self.db.set_setting('lean_active_request', '')
            self.send('❌ <b>تعذر تشغيل LEAN.</b>\n' + str(exc), cid)

    def handle_lean_callback(self, run_id, request_id):
        try:
            result, status = self.lean_client.handle_callback(run_id, request_id, self)
            if status == 200:
                self.backtest_running = False
            elif status >= 500:
                print(f'[lean-callback] delivery failed: {result}', flush=True)
            return result, status
        except Exception as exc:
            print(f'[lean-callback] unhandled error: {type(exc).__name__}: {exc}', flush=True)
            return {'ok': False, 'error': 'internal_error'}, 500

    def _backtest(self,cid,asset_key='GOLD'):
        if self.backtest_running:
            self.send('⏳ <b>الـBacktest يعمل حاليًا</b>\nانتظر النتيجة الحالية قبل تشغيل اختبار آخر.',cid); return
        self.backtest_running=True
        asset=ASSETS.get(asset_key,ASSETS['GOLD']); symbol=asset['symbol']
        self.send(f'🧪 <b>بدأ Backtest تاريخي موسّع — {asset["label"]}</b>\n\nWalk-Forward على {symbol}\nالفريمات: 5m / 15m / 1H\nالشموع المستهدفة: حسب إعداد كل فريم\nإعادة التدريب كل: {BACKTEST_RETRAIN_EVERY}\n\n⏳ جاري تجهيز البيانات…',cid)
        def work():
            try:
                bt=GoldBacktest(); results={}
                for tf, bars in (('5m', BACKTEST_BARS_5M), ('15m', BACKTEST_BARS_15M), ('1h', BACKTEST_BARS_1H)):
                    self.send(f'⏳ <b>جاري اختبار {tf}</b>\nتدريب Walk-Forward على {bars:,} شمعة تقريبًا…',cid)
                    try:
                        results[tf]=bt.run(tf,bars=bars,retrain_every=BACKTEST_RETRAIN_EVERY,symbol=symbol)
                        if results[tf].get('error'):
                            self.send(f'⚠️ <b>{tf}</b> لم يكتمل: {results[tf]["error"]}',cid)
                        else:
                            r=results[tf]
                            self.send(f'✅ <b>انتهى اختبار {tf}</b>\nالشموع: {r.get("bars",0)}\nالصفقات: {r.get("trades",0)}\nWin Rate: {r.get("win_rate",0):.1f}%\nNet: {r.get("net_r",0):+.1f}R',cid)
                    except Exception as exc:
                        results[tf]={'timeframe':tf,'symbol':symbol,'asset_label':asset['label'],'error':f'{type(exc).__name__}: {exc}'}
                        self.send(f'❌ <b>فشل اختبار {tf}</b>\n{type(exc).__name__}: {exc}',cid)
                report=bt.format_ar(results)
                if not self.send_long(report,cid): self.send('⚠️ <b>اكتمل الـBacktest لكن تعذر إرسال أحد أجزاء التقرير.</b>',cid)
            except Exception as e:
                self.send(f'❌ <b>فشل الـBacktest</b>\n{type(e).__name__}: {e}',cid)
            finally: self.backtest_running=False
        threading.Thread(target=work,daemon=True).start()


    def _strategy_lab(self, cid, asset_key='GOLD'):
        if not self.db:
            self.send('❌ تخزين طلب مختبر LEAN غير متاح في هذه الخدمة.', cid)
            return
        if self.backtest_running or self.db.get_setting('lean_active_request'):
            self.send('⏳ يوجد اختبار LEAN آخر قيد التشغيل. انتظر وصول التقرير قبل تشغيل المختبر.', cid)
            return
        if asset_key not in ASSETS:
            self.send('❌ الأصل المحدد غير مدعوم.', cid)
            return
        timeframes = ['5m', '15m', '1h']
        request_id = uuid.uuid4().hex
        asset = ASSETS[asset_key]
        record = {
            'request_id': request_id,
            'chat_id': str(cid),
            'asset_key': asset_key,
            'timeframes': timeframes,
            'mode': 'strategy_lab',
            'status': 'queued',
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.lean_client.validate_request(asset_key, timeframes)
            self.db.set_setting(f'lean_request:{request_id}', json.dumps(record, ensure_ascii=False))
            self.db.set_setting('lean_active_request', request_id)
            self.backtest_running = True
            self.send(
                f'🚀 <b>تم إرسال المختبر الموحد للاستراتيجيات والعتبات إلى LEAN — {asset["label"]}</b>\n\n'
                'سيختبر أربع استراتيجيات، ولكل استراتيجية شبكة عتبات Score/AI كاملة على 5m و15m و1h.\n'
                'كل استراتيجية لها Walk-Forward مستقل واحد لكل فريم، وتُنفّذ كل تركيبة عتبات داخل LEAN باستخدام OHLC التاريخي نفسه.\n'
                'سيعرض التقرير أفضل استراتيجية مع أفضل عتبة، ونتائج BUY وSELL، مع فصل Calibration عن Holdout.\n'
                'المختبر يطلب 20,000 شمعة لكل فريم عند توفرها. اختبار BTC الورقي مستقل لمدة 7 أيام: trend_pullback وScore 65 وAI 60%. إشارات التداول الحية لا تتغير.',
                cid,
            )
            self.lean_client.dispatch_strategy_lab(request_id, asset_key, timeframes)
            record['status'] = 'dispatched'
            self.db.set_setting(f'lean_request:{request_id}', json.dumps(record, ensure_ascii=False))
        except Exception as exc:
            self.backtest_running = False
            self.db.set_setting(f'lean_request:{request_id}', json.dumps({**record, 'status': 'dispatch_failed'}))
            self.db.set_setting('lean_active_request', '')
            self.send('❌ <b>تعذر تشغيل مختبر الاستراتيجيات على LEAN.</b>\n' + str(exc), cid)

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
                        cid=msg['chat']['id']
                        if not self._authorized_chat(cid): continue
                        if not self._remember_chat(cid): continue
                        message_text = msg.get('text', '')
                        if message_text.startswith('/'):
                            self.handle_command(message_text, cid)
                        elif message_text:
                            self.send('استخدم /menu لعرض الأوامر والخيارات المتاحة.', cid)
                    cb=u.get('callback_query')
                    if cb:
                        cid=cb['message']['chat']['id']
                        if not self._authorized_chat(cid): continue
                        self._remember_chat(cid); data=cb.get('data','')
                        try: requests.post(self.base+'/answerCallbackQuery',json={'callback_query_id':cb['id']},timeout=10)
                        except Exception: pass
                        if data.startswith('scan:'):
                            parts=data.split(':')
                            if len(parts)==3: asset_key,tf=parts[1],parts[2]
                            else: asset_key,tf='GOLD',parts[1]
                            asset=ASSETS.get(asset_key,ASSETS['GOLD']); symbol=asset['symbol']
                            try:
                                s=self.engine.scan(tf,symbol)
                                if not s:
                                    d=self.engine.last_diagnostics.get(f'{symbol}|{tf}',{})
                                    labels={'score_below_threshold':'الـScore أقل من الحد المطلوب','ai_below_threshold':'احتمال AI أقل من الحد المطلوب','risk_out_of_range':'المخاطرة خارج النطاق','model_not_ready':'النموذج غير جاهز','open_signal_exists':'توجد إشارة مفتوحة لهذا الفريم','telegram_not_ready':'Telegram غير جاهز لاستقبال الإشارة','telegram_send_failed':'تعذر إرسال الإشارة عبر Telegram','insufficient_feature_bars':'البيانات غير كافية','no_model_probabilities':'لا توجد نتيجة AI','strategy_filter_15m_buy':'إشارات 15m متوقفة مؤقتًا','strategy_filter_15m_sell':'إشارات 15m متوقفة مؤقتًا'}
                                    reason=labels.get(d.get('reject',''),d.get('reject','غير معروف'))
                                    self.send(f'⚪ <b>لا توجد إشارة قوية الآن</b>\nالأصل: {asset["label"]}\nالفريم: {tf}\n\n🔎 السبب: <b>{reason}</b>',cid)
                            except Exception as e: self.send(f'⚠️ تعذر تحليل {asset["label"]} الآن: {e}',cid)
                        elif data=='stats': self.send_long(self.engine.stats_text(),cid)
                        elif data=='diagnostics': self.send(self.engine.diagnostics_text(),cid)
                        elif data=='backtest' or data.startswith('backtest:'):
                            asset_key=data.split(':',1)[1] if ':' in data else 'GOLD'
                            self._backtest(cid,asset_key)
                        elif data.startswith('lean_menu:'):
                            self._lean_menu(cid, data.split(':',1)[1].upper())
                        elif data.startswith('lean:'):
                            parts=data.split(':')
                            if len(parts)==3: self._lean_backtest(cid, parts[1].upper(), parts[2])
                        elif data.startswith('lab:'):
                            asset_key = data.split(':', 1)[1].upper()
                            self._strategy_lab(cid, asset_key)
                        elif data=='recent':
                            rows=self.engine.db.recent(8)
                            if not rows: self.send('📋 لا توجد إشارات مسجلة بعد.',cid)
                            else:
                                lines=['📋 <b>آخر الإشارات</b>','']
                                for s in rows:
                                    icon='🟢' if s['direction']=='BUY' else '🔴'; asset=next((a for a in ASSETS.values() if a['symbol']==s['symbol']), {'label':s['symbol']}); lines.append(f'{asset["label"]} | {icon} {s["timeframe"]} | {s["direction"]} | {s["status"]} | {s["result"] or "—"}')
                                self.send('\n'.join(lines),cid)
                        elif data=='status': self.send('🟢 <b>البوت يعمل</b>\n\n🥇 الذهب: TVC:GOLD\n₿ البيتكوين: BINANCE:BTCUSDT\n⏱️ 5m / 15m / 1H لكل أصل\n🤖 نموذج AI مستقل لكل أصل وفريم\n📡 مراقبة الدخول وSL/TP مفعلة\n♾️ لا توجد مهلة زمنية؛ تبقى الإشارة/الصفقة حتى التفعيل ثم SL أو TP',cid)
            except Exception: time.sleep(5)

    def start(self,engine):
        self.engine=engine
        self._register_commands()
        threading.Thread(target=self.poll,daemon=True).start()
