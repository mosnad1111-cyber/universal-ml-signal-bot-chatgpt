# Gold AI Telegram Bot V9 — TVC:GOLD

نظام بحث/اختبار AI مخصص للذهب `TVC:GOLD` على 5m / 15m / 1H.

## ما الجديد
- XGBoost حقيقي عند توفره، مع fallback آمن إلى HistGradientBoosting.
- تدريب walk-forward: آخر 20% من البيانات اختبار زمني فقط.
- النموذج يتنبأ باحتمال نجاح الصفقة (الوصول إلى TP قبل SL) لكل اتجاه، بدل رقم AI معكوس وغير متسق.
- Features للاتجاه والزخم والتذبذب والشموع والاختراق.
- Context متعدد الفريمات: 5m يستفيد من 15m و1H، و15m يستفيد من 1H.
- تخزين features مع كل إشارة لإتاحة feedback لاحقًا.
- لا يتم إصدار إشارة إذا كان احتمال النجاح أقل من الحد أو لا يوجد توافق كافٍ.
- 1:2 RR افتراضي.
- Telegram عربي بالكامل، بدون TELEGRAM_CHAT_ID.

## Render
Python 3.12.13، Web Service، `pip install -r requirements.txt` ثم Gunicorn `web:app`.

هذا النظام ليس ضمانًا للربح. يجب استخدامه Paper Trading/forward testing قبل المال الحقيقي.
