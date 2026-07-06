import json, random, time, logging, threading, asyncio, secrets, sqlite3, requests, base64, hmac, hashlib
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

ADMIN_WALLET = 'UQCv8Hmrha20qESNP5yihAw-C1DqiFcsJV9pygNJNyQVNwd7'
TONAPI_KEY = 'AHPGLWOMHJHTMWQAAAAH5W3N7B52U7HMLD3EZIQ2EUNTYXBNSETPNE434B7EEU7GL3DLMGI'
TONAPI_URL = 'https://tonapi.io/v2'
DB_PATH = '/root/kaz/casino.db'
CRYPTOBOT_TOKEN = '605286:AA7rTt4SfHrggZmhJJUwT4hGrL8zSeDk2qw'
CRYPTOBOT_API = 'https://pay.crypt.bot/api'

TOKEN = '8731702089:AAHOAcCPSsbQBeYDqdizzxNO4mS8_uHfd4Q'
WEBAPP_URL = 'https://creator-buys-salem-labs.trycloudflare.com'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS players (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
        avatar_url TEXT, balance REAL DEFAULT 100.0, wallet_address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount_game REAL,
        amount_ton REAL, invoice_id TEXT, status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
        amount REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def get_player(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM players WHERE user_id=?', (uid,))
    row = c.fetchone()
    conn.close()
    if row:
        cols = ['user_id','username','first_name','last_name','avatar_url','balance','wallet_address','created_at']
        return dict(zip(cols, row))
    return None

def update_player(uid, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM players WHERE user_id=?', (uid,))
    if c.fetchone():
        set_clause = ','.join([f'{k}=?' for k in kwargs.keys()])
        c.execute(f'UPDATE players SET {set_clause} WHERE user_id=?', list(kwargs.values()) + [uid])
    else:
        cols = ['user_id'] + list(kwargs.keys())
        vals = [uid] + list(kwargs.values())
        placeholders = ','.join(['?' for _ in cols])
        c.execute(f'INSERT INTO players ({",".join(cols)}) VALUES ({placeholders})', vals)
    conn.commit()
    conn.close()

def generate_crash_point():
    r = random.random()
    if r < 0.25: return round(random.uniform(1.0, 1.5), 2)
    elif r < 0.50: return round(random.uniform(1.5, 2.5), 2)
    elif r < 0.75: return round(random.uniform(2.5, 5.0), 2)
    else: return round(random.uniform(5.0, 15.0), 2)

@dataclass
class GameState:
    status: str = 'countdown'
    current_multiplier: float = 1.0
    countdown: int = 10
    crash_point: float = 0.0
    last_results: list = field(default_factory=list)
    bets: dict = field(default_factory=dict)

game_state = GameState()

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/terms')
def terms(): return "<h1>Terms of Use</h1><p>TopGift Crash game terms.</p>"

@app.route('/privacy')
def privacy(): return "<h1>Privacy Policy</h1><p>TopGift Crash privacy policy.</p>"

@app.route('/static/<path:path>')
def static_files(path): return send_from_directory('static', path)

@app.route('/api/init', methods=['POST'])
def init():
    data = request.json; uid = data.get('user_id')
    p = get_player(uid)
    update_player(uid, username=data.get('username',''), first_name=data.get('first_name',''), last_name=data.get('last_name',''), avatar_url=data.get('avatar_url',''))
    p = get_player(uid)
    return jsonify({'status':'ok','balance':p['balance'],'wallet':p['wallet_address'],
        'game_state':{'status':game_state.status,'multiplier':game_state.current_multiplier,'countdown':game_state.countdown,'last_results':game_state.last_results,'crash_point':game_state.crash_point if game_state.status=='crashed' else None},
        'has_bet':uid in game_state.bets,'current_bet':game_state.bets.get(uid,{}).get('amount',0)})

@app.route('/api/save_wallet', methods=['POST'])
def save_wallet():
    data = request.json; uid = data.get('user_id'); addr = data.get('address','')
    if addr and len(addr)>10: update_player(uid, wallet_address=addr)
    return jsonify({'status':'ok','wallet':addr})

@app.route('/api/place_bet', methods=['POST'])
def place_bet():
    data = request.json; uid = data.get('user_id'); amt = float(data.get('amount',0)); auto_cashout = float(data.get('auto_cashout') or 0)
    p = get_player(uid)
    if game_state.status!='countdown': return jsonify({'status':'error','message':'Game not in countdown'})
    if amt<=0 or amt>p['balance']: return jsonify({'status':'error','message':'Invalid amount'})
    if uid in game_state.bets: return jsonify({'status':'error','message':'Already bet'})
    update_player(uid, balance=p['balance']-amt)
    game_state.bets[uid]={'amount':amt,'cashed_out':False,'multiplier':0,'auto_cashout':auto_cashout if auto_cashout >= 1.1 else 0}
    return jsonify({'status':'ok','balance':p['balance']-amt})

@app.route('/api/cashout', methods=['POST'])
def cashout():
    data = request.json; uid = data.get('user_id')
    if uid not in game_state.bets: return jsonify({'status':'error','message':'No bet'})
    bet=game_state.bets[uid]
    if bet['cashed_out'] or game_state.status!='flying': return jsonify({'status':'error','message':'Cannot cashout'})
    winnings=bet['amount']*game_state.current_multiplier
    p=get_player(uid); update_player(uid, balance=p['balance']+winnings)
    bet['cashed_out']=True; bet['multiplier']=game_state.current_multiplier
    return jsonify({'status':'ok','balance':p['balance']+winnings,'multiplier':game_state.current_multiplier,'winnings':winnings})

@app.route('/api/create_deposit', methods=['POST'])
def create_deposit():
    data = request.json; uid = data.get('user_id'); amt = float(data.get('amount',0))
    p = get_player(uid)
    if not p['wallet_address']: return jsonify({'status':'error','message':'Connect wallet'})
    if amt<=0: return jsonify({'status':'error','message':'Invalid amount'})
    ton_amt = round(amt, 4)
    headers = {'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN}
    payload = {'amount': ton_amt, 'currency_type': 'crypto', 'asset': 'TON', 'description': f'Deposit {uid}'}
    try:
        r = requests.post(f'{CRYPTOBOT_API}/createInvoice', headers=headers, json=payload, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get('ok'):
                invoice = data['result']
                invoice_id = invoice['invoice_id']
                pay_url = invoice['pay_url']
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('INSERT INTO deposits (user_id, amount_game, amount_ton, invoice_id) VALUES (?,?,?,?)', (uid, amt, ton_amt, str(invoice_id)))
                dep_id = c.lastrowid
                conn.commit()
                conn.close()
                return jsonify({'status':'ok','deposit_id':dep_id,'pay_url':pay_url,'invoice_id':invoice_id})
    except Exception as e:
        logger.error(f'CryptoBot error: {e}')
    return jsonify({'status':'error','message':'CryptoBot error'})

@app.route('/api/deposit_status', methods=['POST'])
def deposit_status():
    data = request.json; dep_id = data.get('deposit_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT invoice_id, status FROM deposits WHERE id=?', (dep_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({'status':'not_found'})
    invoice_id, status = row
    if status == 'paid':
        return jsonify({'status':'paid'})
    headers = {'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN}
    try:
        r = requests.get(f'{CRYPTOBOT_API}/getInvoices?invoice_id={invoice_id}', headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get('ok') and data['result']['items']:
                inv = data['result']['items'][0]
                if inv['status'] == 'paid':
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute('UPDATE deposits SET status=? WHERE id=?', ('paid', dep_id))
                    c.execute('INSERT INTO transactions (user_id, type, amount) VALUES (?,?,?)', (data.get('user_id'), 'deposit', inv['amount']))
                    conn.commit()
                    conn.close()
                    return jsonify({'status':'paid'})
    except Exception as e:
        logger.error(f'Status check error: {e}')
    return jsonify({'status':'pending'})

@app.route('/api/create_crypto_deposit', methods=['POST'])
def create_crypto_deposit():
    data = request.json; uid = data.get('user_id'); amt = float(data.get('amount',0))
    coin = (data.get('coin') or 'TON').upper()
    if coin not in ('TON', 'USDT') or amt <= 0:
        return jsonify({'status':'error','message':'Invalid'})
    headers = {'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN}
    payload = {'amount': amt, 'currency_type': 'crypto', 'asset': coin, 'description': f'Deposit {uid}'}
    try:
        r = requests.post(f'{CRYPTOBOT_API}/createInvoice', headers=headers, json=payload, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get('ok'):
                return jsonify({'status':'ok','pay_url':data['result']['pay_url']})
    except: pass
    return jsonify({'status':'error','message':'CryptoBot error'})

@app.route('/api/transactions', methods=['POST'])
def transactions():
    data = request.json; uid = data.get('user_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT type, amount, created_at FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 30', (uid,))
    txs = [{'type': r[0], 'amount': r[1], 'date': r[2]} for r in c.fetchall()]
    c.execute('SELECT amount_game, status, created_at FROM deposits WHERE user_id=? ORDER BY created_at DESC LIMIT 30', (uid,))
    deps = [{'amount_game': r[0], 'status': r[1], 'date': r[2]} for r in c.fetchall()]
    conn.close()
    return jsonify({'transactions': txs, 'deposits': deps})

@app.route('/api/set_lang', methods=['POST'])
def set_lang(): return jsonify({'status': 'ok'})

@app.route('/api/set_notify', methods=['POST'])
def set_notify(): return jsonify({'status': 'ok'})

@app.route('/api/game_state')
def get_game_state():
    players_list=[]
    for uid,bet in game_state.bets.items():
        p=get_player(uid)
        players_list.append({'name':f"{p['first_name']} {p['last_name']}".strip() or p['username'] or 'Player','avatar':p['avatar_url'],'bet':bet['amount'],'cashed_out':bet['cashed_out'],'multiplier':bet['multiplier']})
    return jsonify({'status':game_state.status,'multiplier':game_state.current_multiplier,'countdown':game_state.countdown,'last_results':game_state.last_results,'crash_point':game_state.crash_point if game_state.status=='crashed' else None,'players':players_list})

def run_game_loop():
    while True:
        game_state.status='countdown'; game_state.bets.clear()
        for i in range(10,0,-1): game_state.countdown=i; game_state.status='countdown'; time.sleep(1)
        game_state.crash_point=generate_crash_point(); game_state.current_multiplier=1.0; game_state.status='flying'
        st=time.time()
        while game_state.current_multiplier<game_state.crash_point:
            elapsed=time.time()-st
            acceleration=1+game_state.current_multiplier*0.4
            game_state.current_multiplier=round(1.0+elapsed*0.15*acceleration,2)
            for uid, bet in list(game_state.bets.items()):
                target = float(bet.get('auto_cashout') or 0)
                if target and not bet['cashed_out'] and game_state.current_multiplier >= target:
                    winnings = bet['amount'] * target
                    p = get_player(uid)
                    update_player(uid, balance=p['balance'] + winnings)
                    bet['cashed_out'] = True
                    bet['multiplier'] = target
            if game_state.current_multiplier>=game_state.crash_point:
                game_state.current_multiplier=game_state.crash_point; break
            time.sleep(max(0.02,0.06-game_state.current_multiplier*0.002))
        game_state.status='crashed'
        game_state.last_results.insert(0,round(game_state.crash_point,2))
        if len(game_state.last_results)>8: game_state.last_results=game_state.last_results[:8]
        time.sleep(3)

def deposit_checker():
    while True:
        time.sleep(3)

def run_flask(): app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)

async def run_bot():
    bot=Bot(token=TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
    dp=Dispatcher()
    @dp.message(Command('start'))
    async def cmd_start(message: types.Message):
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Play TopGift (RU)", web_app=WebAppInfo(url=WEBAPP_URL))]])
        await message.answer("Welcome to TopGift! Start winning real Telegram Gifts right now!", reply_markup=kb)
    await dp.start_polling(bot)

if __name__=='__main__':
    init_db()
    threading.Thread(target=run_game_loop, daemon=True).start()
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=deposit_checker, daemon=True).start()
    asyncio.run(run_bot())
