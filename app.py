#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TradeSight Pro v29.5 WHISPER (Наблюдатель)
Полный автопилот. Сам считает сделки, сам ведёт историю, сам блокирует убытки.
Убраны лишние кнопки: ГРАФИК, АКТИВНЫЕ.
"""
import asyncio, json, logging, os, sys, time, traceback, random, pytz, re, io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from logging.handlers import RotatingFileHandler
import requests
import xml.etree.ElementTree as ET

try:
    import pandas as pd
    import numpy as np
    import ta
    from pybit.unified_trading import HTTP
    from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
    from telegram.constants import ParseMode
except ImportError as e:
    print(f"❌ {e}")
    os.system(f"{sys.executable} -m pip install pandas ta pybit python-telegram-bot pytz requests matplotlib")
    sys.exit(0)

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
MEMORY_FILE = DATA_DIR / "memory.json"
HISTORY_FILE = DATA_DIR / "history.json"
ANTI_TILT_FILE = DATA_DIR / "anti_tilt.json"

def load_memory():
    if MEMORY_FILE.exists(): return json.load(open(MEMORY_FILE, 'r'))
    return {"favorite": [], "hated": [], "mood": "neutral", "last_mood_change": datetime.now().isoformat()}
def save_memory(mem): json.dump(mem, open(MEMORY_FILE, 'w'), indent=2)

memory = load_memory()
FAVORITE_COINS = memory.get("favorite", [])
HATED_COINS = memory.get("hated", [])
BOT_MOOD = memory.get("mood", "neutral")
LAST_USER_INTERACTION = datetime.now()
ANTI_TILT_BLOCKS = {"ПРОБОЙ": None, "ОТСКОК": None, "СКРЫТЫЙ": None, "КИТ": None, "КРЕСТ": None}

PHRASES = {
    "wake_up": ["🌅 Рынок просыпается. Сегодня я чувствую прилив сил.", "☕️ Пробуждение. Мои видения пока туманны, но скоро прояснятся."],
    "market_up": ["📈 Рынок зеленеет! Быки правят бал!", "🐂 Чувствую силу быков!"],
    "market_down": ["📉 Кровь на улицах... Медведи рвут всех.", "🐻 Медвежий рёв сотрясает Бездну."],
    "market_neutral": ["😴 Рынок замер. Даже мои алгоритмы засыпают.", "⏳ Боковик. Время учиться."],
    "idle_thoughts": ["🤔 Смотрю на график BTC... Красиво.", "💭 Интересно, почему люди боятся красных свечей?"],
    "signal_found_buy": ["🔥 Огонь! Нашёл точку для покупки.", "🟢 Зелёный свет! Можно входить."],
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
DAILY_STATS = {"signals_found": 0, "predictions_made": 0, "predictions_success": 0}
WEEKLY_STATS = {"user_trades": 0, "user_wins": 0, "user_pnl": 0.0, "bot_predictions": 0, "bot_wins": 0}
CONSECUTIVE_LOSSES = {"ПРОБОЙ": 0, "ОТСКОК": 0, "СКРЫТЫЙ": 0, "КИТ": 0, "КРЕСТ": 0}
COIN_OF_DAY = {"symbol": "BTCUSDT", "reason": "Рынок ждёт новостей."}

LESSONS = [
    {"title": "📊 RSI", "text": "RSI от 0 до 100. Выше 70 — перекуплен. Ниже 30 — перепродан.", "use": "Покупай когда RSI между 50 и 70 при восходящем тренде."},
    {"title": "📈 Объём", "text": "Объём подтверждает силу движения.", "use": "Входи только если объём выше среднего в 1.5+ раза."},
    {"title": "🎯 ATR", "text": "ATR показывает волатильность.", "use": "Ставь стоп-лосс на расстоянии 1.5-2 ATR от входа."}
]

CANDLE_PATTERNS = {
    "doji": {"name": "Доджи", "desc": "Нерешительность рынка.", "action": "Жди подтверждения."},
    "hammer": {"name": "Молот", "desc": "Бычий разворот.", "action": "Присмотрись к покупкам."},
    "bullish_engulfing": {"name": "Бычье поглощение", "desc": "Покупатели перехватили инициативу.", "action": "Отличный сигнал для лонга."},
    "bearish_engulfing": {"name": "Медвежье поглощение", "desc": "Продавцы перехватили инициативу.", "action": "Отличный сигнал для шорта."}
}

SECTORS = {
    "Layer-1": ["BTC","ETH","SOL","ADA","AVAX","DOT","NEAR","ALGO"], "DeFi": ["UNI","AAVE","MKR","SNX","COMP","CRV","SUSHI"],
    "AI": ["FET","AGIX","OCEAN","RNDR","TAO","WLD"], "Meme": ["DOGE","SHIB","PEPE","BONK","WIF","FLOKI"],
    "Gaming": ["IMX","GALA","SAND","MANA","AXS","ENJ"], "L2": ["MATIC","ARB","OP","STRK","ZKS","METIS"]
}

def is_sleep_time():
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    return 1 <= now.hour < 6 or (now.hour == 6 and now.minute < 50)

def safe_api_call(func, *args, **kwargs):
    for attempt in range(3):
        try: return func(*args, **kwargs)
        except Exception:
            if attempt == 2: return None
            time.sleep(2)

def get_btc_correlation(symbol: str) -> float:
    if symbol == "BTCUSDT": return 1.0
    try:
        df_btc = get_klines("BTCUSDT", "5", 100); df_sym = get_klines(symbol, "5", 100)
        if df_btc is None or df_sym is None: return 0.0
        ret_btc = df_btc["close"].pct_change().dropna(); ret_sym = df_sym["close"].pct_change().dropna()
        idx = ret_btc.index.intersection(ret_sym.index)
        return ret_btc.loc[idx].corr(ret_sym.loc[idx]) if len(idx) >= 30 else 0.0
    except: return 0.0

MAIN_KEYBOARD = ReplyKeyboardMarkup([["📊 СВОДКА", "🔥 СИГНАЛЫ"], ["📚 ОБУЧЕНИЕ", "⚡ СКАЛЬП"], ["⚙️ ЕЩЁ"]], resize_keyboard=True)
MORE_KEYBOARD = ReplyKeyboardMarkup([["📆 ИТОГИ ДНЯ", "🌫️ ИСТОРИЯ"], ["🧠 СТАТ ПРОГНОЗОВ", "⚙️ СТРОГОСТЬ"], ["🔇 ТИХО", "🔙 НАЗАД"]], resize_keyboard=True)

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
        phrase = get_phrase("prediction_success") if success else get_phrase("prediction_fail")
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"{phrase}\n{p['symbol']}: ${p['start_price']:.4f} → ${cur:.4f}")
    if updated:
        save_predictions(preds); save_stats_predict(stats)
        memory["favorite"] = FAVORITE_COINS[:5]; memory["hated"] = HATED_COINS[:5]; save_memory(memory)

def get_stats_message():
    stats = load_stats_predict()
    if stats["total"] == 0: return "🧠 **СТАТИСТИКА ПРОГНОЗОВ**\n\nПока нет данных."
    winrate = (stats["success"] / stats["total"] * 100) if stats["total"] > 0 else 0
    return f"🧠 **ТОЧНОСТЬ ДУХОВ**\n\n📊 За всё время: {stats['total']}\n✅ Сбылось: {stats['success']}\n❌ Не сбылось: {stats['failed']}\n🎯 Точность: **{winrate:.1f}%**"

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

def get_top_in_sector(sector_name, limit=3):
    try:
        data = session.get_tickers(category="spot")
        if data.get("retCode") != 0: return []
        tickers = data["result"]["list"]
        sector_coins = []
        for t in tickers:
            sym = t["symbol"].replace("USDT", "")
            if sym in SECTORS.get(sector_name, []):
                ch = float(t.get("price24hPcnt", 0)) * 100
                sector_coins.append((sym, ch))
        sector_coins.sort(key=lambda x: x[1], reverse=True)
        return sector_coins[:limit]
    except: return []

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
        msg = "📈 **СЕКТОРА И КОНКРЕТНЫЕ МОНЕТЫ:**\n"
        for sec, avg in sorted(sector_perf.items(), key=lambda x: x[1], reverse=True):
            em = "🟢" if avg > 0 else "🔴"
            msg += f"{em} **{sec}:** {avg:+.1f}% — "
            top_coins = get_top_in_sector(sec, 3)
            if top_coins: msg += ", ".join([f"{c[0]} ({c[1]:+.1f}%)" for c in top_coins]) + "\n"
            else: msg += "данные не загружены\n"
        best = max(sector_perf, key=sector_perf.get); worst = min(sector_perf, key=sector_perf.get)
        msg += f"\n💡 **СОВЕТ:** Капитал идёт в **{best}**. Из **{worst}** лучше пока выйти.\n"
        return msg
    except: return ""

def get_dxy() -> str:
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=10)
        eur = r.json()['rates']['EUR']; dxy = 100 * (1/eur) ** 0.576
        return f"💵 **DXY:** {dxy:.1f}"
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
    bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_upper"] = bb.bollinger_hband()
    return df

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
        p = CANDLE_PATTERNS[key]; return f"🕯️ **{p['name']}**: {p['desc']} {p['action']}"
    return ""

def is_strategy_blocked(strategy_name):
    if not strategy_name: return False
    for key in ANTI_TILT_BLOCKS:
        if key in strategy_name:
            block_time = ANTI_TILT_BLOCKS[key]
            if block_time and datetime.now() < block_time:
                return True
    return False

def analyze_symbol(symbol, interval="5", fast_mode=False):
    df = get_klines(symbol, interval, 100 if fast_mode else 200)
    if df is None or len(df) < 50: return None
    df = calculate_indicators(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = last["close"]
    atr = last["atr"]
    vol_thresh = 1.0 if fast_mode else 1.2

    if last["volume_ratio"] < vol_thresh: return None

    signal_info = None

    # 1. ПРОБОЙ ТРЕНДА
    if (last["ema20"] > last["ema50"] and last["close"] > prev["high"] * 1.001 and 
        50 < last["rsi"] < (80 if fast_mode else 75) and last["macd"] > last["macd_signal"]):
        if not is_strategy_blocked("ПРОБОЙ"):
            sl = price - atr * (1.0 if fast_mode else 1.5)
            tp = price + atr * (1.2 if fast_mode else 2.5)
            signal_info = (sl, tp, 65, "🟢 ПРОБОЙ ТРЕНДА", "Рынок в движении. Цена пробила максимум.")

    # 2. ОТСКОК
    elif (last["close"] <= last["bb_lower"] and last["rsi"] < 45 and 
          last["volume_ratio"] > 1.2 and not (last["ema20"] > last["ema50"])):
        if not is_strategy_blocked("ОТСКОК"):
            sl = price - atr * 0.8
            tp = price + atr * 1.5
            signal_info = (sl, tp, 55, "🟡 ОТСКОК ОТ БЕЗДНЫ", "Рынок в боковике. Цена у нижней границы.")

    # 3. СКРЫТЫЙ БЫК
    elif (last["close"] < prev["close"] and last["rsi"] > prev["rsi"] and 
          last["rsi"] < 50 and last["volume_ratio"] > 1.0):
        if not is_strategy_blocked("СКРЫТЫЙ"):
            sl = price - atr * 1.0
            tp = price + atr * 1.8
            signal_info = (sl, tp, 60, "🐂 СКРЫТЫЙ БЫК", "Цена падает, но сила медведей иссякает.")

    # 4. КИТ
    elif (last["close"] < last["open"] and last["volume_ratio"] > 2.5 and 
          last["low"] > prev["low"]):
        if not is_strategy_blocked("КИТ"):
            sl = price - atr * 0.5
            tp = price + atr * 1.5
            signal_info = (sl, tp, 70, "🐋 КИТ НА ОХОТЕ", "Кто-то крупный вытряхнул слабые руки.")

    # 5. КРЕСТ
    elif (prev["ema20"] <= prev["ema50"] and last["ema20"] > last["ema50"] and 
          last["volume_ratio"] > 1.0):
        if not is_strategy_blocked("КРЕСТ"):
            sl = price - atr * 1.5
            tp = price + atr * 2.0
            signal_info = (sl, tp, 75, "✝️ ЗОЛОТОЙ КРЕСТ", "Быстрая EMA пересекла медленную вверх.")

    if not signal_info: return None

    sl, tp, base_score, strat_name, strat_desc = signal_info
    
    score = base_score
    if last["rsi"] > 55 and "ПРОБОЙ" in strat_name: score += 10
    if last["rsi"] < 40 and "ОТСКОК" in strat_name: score += 10
    if last["volume_ratio"] > 1.8: score += 10
    
    min_score = 40 if fast_mode else MIN_SCORE
    if score < min_score: return None

    rr = abs(tp - price) / abs(price - sl) if abs(price - sl) > 0 else 0
    btc_corr = get_btc_correlation(symbol)
    pattern = detect_candle_pattern(df)
    
    return {
        "symbol": symbol, "signal": "BUY", "price": price, "tp": tp, "sl": sl,
        "score": score, "rsi": last["rsi"], "volume_ratio": last["volume_ratio"],
        "rr": rr, "time": datetime.now(), "atr": atr, "btc_corr": btc_corr, "pattern": pattern,
        "strategy": strat_name, "strategy_desc": strat_desc
    }

def format_signal(s):
    stars = "🔥" * min(5, int(s["score"] / 20) + 1)
    corr_line = f"\n📈 Корр. с BTC: {s.get('btc_corr', 0):.2f}" if s.get('btc_corr', 0) > 0.5 else ""
    
    strat_emoji = "🟢"
    if "ОТСКОК" in s['strategy']: strat_emoji = "🟡"
    elif "СКРЫТЫЙ" in s['strategy']: strat_emoji = "🐂"
    elif "КИТ" in s['strategy']: strat_emoji = "🐋"
    elif "КРЕСТ" in s['strategy']: strat_emoji = "✝️"
        
    personality = "\n💚 *О, мой старый знакомый!*" if s['symbol'] in FAVORITE_COINS else ("\n💔 *Этот актив вечно меня обманывает.*" if s['symbol'] in HATED_COINS else "")
    phrase = get_phrase("signal_found_buy")
    
    return f"""
{strat_emoji} [ СТРАТЕГИЯ: {s['strategy']} ] {strat_emoji}
**Рынок:** {s['strategy_desc']}
**Сигнал:** ПОКУПАТЬ {escape_markdown(s['symbol'])} {stars} Score: {s['score']:.0f}/100
{phrase}

💵 ВХОД: {s['price']:.6f}
🎯 ЦЕЛЬ: {s['tp']:.6f}
🛑 СТОП: {s['sl']:.6f}

📊 RSI: {s['rsi']:.1f} | Объём: x{s['volume_ratio']:.2f}
⚖️ Риск/Прибыль: 1:{s['rr']:.2f}{corr_line}{personality}{s.get('pattern', '')}
⏰ {s['time'].strftime('%H:%M:%S')}
"""

def generate_explanation(s):
    reasons = []
    if "ПРОБОЙ" in s['strategy']:
        if s["volume_ratio"] > 1.8: reasons.append("🔥 Объём высокий.")
        reasons.append("📈 Тренд восходящий.")
    elif "ОТСКОК" in s['strategy']:
        reasons.append("📊 Рынок в боковике, цена у нижней границы.")
    elif "СКРЫТЫЙ" in s['strategy']:
        reasons.append("🐂 Цена падает, но индикатор силы растёт.")
    elif "КИТ" in s['strategy']:
        reasons.append(f"🐋 Крупный игрок выкупил панику.")
    elif "КРЕСТ" in s['strategy']:
        reasons.append("✝️ Скользящие средние пересеклись вверх.")
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
    return f"🌡️ **РЫНОК:** 🟢{green} 🔴{total-green} — {sentiment}"

def get_pending_predictions():
    preds = load_predictions()
    pending = []
    for p in preds:
        if not p.get("checked"):
            pred_time = datetime.fromisoformat(p["time"])
            time_left = pred_time + timedelta(hours=4) - datetime.now()
            if time_left.total_seconds() > 0:
                hours, rem = divmod(time_left.seconds, 3600)
                mins, _ = divmod(rem, 60)
                pending.append({
                    "symbol": p["symbol"],
                    "direction": "рост" if p["direction"] == "up" else "падение",
                    "target": p["start_price"],
                    "time_left": f"{hours}ч {mins}м"
                })
    return pending[:3]

async def check_active_trades(context: ContextTypes.DEFAULT_TYPE):
    global WEEKLY_STATS, CONSECUTIVE_LOSSES, ANTI_TILT_BLOCKS
    if not ACTIVE_SIGNALS: return
    for sid, s in list(ACTIVE_SIGNALS.items()):
        if datetime.now() - s['time'] < timedelta(minutes=1): continue
        try:
            resp = session.get_tickers(category="spot", symbol=s["symbol"])
            if resp.get("retCode") != 0: continue
            cur = float(resp["result"]["list"][0]["lastPrice"])
        except: continue
        is_tp = cur >= s["tp"] if s["signal"] == "BUY" else cur <= s["tp"]
        is_sl = cur <= s["sl"] if s["signal"] == "BUY" else cur >= s["sl"]
        if is_tp or is_sl:
            pnl = abs(cur - s["price"]) / s["price"] * 100 if is_tp else -abs(cur - s["price"]) / s["price"] * 100
            emoji = "✅" if is_tp else "❌"
            act = "Тейк-профиту" if is_tp else "Стоп-лоссу"
            
            # Анти-тильт логика
            if not is_tp:
                strat_key = None
                if "ПРОБОЙ" in s['strategy']: strat_key = "ПРОБОЙ"
                elif "ОТСКОК" in s['strategy']: strat_key = "ОТСКОК"
                if strat_key:
                    CONSECUTIVE_LOSSES[strat_key] += 1
                    if CONSECUTIVE_LOSSES[strat_key] >= 3:
                        ANTI_TILT_BLOCKS[strat_key] = datetime.now() + timedelta(hours=1)
                        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"💀 **АНТИ-ТИЛЬТ**\nСтратегия **{strat_key}** дала 3 убытка подряд.\nЯ заблокировал её на 1 час. Иди пить чай.")
                        CONSECUTIVE_LOSSES[strat_key] = 0
            else:
                for key in CONSECUTIVE_LOSSES: CONSECUTIVE_LOSSES[key] = 0

            # Сохраняем в историю АВТОМАТИЧЕСКИ
            history_file = HISTORY_FILE
            history = {}
            if history_file.exists():
                with open(history_file, 'r') as f: history = json.load(f)
            s["status"] = "tp" if is_tp else "sl"
            s["closed_time"] = datetime.now().isoformat()
            s["exit_price"] = cur
            s["pnl"] = pnl
            history[sid] = s
            with open(history_file, 'w') as f: json.dump(history, f, indent=2)
            
            WEEKLY_STATS["user_trades"] += 1
            if is_tp: WEEKLY_STATS["user_wins"] += 1
            WEEKLY_STATS["user_pnl"] += pnl
            
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"{emoji} **АВТОЗАКРЫТИЕ**\n{s['symbol']} по {act}\nP&L: {pnl:+.2f}%")
            del ACTIVE_SIGNALS[sid]

async def daily_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().date()
    if not HISTORY_FILE.exists():
        await update.message.reply_text("📆 История сделок пуста. Жди авто-закрытий.")
        return
    with open(HISTORY_FILE, 'r') as f: history = json.load(f)
    day_trades = []
    for sid, s in history.items():
        if 'closed_time' not in s: continue
        ct = datetime.fromisoformat(s['closed_time']).date()
        if ct == today: day_trades.append(s)
    if not day_trades:
        await update.message.reply_text("📆 Сегодня сделок не было или они ещё не закрылись.")
        return
    wins = sum(1 for t in day_trades if t.get('status') == 'tp')
    losses = len(day_trades) - wins
    total_pnl = sum(t.get('pnl', 0) for t in day_trades)
    best = max(day_trades, key=lambda x: x.get('pnl', -999))
    worst = min(day_trades, key=lambda x: x.get('pnl', 999))
    msg = f"📆 **ИТОГИ ДНЯ** ({today.strftime('%d.%m.%Y')})\n\n"
    msg += f"📊 Сделок: {len(day_trades)}\n✅ Побед: {wins}\n❌ Поражений: {losses}\n"
    msg += f"💰 Чистый P&L: {total_pnl:+.2f}%\n\n"
    msg += f"🏆 Лучшая: {best['symbol']} ({best.get('pnl', 0):+.2f}%)\n"
    msg += f"💀 Худшая: {worst['symbol']} ({worst.get('pnl', 0):+.2f}%)\n\n"
    msg += "💡 Совет: Продолжай вести дневник."
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def weekday_heatmap(context: ContextTypes.DEFAULT_TYPE):
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    if now.weekday() != 6 or now.hour != 20: return
    if not HISTORY_FILE.exists(): return
    with open(HISTORY_FILE, 'r') as f: history = json.load(f)
    day_stats = {0: [], 1: [], 2: [], 3: [], 4: [], 5: [], 6: []}
    for sid, s in history.items():
        if 'closed_time' not in s: continue
        ct = datetime.fromisoformat(s['closed_time'])
        wd = ct.weekday()
        day_stats[wd].append(s.get('pnl', 0))
    days = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
    msg = "📅 **ТВОЯ СТАТИСТИКА ПО ДНЯМ НЕДЕЛИ:**\n\n"
    best_day = (-1, -100)
    for i, d in enumerate(days):
        if day_stats[i]:
            avg = sum(day_stats[i]) / len(day_stats[i])
            msg += f"{d}: {avg:+.2f}% ({len(day_stats[i])} сделок)\n"
            if avg > best_day[1]: best_day = (i, avg)
        else: msg += f"{d}: Нет сделок\n"
    if best_day[0] != -1:
        msg += f"\n💡 Лучший день — **{days[best_day[0]]}**. Торгуй в это время активнее."
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

async def coin_of_day(context: ContextTypes.DEFAULT_TYPE):
    global COIN_OF_DAY
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    if now.hour != 8 or now.minute != 0: return
    try:
        data = session.get_tickers(category="spot")
        if data.get("retCode") != 0: return
        tickers = data["result"]["list"]
        best_coin = None
        best_ratio = 0
        for t in tickers:
            sym = t["symbol"]
            if not sym.endswith("USDT"): continue
            vol = float(t.get("turnover24h", 0))
            ch = float(t.get("price24hPcnt", 0)) * 100
            if vol > 5_000_000 and -2 < ch < 2:
                ratio = vol / (abs(ch) + 0.1)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_coin = sym
        if best_coin:
            COIN_OF_DAY = {"symbol": best_coin, "reason": "Объёмы растут, цена стоит. Крупный игрок набирает позицию."}
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"🎲 **МОНЕТА ДНЯ ОТ ДЕМОНА**\n\nСегодня я бы присмотрелся к **{best_coin}**.\n{COIN_OF_DAY['reason']}\nДеньги любят тишину. Следи за пробоем.")
    except: pass

async def auto_scan_loop(context):
    global LAST_SIGNAL_TIME
    while AUTO_SCAN:
        await asyncio.sleep(300)
        if SILENT_MODE or is_sleep_time(): continue
        signals = []
        for sym in get_top_symbols(15):
            s = analyze_symbol(sym)
            if s:
                key = f"{sym}-BUY"
                if key in SENT_SIGNALS and datetime.now() - SENT_SIGNALS[key] < timedelta(hours=2): continue
                SENT_SIGNALS[key] = datetime.now()
                signals.append(s)
                if len(signals) >= 3: break
        if signals:
            LAST_SIGNAL_TIME = datetime.now()
            for s in signals:
                try: await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=format_signal(s), parse_mode=ParseMode.MARKDOWN)
                except Exception as e: await notify_error(context, f"Ошибка отправки сигнала: {e}")

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
        except: pass

async def full_summary_loop(context):
    while True:
        await asyncio.sleep(300)
        if SILENT_MODE or is_sleep_time(): continue
        msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
        if now.hour % 4 != 0 or now.minute != 0: continue
        try:
            market = get_market_summary()
            clusters = get_cluster_analysis()
            dxy_val = get_dxy()
            if dxy_val:
                dxy_num = float(dxy_val.replace("💵 **DXY:** ", ""))
                if dxy_num > 105: dxy_text = f"{dxy_val} (высокий, давит на крипту)"
                elif dxy_num < 100: dxy_text = f"{dxy_val} (низкий, попутный ветер для крипты)"
                else: dxy_text = f"{dxy_val} (нейтральный)"
            else: dxy_text = ""
            oi = get_open_interest()
            btc_pattern = ""
            btc_df = get_klines("BTCUSDT", "15", 3)
            if btc_df is not None:
                pattern = detect_candle_pattern(btc_df)
                if pattern: btc_pattern = f"\n🕯️ **СВЕЧНОЙ ПАТТЕРН (BTC):** {pattern}\n"
            pending = get_pending_predictions()
            signals = []
            for sym in get_top_symbols(15):
                s = analyze_symbol(sym)
                if s:
                    signals.append(s)
                    if len(signals) >= 3: break
            report = f"📊 **АВТО-СВОДКА** ({now.strftime('%H:%M')})\n\n{market}\n\n{clusters if clusters else ''}\n{dxy_text if dxy_text else ''}\n{oi if oi else ''}{btc_pattern}"
            if pending:
                report += "\n🧠 **ПРОГНОЗЫ НА ПРОВЕРКЕ:**\n"
                for p in pending: report += f"• {p['symbol']}: жду {p['direction']} до ${p['target']:.4f} (осталось {p['time_left']})\n"
            if signals:
                report += f"\n🟢 **ТОП-{len(signals)} СИГНАЛОВ:**\n"
                for s in signals: report += f"• {s['symbol']} | {s['strategy']} | Score {s['score']:.0f} | ${s['price']:.4f} → ${s['tp']:.4f}\n"
            else: report += f"\n{get_phrase('signal_fail')}"
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=report, parse_mode=ParseMode.MARKDOWN)
        except: pass
        await asyncio.sleep(300)

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
        progress = (cur - s["price"]) / (s["tp"] - s["price"]) if s["tp"] != s["price"] else 0
        if progress >= 0.5 and not s.get("partial_advised"):
            s["partial_advised"] = True
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"✂️ **ЧАСТИЧНАЯ ФИКСАЦИЯ**\n{s['symbol']} прошёл 50% до цели.\n💡 Закрой 30-50% позиции, остальное переведи в безубыток.")
        if progress >= 0.3 and not s.get("trailing_advised"):
            s["trailing_advised"] = True
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"📈 **ТРЕЙЛИНГ-СТОП**\n{s['symbol']} в плюсе.\n💡 Подтяни стоп до {s['price']:.4f} (безубыток).")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_USER_INTERACTION; LAST_USER_INTERACTION = datetime.now()
    mood_text = {"excited": "⚡ Я полон энергии!", "neutral": "🧘 Я в равновесии.", "cautious": "⚠️ Я насторожен.", "tired": "😴 Я немного устал."}.get(BOT_MOOD, "")
    await update.message.reply_text(f"🌙 **ДУХИ БЕЗДНЫ** v29.5\n{mood_text}\nСтрогость: {MIN_SCORE}\nТихий: {'🔇' if SILENT_MODE else '🔊'}", reply_markup=MAIN_KEYBOARD)

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
            market = get_market_summary()
            clusters = get_cluster_analysis()
            dxy_val = get_dxy()
            if dxy_val:
                dxy_num = float(dxy_val.replace("💵 **DXY:** ", ""))
                if dxy_num > 105: dxy_text = f"{dxy_val} (высокий, давит на крипту)"
                elif dxy_num < 100: dxy_text = f"{dxy_val} (низкий, попутный ветер для крипты)"
                else: dxy_text = f"{dxy_val} (нейтральный)"
            else: dxy_text = ""
            oi = get_open_interest()
            btc_pattern = ""
            btc_df = get_klines("BTCUSDT", "15", 3)
            if btc_df is not None:
                pattern = detect_candle_pattern(btc_df)
                if pattern: btc_pattern = f"\n🕯️ **СВЕЧНОЙ ПАТТЕРН (BTC):** {pattern}\n"
            pending = get_pending_predictions()
            signals = []
            for sym in get_top_symbols(15):
                s = analyze_symbol(sym)
                if s:
                    signals.append(s)
                    if len(signals) >= 3: break
            report = f"📊 **СВОДКА**\n\n{market}\n\n{clusters if clusters else ''}\n{dxy_text if dxy_text else ''}\n{oi if oi else ''}{btc_pattern}"
            if pending:
                report += "\n🧠 **ПРОГНОЗЫ НА ПРОВЕРКЕ:**\n"
                for p in pending: report += f"• {p['symbol']}: жду {p['direction']} до ${p['target']:.4f} (осталось {p['time_left']})\n"
            if signals:
                report += f"\n🟢 **ТОП-{len(signals)} СИГНАЛОВ:**\n"
                for s in signals: report += f"• {s['symbol']} | {s['strategy']} | Score {s['score']:.0f} | ${s['price']:.4f} → ${s['tp']:.4f}\n"
            else: report += f"\n{get_phrase('signal_fail')}"
            await msg.edit_text(report, parse_mode=ParseMode.MARKDOWN)
        elif text == "🔥 СИГНАЛЫ": await signal_search(update, context, fast=False)
        elif text == "⚡ СКАЛЬП": await signal_search(update, context, fast=True)
        elif text == "📚 ОБУЧЕНИЕ":
            l = random.choice(LESSONS)
            await update.message.reply_text(f"{get_phrase('lesson_intro')}\n\n📚 **{l['title']}**\n\n{l['text']}\n\n💡 **Как применять:** {l['use']}", parse_mode=ParseMode.MARKDOWN)
        elif text == "⚙️ ЕЩЁ": await update.message.reply_text("Выбери:", reply_markup=MORE_KEYBOARD)
        elif text == "📆 ИТОГИ ДНЯ": await daily_summary(update, context)
        elif text == "📰 НОВОСТИ":
            msg = await update.message.reply_text("📰 Ищу новости...")
            news = get_news()
            await msg.edit_text(news if news else "🌫️ Новостей нет.", parse_mode=ParseMode.MARKDOWN)
        elif text == "🧠 СТАТ ПРОГНОЗОВ": await update.message.reply_text(get_stats_message(), parse_mode=ParseMode.MARKDOWN)
        elif text == "🌫️ ИСТОРИЯ":
            if not HISTORY_FILE.exists():
                await update.message.reply_text("🌫️ История пуста.")
                return
            with open(HISTORY_FILE, 'r') as f: history = json.load(f)
            recent = list(history.items())[-5:]
            msg = "🌫️ **ИСТОРИЯ (авто)**\n\n"
            for sid, s in reversed(recent):
                emoji = "✅" if s.get('status') == 'tp' else "❌"
                msg += f"{emoji} {s['symbol']} | {s.get('pnl', 0):+.2f}%\n"
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        elif text == "⚙️ СТРОГОСТЬ":
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🟢 50", callback_data="score_50"), InlineKeyboardButton("🟡 60", callback_data="score_60"), InlineKeyboardButton("🔴 70", callback_data="score_70")]])
            await update.message.reply_text(f"Текущая: {MIN_SCORE}", reply_markup=kb)
        elif text == "🔇 ТИХО":
            SILENT_MODE = not SILENT_MODE; settings["SILENT_MODE"] = SILENT_MODE; save_settings(settings)
            await update.message.reply_text(f"🔇 Тихий режим: {'ВКЛ' if SILENT_MODE else 'ВЫКЛ'}")
        elif text == "🔙 НАЗАД": await update.message.reply_text("Главное меню", reply_markup=MAIN_KEYBOARD)
    except Exception as e: await notify_error(context, f"handle_message: {e}")

async def signal_search(update: Update, context: ContextTypes.DEFAULT_TYPE, fast=False):
    mode = "⚡ СКАЛЬП" if fast else "🔥 СИГНАЛЫ"
    msg = await update.message.reply_text(f"🔮 Ищу {mode}...")
    signals = []; interval = "1" if fast else "5"
    for sym in get_top_symbols(25):
        s = analyze_symbol(sym, interval, fast_mode=fast)
        if s:
            if fast and s['score'] < 45 and len(signals) > 0: continue
            key = f"{sym}-BUY-{interval}"
            if key in SENT_SIGNALS and datetime.now() - SENT_SIGNALS[key] < timedelta(minutes=10 if fast else 120): continue
            SENT_SIGNALS[key] = datetime.now()
            signals.append(s)
            if len(signals) >= 5: break
    if not signals:
        await msg.edit_text(f"{get_phrase('signal_fail')}\n🌫️ Нет сигналов ({mode})"); return
    await msg.edit_text(f"🔮 Найдено: {len(signals)}")
    DAILY_STATS["signals_found"] += len(signals)
    for s in signals:
        sid = f"{s['symbol']}_{s['time'].strftime('%H%M%S')}"; ACTIVE_SIGNALS[sid] = s
        await update.message.reply_text(format_signal(s), parse_mode=ParseMode.MARKDOWN)
        # Убраны кнопки TP/SL/Объясни. Теперь только автотрекинг.
        await update.message.reply_text("⏳ Сделка взята на авто-сопровождение. Я сообщу, когда она закроется.")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MIN_SCORE
    q = update.callback_query; await q.answer(); d = q.data
    if d.startswith("score_"):
        MIN_SCORE = int(d.split("_")[1]); settings["MIN_SCORE"] = MIN_SCORE; save_settings(settings)
        await q.edit_message_text(f"✅ Строгость: {MIN_SCORE}")

def main():
    print("\n" + "="*60)
    print("🌙 TradeSight Pro WHISPER v29.5 (Наблюдатель)")
    print("="*60)
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.job_queue.run_repeating(wake_up_message, interval=60, first=10)
    app.job_queue.run_repeating(coin_of_day, interval=60, first=20)
    app.job_queue.run_repeating(full_summary_loop, interval=300, first=30)
    app.job_queue.run_repeating(auto_scan_loop, interval=300, first=60)
    app.job_queue.run_repeating(emergency_check, interval=300, first=90)
    app.job_queue.run_repeating(check_active_trades, interval=900, first=120)
    app.job_queue.run_repeating(evening_ritual, interval=60, first=150)
    app.job_queue.run_repeating(stop_reminder, interval=60, first=30)
    app.job_queue.run_repeating(check_predictions, interval=900, first=180)
    app.job_queue.run_repeating(idle_thoughts, interval=3600, first=600)
    app.job_queue.run_repeating(mirror_demon, interval=60, first=240)
    app.job_queue.run_repeating(weekday_heatmap, interval=3600, first=300)
    print("🌙 Демон-Наблюдатель запущен. Полный автопилот.")
    app.run_polling()

if __name__ == "__main__":
    main()
