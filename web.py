from flask import Flask, jsonify
from config import GOLD_DATA_SYMBOL, TIMEFRAMES, DB_PATH, thresholds_for
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
        'service': 'gold-ai-telegram-bot',
        'symbol': GOLD_DATA_SYMBOL,
        'data_feed': GOLD_DATA_SYMBOL,
        'timeframes': TIMEFRAMES,
        'active_thresholds': active_thresholds(),
    })

@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'active_thresholds': active_thresholds()})
