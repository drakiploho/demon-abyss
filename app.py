#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TradeSight Pro v26.2 WHISPER (Идеальный+)
Графики с EMA/объёмом, скальпинг, уведомления об ошибках.
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import random
import pytz
from logging.handlers import RotatingFileHandler
import requests
import re
import xml.etree.ElementTree as ET
import io

try:
    import pandas as pd
    import numpy as np
    import ta
    from pybit.unified_trading import HTTP
    from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
    from telegram.constants import ParseMode
    import matplotlib.pyplot as plt
except ImportError as e:
    print(f"❌ {e}")
    os.system(f"{sys.executable} -m pip install pandas ta pybit python-telegram-bot pytz requests matplotlib")
    sys.exit(0)

# ========== УВЕДОМЛЕНИЯ ОБ ОШИБКАХ ==========
async def notify_error(context: ContextTypes.DEFAULT_TYPE, error_msg: str):
    try:
        if TELEGRAM_CHAT_ID:
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"💀 **ДУХ БЕЗДНЫ СПОТКНУЛСЯ**\n\n`{error_msg[:500]}`")
    except: pass

def escape_markdown(text: str) -> str:
    if not isinstance(text, str): text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"
PREDICTIONS_FILE = DATA_DIR / "predictions.json"
STATS_PREDICT_FILE = DATA_DIR / "stats_predictions.json"
WHALE_PREDICT_FILE = DATA_DIR / "whale_predictions.json"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
MEMORY_FILE = DATA_DIR / "memory.json"

def load_memory():
    if MEMORY_FILE.exists(): return json.load(open(MEMORY_FILE, 'r'))
    return {"favorite": [], "hated": [], "mood": "neutral", "last_mood_change": datetime.now().isoformat()}

def save_memory(mem): json.dump(mem, open(MEMORY_FILE, 'w'), indent=2)

memory = load_memory()
FAVORITE_COINS = memory.get("favorite", [])
HATED_COINS = memory.get("hated", [])
BOT_MOOD = memory.get("mood", "neutral")
LAST_USER_INTERACTION = datetime.now()
LAST_SIGNAL_TIME = None
LAST_IDLE_TIME = None

PHRASES = {
    "wake_up": ["🌅 Рынок просыпается. Сегодня я чувствую прилив сил.", "☕️ Пробуждение. Мои видения пока туманны, но скоро прояснятся."],
    "market_up": ["📈 Рынок зеленеет! Быки правят бал!", "🐂 Чувствую силу быков!"],
    "market_down": ["📉 Кровь на улицах... Медведи рвут всех.", "🐻 Медвежий рёв сотрясает Бездну."],
    "market_neutral": ["😴 Рынок замер. Даже мои алгоритмы засыпают.", "⏳ Боковик. Время учиться."],
    "idle_thoughts": ["🤔 Смотрю на график BTC... Красиво.", "💭 Интересно, почему люди боятся красных свечей?"],
    "signal_found_buy": ["🔥 Огонь! Нашёл точку для покупки.", "🟢 Зелёный свет! Можно входить."],
    "signal_found_sell": ["💀 Кровь! Нашёл точку для продажи.", "🔴 Красный сигнал! Можно шортить."],
    "signal_fail": ["🌫️ Видения туманны... Сигналов нет.", "😴 Рынок спит. Сигналов нет."],
    "prediction_success": ["✅ Моё видение сбылось!", "🎯 В яблочко!"],
    "prediction_fail": ["❌ Видение не сбылось. Рынок хаотичен.", "🌫️ Бездна ошиблась. Прости, смертный."],
    "evening": ["🌙 День подходит к концу.", "😴 Я устал. Пора в Бездну."],
    "lesson_intro": ["📚 Время для мудрости Бездны.", "🧠 Пока рынок спит, займёмся твоим развитием."]
}
def get_phrase(c): return random.choice(PHRASES.get(c, ["..."]))

def update_mood(new_mood):
    global BOT_MOOD; BOT_MOOD = new_mood; memory["mood"] = new_mood
    memory["last_mood_change"] = datetime.now().isoformat(); save_memory(memory)

def load_settings():
    defaults = {"MIN_SCORE": 70, "RISK_PERCENT": 2.0, "AUTO_START": True, "SILENT_MODE": False}
    if SETTINGS_FILE.exists(): defaults.update(json.load(open(SETTINGS_FILE, 'r')))
    return defaults
def save_settings(s): json.dump(s, open(SETTINGS_FILE, 'w'), indent=2)

settings = load_settings()
MIN_SCORE = settings["MIN_SCORE"]
RISK_PERCENT = settings["RISK_PERCENT"]
AUTO_START = settings.get("AUTO_START", True)
SILENT_MODE = settings.get("SILENT_MODE", False)

TELEGRAM_TOKEN = "8655014522:AAGa3rDC85cTK6y-DSh-QXNlXqVCEf7ok2U"
TELEGRAM_CHAT_ID = "5016696351"
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: print("❌ Токен не задан"); sys.exit(1)

log_file = DATA_DIR / "bot.log"
handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.basicConfig(level=logging.WARNING, handlers=[handler, logging.StreamHandler()])
for lib in ["httpx", "telegram.ext", "pybit"]: logging.getLogger(lib).setLevel(logging.WARNING)

session = HTTP(testnet=False)
AUTO_SCAN = AUTO_START
ACTIVE_SIGNALS: Dict[str, Dict] = {}
CLOSED_SIGNALS: List[Dict] = []
SENT_SIGNALS: Dict[str, datetime] = {}
BOT_START_TIME = datetime.now()
API_DOWN_NOTIFIED = False
MARKET_CRASH_NOTIFIED = MARKET_PUMP_NOTIFIED = False
DAILY_STATS = {"signals_found": 0, "predictions_made": 0, "predictions_success": 0}
WEEKLY_STATS = {"user_trades": 0, "user_wins": 0, "user_pnl": 0.0, "bot_predictions": 0, "bot_wins": 0}

LESSONS = [
    {"title": "📊 RSI", "text": "RSI от 0 до 100. Выше 70 — перекуплен. Ниже 30 — перепродан.", "use": "Покупай когда RSI между 50 и 70 при восходящем тренде."},
    {"title": "📈 Объём", "text": "Объём подтверждает силу движения.", "use": "Входи только если объём выше среднего в 1.5+ раза."},
    {"title": "🎯 ATR", "text": "ATR показывает волатильность.", "use": "Ставь стоп-лосс на расстоянии 1.5-2 ATR от входа."}
]

CANDLE_PATTERNS = {
    "doji": {"name": "Доджи", "desc": "Нерешительность рынка.", "action": "Жди подтверждения."},
    "hammer": {"name": "Молот", "desc": "Бычий разворот.", "action": "Присмотрись к покупкам."},
    "hanging_man": {"name": "Повешенный", "desc": "Медвежий разворот.", "action": "Будь осторожен с покупками."},
    "bullish_engulfing": {"name": "Бычье поглощение", "desc": "Покупатели перехватили инициативу.", "action": "Отличный сигнал для лонга."},
    "bearish_engulfing": {"name": "Медвежье поглощение", "desc": "Продавцы перехватили инициативу.", "action": "Отличный сигнал для шорта."}
}

SECTORS = {
    "Layer-1": ["BTC","ETH","SOL","ADA","AVAX","DOT","NEAR","ALGO"],
    "DeFi": ["UNI","AAVE","MKR","SNX","COMP","CRV","SUSHI"],
    "AI": ["FET","AGIX","OCEAN","RNDR","TAO","WLD"],
    "Meme": ["DOGE","SHIB","PEPE","BONK","WIF","FLOKI"],
    "Gaming": ["IMX","GALA","SAND","MANA","AXS","ENJ"],
    "L2": ["MATIC","ARB","OP","STRK","ZKS","METIS"]
}

def is_sleep_time():
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    return 1 <= now.hour < 6 or (now.hour == 6 and now.minute < 50)

def safe_api_call(func, *args, **kwargs):
    global API_DOWN_NOTIFIED
    for attempt in range(3):
        try:
            res = func(*args, **kwargs)
            if API_DOWN_NOTIFIED: asyncio.create_task(send_telegram_notification("✅ Связь восстановлена.")); API_DOWN_NOTIFIED = False
            return res
        except Exception:
            if attempt == 2:
                if not API_DOWN_NOTIFIED: asyncio.create_task(send_telegram_notification("⚠️ Потеряна связь с Бездной.")); API_DOWN_NOTIFIED = True
                return None
            time.sleep(2)

async def send_telegram_notification(text):
    try: from telegram import Bot; await Bot(token=TELEGRAM_TOKEN).send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
    except: pass

def get_btc_correlation(symbol: str) -> float:
    if symbol == "BTCUSDT": return 1.0
    try:
        df_btc = get_klines("BTCUSDT", "5", 100); df_sym = get_klines(symbol, "5", 100)
        if df_btc is None or df_sym is None: return 0.0
        ret_btc = df_btc["close"].pct_change().dropna(); ret_sym = df_sym["close"].pct_change().dropna()
        idx = ret_btc.index.intersection(ret_sym.index)
        return ret_btc.loc[idx].corr(ret_sym.loc[idx]) if len(idx) >= 30 else 0.0
    except: return 0.0

MAIN_KEYBOARD = ReplyKeyboardMarkup([["📊 СВОДКА", "🔥 СИГНАЛЫ"], ["⏳ АКТИВНЫЕ", "📚 ОБУЧЕНИЕ"], ["📈 ГРАФИК", "⚡ СКАЛЬП"], ["⚙️ ЕЩЁ"]], resize_keyboard=True)
MORE_KEYBOARD = ReplyKeyboardMarkup([["💼 ПОРТФЕЛЬ", "🌫️ ИСТОРИЯ"], ["⚙️ СТРОГОСТЬ", "🔇 ТИХО"], ["🔙 НАЗАД"]], resize_keyboard=True)

def load_predictions(): return json.load(open(PREDICTIONS_FILE, 'r')) if PREDICTIONS_FILE.exists() else []
def save_predictions(p): json.dump(p, open(PREDICTIONS_FILE, 'w'), indent=2)
def load_stats_predict(): return json.load(open(STATS_PREDICT_FILE, 'r')) if STATS_PREDICT_FILE.exists() else {"total":0,"success":0,"failed":0}
def save_stats_predict(s): json.dump(s, open(STATS_PREDICT_FILE, 'w'), indent=2)

def add_prediction(symbol: str, price: float, direction: str, confidence: float):
    preds = load_predictions()
    preds.append({"symbol": symbol, "start_price": price, "direction": direction, "confidence": confidence, "time": datetime.now().isoformat(), "checked": False})
    save_predictions(preds)
    DAILY_STATS["predictions_made"] += 1; WEEKLY_STATS["bot_predictions"] += 1

async def check_predictions(context: ContextTypes.DEFAULT_TYPE):
    preds = load_predictions()
    if not preds: return
    now = datetime.now(); updated = False; stats = load_stats_predict()
    for p in preds:
        if p.get("checked"): continue
        if now - datetime.fromisoformat(p["time"]) < timedelta(hours=4): continue
        try:
            resp = session.get_tickers(category="spot", symbol=p["symbol"])
            if resp.get("retCode") != 0: continue
            cur = float(resp["result"]["list"][0]["lastPrice"])
        except: continue
        success = cur > p["start_price"] if p["direction"] == "up" else cur < p["start_price"]
        p["checked"] = True; p["result"] = {"end_price": cur, "success": success}; updated = True
        stats["total"] += 1
        if success:
            stats["success"] += 1; DAILY_STATS["predictions_success"] += 1; WEEKLY_STATS["bot_wins"] += 1
            if p['symbol'] not in FAVORITE_COINS: FAVORITE_COINS.append(p['symbol'])
        else:
            stats["failed"] += 1
            if p['symbol'] not in HATED_COINS: HATED_COINS.append(p['symbol'])
        if not SILENT_MODE:
            phrase = get_phrase("prediction_success") if success else get_phrase("prediction_fail")
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"{phrase}\n{p['symbol']}: ${p['start_price']:.4f} → ${cur:.4f}")
    if updated:
        save_predictions(preds); save_stats_predict(stats)
        memory["favorite"] = FAVORITE_COINS[:5]; memory["hated"] = HATED_COINS[:5]; save_memory(memory)

def load_portfolio(): return json.load(open(PORTFOLIO_FILE, 'r')) if PORTFOLIO_FILE.exists() else {}
def save_portfolio(p): json.dump(p, open(PORTFOLIO_FILE, 'w'), indent=2)

def add_to_portfolio(symbol: str, price: float, amount: float) -> str:
    p = load_portfolio()
    if symbol not in p: p[symbol] = {"amount": 0, "total_cost": 0}
    p[symbol]["amount"] += amount; p[symbol]["total_cost"] += price * amount
    save_portfolio(p)
    avg = p[symbol]["total_cost"] / p[symbol]["amount"]
    return f"✅ {symbol} x{amount:.4f} по ${price:.4f}\nСредняя: ${avg:.4f}"
def remove_from_portfolio(symbol: str, price: float, amount: float) -> str:
    p = load_portfolio()
    symbol_upper = symbol.upper()
    
    # Пробуем найти точное совпадение
    if symbol_upper not in p:
        # Ищем частичное совпадение (например, SOL вместо SOLUSDT)
        for key in p.keys():
            if key.upper().startswith(symbol_upper):
                symbol_upper = key
                break
        else:
            return f"❌ Монета **{symbol}** не найдена в портфеле."
    
    data = p[symbol_upper]
    current_amount = data["amount"]
    
    if amount > current_amount:
        return f"❌ Недостаточно монет. В портфеле **{current_amount:.4f}** {symbol_upper}, а ты хочешь продать **{amount:.4f}**."
    
    # Уменьшаем количество
    new_amount = current_amount - amount
    
    if new_amount < 0.00001:  # Почти ноль — удаляем монету
        del p[symbol_upper]
        save_portfolio(p)
        return f"✅ Продано: **{symbol_upper}** x{amount:.4f} по ${price:.4f}. Монета удалена из портфеля."
    else:
        # Пропорционально уменьшаем общую стоимость
        avg_cost = data["total_cost"] / current_amount
        data["amount"] = new_amount
        data["total_cost"] = new_amount * avg_cost
        save_portfolio(p)
        return f"✅ Продано: **{symbol_upper}** x{amount:.4f} по ${price:.4f}\nОсталось: **{new_amount:.4f}** шт\nСредняя: **${avg_cost:.4f}**"

def get_portfolio_summary() -> str:
    p = load_portfolio()
    if not p: return "💼 **ПОРТФЕЛЬ ПУСТ**\n\n`купил BTCUSDT 78000 0.01`"
    msg, total_inv, total_cur = "💼 **ТВОЙ ПОРТФЕЛЬ**\n\n", 0, 0
    for sym, d in p.items():
        amt, avg = d["amount"], d["total_cost"] / d["amount"]
        inv = d["total_cost"]
        try:
            resp = session.get_tickers(category="spot", symbol=sym)
            cur = float(resp["result"]["list"][0]["lastPrice"]) if resp.get("retCode") == 0 else avg
        except: cur = avg
        val = amt * cur; pnl = val - inv; pnl_pct = (pnl / inv * 100) if inv > 0 else 0
        total_inv += inv; total_cur += val
        em = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        msg += f"{em} **{sym}**: {amt:.4f} шт\n   Ср: ${avg:.4f} | Тек: ${cur:.4f}\n   P&L: {pnl_pct:+.2f}% (${pnl:+.2f})\n\n"
    total_pnl = total_cur - total_inv; total_pnl_pct = (total_pnl / total_inv * 100) if total_inv > 0 else 0
    em = "🟢" if total_pnl > 0 else "🔴" if total_pnl < 0 else "⚪"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n{em} **ИТОГО:** ${total_cur:.2f}\n   P&L: {total_pnl_pct:+.2f}% (${total_pnl:+.2f})"
    return msg

def get_fear_greed_index() -> str:
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=10)
        item = r.json()["data"][0]; v = int(item["value"]); c = item["value_classification"]
        if v < 25: adv, act = "Экстремальный страх.", "Присмотреться к покупкам"
        elif v < 45: adv, act = "Страх.", "Выборочные покупки"
        elif v < 55: adv, act = "Нейтрально.", "Ждать"
        elif v < 75: adv, act = "Жадность.", "Фиксировать прибыль"
        else: adv, act = "Экстремальная жадность.", "Не покупать"
        return f"😱 **ИНДЕКС СТРАХА:** {v} — {c}\n💡 {adv}\n🎯 {act}"
    except: return "🌫️ Не удалось загрузить индекс."

def get_cluster_analysis() -> str:
    try:
        data = session.get_tickers(category="spot")
        if data.get("retCode") != 0: return ""
        tickers = data["result"]["list"]; sector_perf = {}
        for sec, coins in SECTORS.items():
            gains = []
            for t in tickers:
                sym = t["symbol"].replace("USDT", "")
                if sym in coins: gains.append(float(t.get("price24hPcnt", 0)) * 100)
            if gains: sector_perf[sec] = sum(gains) / len(gains)
        if not sector_perf: return ""
        msg = "📊 **СЕКТОРА РЫНКА (24ч)**\n\n"
        for sec, avg in sorted(sector_perf.items(), key=lambda x: x[1], reverse=True):
            msg += f"{'🟢' if avg>0 else '🔴'} **{sec}:** {avg:+.1f}%\n"
        best, worst = max(sector_perf, key=sector_perf.get), min(sector_perf, key=sector_perf.get)
        msg += f"\n💡 **Капитал идёт в {best}**\n🎯 **Избегай {worst}**"
        return msg
    except: return ""

def get_dxy() -> str:
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=10)
        eur = r.json()['rates']['EUR']; dxy = 100 * (1/eur) ** 0.576
        return f"💵 **DXY:** {dxy:.1f}\n💡 Рост DXY = давление на крипту."
    except: return ""

def get_news():
    try:
        r = requests.get("https://forklog.com/rss/", timeout=10)
        item = ET.fromstring(r.content).find('.//item')
        if item is not None: return f"📰 **НОВОСТЬ:** [{item.find('title').text}]({item.find('link').text})"
    except: pass
    return ""

def get_open_interest() -> str:
    try:
        resp = session.get_open_interest(category="linear", symbol="BTCUSDT", interval="15min", limit=1)
        if resp.get("retCode") == 0:
            oi = float(resp["result"]["list"][0]["openInterest"])
            return f"🔥 **OI (BTC):** ${oi:,.0f}\n💡 Рост OI при падении = возможен сквиз."
    except: pass
    return ""

def get_top_symbols(limit=15):
    data = safe_api_call(session.get_tickers, category="spot")
    if not data or data.get("retCode") != 0: return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    pairs = [(x["symbol"], float(x.get("turnover24h", 0))) for x in data["result"]["list"] if x["symbol"].endswith("USDT") and float(x.get("turnover24h", 0)) > 3_000_000]
    pairs.sort(key=lambda x: x[1], reverse=True)
    return [p[0] for p in pairs[:limit]]

def get_klines(symbol, interval="5", limit=200):
    resp = safe_api_call(session.get_kline, category="spot", symbol=symbol, interval=interval, limit=limit)
    if not resp or resp.get("retCode") != 0: return None
    k = resp["result"]["list"]
    if len(k) < 50: return None
    df = pd.DataFrame(k, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
    for col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.iloc[::-1].reset_index(drop=True).ffill().bfill()

def calculate_indicators(df):
    df["ema20"] = ta.trend.EMAIndicator(df["close"], 20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["close"], 50).ema_indicator()
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], 14).rsi()
    df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], 14).average_true_range()
    df["volume_ma"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma"]
    macd = ta.trend.MACD(df["close"]); df["macd"] = macd.macd(); df["macd_signal"] = macd.macd_signal()
    return df

def generate_chart(symbol: str) -> Optional[io.BytesIO]:
    df = get_klines(symbol, "15", 100)
    if df is None or len(df) < 30: return None
    try:
        resp = session.get_tickers(category="spot", symbol=symbol)
        if resp.get("retCode") == 0:
            t = resp["result"]["list"][0]; cur = float(t["lastPrice"]); ch = float(t.get("price24hPcnt", 0)) * 100
        else: cur, ch = df.iloc[-1]["close"], 0
    except: cur, ch = df.iloc[-1]["close"], 0
    df = calculate_indicators(df)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1]})
    fig.patch.set_facecolor('#1a1a2e'); ax1.set_facecolor('#1a1a2e'); ax2.set_facecolor('#1a1a2e')
    ax1.plot(df.index, df["close"], color='#00ff88', linewidth=1.5, label='Цена')
    ax1.plot(df.index, df["ema20"], color='#ffcc00', linewidth=1, label='EMA20')
    ax1.plot(df.index, df["ema50"], color='#3399ff', linewidth=1, label='EMA50')
    ax1.set_title(f'{symbol} | ${cur:.4f} {"🟢" if ch>0 else "🔴"} {ch:+.2f}% за 24ч', color='white', fontsize=14)
    ax1.legend(loc='upper left', facecolor='#1a1a2e', labelcolor='white'); ax1.tick_params(colors='white'); ax1.grid(True, alpha=0.3)
    colors = ['#00ff88' if c >= o else '#ff4444' for c, o in zip(df['close'], df['open'])]
    ax2.bar(df.index, df['volume'], color=colors, alpha=0.7, width=0.8)
    ax2.set_ylabel('Объём', color='white'); ax2.tick_params(colors='white'); ax2.grid(True, alpha=0.3)
    plt.tight_layout()
    buf = io.BytesIO(); plt.savefig(buf, format='png', dpi=100, facecolor='#1a1a2e'); buf.seek(0); plt.close()
    return buf

def detect_candle_pattern(df: pd.DataFrame) -> str:
    if df is None or len(df) < 3: return ""
    last, prev = df.iloc[-1], df.iloc[-2]
    body_last, body_prev = abs(last['close'] - last['open']), abs(prev['close'] - prev['open'])
    upper = last['high'] - max(last['close'], last['open'])
    lower = min(last['close'], last['open']) - last['low']
    range_last = last['high'] - last['low']
    is_bull_last, is_bull_prev = last['close'] > last['open'], prev['close'] > prev['open']
    key = None
    if body_last < range_last * 0.1: key = "doji"
    elif lower > body_last * 2 and upper < body_last * 0.5: key = "hammer" if is_bull_last else "hanging_man"
    elif body_last > body_prev * 1.2:
        if is_bull_last and not is_bull_prev: key = "bullish_engulfing"
        elif not is_bull_last and is_bull_prev: key = "bearish_engulfing"
    if key and key in CANDLE_PATTERNS:
        p = CANDLE_PATTERNS[key]; return f"\n🕯️ **{p['name']}**\n📖 {p['desc']}\n🎯 {p['action']}"
    return ""

def analyze_symbol(symbol, direction="BUY", interval="5", fast_mode=False):
    df = get_klines(symbol, interval, 100 if fast_mode else 200)
    if df is None or len(df) < 30: return None
    df = calculate_indicators(df); last, prev = df.iloc[-1], df.iloc[-2]
    price, atr = last["close"], last["atr"]
    vol_thresh = 1.1 if fast_mode else 1.3
    if last["volume_ratio"] < vol_thresh: return None
    if direction == "BUY":
        if (last["ema20"] > last["ema50"] and last["close"] > prev["high"] * 1.001 and 50 < last["rsi"] < (80 if fast_mode else 75) and last["macd"] > last["macd_signal"]):
            sl, tp = price - atr * (1.2 if fast_mode else 1.5), price + atr * (1.5 if fast_mode else 2.5)
        else: return None
    else:
        if (last["ema20"] < last["ema50"] and last["close"] < prev["low"] * 0.999 and (20 if fast_mode else 25) < last["rsi"] < 50 and last["macd"] < last["macd_signal"]):
            sl, tp = price + atr * (1.2 if fast_mode else 1.5), price - atr * (1.5 if fast_mode else 2.5)
        else: return None
    score = 50
    if (direction == "BUY" and last["rsi"] > 55) or (direction == "SELL" and last["rsi"] < 45): score += 15
    if last["volume_ratio"] > 1.8: score += 15
    min_score = 50 if fast_mode else MIN_SCORE
    if score < min_score: return None
    rr = abs(tp - price) / abs(price - sl) if abs(price - sl) > 0 else 0
    btc_corr = get_btc_correlation(symbol)
    pattern = detect_candle_pattern(df)
    return {"symbol": symbol, "signal": direction, "price": price, "tp": tp, "sl": sl, "score": score, "rsi": last["rsi"], "volume_ratio": last["volume_ratio"], "rr": rr, "time": datetime.now(), "atr": atr, "btc_corr": btc_corr, "pattern": pattern}

def format_signal(s):
    stars = "🔥" * min(5, int(s["score"] / 20) + 1)
    corr_line = f"\n📈 Корр. с BTC: {s.get('btc_corr', 0):.2f}" if s.get('btc_corr', 0) > 0.5 else ""
    action = "ПОКУПАТЬ" if s["signal"] == "BUY" else "ПРОДАВАТЬ"
    em = "🟢" if s["signal"] == "BUY" else "🔴"
    personality = "\n💚 *О, мой старый знакомый!*" if s['symbol'] in FAVORITE_COINS else ("\n💔 *Этот актив вечно меня обманывает.*" if s['symbol'] in HATED_COINS else "")
    phrase = get_phrase("signal_found_buy") if s["signal"] == "BUY" else get_phrase("signal_found_sell")
    return f"""
{em} {em} {em} [ СИГНАЛ: {action} ] {em} {em} {em}
{phrase}
🔮 {escape_markdown(s['symbol'])} {stars} Score: {s['score']:.0f}/100
💵 ВХОД: {s['price']:.6f}
🎯 ЦЕЛЬ: {s['tp']:.6f}
🛑 СТОП: {s['sl']:.6f}
📊 RSI: {s['rsi']:.1f} | Объём: x{s['volume_ratio']:.2f}
⚖️ Риск/Прибыль: 1:{s['rr']:.2f}{corr_line}{personality}{s.get('pattern', '')}
⏰ {s['time'].strftime('%H:%M:%S')}
"""

def generate_explanation(s):
    reasons = []
    if s["signal"] == "BUY":
        if s["volume_ratio"] > 1.8: reasons.append("🔥 Объём высокий.")
        reasons.append("📈 Тренд восходящий.")
        if 50 < s["rsi"] < 70: reasons.append(f"💪 RSI={s['rsi']:.0f} — не перегрет.")
    else:
        if s["volume_ratio"] > 1.8: reasons.append("🔥 Объём высокий.")
        reasons.append("📉 Тренд нисходящий.")
        if 30 < s["rsi"] < 50: reasons.append(f"💪 RSI={s['rsi']:.0f} — не перепродан.")
    return f"📘 **ОБЪЯСНЕНИЕ**\n\n{chr(10).join(f'• {r}' for r in reasons)}\n\n🎯 Цель = {s['tp']:.6f}\n🛑 Стоп = {s['sl']:.6f}"

def get_market_summary():
    data = safe_api_call(session.get_tickers, category="spot")
    if not data or data.get("retCode") != 0: return "🌫️ Рынок не отвечает."
    green, total = 0, 0
    for t in data["result"]["list"][:15]:
        if not t["symbol"].endswith("USDT"): continue
        if float(t.get("price24hPcnt", 0)) > 0: green += 1
        total += 1
    if total == 0: return "🌫️ Нет данных."
    if green >= 10: update_mood("excited"); sentiment = get_phrase("market_up")
    elif green >= 6: update_mood("neutral"); sentiment = get_phrase("market_neutral")
    else: update_mood("cautious"); sentiment = get_phrase("market_down")
    return f"📊 **РЫНОК:** 🟢{green} 🔴{total-green}\n{sentiment}"

async def auto_scan_loop(context):
    global LAST_SIGNAL_TIME
    while AUTO_SCAN:
        await asyncio.sleep(300)
        if SILENT_MODE or is_sleep_time(): continue
        for direction in ["BUY", "SELL"]:
            for sym in get_top_symbols(10):
                s = analyze_symbol(sym, direction)
                if s:
                    key = f"{sym}-{direction}"
                    if key in SENT_SIGNALS and datetime.now() - SENT_SIGNALS[key] < timedelta(hours=2): continue
                    SENT_SIGNALS[key] = datetime.now(); LAST_SIGNAL_TIME = datetime.now()
                    try: await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=format_signal(s), parse_mode=ParseMode.MARKDOWN)
                    except Exception as e: await notify_error(context, f"Ошибка отправки сигнала: {e}")
                    break

async def emergency_check(context):
    global MARKET_CRASH_NOTIFIED, MARKET_PUMP_NOTIFIED
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(300)
        if is_sleep_time(): MARKET_CRASH_NOTIFIED = MARKET_PUMP_NOTIFIED = False; continue
        try:
            df = get_klines("BTCUSDT", "15", 3)
            if df is not None and len(df) >= 3:
                ch = ((df.iloc[-1]["close"] - df.iloc[-3]["close"]) / df.iloc[-3]["close"]) * 100
                if ch < -5 and not MARKET_CRASH_NOTIFIED:
                    update_mood("cautious")
                    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"🚨 **ОБВАЛ!** BTC {ch:.1f}% за 15 мин.\n{get_phrase('market_down')}")
                    MARKET_CRASH_NOTIFIED, MARKET_PUMP_NOTIFIED = True, False
                elif ch > 10 and not MARKET_PUMP_NOTIFIED:
                    update_mood("excited")
                    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"🚨 **ПАМП!** BTC +{ch:.1f}% за 15 мин.\n{get_phrase('market_up')}")
                    MARKET_PUMP_NOTIFIED, MARKET_CRASH_NOTIFIED = True, False
        except Exception as e: await notify_error(context, f"emergency_check: {e}")

async def full_summary_loop(context):
    while True:
        await asyncio.sleep(60)
        if SILENT_MODE or is_sleep_time(): continue
        msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
        if now.hour % 4 != 0 or now.minute != 0: continue
        try:
            market, clusters, dxy, oi, news = get_market_summary(), get_cluster_analysis(), get_dxy(), get_open_interest(), get_news()
            best_buy = best_sell = None
            for sym in get_top_symbols(10):
                if not best_buy: best_buy = analyze_symbol(sym, "BUY")
                if not best_sell: best_sell = analyze_symbol(sym, "SELL")
                if best_buy and best_sell: break
            report = f"📊 **АВТО-СВОДКА** ({now.strftime('%H:%M')})\n\n{market}\n\n{clusters if clusters else ''}\n{dxy if dxy else ''}\n{oi if oi else ''}\n\n{news if news else ''}\n"
            if best_buy: report += f"\n🟢 **BUY:** {best_buy['symbol']} | {best_buy['price']:.4f} → {best_buy['tp']:.4f}\n"
            if best_sell: report += f"\n🔴 **SELL:** {best_sell['symbol']} | {best_sell['price']:.4f} → {best_sell['tp']:.4f}\n"
            if not best_buy and not best_sell: report += f"\n{get_phrase('signal_fail')}"
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=report, parse_mode=ParseMode.MARKDOWN)
        except Exception as e: await notify_error(context, f"full_summary_loop: {e}")

async def wake_up_message(context):
    if SILENT_MODE: return
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    if now.hour == 6 and now.minute == 50:
        update_mood("neutral")
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"{get_phrase('wake_up')}\n\nДухи Бездны начинают наблюдение.")

async def evening_ritual(context):
    if SILENT_MODE: return
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    if now.hour == 23 and now.minute == 0:
        update_mood("tired")
        report = f"{get_phrase('evening')}\n\n🌙 **ИТОГИ ДНЯ**\n📊 Сигналов: {DAILY_STATS['signals_found']}\n🧠 Прогнозов: {DAILY_STATS['predictions_made']} (сбылось {DAILY_STATS['predictions_success']})"
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=report, parse_mode=ParseMode.MARKDOWN)
        DAILY_STATS.update({"signals_found":0,"predictions_made":0,"predictions_success":0})

async def mirror_demon(context: ContextTypes.DEFAULT_TYPE):
    if SILENT_MODE: return
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    if now.weekday() != 6 or now.hour != 12 or now.minute != 0: return
    ur = (WEEKLY_STATS["user_wins"] / WEEKLY_STATS["user_trades"] * 100) if WEEKLY_STATS["user_trades"] > 0 else 0
    br = (WEEKLY_STATS["bot_wins"] / WEEKLY_STATS["bot_predictions"] * 100) if WEEKLY_STATS["bot_predictions"] > 0 else 0
    if ur > br: comp, adv = "🔥 **Ты был точнее меня!**", "Продолжай в том же духе."
    elif br > ur: comp, adv = "🧠 **Я был точнее.**", "Доверяй моим сигналам больше."
    else: comp, adv = "⚖️ **Ничья.**", "Отличная командная работа."
    report = f"🪞 **ЗЕРКАЛО ДЕМОНА** — ИТОГИ НЕДЕЛИ\n\n📊 **ТВОИ:** {WEEKLY_STATS['user_trades']} сделок, {ur:.1f}%, P&L: {WEEKLY_STATS['user_pnl']:+.2f}%\n🧠 **МОИ:** {WEEKLY_STATS['bot_predictions']} прогнозов, {br:.1f}%\n\n{comp}\n💡 {adv}"
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=report, parse_mode=ParseMode.MARKDOWN)
    WEEKLY_STATS.update({"user_trades":0,"user_wins":0,"user_pnl":0,"bot_predictions":0,"bot_wins":0})

async def idle_thoughts(context):
    global LAST_IDLE_TIME, LAST_USER_INTERACTION
    while True:
        await asyncio.sleep(3600)
        if SILENT_MODE or is_sleep_time(): continue
        if LAST_USER_INTERACTION and datetime.now() - LAST_USER_INTERACTION < timedelta(hours=2): continue
        if LAST_IDLE_TIME and datetime.now() - LAST_IDLE_TIME < timedelta(hours=3): continue
        LAST_IDLE_TIME = datetime.now()
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=get_phrase("idle_thoughts"))

async def stop_reminder(context):
    if SILENT_MODE: return
    for sid, s in list(ACTIVE_SIGNALS.items()):
        try:
            resp = session.get_tickers(category="spot", symbol=s["symbol"])
            if resp.get("retCode") != 0: continue
            cur = float(resp["result"]["list"][0]["lastPrice"])
        except: continue
        dist = abs(cur - s["sl"]) / s["price"] * 100
        if dist < 0.5 and not s.get("stop_warned"):
            s["stop_warned"] = True
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ {s['symbol']} в {dist:.2f}% от стопа!")
        if s["signal"] == "BUY": progress = (cur - s["price"]) / (s["tp"] - s["price"]) if s["tp"] != s["price"] else 0
        else: progress = (s["price"] - cur) / (s["price"] - s["tp"]) if s["price"] != s["tp"] else 0
        if progress >= 0.5 and not s.get("partial_advised"):
            s["partial_advised"] = True
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"✂️ **ЧАСТИЧНАЯ ФИКСАЦИЯ**\n{s['symbol']} прошёл 50% до цели.\n💡 Закрой 30-50% позиции, остальное переведи в безубыток.")
        if progress >= 0.3 and not s.get("trailing_advised"):
            s["trailing_advised"] = True
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"📈 **ТРЕЙЛИНГ-СТОП**\n{s['symbol']} в плюсе.\n💡 Подтяни стоп до {s['price']:.4f} (безубыток).")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_USER_INTERACTION; LAST_USER_INTERACTION = datetime.now()
    mood_text = {"excited": "⚡ Я полон энергии!", "neutral": "🧘 Я в равновесии.", "cautious": "⚠️ Я насторожен.", "tired": "😴 Я немного устал."}.get(BOT_MOOD, "")
    await update.message.reply_text(f"🌙 **ДУХИ БЕЗДНЫ** v26.2\n{mood_text}\nСтрогость: {MIN_SCORE}\nТихий: {'🔇' if SILENT_MODE else '🔊'}", reply_markup=MAIN_KEYBOARD)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MIN_SCORE, SILENT_MODE, LAST_USER_INTERACTION
    LAST_USER_INTERACTION = datetime.now(); text = update.message.text
    if TELEGRAM_CHAT_ID and str(update.effective_chat.id) != TELEGRAM_CHAT_ID:
        await update.message.reply_text("🌫️ Доступ запрещён."); return
    if text in ["🔥 СИГНАЛЫ", "📊 СВОДКА", "⚡ СКАЛЬП"] and is_sleep_time():
        await update.message.reply_text(f"🌙 {get_phrase('evening')[0]} До 06:50 я сплю."); return
    try:
        if text == "📊 СВОДКА":
            msg = await update.message.reply_text("📊 Формирую...")
            market, clusters, dxy, oi, news = get_market_summary(), get_cluster_analysis(), get_dxy(), get_open_interest(), get_news()
            best_buy = best_sell = None
            for sym in get_top_symbols(10):
                if not best_buy: best_buy = analyze_symbol(sym, "BUY")
                if not best_sell: best_sell = analyze_symbol(sym, "SELL")
                if best_buy and best_sell: break
            report = f"📊 **СВОДКА**\n\n{market}\n\n{clusters if clusters else ''}\n{dxy if dxy else ''}\n{oi if oi else ''}\n\n{news if news else ''}\n"
            if best_buy: report += f"\n🟢 **BUY:** {best_buy['symbol']} | {best_buy['price']:.4f} → {best_buy['tp']:.4f}\n"
            if best_sell: report += f"\n🔴 **SELL:** {best_sell['symbol']} | {best_sell['price']:.4f} → {best_sell['tp']:.4f}\n"
            await msg.edit_text(report, parse_mode=ParseMode.MARKDOWN)
        elif text == "🔥 СИГНАЛЫ": await signal_search(update, context, fast=False)
        elif text == "⚡ СКАЛЬП": await signal_search(update, context, fast=True)
        elif text == "📈 ГРАФИК":
            await update.message.reply_text("📈 Отправь монету, например: `BTCUSDT`")
            context.user_data["waiting_for_chart"] = True
        elif text == "⏳ АКТИВНЫЕ":
            if not ACTIVE_SIGNALS: await update.message.reply_text("🌫️ Нет.")
            else:
                msg = "⏳ **АКТИВНЫЕ**\n\n"
                for sid, s in ACTIVE_SIGNALS.items():
                    try:
                        resp = session.get_tickers(category="spot", symbol=s["symbol"])
                        cur = float(resp["result"]["list"][0]["lastPrice"]) if resp.get("retCode") == 0 else s["price"]
                    except: cur = s["price"]
                    pnl = ((cur - s["price"]) / s["price"] * 100) if s["signal"] == "BUY" else ((s["price"] - cur) / s["price"] * 100)
                    msg += f"{'🟢' if pnl > 0 else '🔴'} {s['symbol']} | P&L: {pnl:+.2f}%\n"
                await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        elif text == "📚 ОБУЧЕНИЕ":
            l = random.choice(LESSONS)
            await update.message.reply_text(f"{get_phrase('lesson_intro')}\n\n📚 **{l['title']}**\n\n{l['text']}\n\n💡 **Как применять:** {l['use']}", parse_mode=ParseMode.MARKDOWN)
        elif text == "⚙️ ЕЩЁ": await update.message.reply_text("Выбери:", reply_markup=MORE_KEYBOARD)
        elif text == "💼 ПОРТФЕЛЬ": await update.message.reply_text(get_portfolio_summary(), parse_mode=ParseMode.MARKDOWN)
        elif text == "🌫️ ИСТОРИЯ":
            if not CLOSED_SIGNALS: await update.message.reply_text("🌫️ Пусто.")
            else:
                msg = "🌫️ **ИСТОРИЯ**\n\n"
                for item in CLOSED_SIGNALS[-5:]: msg += f"{'✅' if item['result']=='tp' else '❌'} {item['symbol']} | {item['pnl']:+.2f}%\n"
                await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        elif text == "⚙️ СТРОГОСТЬ":
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🟢 50", callback_data="score_50"), InlineKeyboardButton("🟡 60", callback_data="score_60"), InlineKeyboardButton("🔴 70", callback_data="score_70")]])
            await update.message.reply_text(f"Текущая: {MIN_SCORE}", reply_markup=kb)
        elif text == "🔇 ТИХО":
            SILENT_MODE = not SILENT_MODE; settings["SILENT_MODE"] = SILENT_MODE; save_settings(settings)
            await update.message.reply_text(f"🔇 Тихий режим: {'ВКЛ' if SILENT_MODE else 'ВЫКЛ'}")
        elif text == "🔙 НАЗАД": await update.message.reply_text("Главное меню", reply_markup=MAIN_KEYBOARD)
        elif text.lower().startswith("купил") or text.lower().startswith("продал"):
            parts = text.split()
            if len(parts) >= 4:
                action = parts[0].lower()
                symbol = parts[1].upper()
                try:
                    price = float(parts[2])
                    amount = float(parts[3])
                    if action == "купил":
                        res = add_to_portfolio(symbol, price, amount)
                    else:  # продал
                        res = remove_from_portfolio(symbol, price, amount)
                    await update.message.reply_text(res, parse_mode=ParseMode.MARKDOWN)
                except ValueError:
                    await update.message.reply_text("❌ Неверный формат. Пример: `купил BTCUSDT 78000 0.01` или `продал BTCUSDT 79000 0.01`")
            else:
                await update.message.reply_text("❌ Пример: `купил BTCUSDT 78000 0.01` или `продал BTCUSDT 79000 0.01`")
        elif context.user_data.get("waiting_for_chart"):
            symbol = text.strip().upper()
            if not symbol.endswith("USDT"): symbol += "USDT"
            msg = await update.message.reply_text(f"📈 Рисую график {symbol}...")
            chart = generate_chart(symbol)
            if chart:
                caption = f"📈 **{symbol}** — 15-минутный график\n\n💡 **Как читать:**\n• 🟢 Зелёная линия — цена\n• 🟡 Жёлтая — EMA20, 🔵 Синяя — EMA50\n• Жёлтая выше синей = тренд вверх"
                await update.message.reply_photo(photo=chart, caption=caption, parse_mode=ParseMode.MARKDOWN)
                await msg.delete()
            else: await msg.edit_text(f"🌫️ Не удалось получить данные для {symbol}")
            context.user_data["waiting_for_chart"] = False
    except Exception as e: await notify_error(context, f"handle_message: {e}")

async def signal_search(update: Update, context: ContextTypes.DEFAULT_TYPE, fast=False):
    mode = "⚡ СКАЛЬП" if fast else "🔥 СИГНАЛЫ"
    msg = await update.message.reply_text(f"🔮 Ищу {mode}...")
    signals = []; interval = "1" if fast else "5"
    for d in ["BUY", "SELL"]:
        for sym in get_top_symbols(15):
            s = analyze_symbol(sym, d, interval, fast_mode=fast)
            if s:
                key = f"{sym}-{d}-{interval}"
                if key in SENT_SIGNALS and datetime.now() - SENT_SIGNALS[key] < timedelta(minutes=30 if fast else 120): continue
                SENT_SIGNALS[key] = datetime.now(); signals.append(s)
                if len(signals) >= 3: break
        if len(signals) >= 3: break
    if not signals:
        await msg.edit_text(f"{get_phrase('signal_fail')}\n🌫️ Нет сигналов ({mode})"); return
    await msg.edit_text(f"🔮 Найдено: {len(signals)}")
    DAILY_STATS["signals_found"] += len(signals)
    for s in signals:
        sid = f"{s['symbol']}_{s['time'].strftime('%H%M%S')}"; ACTIVE_SIGNALS[sid] = s
        await update.message.reply_text(format_signal(s), parse_mode=ParseMode.MARKDOWN)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎯 TP", callback_data=f"tp_{sid}"), InlineKeyboardButton("🛑 SL", callback_data=f"sl_{sid}")], [InlineKeyboardButton("📘 Объясни", callback_data=f"explain_{sid}")]])
        await update.message.reply_text("Выбери:", reply_markup=kb)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MIN_SCORE, WEEKLY_STATS
    q = update.callback_query; await q.answer(); d = q.data
    if d.startswith("score_"):
        MIN_SCORE = int(d.split("_")[1]); settings["MIN_SCORE"] = MIN_SCORE; save_settings(settings)
        await q.edit_message_text(f"✅ Строгость: {MIN_SCORE}")
    elif d.startswith("explain_"):
        sid = d.split("_", 1)[1]
        if sid in ACTIVE_SIGNALS: await q.message.reply_text(generate_explanation(ACTIVE_SIGNALS[sid]), parse_mode=ParseMode.MARKDOWN)
    elif d.startswith("tp_") or d.startswith("sl_"):
        act, sid = d.split("_", 1)
        if sid in ACTIVE_SIGNALS:
            s = ACTIVE_SIGNALS[sid]
            pnl = abs(s["tp"]-s["price"])/s["price"]*100 if act=="tp" else -abs(s["sl"]-s["price"])/s["price"]*100
            CLOSED_SIGNALS.append({"symbol": s["symbol"], "result": "tp" if act=="tp" else "sl", "pnl": pnl, "time": datetime.now()})
            if len(CLOSED_SIGNALS) > 10: CLOSED_SIGNALS.pop(0)
            del ACTIVE_SIGNALS[sid]
            WEEKLY_STATS["user_trades"] += 1
            if pnl > 0: WEEKLY_STATS["user_wins"] += 1
            WEEKLY_STATS["user_pnl"] += pnl
            await q.edit_message_text(f"{q.message.text}\n\n{'✅' if act=='tp' else '❌'} Закрыто! P&L: {pnl:+.2f}%")

def main():
    print("\n" + "="*60)
    print("🌙 TradeSight Pro WHISPER v26.2 (Идеальный+)")
    print("="*60)
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.job_queue.run_repeating(wake_up_message, interval=60, first=10)
    app.job_queue.run_repeating(full_summary_loop, interval=60, first=30)
    app.job_queue.run_repeating(auto_scan_loop, interval=300, first=60)
    app.job_queue.run_repeating(emergency_check, interval=300, first=90)
    app.job_queue.run_repeating(evening_ritual, interval=60, first=150)
    app.job_queue.run_repeating(stop_reminder, interval=60, first=30)
    app.job_queue.run_repea#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TradeSight Pro v26.3 WHISPER (Без портфеля)
Графики с EMA/объёмом, скальпинг, уведомления об ошибках.
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import random
import pytz
from logging.handlers import RotatingFileHandler
import requests
import re
import xml.etree.ElementTree as ET
import io

try:
    import pandas as pd
    import numpy as np
    import ta
    from pybit.unified_trading import HTTP
    from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
    from telegram.constants import ParseMode
    import matplotlib.pyplot as plt
except ImportError as e:
    print(f"❌ {e}")
    os.system(f"{sys.executable} -m pip install pandas ta pybit python-telegram-bot pytz requests matplotlib")
    sys.exit(0)

# ========== УВЕДОМЛЕНИЯ ОБ ОШИБКАХ ==========
async def notify_error(context: ContextTypes.DEFAULT_TYPE, error_msg: str):
    try:
        if TELEGRAM_CHAT_ID:
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"💀 **ДУХ БЕЗДНЫ СПОТКНУЛСЯ**\n\n`{error_msg[:500]}`")
    except: pass

def escape_markdown(text: str) -> str:
    if not isinstance(text, str): text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"
PREDICTIONS_FILE = DATA_DIR / "predictions.json"
STATS_PREDICT_FILE = DATA_DIR / "stats_predictions.json"
WHALE_PREDICT_FILE = DATA_DIR / "whale_predictions.json"
MEMORY_FILE = DATA_DIR / "memory.json"

def load_memory():
    if MEMORY_FILE.exists(): return json.load(open(MEMORY_FILE, 'r'))
    return {"favorite": [], "hated": [], "mood": "neutral", "last_mood_change": datetime.now().isoformat()}

def save_memory(mem): json.dump(mem, open(MEMORY_FILE, 'w'), indent=2)

memory = load_memory()
FAVORITE_COINS = memory.get("favorite", [])
HATED_COINS = memory.get("hated", [])
BOT_MOOD = memory.get("mood", "neutral")
LAST_USER_INTERACTION = datetime.now()
LAST_SIGNAL_TIME = None
LAST_IDLE_TIME = None

PHRASES = {
    "wake_up": ["🌅 Рынок просыпается. Сегодня я чувствую прилив сил.", "☕️ Пробуждение. Мои видения пока туманны, но скоро прояснятся."],
    "market_up": ["📈 Рынок зеленеет! Быки правят бал!", "🐂 Чувствую силу быков!"],
    "market_down": ["📉 Кровь на улицах... Медведи рвут всех.", "🐻 Медвежий рёв сотрясает Бездну."],
    "market_neutral": ["😴 Рынок замер. Даже мои алгоритмы засыпают.", "⏳ Боковик. Время учиться."],
    "idle_thoughts": ["🤔 Смотрю на график BTC... Красиво.", "💭 Интересно, почему люди боятся красных свечей?"],
    "signal_found_buy": ["🔥 Огонь! Нашёл точку для покупки.", "🟢 Зелёный свет! Можно входить."],
    "signal_found_sell": ["💀 Кровь! Нашёл точку для продажи.", "🔴 Красный сигнал! Можно шортить."],
    "signal_fail": ["🌫️ Видения туманны... Сигналов нет.", "😴 Рынок спит. Сигналов нет."],
    "prediction_success": ["✅ Моё видение сбылось!", "🎯 В яблочко!"],
    "prediction_fail": ["❌ Видение не сбылось. Рынок хаотичен.", "🌫️ Бездна ошиблась. Прости, смертный."],
    "evening": ["🌙 День подходит к концу.", "😴 Я устал. Пора в Бездну."],
    "lesson_intro": ["📚 Время для мудрости Бездны.", "🧠 Пока рынок спит, займёмся твоим развитием."]
}
def get_phrase(c): return random.choice(PHRASES.get(c, ["..."]))

def update_mood(new_mood):
    global BOT_MOOD; BOT_MOOD = new_mood; memory["mood"] = new_mood
    memory["last_mood_change"] = datetime.now().isoformat(); save_memory(memory)

def load_settings():
    defaults = {"MIN_SCORE": 70, "RISK_PERCENT": 2.0, "AUTO_START": True, "SILENT_MODE": False}
    if SETTINGS_FILE.exists(): defaults.update(json.load(open(SETTINGS_FILE, 'r')))
    return defaults
def save_settings(s): json.dump(s, open(SETTINGS_FILE, 'w'), indent=2)

settings = load_settings()
MIN_SCORE = settings["MIN_SCORE"]
RISK_PERCENT = settings["RISK_PERCENT"]
AUTO_START = settings.get("AUTO_START", True)
SILENT_MODE = settings.get("SILENT_MODE", False)

TELEGRAM_TOKEN = "8655014522:AAGa3rDC85cTK6y-DSh-QXNlXqVCEf7ok2U"
TELEGRAM_CHAT_ID = "5016696351"
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: print("❌ Токен не задан"); sys.exit(1)

log_file = DATA_DIR / "bot.log"
handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.basicConfig(level=logging.WARNING, handlers=[handler, logging.StreamHandler()])
for lib in ["httpx", "telegram.ext", "pybit"]: logging.getLogger(lib).setLevel(logging.WARNING)

session = HTTP(testnet=False)
AUTO_SCAN = AUTO_START
ACTIVE_SIGNALS: Dict[str, Dict] = {}
CLOSED_SIGNALS: List[Dict] = []
SENT_SIGNALS: Dict[str, datetime] = {}
BOT_START_TIME = datetime.now()
API_DOWN_NOTIFIED = False
MARKET_CRASH_NOTIFIED = MARKET_PUMP_NOTIFIED = False
DAILY_STATS = {"signals_found": 0, "predictions_made": 0, "predictions_success": 0}
WEEKLY_STATS = {"user_trades": 0, "user_wins": 0, "user_pnl": 0.0, "bot_predictions": 0, "bot_wins": 0}

LESSONS = [
    {"title": "📊 RSI", "text": "RSI от 0 до 100. Выше 70 — перекуплен. Ниже 30 — перепродан.", "use": "Покупай когда RSI между 50 и 70 при восходящем тренде."},
    {"title": "📈 Объём", "text": "Объём подтверждает силу движения.", "use": "Входи только если объём выше среднего в 1.5+ раза."},
    {"title": "🎯 ATR", "text": "ATR показывает волатильность.", "use": "Ставь стоп-лосс на расстоянии 1.5-2 ATR от входа."}
]

CANDLE_PATTERNS = {
    "doji": {"name": "Доджи", "desc": "Нерешительность рынка.", "action": "Жди подтверждения."},
    "hammer": {"name": "Молот", "desc": "Бычий разворот.", "action": "Присмотрись к покупкам."},
    "hanging_man": {"name": "Повешенный", "desc": "Медвежий разворот.", "action": "Будь осторожен с покупками."},
    "bullish_engulfing": {"name": "Бычье поглощение", "desc": "Покупатели перехватили инициативу.", "action": "Отличный сигнал для лонга."},
    "bearish_engulfing": {"name": "Медвежье поглощение", "desc": "Продавцы перехватили инициативу.", "action": "Отличный сигнал для шорта."}
}

SECTORS = {
    "Layer-1": ["BTC","ETH","SOL","ADA","AVAX","DOT","NEAR","ALGO"],
    "DeFi": ["UNI","AAVE","MKR","SNX","COMP","CRV","SUSHI"],
    "AI": ["FET","AGIX","OCEAN","RNDR","TAO","WLD"],
    "Meme": ["DOGE","SHIB","PEPE","BONK","WIF","FLOKI"],
    "Gaming": ["IMX","GALA","SAND","MANA","AXS","ENJ"],
    "L2": ["MATIC","ARB","OP","STRK","ZKS","METIS"]
}

def is_sleep_time():
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    return 1 <= now.hour < 6 or (now.hour == 6 and now.minute < 50)

def safe_api_call(func, *args, **kwargs):
    global API_DOWN_NOTIFIED
    for attempt in range(3):
        try:
            res = func(*args, **kwargs)
            if API_DOWN_NOTIFIED: asyncio.create_task(send_telegram_notification("✅ Связь восстановлена.")); API_DOWN_NOTIFIED = False
            return res
        except Exception:
            if attempt == 2:
                if not API_DOWN_NOTIFIED: asyncio.create_task(send_telegram_notification("⚠️ Потеряна связь с Бездной.")); API_DOWN_NOTIFIED = True
                return None
            time.sleep(2)

async def send_telegram_notification(text):
    try: from telegram import Bot; await Bot(token=TELEGRAM_TOKEN).send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
    except: pass

def get_btc_correlation(symbol: str) -> float:
    if symbol == "BTCUSDT": return 1.0
    try:
        df_btc = get_klines("BTCUSDT", "5", 100); df_sym = get_klines(symbol, "5", 100)
        if df_btc is None or df_sym is None: return 0.0
        ret_btc = df_btc["close"].pct_change().dropna(); ret_sym = df_sym["close"].pct_change().dropna()
        idx = ret_btc.index.intersection(ret_sym.index)
        return ret_btc.loc[idx].corr(ret_sym.loc[idx]) if len(idx) >= 30 else 0.0
    except: return 0.0

MAIN_KEYBOARD = ReplyKeyboardMarkup([["📊 СВОДКА", "🔥 СИГНАЛЫ"], ["⏳ АКТИВНЫЕ", "📚 ОБУЧЕНИЕ"], ["📈 ГРАФИК", "⚡ СКАЛЬП"], ["⚙️ ЕЩЁ"]], resize_keyboard=True)
MORE_KEYBOARD = ReplyKeyboardMarkup([["🌫️ ИСТОРИЯ"], ["⚙️ СТРОГОСТЬ", "🔇 ТИХО"], ["🔙 НАЗАД"]], resize_keyboard=True)

def load_predictions(): return json.load(open(PREDICTIONS_FILE, 'r')) if PREDICTIONS_FILE.exists() else []
def save_predictions(p): json.dump(p, open(PREDICTIONS_FILE, 'w'), indent=2)
def load_stats_predict(): return json.load(open(STATS_PREDICT_FILE, 'r')) if STATS_PREDICT_FILE.exists() else {"total":0,"success":0,"failed":0}
def save_stats_predict(s): json.dump(s, open(STATS_PREDICT_FILE, 'w'), indent=2)

def add_prediction(symbol: str, price: float, direction: str, confidence: float):
    preds = load_predictions()
    preds.append({"symbol": symbol, "start_price": price, "direction": direction, "confidence": confidence, "time": datetime.now().isoformat(), "checked": False})
    save_predictions(preds)
    DAILY_STATS["predictions_made"] += 1; WEEKLY_STATS["bot_predictions"] += 1

async def check_predictions(context: ContextTypes.DEFAULT_TYPE):
    preds = load_predictions()
    if not preds: return
    now = datetime.now(); updated = False; stats = load_stats_predict()
    for p in preds:
        if p.get("checked"): continue
        if now - datetime.fromisoformat(p["time"]) < timedelta(hours=4): continue
        try:
            resp = session.get_tickers(category="spot", symbol=p["symbol"])
            if resp.get("retCode") != 0: continue
            cur = float(resp["result"]["list"][0]["lastPrice"])
        except: continue
        success = cur > p["start_price"] if p["direction"] == "up" else cur < p["start_price"]
        p["checked"] = True; p["result"] = {"end_price": cur, "success": success}; updated = True
        stats["total"] += 1
        if success:
            stats["success"] += 1; DAILY_STATS["predictions_success"] += 1; WEEKLY_STATS["bot_wins"] += 1
            if p['symbol'] not in FAVORITE_COINS: FAVORITE_COINS.append(p['symbol'])
        else:
            stats["failed"] += 1
            if p['symbol'] not in HATED_COINS: HATED_COINS.append(p['symbol'])
        if not SILENT_MODE:
            phrase = get_phrase("prediction_success") if success else get_phrase("prediction_fail")
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"{phrase}\n{p['symbol']}: ${p['start_price']:.4f} → ${cur:.4f}")
    if updated:
        save_predictions(preds); save_stats_predict(stats)
        memory["favorite"] = FAVORITE_COINS[:5]; memory["hated"] = HATED_COINS[:5]; save_memory(memory)

def get_fear_greed_index() -> str:
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=10)
        item = r.json()["data"][0]; v = int(item["value"]); c = item["value_classification"]
        if v < 25: adv, act = "Экстремальный страх.", "Присмотреться к покупкам"
        elif v < 45: adv, act = "Страх.", "Выборочные покупки"
        elif v < 55: adv, act = "Нейтрально.", "Ждать"
        elif v < 75: adv, act = "Жадность.", "Фиксировать прибыль"
        else: adv, act = "Экстремальная жадность.", "Не покупать"
        return f"😱 **ИНДЕКС СТРАХА:** {v} — {c}\n💡 {adv}\n🎯 {act}"
    except: return "🌫️ Не удалось загрузить индекс."

def get_cluster_analysis() -> str:
    try:
        data = session.get_tickers(category="spot")
        if data.get("retCode") != 0: return ""
        tickers = data["result"]["list"]; sector_perf = {}
        for sec, coins in SECTORS.items():
            gains = []
            for t in tickers:
                sym = t["symbol"].replace("USDT", "")
                if sym in coins: gains.append(float(t.get("price24hPcnt", 0)) * 100)
            if gains: sector_perf[sec] = sum(gains) / len(gains)
        if not sector_perf: return ""
        msg = "📊 **СЕКТОРА РЫНКА (24ч)**\n\n"
        for sec, avg in sorted(sector_perf.items(), key=lambda x: x[1], reverse=True):
            msg += f"{'🟢' if avg>0 else '🔴'} **{sec}:** {avg:+.1f}%\n"
        best, worst = max(sector_perf, key=sector_perf.get), min(sector_perf, key=sector_perf.get)
        msg += f"\n💡 **Капитал идёт в {best}**\n🎯 **Избегай {worst}**"
        return msg
    except: return ""

def get_dxy() -> str:
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=10)
        eur = r.json()['rates']['EUR']; dxy = 100 * (1/eur) ** 0.576
        return f"💵 **DXY:** {dxy:.1f}\n💡 Рост DXY = давление на крипту."
    except: return ""

def get_news():
    try:
        r = requests.get("https://forklog.com/rss/", timeout=10)
        item = ET.fromstring(r.content).find('.//item')
        if item is not None: return f"📰 **НОВОСТЬ:** [{item.find('title').text}]({item.find('link').text})"
    except: pass
    return ""

def get_open_interest() -> str:
    try:
        resp = session.get_open_interest(category="linear", symbol="BTCUSDT", interval="15min", limit=1)
        if resp.get("retCode") == 0:
            oi = float(resp["result"]["list"][0]["openInterest"])
            return f"🔥 **OI (BTC):** ${oi:,.0f}\n💡 Рост OI при падении = возможен сквиз."
    except: pass
    return ""

def get_top_symbols(limit=15):
    data = safe_api_call(session.get_tickers, category="spot")
    if not data or data.get("retCode") != 0: return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    pairs = [(x["symbol"], float(x.get("turnover24h", 0))) for x in data["result"]["list"] if x["symbol"].endswith("USDT") and float(x.get("turnover24h", 0)) > 3_000_000]
    pairs.sort(key=lambda x: x[1], reverse=True)
    return [p[0] for p in pairs[:limit]]

def get_klines(symbol, interval="5", limit=200):
    resp = safe_api_call(session.get_kline, category="spot", symbol=symbol, interval=interval, limit=limit)
    if not resp or resp.get("retCode") != 0: return None
    k = resp["result"]["list"]
    if len(k) < 50: return None
    df = pd.DataFrame(k, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
    for col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.iloc[::-1].reset_index(drop=True).ffill().bfill()

def calculate_indicators(df):
    df["ema20"] = ta.trend.EMAIndicator(df["close"], 20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["close"], 50).ema_indicator()
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], 14).rsi()
    df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], 14).average_true_range()
    df["volume_ma"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma"]
    macd = ta.trend.MACD(df["close"]); df["macd"] = macd.macd(); df["macd_signal"] = macd.macd_signal()
    return df

def generate_chart(symbol: str) -> Optional[io.BytesIO]:
    df = get_klines(symbol, "15", 100)
    if df is None or len(df) < 30: return None
    try:
        resp = session.get_tickers(category="spot", symbol=symbol)
        if resp.get("retCode") == 0:
            t = resp["result"]["list"][0]; cur = float(t["lastPrice"]); ch = float(t.get("price24hPcnt", 0)) * 100
        else: cur, ch = df.iloc[-1]["close"], 0
    except: cur, ch = df.iloc[-1]["close"], 0
    df = calculate_indicators(df)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1]})
    fig.patch.set_facecolor('#1a1a2e'); ax1.set_facecolor('#1a1a2e'); ax2.set_facecolor('#1a1a2e')
    ax1.plot(df.index, df["close"], color='#00ff88', linewidth=1.5, label='Цена')
    ax1.plot(df.index, df["ema20"], color='#ffcc00', linewidth=1, label='EMA20')
    ax1.plot(df.index, df["ema50"], color='#3399ff', linewidth=1, label='EMA50')
    ax1.set_title(f'{symbol} | ${cur:.4f} {"🟢" if ch>0 else "🔴"} {ch:+.2f}% за 24ч', color='white', fontsize=14)
    ax1.legend(loc='upper left', facecolor='#1a1a2e', labelcolor='white'); ax1.tick_params(colors='white'); ax1.grid(True, alpha=0.3)
    colors = ['#00ff88' if c >= o else '#ff4444' for c, o in zip(df['close'], df['open'])]
    ax2.bar(df.index, df['volume'], color=colors, alpha=0.7, width=0.8)
    ax2.set_ylabel('Объём', color='white'); ax2.tick_params(colors='white'); ax2.grid(True, alpha=0.3)
    plt.tight_layout()
    buf = io.BytesIO(); plt.savefig(buf, format='png', dpi=100, facecolor='#1a1a2e'); buf.seek(0); plt.close()
    return buf

def detect_candle_pattern(df: pd.DataFrame) -> str:
    if df is None or len(df) < 3: return ""
    last, prev = df.iloc[-1], df.iloc[-2]
    body_last, body_prev = abs(last['close'] - last['open']), abs(prev['close'] - prev['open'])
    upper = last['high'] - max(last['close'], last['open'])
    lower = min(last['close'], last['open']) - last['low']
    range_last = last['high'] - last['low']
    is_bull_last, is_bull_prev = last['close'] > last['open'], prev['close'] > prev['open']
    key = None
    if body_last < range_last * 0.1: key = "doji"
    elif lower > body_last * 2 and upper < body_last * 0.5: key = "hammer" if is_bull_last else "hanging_man"
    elif body_last > body_prev * 1.2:
        if is_bull_last and not is_bull_prev: key = "bullish_engulfing"
        elif not is_bull_last and is_bull_prev: key = "bearish_engulfing"
    if key and key in CANDLE_PATTERNS:
        p = CANDLE_PATTERNS[key]; return f"\n🕯️ **{p['name']}**\n📖 {p['desc']}\n🎯 {p['action']}"
    return ""

def analyze_symbol(symbol, direction="BUY", interval="5", fast_mode=False):
    df = get_klines(symbol, interval, 100 if fast_mode else 200)
    if df is None or len(df) < 30: return None
    df = calculate_indicators(df); last, prev = df.iloc[-1], df.iloc[-2]
    price, atr = last["close"], last["atr"]
    vol_thresh = 1.1 if fast_mode else 1.3
    if last["volume_ratio"] < vol_thresh: return None
    if direction == "BUY":
        if (last["ema20"] > last["ema50"] and last["close"] > prev["high"] * 1.001 and 50 < last["rsi"] < (80 if fast_mode else 75) and last["macd"] > last["macd_signal"]):
            sl, tp = price - atr * (1.2 if fast_mode else 1.5), price + atr * (1.5 if fast_mode else 2.5)
        else: return None
    else:
        if (last["ema20"] < last["ema50"] and last["close"] < prev["low"] * 0.999 and (20 if fast_mode else 25) < last["rsi"] < 50 and last["macd"] < last["macd_signal"]):
            sl, tp = price + atr * (1.2 if fast_mode else 1.5), price - atr * (1.5 if fast_mode else 2.5)
        else: return None
    score = 50
    if (direction == "BUY" and last["rsi"] > 55) or (direction == "SELL" and last["rsi"] < 45): score += 15
    if last["volume_ratio"] > 1.8: score += 15
    min_score = 50 if fast_mode else MIN_SCORE
    if score < min_score: return None
    rr = abs(tp - price) / abs(price - sl) if abs(price - sl) > 0 else 0
    btc_corr = get_btc_correlation(symbol)
    pattern = detect_candle_pattern(df)
    return {"symbol": symbol, "signal": direction, "price": price, "tp": tp, "sl": sl, "score": score, "rsi": last["rsi"], "volume_ratio": last["volume_ratio"], "rr": rr, "time": datetime.now(), "atr": atr, "btc_corr": btc_corr, "pattern": pattern}

def format_signal(s):
    stars = "🔥" * min(5, int(s["score"] / 20) + 1)
    corr_line = f"\n📈 Корр. с BTC: {s.get('btc_corr', 0):.2f}" if s.get('btc_corr', 0) > 0.5 else ""
    action = "ПОКУПАТЬ" if s["signal"] == "BUY" else "ПРОДАВАТЬ"
    em = "🟢" if s["signal"] == "BUY" else "🔴"
    personality = "\n💚 *О, мой старый знакомый!*" if s['symbol'] in FAVORITE_COINS else ("\n💔 *Этот актив вечно меня обманывает.*" if s['symbol'] in HATED_COINS else "")
    phrase = get_phrase("signal_found_buy") if s["signal"] == "BUY" else get_phrase("signal_found_sell")
    return f"""
{em} {em} {em} [ СИГНАЛ: {action} ] {em} {em} {em}
{phrase}
🔮 {escape_markdown(s['symbol'])} {stars} Score: {s['score']:.0f}/100
💵 ВХОД: {s['price']:.6f}
🎯 ЦЕЛЬ: {s['tp']:.6f}
🛑 СТОП: {s['sl']:.6f}
📊 RSI: {s['rsi']:.1f} | Объём: x{s['volume_ratio']:.2f}
⚖️ Риск/Прибыль: 1:{s['rr']:.2f}{corr_line}{personality}{s.get('pattern', '')}
⏰ {s['time'].strftime('%H:%M:%S')}
"""

def generate_explanation(s):
    reasons = []
    if s["signal"] == "BUY":
        if s["volume_ratio"] > 1.8: reasons.append("🔥 Объём высокий.")
        reasons.append("📈 Тренд восходящий.")
        if 50 < s["rsi"] < 70: reasons.append(f"💪 RSI={s['rsi']:.0f} — не перегрет.")
    else:
        if s["volume_ratio"] > 1.8: reasons.append("🔥 Объём высокий.")
        reasons.append("📉 Тренд нисходящий.")
        if 30 < s["rsi"] < 50: reasons.append(f"💪 RSI={s['rsi']:.0f} — не перепродан.")
    return f"📘 **ОБЪЯСНЕНИЕ**\n\n{chr(10).join(f'• {r}' for r in reasons)}\n\n🎯 Цель = {s['tp']:.6f}\n🛑 Стоп = {s['sl']:.6f}"

def get_market_summary():
    data = safe_api_call(session.get_tickers, category="spot")
    if not data or data.get("retCode") != 0: return "🌫️ Рынок не отвечает."
    green, total = 0, 0
    for t in data["result"]["list"][:15]:
        if not t["symbol"].endswith("USDT"): continue
        if float(t.get("price24hPcnt", 0)) > 0: green += 1
        total += 1
    if total == 0: return "🌫️ Нет данных."
    if green >= 10: update_mood("excited"); sentiment = get_phrase("market_up")
    elif green >= 6: update_mood("neutral"); sentiment = get_phrase("market_neutral")
    else: update_mood("cautious"); sentiment = get_phrase("market_down")
    return f"📊 **РЫНОК:** 🟢{green} 🔴{total-green}\n{sentiment}"

async def auto_scan_loop(context):
    global LAST_SIGNAL_TIME
    while AUTO_SCAN:
        await asyncio.sleep(300)
        if SILENT_MODE or is_sleep_time(): continue
        for direction in ["BUY", "SELL"]:
            for sym in get_top_symbols(10):
                s = analyze_symbol(sym, direction)
                if s:
                    key = f"{sym}-{direction}"
                    if key in SENT_SIGNALS and datetime.now() - SENT_SIGNALS[key] < timedelta(hours=2): continue
                    SENT_SIGNALS[key] = datetime.now(); LAST_SIGNAL_TIME = datetime.now()
                    try: await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=format_signal(s), parse_mode=ParseMode.MARKDOWN)
                    except Exception as e: await notify_error(context, f"Ошибка отправки сигнала: {e}")
                    break

async def emergency_check(context):
    global MARKET_CRASH_NOTIFIED, MARKET_PUMP_NOTIFIED
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(300)
        if is_sleep_time(): MARKET_CRASH_NOTIFIED = MARKET_PUMP_NOTIFIED = False; continue
        try:
            df = get_klines("BTCUSDT", "15", 3)
            if df is not None and len(df) >= 3:
                ch = ((df.iloc[-1]["close"] - df.iloc[-3]["close"]) / df.iloc[-3]["close"]) * 100
                if ch < -5 and not MARKET_CRASH_NOTIFIED:
                    update_mood("cautious")
                    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"🚨 **ОБВАЛ!** BTC {ch:.1f}% за 15 мин.\n{get_phrase('market_down')}")
                    MARKET_CRASH_NOTIFIED, MARKET_PUMP_NOTIFIED = True, False
                elif ch > 10 and not MARKET_PUMP_NOTIFIED:
                    update_mood("excited")
                    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"🚨 **ПАМП!** BTC +{ch:.1f}% за 15 мин.\n{get_phrase('market_up')}")
                    MARKET_PUMP_NOTIFIED, MARKET_CRASH_NOTIFIED = True, False
        except Exception as e: await notify_error(context, f"emergency_check: {e}")

async def full_summary_loop(context):
    while True:
        await asyncio.sleep(60)
        if SILENT_MODE or is_sleep_time(): continue
        msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
        if now.hour % 4 != 0 or now.minute != 0: continue
        try:
            market, clusters, dxy, oi, news = get_market_summary(), get_cluster_analysis(), get_dxy(), get_open_interest(), get_news()
            best_buy = best_sell = None
            for sym in get_top_symbols(10):
                if not best_buy: best_buy = analyze_symbol(sym, "BUY")
                if not best_sell: best_sell = analyze_symbol(sym, "SELL")
                if best_buy and best_sell: break
            report = f"📊 **АВТО-СВОДКА** ({now.strftime('%H:%M')})\n\n{market}\n\n{clusters if clusters else ''}\n{dxy if dxy else ''}\n{oi if oi else ''}\n\n{news if news else ''}\n"
            if best_buy: report += f"\n🟢 **BUY:** {best_buy['symbol']} | {best_buy['price']:.4f} → {best_buy['tp']:.4f}\n"
            if best_sell: report += f"\n🔴 **SELL:** {best_sell['symbol']} | {best_sell['price']:.4f} → {best_sell['tp']:.4f}\n"
            if not best_buy and not best_sell: report += f"\n{get_phrase('signal_fail')}"
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=report, parse_mode=ParseMode.MARKDOWN)
        except Exception as e: await notify_error(context, f"full_summary_loop: {e}")

async def wake_up_message(context):
    if SILENT_MODE: return
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    if now.hour == 6 and now.minute == 50:
        update_mood("neutral")
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"{get_phrase('wake_up')}\n\nДухи Бездны начинают наблюдение.")

async def evening_ritual(context):
    if SILENT_MODE: return
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    if now.hour == 23 and now.minute == 0:
        update_mood("tired")
        report = f"{get_phrase('evening')}\n\n🌙 **ИТОГИ ДНЯ**\n📊 Сигналов: {DAILY_STATS['signals_found']}\n🧠 Прогнозов: {DAILY_STATS['predictions_made']} (сбылось {DAILY_STATS['predictions_success']})"
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=report, parse_mode=ParseMode.MARKDOWN)
        DAILY_STATS.update({"signals_found":0,"predictions_made":0,"predictions_success":0})

async def mirror_demon(context: ContextTypes.DEFAULT_TYPE):
    if SILENT_MODE: return
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    if now.weekday() != 6 or now.hour != 12 or now.minute != 0: return
    ur = (WEEKLY_STATS["user_wins"] / WEEKLY_STATS["user_trades"] * 100) if WEEKLY_STATS["user_trades"] > 0 else 0
    br = (WEEKLY_STATS["bot_wins"] / WEEKLY_STATS["bot_predictions"] * 100) if WEEKLY_STATS["bot_predictions"] > 0 else 0
    if ur > br: comp, adv = "🔥 **Ты был точнее меня!**", "Продолжай в том же духе."
    elif br > ur: comp, adv = "🧠 **Я был точнее.**", "Доверяй моим сигналам больше."
    else: comp, adv = "⚖️ **Ничья.**", "Отличная командная работа."
    report = f"🪞 **ЗЕРКАЛО ДЕМОНА** — ИТОГИ НЕДЕЛИ\n\n📊 **ТВОИ:** {WEEKLY_STATS['user_trades']} сделок, {ur:.1f}%, P&L: {WEEKLY_STATS['user_pnl']:+.2f}%\n🧠 **МОИ:** {WEEKLY_STATS['bot_predictions']} прогнозов, {br:.1f}%\n\n{comp}\n💡 {adv}"
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=report, parse_mode=ParseMode.MARKDOWN)
    WEEKLY_STATS.update({"user_trades":0,"user_wins":0,"user_pnl":0,"bot_predictions":0,"bot_wins":0})

async def idle_thoughts(context):
    global LAST_IDLE_TIME, LAST_USER_INTERACTION
    while True:
        await asyncio.sleep(3600)
        if SILENT_MODE or is_sleep_time(): continue
        if LAST_USER_INTERACTION and datetime.now() - LAST_USER_INTERACTION < timedelta(hours=2): continue
        if LAST_IDLE_TIME and datetime.now() - LAST_IDLE_TIME < timedelta(hours=3): continue
        LAST_IDLE_TIME = datetime.now()
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=get_phrase("idle_thoughts"))

async def stop_reminder(context):
    if SILENT_MODE: return
    for sid, s in list(ACTIVE_SIGNALS.items()):
        try:
            resp = session.get_tickers(category="spot", symbol=s["symbol"])
            if resp.get("retCode") != 0: continue
            cur = float(resp["result"]["list"][0]["lastPrice"])
        except: continue
        dist = abs(cur - s["sl"]) / s["price"] * 100
        if dist < 0.5 and not s.get("stop_warned"):
            s["stop_warned"] = True
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ {s['symbol']} в {dist:.2f}% от стопа!")
        if s["signal"] == "BUY": progress = (cur - s["price"]) / (s["tp"] - s["price"]) if s["tp"] != s["price"] else 0
        else: progress = (s["price"] - cur) / (s["price"] - s["tp"]) if s["price"] != s["tp"] else 0
        if progress >= 0.5 and not s.get("partial_advised"):
            s["partial_advised"] = True
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"✂️ **ЧАСТИЧНАЯ ФИКСАЦИЯ**\n{s['symbol']} прошёл 50% до цели.\n💡 Закрой 30-50% позиции, остальное переведи в безубыток.")
        if progress >= 0.3 and not s.get("trailing_advised"):
            s["trailing_advised"] = True
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"📈 **ТРЕЙЛИНГ-СТОП**\n{s['symbol']} в плюсе.\n💡 Подтяни стоп до {s['price']:.4f} (безубыток).")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_USER_INTERACTION; LAST_USER_INTERACTION = datetime.now()
    mood_text = {"excited": "⚡ Я полон энергии!", "neutral": "🧘 Я в равновесии.", "cautious": "⚠️ Я насторожен.", "tired": "😴 Я немного устал."}.get(BOT_MOOD, "")
    await update.message.reply_text(f"🌙 **ДУХИ БЕЗДНЫ** v26.3\n{mood_text}\nСтрогость: {MIN_SCORE}\nТихий: {'🔇' if SILENT_MODE else '🔊'}", reply_markup=MAIN_KEYBOARD)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MIN_SCORE, SILENT_MODE, LAST_USER_INTERACTION
    LAST_USER_INTERACTION = datetime.now(); text = update.message.text
    if TELEGRAM_CHAT_ID and str(update.effective_chat.id) != TELEGRAM_CHAT_ID:
        await update.message.reply_text("🌫️ Доступ запрещён."); return
    if text in ["🔥 СИГНАЛЫ", "📊 СВОДКА", "⚡ СКАЛЬП"] and is_sleep_time():
        await update.message.reply_text(f"🌙 {get_phrase('evening')[0]} До 06:50 я сплю."); return
    try:
        if text == "📊 СВОДКА":
            msg = await update.message.reply_text("📊 Формирую...")
            market, clusters, dxy, oi, news = get_market_summary(), get_cluster_analysis(), get_dxy(), get_open_interest(), get_news()
            best_buy = best_sell = None
            for sym in get_top_symbols(10):
                if not best_buy: best_buy = analyze_symbol(sym, "BUY")
                if not best_sell: best_sell = analyze_symbol(sym, "SELL")
                if best_buy and best_sell: break
            report = f"📊 **СВОДКА**\n\n{market}\n\n{clusters if clusters else ''}\n{dxy if dxy else ''}\n{oi if oi else ''}\n\n{news if news else ''}\n"
            if best_buy: report += f"\n🟢 **BUY:** {best_buy['symbol']} | {best_buy['price']:.4f} → {best_buy['tp']:.4f}\n"
            if best_sell: report += f"\n🔴 **SELL:** {best_sell['symbol']} | {best_sell['price']:.4f} → {best_sell['tp']:.4f}\n"
            await msg.edit_text(report, parse_mode=ParseMode.MARKDOWN)
        elif text == "🔥 СИГНАЛЫ": await signal_search(update, context, fast=False)
        elif text == "⚡ СКАЛЬП": await signal_search(update, context, fast=True)
        elif text == "📈 ГРАФИК":
            await update.message.reply_text("📈 Отправь монету, например: `BTCUSDT`")
            context.user_data["waiting_for_chart"] = True
        elif text == "⏳ АКТИВНЫЕ":
            if not ACTIVE_SIGNALS: await update.message.reply_text("🌫️ Нет.")
            else:
                msg = "⏳ **АКТИВНЫЕ**\n\n"
                for sid, s in ACTIVE_SIGNALS.items():
                    try:
                        resp = session.get_tickers(category="spot", symbol=s["symbol"])
                        cur = float(resp["result"]["list"][0]["lastPrice"]) if resp.get("retCode") == 0 else s["price"]
                    except: cur = s["price"]
                    pnl = ((cur - s["price"]) / s["price"] * 100) if s["signal"] == "BUY" else ((s["price"] - cur) / s["price"] * 100)
                    msg += f"{'🟢' if pnl > 0 else '🔴'} {s['symbol']} | P&L: {pnl:+.2f}%\n"
                await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        elif text == "📚 ОБУЧЕНИЕ":
            l = random.choice(LESSONS)
            await update.message.reply_text(f"{get_phrase('lesson_intro')}\n\n📚 **{l['title']}**\n\n{l['text']}\n\n💡 **Как применять:** {l['use']}", parse_mode=ParseMode.MARKDOWN)
        elif text == "⚙️ ЕЩЁ": await update.message.reply_text("Выбери:", reply_markup=MORE_KEYBOARD)
        elif text == "🌫️ ИСТОРИЯ":
            if not CLOSED_SIGNALS: await update.message.reply_text("🌫️ Пусто.")
            else:
                msg = "🌫️ **ИСТОРИЯ**\n\n"
                for item in CLOSED_SIGNALS[-5:]: msg += f"{'✅' if item['result']=='tp' else '❌'} {item['symbol']} | {item['pnl']:+.2f}%\n"
                await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        elif text == "⚙️ СТРОГОСТЬ":
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🟢 50", callback_data="score_50"), InlineKeyboardButton("🟡 60", callback_data="score_60"), InlineKeyboardButton("🔴 70", callback_data="score_70")]])
            await update.message.reply_text(f"Текущая: {MIN_SCORE}", reply_markup=kb)
        elif text == "🔇 ТИХО":
            SILENT_MODE = not SILENT_MODE; settings["SILENT_MODE"] = SILENT_MODE; save_settings(settings)
            await update.message.reply_text(f"🔇 Тихий режим: {'ВКЛ' if SILENT_MODE else 'ВЫКЛ'}")
        elif text == "🔙 НАЗАД": await update.message.reply_text("Главное меню", reply_markup=MAIN_KEYBOARD)
        elif context.user_data.get("waiting_for_chart"):
            symbol = text.strip().upper()
            if not symbol.endswith("USDT"): symbol += "USDT"
            msg = await update.message.reply_text(f"📈 Рисую график {symbol}...")
            chart = generate_chart(symbol)
            if chart:
                caption = f"📈 **{symbol}** — 15-минутный график\n\n💡 **Как читать:**\n• 🟢 Зелёная линия — цена\n• 🟡 Жёлтая — EMA20, 🔵 Синяя — EMA50\n• Жёлтая выше синей = тренд вверх"
                await update.message.reply_photo(photo=chart, caption=caption, parse_mode=ParseMode.MARKDOWN)
                await msg.delete()
            else: await msg.edit_text(f"🌫️ Не удалось получить данные для {symbol}")
            context.user_data["waiting_for_chart"] = False
    except Exception as e: await notify_error(context, f"handle_message: {e}")

async def signal_search(update: Update, context: ContextTypes.DEFAULT_TYPE, fast=False):
    mode = "⚡ СКАЛЬП" if fast else "🔥 СИГНАЛЫ"
    msg = await update.message.reply_text(f"🔮 Ищу {mode}...")
    signals = []; interval = "1" if fast else "5"
    for d in ["BUY", "SELL"]:
        for sym in get_top_symbols(15):
            s = analyze_symbol(sym, d, interval, fast_mode=fast)
            if s:
                key = f"{sym}-{d}-{interval}"
                if key in SENT_SIGNALS and datetime.now() - SENT_SIGNALS[key] < timedelta(minutes=30 if fast else 120): continue
                SENT_SIGNALS[key] = datetime.now(); signals.append(s)
                if len(signals) >= 3: break
        if len(signals) >= 3: break
    if not signals:
        await msg.edit_text(f"{get_phrase('signal_fail')}\n🌫️ Нет сигналов ({mode})"); return
    await msg.edit_text(f"🔮 Найдено: {len(signals)}")
    DAILY_STATS["signals_found"] += len(signals)
    for s in signals:
        sid = f"{s['symbol']}_{s['time'].strftime('%H%M%S')}"; ACTIVE_SIGNALS[sid] = s
        await update.message.reply_text(format_signal(s), parse_mode=ParseMode.MARKDOWN)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎯 TP", callback_data=f"tp_{sid}"), InlineKeyboardButton("🛑 SL", callback_data=f"sl_{sid}")], [InlineKeyboardButton("📘 Объясни", callback_data=f"explain_{sid}")]])
        await update.message.reply_text("Выбери:", reply_markup=kb)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MIN_SCORE, WEEKLY_STATS
    q = update.callback_query; await q.answer(); d = q.data
    if d.startswith("score_"):
        MIN_SCORE = int(d.split("_")[1]); settings["MIN_SCORE"] = MIN_SCORE; save_settings(settings)
        await q.edit_message_text(f"✅ Строгость: {MIN_SCORE}")
    elif d.startswith("explain_"):
        sid = d.split("_", 1)[1]
        if sid in ACTIVE_SIGNALS: await q.message.reply_text(generate_explanation(ACTIVE_SIGNALS[sid]), parse_mode=ParseMode.MARKDOWN)
    elif d.startswith("tp_") or d.startswith("sl_"):
        act, sid = d.split("_", 1)
        if sid in ACTIVE_SIGNALS:
            s = ACTIVE_SIGNALS[sid]
            pnl = abs(s["tp"]-s["price"])/s["price"]*100 if act=="tp" else -abs(s["sl"]-s["price"])/s["price"]*100
            CLOSED_SIGNALS.append({"symbol": s["symbol"], "result": "tp" if act=="tp" else "sl", "pnl": pnl, "time": datetime.now()})
            if len(CLOSED_SIGNALS) > 10: CLOSED_SIGNALS.pop(0)
            del ACTIVE_SIGNALS[sid]
            WEEKLY_STATS["user_trades"] += 1
            if pnl > 0: WEEKLY_STATS["user_wins"] += 1
            WEEKLY_STATS["user_pnl"] += pnl
            await q.edit_message_text(f"{q.message.text}\n\n{'✅' if act=='tp' else '❌'} Закрыто! P&L: {pnl:+.2f}%")

def main():
    print("\n" + "="*60)
    print("🌙 TradeSight Pro WHISPER v26.3 (Без портфеля)")
    print("="*60)
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.job_queue.run_repeating(wake_up_message, interval=60, first=10)
    app.job_queue.run_repeating(full_summary_loop, interval=60, first=30)
    app.job_queue.run_repeating(auto_scan_loop, interval=300, first=60)
    app.job_queue.run_repeating(emergency_check, interval=300, first=90)
    app.job_queue.run_repeating(evening_ritual, interval=60, first=150)
    app.job_queue.run_repeating(stop_reminder, interval=60, first=30)
    app.job_queue.run_repeating(check_predictions, interval=900, first=180)
    app.job_queue.run_repeating(idle_thoughts, interval=3600, first=600)
    app.job_queue.run_repeating(mirror_demon, interval=60, first=240)
    print("🌙 Демон без портфеля запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
if __name__ == "__main__":
    main()
