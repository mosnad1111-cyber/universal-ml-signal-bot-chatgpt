# Gold + Bitcoin AI Telegram Bot

نظام تحليل وإشارات Telegram باللغة العربية للذهب والبيتكوين، مع نموذج AI منفصل لكل أصل وفريم.

## الأصول والفريمات
- الذهب: `TVC:GOLD`
- البيتكوين مقابل USDT: `BINANCE:BTCUSDT`
- الفريمات: `5m / 15m / 1H`
- النماذج، بيانات السوق، والإشارات المفتوحة معزولة لكل أصل.
- مراقبة الدخول ووقف الخسارة والهدف تستخدم رمز الصفقة نفسها حتى لا تختلط أسعار الذهب والبيتكوين.
- الإشارات للتحليل والتنفيذ اليدوي فقط؛ لا يوجد تنفيذ صفقات لدى وسيط.

## Backtest من Telegram
زر Backtest الحالي يختبر استراتيجية الذهب. لا تعتبر نتائج الذهب دليلًا على أداء البيتكوين؛ يلزم تشغيل Backtest مستقل للبيتكوين قبل الاعتماد على إشاراته.

## Render
Python 3.12.13، Web Service، ثم تشغيل Gunicorn عبر `web:app`. يلزم وجود `TELEGRAM_BOT_TOKEN` في متغيرات البيئة.

للوصول إلى TradingView: يدعم التطبيق متغيرَي `TV_USERNAME` و`TV_PASSWORD` اختياريًا لاستخدام حساب TradingView بدل الوصول المجهول. أضِفهما في Render فقط إذا كان لديك حساب وتريد استخدامه؛ لا تضع بيانات الدخول داخل ملفات الكود. من دون هذين المتغيرين يستمر التطبيق باستخدام الوصول المجهول، وقد تظل قيود أو انقطاعات مزود البيانات قائمة. عند فشل WebSocket، يعيد التطبيق المحاولة ثلاث مرات ثم يتوقف عن طلب أحجام تاريخ أصغر لأنها لا تصلح انقطاع الاتصال.


## مختبر أبحاث الاستراتيجيات
يوفر زر **مختبر الاستراتيجيات** مقارنة بحثية بين الاستراتيجية الحالية وثلاث فرضيات: الارتداد داخل الاتجاه، الاختراق وإعادة الاختبار، واستمرار الزخم. يتدرب نموذج مستقل لكل فرضية باستخدام قواعد دخول ووقف وهدف خاصة بها، ثم تُقيّم النتائج زمنيًا على مجموعة معايرة ومجموعة احتجاز لاحقة. لا يغيّر المختبر استراتيجية الإشارات الحية تلقائيًا.

تُعرض حساسية النتائج لخصم افتراضي قدره 0.05R و0.10R لكل صفقة؛ هذه ليست تكاليف وسيط فعلية. كما أن تسميات التدريب تظل ضمن نافذة ملاحظة محدودة، لذلك يجب مراجعة تغطية التسميات والحالات غير المحسومة قبل اعتماد أي مرشح. اختيار استراتيجية من عدة مرشحين على المجموعة الاحتجازية يقتضي اختبارًا نهائيًا جديدًا على فترة لم تُستخدم في الاختيار.


## Independent LEAN / QuantConnect backtesting (optional)

The application can connect to the documented QuantConnect Cloud API to compile
and submit a backtest for a QuantConnect project. This is intentionally separate
from the existing in-process Walk-Forward research lab.

Configure these Render environment variables to enable the API client:

- `QC_USER_ID`: QuantConnect user ID.
- `QC_API_TOKEN`: API token from QuantConnect account settings. Store it only
  as a Render secret; never commit it or send it in chat.
- `QC_PROJECT_ID`: ID of a QuantConnect project containing a LEAN algorithm.

The client is disabled when any required value is missing, so the current bot
starts and behaves as before. The API adapter authenticates, requests a compile,
and submits a backtest; it does not silently claim that a QuantConnect algorithm
is equivalent to this bot's XGBoost strategy. Before exposing a Telegram action
or comparing performance, the LEAN project must be populated with an algorithm
that consumes the same completed XAU/USD and BTC/USDT candles, reproduces the
bot's signal/entry/SL/TP rules, and models the intended spread, fees and slippage.
The project must also be tested against known fixtures from the current engine.

Local `lean backtest` uses Docker; the current Render Python web service does
not provide a Docker daemon. This integration therefore targets QuantConnect
Cloud's API rather than attempting to start Docker inside the web service.
QuantConnect Cloud account/organization plan requirements and usage charges
are controlled by QuantConnect. No live-trading endpoints are used here.

This first integration step deliberately does not replace the existing Backtest
button or change live signals. Enable the API only after a valid project is
available and its algorithm parity has been verified.


## LEAN historical backtest from Telegram

The Telegram menu has a separate **LEAN تاريخي حقيقي** action. The workflow fetches
completed historical OHLC from the configured providers (Dukascopy XAU/USD BID for
gold; Binance BTC/USDT klines, with the source recorded if TradingView fallback is
used), builds chronological XGBoost Walk-Forward decisions, and sends only the
market bars and decision levels to the open-source LEAN engine. LEAN independently
replays entry/exit and order fees on those historical candles. The synthetic
`lean_smoke` fixture is only a CI test and is never used by this Telegram workflow.

To enable the action on Render, set `GITHUB_ACTIONS_TOKEN` to a fine-grained GitHub
token scoped only to this repository with **Actions: Read and write** permission.
The repository defaults to `mosnad1111-cyber/universal-ml-signal-bot-chatgpt`.
Render provides `RENDER_EXTERNAL_URL` automatically; set `LEAN_CALLBACK_BASE_URL`
only if you need to override it. Keep the token only in Render environment secrets.

Gold OHLC is historical Dukascopy data, but its volume field is the project's
constant neutral proxy and is not real traded volume. LEAN fees currently use a
declared 0.05R round-trip assumption, not broker-specific commissions, spread or
slippage. Results are research/backtest output, not broker execution.
