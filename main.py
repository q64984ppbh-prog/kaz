import json, random, time, logging, threading, asyncio, secrets, sqlite3, requests, base64
from pathlib import Path
from typing import Dict
from dataclasses import dataclass, field
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = '8731702089:AAHOAcCPSsbQBeYDqdizzxNO4mS8_uHfd4Q'
WEBAPP_URL = 'https://creator-buys-salem-labs.trycloudflare.com'

ADMIN_WALLET = 'UQCv8Hmrha20qESNP5yihAw-C1DqiFcsJV9pygNJNyQVNwd7'
TONAPI_KEY = 'AHPGLWOMHJHTMWQAAAAH5W3N7B52U7HMLD3EZIQ2EUNTYXBNSETPNE434B7EEU7GL3DLMGI'
TONAPI_URL = 'https://tonapi.io/v2'
DB_PATH = '/root/kaz/casino.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS players (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        avatar_url TEXT,
        balance REAL DEFAULT 100.0,
        wallet_address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        type TEXT,
        amount REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount_game REAL,
        amount_ton REAL,
        comment TEXT,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def get_player(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id, username, first_name, last_name, avatar_url, balance, wallet_address, created_at FROM players WHERE user_id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {'user_id': row[0], 'username': row[1], 'first_name': row[2], 'last_name': row[3], 'avatar_url': row[4], 'balance': row[5], 'wallet_address': row[6], 'created_at': row[7]}
    return None

def update_player(user_id, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    fields = []
    values = []
    for key, value in kwargs.items():
        if value is not None:
            fields.append(f"{key}=?")
            values.append(value)
    if fields:
        values.append(user_id)
        c.execute(f"UPDATE players SET {', '.join(fields)} WHERE user_id=?", values)
    else:
        c.execute('INSERT OR IGNORE INTO players (user_id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()

@dataclass
class GameState:
    status: str = 'countdown'
    current_multiplier: float = 1.0
    crash_point: float = 1.0
    countdown: int = 10
    last_results: list = field(default_factory=list)
    bets: Dict[int, dict] = field(default_factory=dict)

game_state = GameState()

def generate_crash_point():
    r = random.random()
    if r < 0.05:
        return 1.00
    return round(1.0 + (1.0 / (1.0 - r)) * 0.8, 2)

app = Flask(__name__)
CORS(app)

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/terms')
def terms(): 
    return "<h1>Terms of Use</h1><p>TopGift Crash game terms.</p>"

@app.route('/privacy')
def privacy(): 
    return "<h1>Privacy Policy</h1><p>TopGift Crash privacy policy.</p>"

@app.route('/static/<path:path>')
def static_files(path): 
    return send_from_directory('static', path)

@app.route('/api/init', methods=['POST'])
def init():
    data = request.json
    uid = data.get('user_id')
    p = get_player(uid)
    update_player(uid, username=data.get('username',''), first_name=data.get('first_name',''), last_name=data.get('last_name',''), avatar_url=data.get('avatar_url',''))
    p = get_player(uid)
    return jsonify({
        'status':'ok',
        'balance':p['balance'],
        'wallet':p['wallet_address'],
        'game_state':{
            'status':game_state.status,
            'multiplier':game_state.current_multiplier,
            'countdown':game_state.countdown,
            'last_results':game_state.last_results,
            'crash_point':game_state.crash_point if game_state.status=='crashed' else None
        },
        'has_bet':uid in game_state.bets,
        'current_bet':game_state.bets.get(uid,{}).get('amount',0)
    })

@app.route('/api/save_wallet', methods=['POST'])
def save_wallet():
    data = request.json
    uid = data.get('user_id')
    addr = data.get('address','')
    if addr and len(addr)>10: 
        update_player(uid, wallet_address=addr)
    return jsonify({'status':'ok','wallet':addr})

@app.route('/api/place_bet', methods=['POST'])
def place_bet():
    data = request.json
    uid = data.get('user_id')
    amt = float(data.get('amount',0))
    auto_cashout = float(data.get('auto_cashout') or 0)
    p = get_player(uid)
    if game_state.status!='countdown': 
        return jsonify({'status':'error', 'message':'Game not in countdown'})
    if amt<=0 or amt>p['balance']: 
        return jsonify({'status':'error', 'message':'Invalid amount'})
    if uid in game_state.bets: 
        return jsonify({'status':'error', 'message':'Bet already placed'})
    update_player(uid, balance=p['balance']-amt)
    game_state.bets[uid] = {
        'amount':amt,
        'cashed_out':False,
        'multiplier':0,
        'auto_cashout':auto_cashout if auto_cashout >= 1.1 else 0
    }
    return jsonify({'status':'ok','balance':p['balance']-amt})

@app.route('/api/cashout', methods=['POST'])
def cashout():
    data = request.json
    uid = data.get('user_id')
    if uid not in game_state.bets: 
        return jsonify({'status':'error', 'message':'No bet found'})
    bet=game_state.bets[uid]
    if bet['cashed_out'] or game_state.status!='flying': 
        return jsonify({'status':'error', 'message':'Cannot cash out'})
    winnings=bet['amount']*game_state.current_multiplier
    p=get_player(uid)
    update_player(uid, balance=p['balance']+winnings)
    bet['cashed_out']=True
    bet['multiplier']=game_state.current_multiplier
    return jsonify({'status':'ok','balance':p['balance']+winnings,'multiplier':game_state.current_multiplier,'winnings':winnings})

@app.route('/api/create_deposit', methods=['POST'])
def create_deposit():
    data = request.json
    uid = data.get('user_id')
    amt = float(data.get('amount',0))
    p = get_player(uid)
    if not p['wallet_address']: 
        return jsonify({'status':'error','message':'Connect wallet first'})
    if amt<=0: 
        return jsonify({'status':'error','message':'Invalid amount'})
    ton_amt = round(amt, 4)
    comment = f'dep_{int(time.time())}_{uid}'
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO deposits (user_id, amount_game, amount_ton, comment) VALUES (?,?,?,?)', (uid, amt, ton_amt, comment))
    dep_id = c.lastrowid
    conn.commit()
    conn.close()
    payload = base64.b64encode(comment.encode()).decode()
    return jsonify({
        'status':'ok',
        'deposit_id':dep_id,
        'amount_ton':ton_amt,
        'admin_wallet':ADMIN_WALLET,
        'comment':comment,
        'payload':payload
    })

@app.route('/api/deposit_status', methods=['POST'])
def deposit_status():
    data = request.json
    dep_id = data.get('deposit_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT status FROM deposits WHERE id=?', (dep_id,))
    row = c.fetchone()
    conn.close()
    return jsonify({'status': row[0] if row else 'not_found'})

@app.route('/api/create_crypto_deposit', methods=['POST'])
def create_crypto_deposit():
    data = request.json
    coin = (data.get('coin') or 'TON').upper()
    amt = float(data.get('amount', 0))
    if coin not in ('TON', 'USDT') or amt <= 0:
        return jsonify({'status': 'error', 'message': 'Invalid amount or coin'})
    return jsonify({
        'status': 'ok', 
        'message': f'Счёт Crypto Bot на {amt:g} {coin} создан',
        'pay_url': f'https://t.me/CryptoBot?start={coin}_{int(amt*100)}'
    })

@app.route('/api/transactions', methods=['POST'])
def transactions():
    data = request.json
    uid = data.get('user_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT type, amount, created_at FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 30', (uid,))
    txs = [{'type': r[0], 'amount': r[1], 'date': r[2]} for r in c.fetchall()]
    c.execute('SELECT amount_game, status, created_at FROM deposits WHERE user_id=? ORDER BY created_at DESC LIMIT 30', (uid,))
    deps = [{'amount_game': r[0], 'status': r[1], 'date': r[2]} for r in c.fetchall()]
    conn.close()
    return jsonify({'transactions': txs, 'deposits': deps})

@app.route('/api/set_lang', methods=['POST'])
def set_lang(): 
    return jsonify({'status': 'ok'})

@app.route('/api/set_notify', methods=['POST'])
def set_notify(): 
    return jsonify({'status': 'ok'})

@app.route('/api/game_state')
def get_game_state():
    players_list=[]
    for uid,bet in game_state.bets.items():
        p=get_player(uid)
        if p:
            players_list.append({
                'name':f"{p['first_name']} {p['last_name']}".strip() or p['username'] or 'Player',
                'avatar':p['avatar_url'],
                'bet':bet['amount'],
                'cashed_out':bet['cashed_out'],
                'multiplier':bet['multiplier']
            })
    return jsonify({
        'status':game_state.status,
        'multiplier':game_state.current_multiplier,
        'countdown':game_state.countdown,
        'last_results':game_state.last_results,
        'crash_point':game_state.crash_point if game_state.status=='crashed' else None,
        'players':players_list
    })

def deposit_checker():
    while True:
        try:
            time.sleep(30)
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT id, user_id, amount_ton, comment FROM deposits WHERE status="pending"')
            deposits = c.fetchall()
            for dep in deposits:
                dep_id, user_id, amount_ton, comment = dep
                if time.time() > dep_id + 300:
                    c.execute('UPDATE deposits SET status="completed" WHERE id=?', (dep_id,))
                    p = get_player(user_id)
                    if p:
                        update_player(user_id, balance=p['balance'] + amount_ton)
                    conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Deposit checker error: {e}")

def run_game_loop():
    while True:
        game_state.status='countdown'
        game_state.bets.clear()
        for i in range(10,0,-1): 
            game_state.countdown=i
            game_state.status='countdown'
            time.sleep(1)
        game_state.crash_point=generate_crash_point()
        game_state.current_multiplier=1.0
        game_state.status='flying'
        st=time.time()
        while game_state.current_multiplier<game_state.crash_point:
            elapsed=time.time()-st
            acceleration=1+game_state.current_multiplier*0.3
            game_state.current_multiplier=round(1.0+elapsed*0.12*acceleration,2)
            for uid, bet in list(game_state.bets.items()):
                target = float(bet.get('auto_cashout') or 0)
                if target and not bet['cashed_out'] and game_state.current_multiplier >= target:
                    winnings = bet['amount'] * target
                    p = get_player(uid)
                    if p:
                        update_player(uid, balance=p['balance'] + winnings)
                        bet['cashed_out'] = True
                        bet['multiplier'] = target
            if game_state.current_multiplier>=game_state.crash_point:
                game_state.current_multiplier=game_state.crash_point
                break
            time.sleep(max(0.03,0.08-game_state.current_multiplier*0.003))
        game_state.status='crashed'
        game_state.last_results.insert(0,round(game_state.crash_point,2))
        if len(game_state.last_results)>8: 
            game_state.last_results=game_state.last_results[:8]
        time.sleep(3)

def run_flask(): 
    app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)

async def run_bot():
    bot=Bot(token=TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
    dp=Dispatcher()
    @dp.message(Command('start'))
    async def cmd_start(message: types.Message):
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Play TopGift", web_app=WebAppInfo(url=WEBAPP_URL))]
        ])
        await message.answer("🎰 Добро пожаловать в TopGift!\n\nЗарабатывайте реальные подарки Telegram прямо сейчас!\n💰 Играйте, выигрывайте и выводите TON!", reply_markup=kb)
    await dp.start_polling(bot)

if __name__=='__main__':
    init_db()
    threading.Thread(target=run_game_loop, daemon=True).start()
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=deposit_checker, daemon=True).start()
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
