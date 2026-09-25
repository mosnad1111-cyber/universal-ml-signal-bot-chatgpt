# Gold AI Telegram Bot V9 — TVC:GOLD

نظام تحليل واختبار AI مخصص للذهب `TVC:GOLD` على الفريمين `5m / 1H` فقط.

## المكونات
- XGBoost حقيقي عند توفره، مع fallback آمن إلى HistGradientBoosting.
- تدريب Walk-Forward زمني.
- Features للاتجاه والزخم والتذبذب والشموع والاختراق.
- سياق الفريم الأعلى: `1H` لتأكيد اتجاه إشارات `5m`.
- حساب SL/TP بواسطة ATR وRR.
- Backtest تاريخي أصلي متاح من خلال Telegram على `5m / 1H` فقط.
- واجهة Telegram عربية بالكامل.

## Backtest من Telegram
من قائمة البوت اختر:

`🧪 Backtest تاريخي`

سيتم اختبار الاستراتيجية بنظام Walk-Forward على `5m / 1H` فقط، مع عرض الصفقات ونسبة الفوز وصافي `R` وProfit Factor وMax Drawdown.

## Render
Python 3.12.13، Web Service، ثم تشغيل Gunicorn عبر `web:app`.
