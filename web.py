import os
import time
import threading
from flask import Flask, jsonify
from config import ASSETS, GOLD_DATA_SYMBOL, TIMEFRAMES, DB_PATH, thresholds_for
from telegram_ar import Telegram
from engine import Engine
from db import DB

app=Flask(__name__)
APP_STARTED_AT=time.time()
db=DB(DB_PATH)
tg=Telegram(db)
engine=Engine(tg.send)

# Start background workers in the serving worker, not at module import time.
# This remains safe if the WSGI server imports the app before forking workers.
_background_start_lock = threading.Lock()
_background_started_pid = None

@app.before_request
def ensure_background_services():
    global _background_started_pid
    pid = os.getpid()
    if _background_started_pid == pid:
        return
    with _background_start_lock:
        if _background_started_pid == pid:
            return
        # Mark this process before starting threads to prevent duplicate starts
        # if startup overlaps with another request.
        _background_started_pid = pid
        print(f"[startup] starting Telegram polling and engine workers in pid={pid}", flush=True)
        tg.start(engine)
        engine.start()

def active_thresholds():
    return {
        tf: {'min_score': thresholds_for(tf)[0], 'min_ai_probability': thresholds_for(tf)[1]}
        for tf in TIMEFRAMES
    }

def _age_seconds(timestamp):
    try:
        return max(0, round(time.time() - float(timestamp), 1)) if timestamp else None
    except (TypeError, ValueError):
        return None

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
    threads = {t.name: t.is_alive() for t in threading.enumerate()}
    scans = {}
    for key in engine.last_diagnostics:
        symbol, tf = key.split('|', 1)
        ai = engine.ai.get(symbol)
        last = dict(engine.last_diagnostics.get(key) or {})
        scan_at = engine.last_scan_at.get(key, 0)
        scans[key] = {
            'scan_count': engine.scan_counts.get(key, 0),
            'reject_counts': engine.reject_counts.get(key, {}),
            'last': last,
            'last_scan_at': scan_at,
            'last_scan_age_seconds': _age_seconds(scan_at),
            'model_training_ok': engine.model_training_ok.get(key),
            'model_errors': dict(ai.errors) if ai else {},
            'model_metrics': (ai.info().get(tf, {}) if ai else {}),
            'model_loaded': bool(ai and tf in ai.models),
        }
    return jsonify({
        'status': 'ok',
        'process_id': os.getpid(),
        'app_started_at': APP_STARTED_AT,
        'app_uptime_seconds': round(time.time() - APP_STARTED_AT, 1),
        'background_threads': threads,
        'scanner_thread_alive': threads.get('market-scanner', False),
        'monitor_thread_alive': threads.get('trade-monitor', False),
        'active_thresholds': active_thresholds(),
        'scans': scans,
    })
