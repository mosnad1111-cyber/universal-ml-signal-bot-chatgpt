from flask import Flask, jsonify
from config import ASSETS, GOLD_DATA_SYMBOL, TIMEFRAMES, DB_PATH, thresholds_for
from telegram_ar import Telegram
from engine import Engine
from db import DB

app=Flask(__name__)
db=DB(DB_PATH)
tg=Telegram(db)
engine=Engine(tg.send)
tg.start(engine)
engine.start()

def active_thresholds():
    return {
        tf: {'min_score': thresholds_for(tf)[0], 'min_ai_probability': thresholds_for(tf)[1]}
        for tf in TIMEFRAMES
    }

@app.get('/')
def home():
    return jsonify({
        'ok': True,
        'service': 'gold-bitcoin-ai-telegram-bot',
        'assets': {key: {'symbol': value['symbol'], 'label': value['label']} for key, value in ASSETS.items()},
        'symbol': GOLD_DATA_SYMBOL,
        'timeframes': TIMEFRAMES,
        'active_thresholds': active_thresholds(),
    })

@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'active_thresholds': active_thresholds()})

@app.get('/diagnostics')
def diagnostics():
    return jsonify({
        'status': 'ok',
        'active_thresholds': active_thresholds(),
        'scans': {
            key: {
                'scan_count': engine.scan_counts.get(key, 0),
                'reject_counts': engine.reject_counts.get(key, {}),
                'last': engine.last_diagnostics.get(key, {}),
                'last_scan_at': engine.last_scan_at.get(key, 0),
                'model_training_ok': engine.model_training_ok.get(key),
                'model_errors': engine.ai[key.split('|', 1)[0]].errors,
            }
            for key in engine.last_diagnostics
        }
    })
