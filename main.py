import json, random, time, logging, threading, asyncio, secrets, sqlite3, requests
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
DB_PATH = str(Path(__file__).with_name('casino.db'))

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
        amount_ton REAL, comment TEXT, status TEXT DEFAULT 'pending',
        tx_hash TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        confirmed_at TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
        amount REAL, tx_hash TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_db()

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
CORS(app)

TOKEN = '8731702089:AAHOAcCPSsbQBeYDqdizzxNO4mS8_uHfd4Q'
WEBAPP_URL = 'https://part-consideration-better-barrel.trycloudflare.com'

@dataclass
class GameState:
    status: str = 'countdown'; current_multiplier: float = 1.0; crash_point: float = 1.0
    countdown: int = 10; last_results: list = field(default_factory=list)
    bets: Dict[int, dict] = field(default_factory=dict)

game_state = GameState()

def get_player(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM players WHERE user_id=?', (user_id,))
    row = c.fetchone()
    if not row:
        c.execute('INSERT INTO players (user_id, balance) VALUES (?, 100.0)', (user_id,))
        conn.commit()
        conn.close()
        return {'user_id': user_id, 'balance': 100.0, 'wallet_address': None, 'username': '', 'first_name': '', 'last_name': '', 'avatar_url': ''}
    conn.close()
    return {'user_id': row[0], 'username': row[1] or '', 'first_name': row[2] or '', 'last_name': row[3] or '', 'avatar_url': row[4] or '', 'balance': row[5], 'wallet_address': row[6]}

def update_player(user_id, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    sets = ', '.join(f'{k}=?' for k in kwargs)
    c.execute(f'UPDATE players SET {sets} WHERE user_id=?', (*kwargs.values(), user_id))
    conn.commit()
    conn.close()

def check_tonapi_transactions():
    headers = {'Authorization': f'Bearer {TONAPI_KEY}'}
    try:
        resp = requests.get(f'{TONAPI_URL}/blockchain/accounts/{ADMIN_WALLET}/transactions?limit=20', headers=headers, timeout=10)
        if resp.status_code == 200:
            txs = resp.json().get('transactions', [])
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT id, comment, amount_ton, user_id FROM deposits WHERE status="pending"')
            pending = c.fetchall()
            for dep in pending:
                dep_id, comment, amount_ton, user_id = dep
                for tx in txs:
                    if tx.get('in_msg', {}).get('decoded_body', {}).get('text', '') == comment:
                        tx_value = int(tx.get('in_msg', {}).get('value', 0))
                        tx_ton = tx_value / 1e9
                        if abs(tx_ton - amount_ton) < 0.01:
                            tx_hash = tx.get('hash', '')
                            c.execute('SELECT id FROM transactions WHERE tx_hash=?', (tx_hash,))
                            if not c.fetchone():
                                c.execute('UPDATE deposits SET status="paid", tx_hash=?, confirmed_at=CURRENT_TIMESTAMP WHERE id=?', (tx_hash, dep_id))
                                p = get_player(user_id)
                                update_player(user_id, balance=p['balance'] + dep[2])
                                c.execute('INSERT INTO transactions (user_id, type, amount, tx_hash) VALUES (?,?,?,?)', (user_id, 'deposit', dep[2], tx_hash))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"TonAPI: {e}")

def deposit_checker():
    while True:
        check_tonapi_transactions()
        time.sleep(5)

def generate_crash_point():
    r = random.random()
    if r < 0.005: return 1.0
    if r < 0.03: return random.uniform(1.0, 1.3)
    if r < 0.08: return random.uniform(1.0, 2.0)
    if r < 0.18: return random.uniform(1.0, 3.0)
    if r < 0.35: return random.uniform(2.0, 8.0)
    if r < 0.6: return random.uniform(5.0, 20.0)
    if r < 0.8: return random.uniform(15.0, 80.0)
    if r < 0.93: return random.uniform(50.0, 300.0)
    return random.uniform(200.0, 1000.0)

@app.route('/')
def index():
    resp = send_from_directory('templates', 'index.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

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
    data = request.json; uid = data.get('user_id'); amt = float(data.get('amount',0))
    p = get_player(uid)
    if game_state.status!='countdown': return jsonify({'status':'error'})
    if amt<=0 or amt>p['balance']: return jsonify({'status':'error'})
    if uid in game_state.bets: return jsonify({'status':'error'})
    update_player(uid, balance=p['balance']-amt)
    game_state.bets[uid]={'amount':amt,'cashed_out':False,'multiplier':0}
    return jsonify({'status':'ok','balance':p['balance']-amt})

@app.route('/api/cashout', methods=['POST'])
def cashout():
    data = request.json; uid = data.get('user_id')
    if uid not in game_state.bets: return jsonify({'status':'error'})
    bet=game_state.bets[uid]
    if bet['cashed_out'] or game_state.status!='flying': return jsonify({'status':'error'})
    winnings=bet['amount']*game_state.current_multiplier
    p=get_player(uid); update_player(uid, balance=p['balance']+winnings)
    bet['cashed_out']=True; bet['multiplier']=game_state.current_multiplier
    return jsonify({'status':'ok','balance':p['balance']+winnings,'multiplier':game_state.current_multiplier,'winnings':winnings})

@app.route('/api/create_deposit', methods=['POST'])
def create_deposit():
    data = request.json; uid = data.get('user_id'); amt = float(data.get('amount',0))
    p = get_player(uid)
    if not p['wallet_address']: return jsonify({'status':'error','message':'Connect wallet'})
    if amt<=0: return jsonify({'status':'error'})
    ton_amt = round(amt*0.1, 4)
    comment = f'dep_{int(time.time())}_{uid}'
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO deposits (user_id, amount_game, amount_ton, comment) VALUES (?,?,?,?)', (uid, amt, ton_amt, comment))
    dep_id = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'status':'ok','deposit_id':dep_id,'amount_ton':ton_amt,'admin_wallet':ADMIN_WALLET,'comment':comment})

@app.route('/api/deposit_status', methods=['POST'])
def deposit_status():
    data = request.json; dep_id = data.get('deposit_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT status FROM deposits WHERE id=?', (dep_id,))
    row = c.fetchone()
    conn.close()
    return jsonify({'status': row[0] if row else 'not_found'})

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
            acceleration=1+game_state.current_multiplier*0.3
            game_state.current_multiplier=round(1.0+elapsed*0.12*acceleration,2)
            if game_state.current_multiplier>=game_state.crash_point:
                game_state.current_multiplier=game_state.crash_point; break
            time.sleep(max(0.03,0.08-game_state.current_multiplier*0.003))
        game_state.status='crashed'
        game_state.last_results.insert(0,round(game_state.crash_point,2))
        if len(game_state.last_results)>8: game_state.last_results=game_state.last_results[:8]
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
    threading.Thread(target=run_game_loop, daemon=True).start()
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=deposit_checker, daemon=True).start()
    asyncio.run(run_bot())
