#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TradeSight Pro v32.8 WHISPER (Автоматон-Болтун)
+ Исправлен JobQueue
+ Защита от пропуска SL через проверку low минутной свечи
+ Анти-ложный пробой, дивергенция RSI, фильтр спреда, Order Book КИТ
+ Авто-фильтр секторов, анализ ликвидаций
+ Google Sheets авто-экспорт (новый лист «Сделки v32.7»)
+ Актуальный SL в таблице, SL (безубыток)
- Убрана кнопка «Обучение»
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
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, JobQueue
    from telegram.constants import ParseMode
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
except ImportError as e:
    print(f"❌ {e}")
    os.system(f"{sys.executable} -m pip install pandas ta pybit python-telegram-bot pytz requests matplotlib gspread oauth2client")
    sys.exit(0)

GOOGLE_CREDENTIALS_FILE = "credentials.json"
GOOGLE_SHEET_ID = "1PKH_z-ec-23a4cHFpJp1C-3PePlmuI0Ksnu0j_A3hsU"
GOOGLE_SHEET_NAME = "Сделки v32.7"

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
TRADES_CSV_FILE = DATA_DIR / "trades.csv"

def load_memory():
    if MEMORY_FILE.exists(): return json.load(open(MEMORY_FILE, 'r'))
    return {"favorite": [], "hated": [], "mood": "neutral", "last_mood_change": datetime.now().isoformat()}
def save_memory(mem): json.dump(mem, open(MEMORY_FILE, 'w'), indent=2)

memory = load_memory()
FAVORITE_COINS = memory.get("favorite", [])
HATED_COINS = memory.get("hated", [])
BOT_MOOD = memory.get("mood", "neutral")
LAST_USER_INTERACTION = datetime.now()

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
    "tilt_warning": ["💀 Три удара подряд... Бездна шепчет быть осторожнее.", "😵‍💫 Стратегия хромает. Может, сменим пластинку?", "☕️ Тяжелый период. Сделай паузу, смертный."]
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
for lib in ["httpx", "telegram.ext", "pybit", "gspread", "oauth2client"]: logging.getLogger(lib).setLevel(logging.WARNING)

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
LIQUIDATIONS_WARNING_UNTIL = None
TOP_SECTORS = []

# ========== GOOGLE SHEETS ==========
gc = None
sheet = None
try:
    if os.path.exists(GOOGLE_CREDENTIALS_FILE):
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_FILE, scope)
        gc = gspread.authorize(creds)
        try:
            sh = gc.open_by_key(GOOGLE_SHEET_ID)
            try:
                sheet = sh.worksheet(GOOGLE_SHEET_NAME)
                print(f"📊 Лист '{GOOGLE_SHEET_NAME}' найден.")
            except:
                sheet = sh.add_worksheet(title=GOOGLE_SHEET_NAME, rows=1000, cols=13)
                headers = ["Дата", "Время", "Монета", "Сектор", "Стратегия", "Score", "Вход", "Выход", "P&L%", "Причина", "RSI", "Объём", "Риск/Прибыль"]
                sheet.append_row(headers)
                print(f"📊 Создан новый лист '{GOOGLE_SHEET_NAME}' с заголовками.")
        except Exception as e:
            print(f"⚠️ Ошибка доступа к Google Sheets: {e}. Сделки будут сохраняться в CSV.")
            sheet = None
    else:
        print("⚠️ credentials.json не найден. Сделки будут сохраняться в CSV.")
except Exception as e:
    print(f"⚠️ Google Sheets недоступен: {e}. Сделки будут сохраняться в CSV.")
    sheet = None

if sheet is None:
    if not TRADES_CSV_FILE.exists():
        with open(TRADES_CSV_FILE, 'w', encoding='utf-8') as f:
            f.write("Дата,Время,Монета,Сектор,Стратегия,Score,Вход,Выход,P&L%,Причина,RSI,Объём,Риск/Прибыль\n")
        print(f"📊 Создан CSV-файл: {TRADES_CSV_FILE}")

def save_trade_to_sheet(trade_data):
    row = [
        trade_data.get("date", ""),
        trade_data.get("time", ""),
        trade_data.get("symbol", ""),
        trade_data.get("sector", ""),
        trade_data.get("strategy", ""),
        trade_data.get("score", ""),
        trade_data.get("entry", ""),
        trade_data.get("exit", ""),
        trade_data.get("pnl", ""),
        trade_data.get("reason", ""),
        trade_data.get("rsi", ""),
        trade_data.get("volume", ""),
        trade_data.get("rr", "")
    ]
    
    if sheet is not None:
        try:
            sheet.append_row(row)
            print(f"📊 Сделка записана в Google Sheets")
        except Exception as e:
            print(f"⚠️ Ошибка записи в Google Sheets: {e}")
    
    try:
        with open(TRADES_CSV_FILE, 'a', encoding='utf-8') as f:
            f.write(','.join(str(x) for x in row) + '\n')
    except Exception as e:
        print(f"⚠️ Ошибка записи в CSV: {e}")
# =====================================

SECTORS = {
    "Layer-1": ["BTC","ETH","SOL","ADA","AVAX","DOT","NEAR","ALGO","ATOM","FTM","INJ","ICP","APT","SUI","SEI","TIA","TON"],
    "DeFi": ["UNI","AAVE","MKR","SNX","COMP","CRV","SUSHI","LDO","GMX","HYPE","JUP","JTO","RAY","DYDX","1INCH"],
    "AI": ["FET","AGIX","OCEAN","RNDR","TAO","WLD","AKT","CTXC"],
    "Meme": ["DOGE","SHIB","PEPE","BONK","WIF","FLOKI","PEOPLE","TURBO","MYRO","SAMO"],
    "Gaming": ["IMX","GALA","SAND","MANA","AXS","ENJ","BEAM","PIXEL","NAKA"],
    "L2": ["MATIC","ARB","OP","STRK","ZKS","METIS","MNT","SCROLL","BLAST"],
    "Infrastructure": ["LINK","GRT","BAND","TRB","PYTH"],
    "Payments": ["XRP","XLM","ALGO"],
    "Exchange": ["BNB","OKB","BGB","LEO"],
    "Storage": ["FIL","AR","STORJ"],
    "GambleFi": ["RLB","WIN","FUN"],
    "RWA": ["ONDO","TRU","SNX"],
    "Derivatives": ["DYDX","GMX","GNS"],
    "Launchpad": ["SUPER","SNFT","TKO"],
    "Metaverse": ["RENDER","WEMIX","MBOX","ILV"],
    "Commodities": ["XAUT", "PAXG"]
}

def get_sector_for_symbol(symbol):
    coin = symbol.replace("USDT", "")
    for sector, coins in SECTORS.items():
        if coin in coins: return sector
    return "Other"

def get_top_sectors() -> List[str]:
    global TOP_SECTORS
    try:
        data = session.get_tickers(category="spot")
        if data.get("retCode") != 0: return TOP_SECTORS if TOP_SECTORS else []
        tickers = data["result"]["list"]
        sector_perf = {}
        for sec, coins in SECTORS.items():
            gains = []
            for t in tickers:
                sym = t["symbol"].replace("USDT", "")
                if sym in coins: gains.append(float(t.get("price24hPcnt", 0)) * 100)
            if gains: sector_perf[sec] = sum(gains) / len(gains)
        sorted_sectors = sorted(sector_perf.items(), key=lambda x: x[1], reverse=True)
        TOP_SECTORS = [s[0] for s in sorted_sectors[:3]]
        return TOP_SECTORS
    except: return TOP_SECTORS if TOP_SECTORS else []

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

def get_spread(symbol: str) -> float:
    try:
        resp = session.get_tickers(category="spot", symbol=symbol)
        if resp.get("retCode") != 0: return 0.0
        ticker = resp["result"]["list"][0]
        ask = float(ticker.get("askPrice", 0))
        bid = float(ticker.get("bidPrice", 0))
        if ask <= 0 or bid <= 0: return 0.0
        return (ask - bid) / bid * 100
    except: return 0.0

def get_orderbook_imbalance(symbol: str) -> float:
    try:
        resp = session.get_orderbook(category="spot", symbol=symbol, limit=25)
        if resp.get("retCode") != 0: return 1.0
        bids = resp["result"].get("b", [])
        asks = resp["result"].get("a", [])
        if not bids or not asks: return 1.0
        bids_sum = sum(float(b[1]) for b in bids[:10])
        asks_sum = sum(float(a[1]) for a in asks[:10])
        if asks_sum == 0: return 2.0
        return bids_sum / asks_sum
    except: return 1.0

def get_liquidation_spike() -> Optional[float]:
    global LIQUIDATIONS_WARNING_UNTIL
    try:
        resp = session.get_public_liq_records(category="linear", limit=100)
        if resp.get("retCode") != 0: return None
        records = resp["result"]["list"]
        now = datetime.now()
        total_liq = 0.0
        for r in records:
            liq_time = datetime.fromtimestamp(int(r.get("updatedTime", 0)) / 1000)
            if (now - liq_time).seconds < 300:
                total_liq += float(r.get("size", 0))
        if total_liq > 10_000_000:
            LIQUIDATIONS_WARNING_UNTIL = now + timedelta(minutes=15)
            return total_liq
        return None
    except:
        return None

def is_liquidation_warning_active() -> bool:
    if LIQUIDATIONS_WARNING_UNTIL is None: return False
    return datetime.now() < LIQUIDATIONS_WARNING_UNTIL

MAIN_KEYBOARD = ReplyKeyboardMarkup([["📊 СВОДКА", "🔥 СИГНАЛЫ"], ["⚡ СКАЛЬП", "⚙️ ЕЩЁ"]], resize_keyboard=True)
MORE_KEYBOARD = ReplyKeyboardMarkup([["📆 ИТОГИ ДНЯ", "🌫️ ИСТОРИЯ"], ["🧠 СТАТ ПРОГНОЗОВ", "⚙️ СТРОГОСТЬ"], ["🔇 ТИХО", "🔙 НАЗАД"]], resize_keyboard=True)

def load_predictions():
    if PREDICTIONS_FILE.exists():
        try:
            with open(PREDICTIONS_FILE, 'r') as f:
                content = f.read().strip()
                if content: return json.loads(content)
        except: pass
    return []

def save_predictions(p):
    try:
        with open(PREDICTIONS_FILE, 'w') as f:
            json.dump(p, f, indent=2, default=str)
    except: pass

def load_stats_predict():
    if STATS_PREDICT_FILE.exists():
        try:
            with open(STATS_PREDICT_FILE, 'r') as f:
                content = f.read().strip()
                if content: return json.loads(content)
        except: pass
    return {"total": 0, "success": 0, "failed": 0}

def save_stats_predict(s):
    try:
        with open(STATS_PREDICT_FILE, 'w') as f:
            json.dump(s, f, indent=2)
    except: pass

def add_prediction(symbol: str, price: float, direction: str, confidence: float):
    preds = load_predictions()
    preds.append({
        "symbol": symbol,
        "start_price": price,
        "direction": direction,
        "confidence": confidence,
        "time": datetime.now().isoformat(),
        "checked": False
    })
    save_predictions(preds)
    DAILY_STATS["predictions_made"] += 1
    WEEKLY_STATS["bot_predictions"] += 1

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
    if stats["total"] == 0:
        preds = load_predictions()
        if preds:
            return f"🧠 **СТАТИСТИКА ПРОГНОЗОВ**\n\n📊 Прогнозов сделано: {len(preds)}\n⏳ Ожидают проверки...\n\nПрогнозы проверяются через 4 часа после создания."
        return "🧠 **СТАТИСТИКА ПРОГНОЗОВ**\n\nПока нет данных. Прогнозы создаются автоматически при анализе рынка."
    winrate = (stats["success"] / stats["total"] * 100) if stats["total"] > 0 else 0
    return f"""🧠 **ТОЧНОСТЬ ДУХОВ**

📊 **За всё время:**
• Всего прогнозов: {stats['total']}
• ✅ Сбылось: {stats['success']}
• ❌ Не сбылось: {stats['failed']}
• 🎯 Точность: **{winrate:.1f}%**

💫 {'Духи мудры и точны.' if winrate >= 60 else 'Духи учатся с каждой сделкой.'}"""

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

def get_news():
    try:
        r = requests.get("https://forklog.com/rss/", timeout=10)
        item = ET.fromstring(r.content).find('.//item')
        if item is not None: return f"📰 **НОВОСТЬ:** [{item.find('title').text}]({item.find('link').text})"
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

def analyze_symbol(symbol, interval="5", fast_mode=False):
    global TOP_SECTORS
    
    spread = get_spread(symbol)
    if spread > 1.0:
        return None
    spread_penalty = 20 if spread > 0.5 else 0
    
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
    bonus_score = 0
    tags = []

    if not TOP_SECTORS:
        get_top_sectors()
    sector = get_sector_for_symbol(symbol)
    if sector in TOP_SECTORS:
        bonus_score += 10
        tags.append("🔥 Фаворит сектора")
    
    if is_liquidation_warning_active():
        bonus_score -= 15
        tags.append("⚠️ Высокие ликвидации")

    # 1. ПРОБОЙ ТРЕНДА
    if (last["ema20"] > last["ema50"] and last["close"] > prev["high"] * 1.001 and 
        50 < last["rsi"] < (80 if fast_mode else 75) and last["macd"] > last["macd_signal"]):
        sl = price - atr * (1.0 if fast_mode else 1.5)
        tp = price + atr * (1.2 if fast_mode else 2.5)
        base_score = 65
        is_green = last["close"] > last["open"]
        vol_ok = last["volume_ratio"] > 0.8
        if is_green and vol_ok:
            base_score += 10
            tags.append("✅ Пробой подтверждён")
        elif not is_green:
            base_score -= 20
            tags.append("⚠️ Пробой слабый")
        signal_info = (sl, tp, base_score, "🟢 ПРОБОЙ ТРЕНДА", "Рынок в движении. Цена пробила максимум.")

    # 2. ОТСКОК
    elif (last["close"] <= last["bb_lower"] and last["rsi"] < 45 and 
          last["volume_ratio"] > 1.2 and not (last["ema20"] > last["ema50"])):
        sl = price - atr * 0.8
        tp = price + atr * 1.5
        base_score = 55
        if len(df) >= 40:
            lookback = min(20, len(df) - 2)
            recent = df.iloc[-lookback:]
            price_lows = recent["close"].values
            rsi_lows = recent["rsi"].values
            min_idx1 = price_lows.argmin()
            min_idx2 = price_lows[:min_idx1].argmin() if min_idx1 > 0 else 0
            if min_idx1 > 0 and min_idx2 >= 0:
                if price_lows[min_idx1] < price_lows[min_idx2] and rsi_lows[min_idx1] > rsi_lows[min_idx2]:
                    base_score += 15
                    tags.append("🔄 Сила медведей иссякает")
        signal_info = (sl, tp, base_score, "🟡 ОТСКОК ОТ БЕЗДНЫ", "Рынок в боковике. Цена у нижней границы.")

    # 3. СКРЫТЫЙ БЫК
    elif (last["close"] < prev["close"] and last["rsi"] > prev["rsi"] and 
          last["rsi"] < 50 and last["volume_ratio"] > 1.0):
        sl = price - atr * 1.0
        tp = price + atr * 1.8
        base_score = 60
        signal_info = (sl, tp, base_score, "🐂 СКРЫТЫЙ БЫК", "Цена падает, но сила медведей иссякает.")

    # 4. КИТ
    elif (last["close"] < last["open"] and last["volume_ratio"] > 2.5 and 
          last["low"] > prev["low"]):
        sl = price - atr * 0.5
        tp = price + atr * 1.5
        base_score = 70
        imbalance = get_orderbook_imbalance(symbol)
        if imbalance > 1.5:
            base_score += 15
            tags.append("🐋 Кит подтверждён в стакане")
        elif imbalance < 0.5:
            return None
        signal_info = (sl, tp, base_score, "🐋 КИТ НА ОХОТЕ", "Кто-то крупный вытряхнул слабые руки.")

    # 5. КРЕСТ
    elif (prev["ema20"] <= prev["ema50"] and last["ema20"] > last["ema50"] and 
          last["volume_ratio"] > 1.0):
        sl = price - atr * 1.5
        tp = price + atr * 2.0
        base_score = 75
        signal_info = (sl, tp, base_score, "✝️ ЗОЛОТОЙ КРЕСТ", "Быстрая EMA пересекла медленную вверх.")

    if not signal_info: return None

    sl, tp, base_score, strat_name, strat_desc = signal_info
    
    score = base_score + bonus_score
    if last["rsi"] > 55 and "ПРОБОЙ" in strat_name: score += 10
    if last["rsi"] < 40 and "ОТСКОК" in strat_name: score += 10
    if last["volume_ratio"] > 1.8: score += 10
    
    score -= spread_penalty
    
    min_score = 40 if fast_mode else MIN_SCORE
    if score < min_score: return None

    rr = abs(tp - price) / abs(price - sl) if abs(price - sl) > 0 else 0
    btc_corr = get_btc_correlation(symbol)
    
    return {
        "symbol": symbol, "signal": "BUY", "price": price, "tp": tp, "sl": sl,
        "score": score, "rsi": last["rsi"], "volume_ratio": last["volume_ratio"],
        "rr": rr, "time": datetime.now(), "atr": atr, "btc_corr": btc_corr,
        "strategy": strat_name, "strategy_desc": strat_desc,
        "sector": sector,
        "events": [],
        "tags": tags,
        "original_sl": sl,
        "breakeven_done": False
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
    
    fat_signal = ""
    if s['rr'] >= 3.0:
        fat_signal = "\n🔥 **ЖИРНЫЙ СИГНАЛ!** (Риск/Прибыль 1:{:.1f})".format(s['rr'])
    
    sector_str = f" ({s.get('sector', 'Other')})" if s.get('sector') else ""
    
    tags_line = ""
    if s.get('tags'):
        tags_line = "\n" + " | ".join(s['tags'])
    
    spread_warning = ""
    spread_val = get_spread(s['symbol'])
    if spread_val > 0.5:
        spread_warning = f"\n⚠️ Спред: {spread_val:.1f}% — высокий"
    
    return f"""
{strat_emoji} [ СТРАТЕГИЯ: {s['strategy']} ] {strat_emoji}
**Рынок:** {s['strategy_desc']}
**Сигнал:** ПОКУПАТЬ {escape_markdown(s['symbol'])}{sector_str} {stars} Score: {s['score']:.0f}/100{fat_signal}
{phrase}

💵 ВХОД: {s['price']:.6f}
🎯 ЦЕЛЬ: {s['tp']:.6f}
🛑 СТОП: {s['sl']:.6f}

📊 RSI: {s['rsi']:.1f} | Объём: x{s['volume_ratio']:.2f}
⚖️ Риск/Прибыль: 1:{s['rr']:.2f}{corr_line}{spread_warning}{tags_line}{personality}
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

async def close_signal(context, sid, s, cur, reason):
    global WEEKLY_STATS, CONSECUTIVE_LOSSES
    is_tp = reason == "TP"
    pnl = abs(cur - s["price"]) / s["price"] * 100 if is_tp else -abs(cur - s["price"]) / s["price"] * 100
    
    display_reason = reason
    active_sl = s.get("sl", s.get("original_sl", 0))
    if reason == "SL" and s.get("breakeven_done"):
        display_reason = "SL (безубыток)"
    
    emoji = "✅" if is_tp else ("⏰" if reason == "TIMEOUT" else "❌")
    
    if not is_tp:
        strat_key = None
        if "ПРОБОЙ" in s['strategy']: strat_key = "ПРОБОЙ"
        elif "ОТСКОК" in s['strategy']: strat_key = "ОТСКОК"
        if strat_key:
            CONSECUTIVE_LOSSES[strat_key] += 1
            if CONSECUTIVE_LOSSES[strat_key] == 3:
                await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"💀 **ТРИ УДАРА**\nСтратегия **{strat_key}** дала 3 убытка подряд.\n{get_phrase('tilt_warning')}")
            elif CONSECUTIVE_LOSSES[strat_key] > 3:
                CONSECUTIVE_LOSSES[strat_key] = 0
    else:
        for key in CONSECUTIVE_LOSSES: CONSECUTIVE_LOSSES[key] = 0

    try:
        history = {}
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, 'r') as f: history = json.load(f)
            except: history = {}
        s["status"] = "tp" if is_tp else "sl"
        s["closed_time"] = datetime.now().isoformat()
        s["exit_price"] = cur
        s["pnl"] = pnl
        s["close_reason"] = display_reason
        s["active_sl"] = active_sl
        history[sid] = s
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2, default=str)
    except:
        try:
            with open(HISTORY_FILE, 'w') as f:
                json.dump({sid: s}, f, indent=2, default=str)
        except: pass
    
    now = datetime.now()
    trade_data = {
        "date": now.strftime("%d.%m.%Y"),
        "time": now.strftime("%H:%M:%S"),
        "symbol": s.get("symbol", ""),
        "sector": s.get("sector", "Other"),
        "strategy": s.get("strategy", ""),
        "score": s.get("score", ""),
        "entry": f"{s.get('price', 0):.6f}",
        "exit": f"{cur:.6f}",
        "pnl": f"{pnl:+.2f}%",
        "reason": display_reason,
        "rsi": f"{s.get('rsi', 0):.1f}",
        "volume": f"x{s.get('volume_ratio', 0):.2f}",
        "rr": f"1:{s.get('rr', 0):.2f}"
    }
    save_trade_to_sheet(trade_data)
    
    WEEKLY_STATS["user_trades"] += 1
    if is_tp: WEEKLY_STATS["user_wins"] += 1
    WEEKLY_STATS["user_pnl"] += pnl
    
    event_log = f"{emoji} **СДЕЛКА ЗАКРЫТА**\n\n"
    event_log += f"🔮 {escape_markdown(s['symbol'])}\n"
    event_log += f"📊 Причина: **{display_reason}**\n"
    event_log += f"💵 Вход: ${s['price']:.6f}\n"
    event_log += f"💵 Выход: ${cur:.6f}\n"
    event_log += f"💰 P&L: **{pnl:+.2f}%**"
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=event_log, parse_mode=ParseMode.MARKDOWN)

async def check_active_trades(context: ContextTypes.DEFAULT_TYPE):
    if not ACTIVE_SIGNALS: return
    timeout = timedelta(hours=6)
    for sid, s in list(ACTIVE_SIGNALS.items()):
        if datetime.now() - s['time'] < timedelta(minutes=1): continue
        
        tp_touched = s.get("tp_touched", False)
        
        try:
            kline = session.get_kline(category="spot", symbol=s["symbol"], interval="1", limit=2)
            high_1m = None
            low_1m = None
            cur = None
            
            if kline and kline.get("retCode") == 0:
                candles = kline["result"]["list"]
                if len(candles) >= 1:
                    high_1m = float(candles[0]["high"])
                    low_1m = float(candles[0]["low"])
                    cur = float(candles[0]["close"])
                    
                    if not tp_touched and high_1m >= s["tp"]:
                        s["tp_touched"] = True
                        await context.bot.send_message(
                            chat_id=TELEGRAM_CHAT_ID,
                            text=f"🎯 **Цена была у цели!**\n{s['symbol']} коснулся **{s['tp']:.6f}** и отошёл.\nСейчас цена: **{cur:.6f}**\n💡 Закрывай сделку руками или держи дальше.",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    
                    if low_1m <= s["sl"]:
                        reason = "SL"
                        await close_signal(context, sid, s, cur, reason)
                        del ACTIVE_SIGNALS[sid]
                        continue
            
            if cur is None:
                resp = session.get_tickers(category="spot", symbol=s["symbol"])
                if resp.get("retCode") != 0: continue
                cur = float(resp["result"]["list"][0]["lastPrice"])
        except:
            continue
        
        is_sl = cur <= s["sl"] if s["signal"] == "BUY" else cur >= s["sl"]
        is_timeout = datetime.now() - s['time'] > timeout

        if is_sl or is_timeout:
            reason = "SL" if is_sl else "TIMEOUT"
            await close_signal(context, sid, s, cur, reason)
            del ACTIVE_SIGNALS[sid]
        elif not tp_touched:
            progress = (cur - s["price"]) / (s["tp"] - s["price"]) if s["tp"] != s["price"] else 0
            
            if progress >= 0.15 and not s.get("breakeven_done"):
                s["breakeven_done"] = True
                safe_sl = s['price'] - (s.get('atr', 0) * 0.2)
                safe_sl = max(safe_sl, s['sl'])
                s["sl"] = safe_sl
                s.setdefault("events", []).append({
                    "time": datetime.now().isoformat(),
                    "type": "breakeven",
                    "message": f"Стоп передвинут на {safe_sl:.4f}",
                    "price": safe_sl
                })
                await context.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=f"🛑 **Передвинь стоп-лосс на {safe_sl:.4f}**\n{s['symbol']} — теперь сделка без убытка."
                )
            
            if progress >= 0.5 and not s.get("partial_done"):
                s["partial_done"] = True
                s.setdefault("events", []).append({
                    "time": datetime.now().isoformat(),
                    "type": "partial",
                    "message": "Закрой половину, остаток держи",
                    "price": cur
                })
                await context.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=f"✂️ **Закрой половину позиции**\n{s['symbol']} — остаток держи. Стоп уже в безубытке."
                )

async def daily_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().date()
    
    history = {}
    try:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, 'r') as f:
                content = f.read().strip()
                if content: history = json.loads(content)
    except:
        try:
            with open(HISTORY_FILE, 'w') as f: json.dump({}, f)
        except: pass
        await update.message.reply_text("📆 История сделок пуста. (Старый файл был повреждён и заменён).")
        return

    if not history:
        await update.message.reply_text("📆 История сделок пуста. Жди авто-закрытий.")
        return

    day_trades = []
    for sid, s in history.items():
        if 'closed_time' not in s: continue
        try:
            ct = datetime.fromisoformat(s['closed_time']).date()
            if ct == today: day_trades.append(s)
        except: continue

    if not day_trades:
        active_count = len(ACTIVE_SIGNALS)
        msg = "📆 Сегодня сделок не было или они ещё не закрылись."
        if active_count > 0:
            msg += f"\n⏳ Активных сделок: {active_count} (ждут закрытия)."
        await update.message.reply_text(msg)
        return

    wins = sum(1 for t in day_trades if t.get('status') == 'tp')
    losses = sum(1 for t in day_trades if t.get('status') in ['sl', 'timeout'])
    total_pnl = sum(t.get('pnl', 0) for t in day_trades)
    total_be = sum(len(t.get('events', [])) for t in day_trades)
    
    best = max(day_trades, key=lambda x: x.get('pnl', -999))
    worst = min(day_trades, key=lambda x: x.get('pnl', 999))
    
    msg = f"📆 **ИТОГИ ДНЯ** ({today.strftime('%d.%m.%Y')})\n\n"
    msg += f"📊 Сделок: {len(day_trades)}\n✅ Побед: {wins}\n❌ Поражений: {losses}\n"
    if total_be > 0:
        msg += f"🔄 Советов выполнено: {total_be}\n"
    msg += f"💰 Чистый P&L: {total_pnl:+.2f}%\n\n"
    if best: msg += f"🏆 Лучшая: {best['symbol']} ({best.get('pnl', 0):+.2f}%)\n"
    if worst: msg += f"💀 Худшая: {worst['symbol']} ({worst.get('pnl', 0):+.2f}%)\n\n"
    msg += "💡 Совет: Продолжай вести дневник."
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def weekday_heatmap(context: ContextTypes.DEFAULT_TYPE):
    msk = pytz.timezone('Europe/Moscow'); now = datetime.now(msk)
    if now.weekday() != 6 or now.hour != 20: return
    if not HISTORY_FILE.exists(): return
    try:
        with open(HISTORY_FILE, 'r') as f: history = json.load(f)
    except: return
    day_stats = {0: [], 1: [], 2: [], 3: [], 4: [], 5: [], 6: []}
    for sid, s in history.items():
        if 'closed_time' not in s: continue
        try:
            ct = datetime.fromisoformat(s['closed_time'])
            wd = ct.weekday()
            day_stats[wd].append(s.get('pnl', 0))
        except: continue
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
            DAILY_STATS["signals_found"] += len(signals)
            for s in signals:
                sid = f"{s['symbol']}_{s['time'].strftime('%H%M%S')}"
                ACTIVE_SIGNALS[sid] = s
                try:
                    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=format_signal(s), parse_mode=ParseMode.MARKDOWN)
                    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⏳ Сделка взята на авто-сопровождение. Я буду держать тебя в курсе каждого важного шага.")
                except Exception as e:
                    await notify_error(context, f"Ошибка отправки сигнала: {e}")

async def emergency_check(context):
    global MARKET_CRASH_NOTIFIED, MARKET_PUMP_NOTIFIED, LIQUIDATIONS_WARNING_UNTIL
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
            
            liq_amount = get_liquidation_spike()
            if liq_amount:
                await context.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=f"💀 **Рынок трясёт!**\nЛиквидаций на ${liq_amount/1_000_000:.0f}M за 5 минут.\nВсе сигналы с пометкой ⚠️ ближайшие 15 минут."
                )
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
            pending = get_pending_predictions()
            top_sec = get_top_sectors()
            signals = []
            for sym in get_top_symbols(15):
                s = analyze_symbol(sym)
                if s:
                    signals.append(s)
                    if len(signals) >= 3: break
            report = f"📊 **АВТО-СВОДКА** ({now.strftime('%H:%M')})\n\n{market}\n\n{clusters if clusters else ''}"
            if top_sec:
                report += f"\n🔥 **Фавориты:** {', '.join(top_sec)}\n"
            if is_liquidation_warning_active():
                report += "\n⚠️ **Рынок трясёт — осторожно!**\n"
            if pending:
                report += "\n🧠 **ПРОГНОЗЫ НА ПРОВЕРКЕ:**\n"
                for p in pending: report += f"• {p['symbol']}: жду {p['direction']} до ${p['target']:.4f} (осталось {p['time_left']})\n"
            if signals:
                report += f"\n🟢 **ТОП-{len(signals)} СИГНАЛОВ:**\n"
                for s in signals: report += f"• {s['symbol']} ({s.get('sector', 'Other')}) | {s['strategy']} | Score {s['score']:.0f} | ${s['price']:.4f} → ${s['tp']:.4f}\n"
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
        today = datetime.now().date()
        day_trades = []
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, 'r') as f: history = json.load(f)
                for sid, s in history.items():
                    if 'closed_time' not in s: continue
                    ct = datetime.fromisoformat(s['closed_time']).date()
                    if ct == today: day_trades.append(s)
            except: pass
        
        total_pnl = sum(t.get('pnl', 0) for t in day_trades)
        wins = sum(1 for t in day_trades if t.get('status') == 'tp')
        losses = len(day_trades) - wins
        active_count = len(ACTIVE_SIGNALS)
        total_be = sum(len(t.get('events', [])) for t in day_trades)
        
        report = f"{get_phrase('evening')}\n\n🌙 **ИТОГИ ДНЯ**\n"
        report += f"📊 Сигналов найдено: {DAILY_STATS['signals_found']}\n"
        report += f"🧠 Прогнозов: {DAILY_STATS['predictions_made']} (сбылось {DAILY_STATS['predictions_success']})\n"
        report += f"📆 Сделок закрыто: {len(day_trades)} (✅{wins} | ❌{losses})\n"
        if total_be > 0:
            report += f"🔄 Советов выполнено: {total_be}\n"
        report += f"💰 P&L за день: {total_pnl:+.2f}%\n"
        if active_count > 0:
            report += f"⏳ Висящих сделок: {active_count} (ждут тайм-аута)"
        
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_USER_INTERACTION; LAST_USER_INTERACTION = datetime.now()
    mood_text = {"excited": "⚡ Я полон энергии!", "neutral": "🧘 Я в равновесии.", "cautious": "⚠️ Я насторожен.", "tired": "😴 Я немного устал."}.get(BOT_MOOD, "")
    await update.message.reply_text(f"🌙 **ДУХИ БЕЗДНЫ** v32.8\n{mood_text}\nСтрогость: {MIN_SCORE}\nТихий: {'🔇' if SILENT_MODE else '🔊'}", reply_markup=MAIN_KEYBOARD)

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
            pending = get_pending_predictions()
            top_sec = get_top_sectors()
            signals = []
            for sym in get_top_symbols(15):
                s = analyze_symbol(sym)
                if s:
                    signals.append(s)
                    if len(signals) >= 3: break
            report = f"📊 **СВОДКА**\n\n{market}\n\n{clusters if clusters else ''}"
            if top_sec:
                report += f"\n🔥 **Фавориты:** {', '.join(top_sec)}\n"
            if is_liquidation_warning_active():
                report += "\n⚠️ **Рынок трясёт — осторожно!**\n"
            if pending:
                report += "\n🧠 **ПРОГНОЗЫ НА ПРОВЕРКЕ:**\n"
                for p in pending: report += f"• {p['symbol']}: жду {p['direction']} до ${p['target']:.4f} (осталось {p['time_left']})\n"
            if signals:
                report += f"\n🟢 **ТОП-{len(signals)} СИГНАЛОВ:**\n"
                for s in signals: report += f"• {s['symbol']} ({s.get('sector', 'Other')}) | {s['strategy']} | Score {s['score']:.0f} | ${s['price']:.4f} → ${s['tp']:.4f}\n"
            else: report += f"\n{get_phrase('signal_fail')}"
            await msg.edit_text(report, parse_mode=ParseMode.MARKDOWN)
        elif text == "🔥 СИГНАЛЫ": await signal_search(update, context, fast=False)
        elif text == "⚡ СКАЛЬП": await signal_search(update, context, fast=True)
        elif text == "⚙️ ЕЩЁ": await update.message.reply_text("Выбери:", reply_markup=MORE_KEYBOARD)
        elif text == "📆 ИТОГИ ДНЯ": await daily_summary(update, context)
        elif text == "📰 НОВОСТИ":
            msg = await update.message.reply_text("📰 Ищу новости...")
            news = get_news()
            await msg.edit_text(news if news else "🌫️ Новостей нет.", parse_mode=ParseMode.MARKDOWN)
        elif text == "🧠 СТАТ ПРОГНОЗОВ": await update.message.reply_text(get_stats_message(), parse_mode=ParseMode.MARKDOWN)
        elif text == "🌫️ ИСТОРИЯ":
            try:
                history = {}
                if HISTORY_FILE.exists():
                    with open(HISTORY_FILE, 'r') as f:
                        content = f.read().strip()
                        if content: history = json.loads(content)
            except:
                await update.message.reply_text("🌫️ Файл истории повреждён. Он будет пересоздан.")
                try:
                    with open(HISTORY_FILE, 'w') as f: json.dump({}, f)
                except: pass
                return
            if not history:
                await update.message.reply_text("🌫️ История пуста.")
                return
            recent = list(history.items())[-5:]
            msg = "🌫️ **ИСТОРИЯ (авто)**\n\n"
            for sid, s in reversed(recent):
                emoji = "✅" if s.get('status') == 'tp' else ("⏰" if s.get('status') == 'timeout' else "❌")
                pnl_str = f"{s.get('pnl', 0):+.2f}%"
                events_count = len(s.get('events', []))
                events_str = f" (🔄{events_count})" if events_count > 0 else ""
                msg += f"{emoji} {s['symbol']} | {pnl_str}{events_str}\n"
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
        await update.message.reply_text("⏳ Сделка взята на авто-сопровождение. Я буду держать тебя в курсе каждого важного шага.")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MIN_SCORE
    q = update.callback_query; await q.answer(); d = q.data
    if d.startswith("score_"):
        MIN_SCORE = int(d.split("_")[1]); settings["MIN_SCORE"] = MIN_SCORE; save_settings(settings)
        await q.edit_message_text(f"✅ Строгость: {MIN_SCORE}")

def main():
    print("\n" + "="*60)
    print("🌙 TradeSight Pro WHISPER v32.8 (Автоматон-Болтун)")
    print("="*60)
    if sheet is not None:
        print(f"📊 Google Sheets: лист '{GOOGLE_SHEET_NAME}' готов")
    else:
        print(f"📊 CSV-режим: {TRADES_CSV_FILE}")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).concurrent_updates(True).job_queue(JobQueue()).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.job_queue.run_repeating(wake_up_message, interval=60, first=10)
    app.job_queue.run_repeating(coin_of_day, interval=60, first=20)
    app.job_queue.run_repeating(full_summary_loop, interval=300, first=30)
    app.job_queue.run_repeating(auto_scan_loop, interval=300, first=60)
    app.job_queue.run_repeating(emergency_check, interval=300, first=90)
    app.job_queue.run_repeating(check_active_trades, interval=60, first=120)
    app.job_queue.run_repeating(check_predictions, interval=900, first=180)
    app.job_queue.run_repeating(evening_ritual, interval=60, first=150)
    app.job_queue.run_repeating(idle_thoughts, interval=3600, first=600)
    app.job_queue.run_repeating(mirror_demon, interval=60, first=240)
    app.job_queue.run_repeating(weekday_heatmap, interval=3600, first=300)
    print("🌙 Автоматон-Болтун v32.8 запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
