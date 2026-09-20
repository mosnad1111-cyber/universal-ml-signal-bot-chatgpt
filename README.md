# Gold AI Telegram Bot V9 — TVC:GOLD

نظام تحليل واختبار AI مخصص للذهب `TVC:GOLD` على الفريمات `5m / 15m / 1H`.

## المكونات
- XGBoost حقيقي عند توفره، مع fallback آمن إلى HistGradientBoosting.
- تدريب Walk-Forward زمني.
- Features للاتجاه والزخم والتذبذب والشموع والاختراق.
- Context متعدد الفريمات.
- حساب SL/TP بواسطة ATR وRR.
- Backtest تاريخي أصلي متاح من خلال Telegram.
- واجهة Telegram عربية بالكامل.

## Backtest من Telegram
من قائمة البوت اختر:

`🧪 Backtest تاريخي`

سيتم اختبار الاستراتيجية الأصلية بنظام Walk-Forward على الفريمات `5m / 15m / 1H`، مع عرض الصفقات ونسبة الفوز وصافي `R` وProfit Factor وMax Drawdown.

## Render
Python 3.12.13، Web Service، ثم تشغيل Gunicorn عبر `web:app`.
