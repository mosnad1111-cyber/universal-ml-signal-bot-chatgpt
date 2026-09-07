import threading
from flask import Flask, jsonify
from config import GOLD_DATA_SYMBOL, TIMEFRAMES
from telegram_ar import Telegram
from engine import Engine

app=Flask(__name__)
tg=Telegram()
engine=Engine(tg.send)
tg.start(engine)
engine.start()

@app.get('/')
def home():
    return jsonify({'ok':True,'service':'gold-ai-telegram-bot','symbol':'TVC:GOLD','data_feed':'TVC:GOLD','timeframes':TIMEFRAMES})

@app.get('/health')
def health():
    return jsonify({'status':'ok'})
