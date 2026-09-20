# Gold AI Telegram Bot V9 — TVC:GOLD

نظام بحث/اختبار AI مخصص للذهب `TVC:GOLD` على 5m / 15m / 1H.

## المكونات
- XGBoost حقيقي عند توفره، مع fallback آمن إلى HistGradientBoosting.
- تدريب walk-forward زمني.
- Features للاتجاه والزخم والتذبذب والشموع والاختراق.
- Context متعدد الفريمات.
- استراتيجية London Breakout مستقلة للاختبار التاريخي في `strategy_backtest.py`.
- حساب SL/TP بواسطة ATR وRR.
- Telegram عربي بالكامل، بدون `TELEGRAM_CHAT_ID`.

## تشغيل باك تيست استراتيجية London Breakout
بعد تثبيت المتطلبات وتشغيل البيئة المحلية:

```bash
python strategy_backtest.py 5m 5000
python strategy_backtest.py 15m 5000
python strategy_backtest.py 1h 5000
```

سيظهر JSON يتضمن عدد الصفقات، الرابحة والخاسرة، نسبة الفوز، صافي R، التوقع الرياضي، وعدد الحالات التي رفضتها فلاتر الاختراق والزخم.

> ملاحظة: ملف `strategy_backtest.py` منفصل عن محرك Telegram الحي حتى تتم مراجعة النتائج التاريخية أولًا. هذا الاختبار ليس ضمانًا للربح.

## Render
Python 3.12.13، Web Service، `pip install -r requirements.txt` ثم Gunicorn `web:app`.
