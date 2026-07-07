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
ADMIN_USER_ID = 8374183799
TONAPI_KEY = 'AHPGLWOMHJHTMWQAAAAH5W3N7B52U7HMLD3EZIQ2EUNTYXBNSETPNE434B7EEU7GL3DLMGI'
TONAPI_URL = 'https://tonapi.io/v2'
DB_PATH = str(Path(__file__).with_name('casino.db'))
CRYPTOBOT_TOKEN = '605286:AA7rTt4SfHrggZmhJJUwT4hGrL8zSeDk2qw'
CRYPTOBOT_API = 'https://pay.crypt.bot/api'

TOKEN = '8731702089:AAHOAcCPSsbQBeYDqdizzxNO4mS8_uHfd4Q'
WEBAPP_URL = 'https://creator-buys-salem-labs.trycloudflare.com'
BOT_USERNAME = 'TopGiftCrashBot'
REF_REWARD_RATE = 0.10
COINGECKO_TON_URL = 'https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd'
ton_usd_rate = {'value': 3.0, 'updated_at': 0}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS players (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
        avatar_url TEXT, balance REAL DEFAULT 100.0, stars_balance REAL DEFAULT 0, wallet_address TEXT,
        ref_code TEXT UNIQUE, referrer_id INTEGER, ref_balance REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    for sql in (
        'ALTER TABLE players ADD COLUMN ref_code TEXT',
        'ALTER TABLE players ADD COLUMN referrer_id INTEGER',
        'ALTER TABLE players ADD COLUMN ref_balance REAL DEFAULT 0',
        'ALTER TABLE players ADD COLUMN stars_balance REAL DEFAULT 0',
        'ALTER TABLE players ADD COLUMN is_banned INTEGER DEFAULT 0',
    ):
        try: c.execute(sql)
        except sqlite3.OperationalError: pass
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        referrer_id INTEGER, referred_id INTEGER PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount_game REAL,
        amount_ton REAL, invoice_id TEXT, pay_url TEXT, status TEXT DEFAULT 'pending', method TEXT DEFAULT 'cryptobot', asset TEXT DEFAULT 'TON',
        telegram_payment_charge_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    for sql in (
        'ALTER TABLE deposits ADD COLUMN pay_url TEXT',
        "ALTER TABLE deposits ADD COLUMN method TEXT DEFAULT 'cryptobot'",
        "ALTER TABLE deposits ADD COLUMN asset TEXT DEFAULT 'TON'",
        'ALTER TABLE deposits ADD COLUMN telegram_payment_charge_id TEXT',
    ):
        try: c.execute(sql)
        except sqlite3.OperationalError: pass
    c.execute('''CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, transfer_id TEXT,
        status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
        amount REAL, currency TEXT DEFAULT 'TON', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    for sql in (
        "ALTER TABLE transactions ADD COLUMN currency TEXT DEFAULT 'TON'",
    ):
        try: c.execute(sql)
        except sqlite3.OperationalError: pass
    conn.commit()
    conn.close()

def get_player(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM players WHERE user_id=?', (uid,))
    row = c.fetchone()
    cols = [desc[0] for desc in c.description]
    conn.close()
    if row:
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

def ref_code_for(uid):
    digest = hashlib.blake2s(str(uid).encode(), digest_size=5).hexdigest().upper()
    return f'TG{digest}'

def public_player(p):
    name = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or (('@' + p['username']) if p.get('username') else f"Игрок {str(p.get('user_id'))[-4:]}")
    return name[:24]

def ensure_referral(uid, ref_code=None):
    p = get_player(uid)
    if not p:
        return
    changes = {}
    if not p.get('ref_code'):
        changes['ref_code'] = ref_code_for(uid)
    if ref_code and not p.get('referrer_id'):
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute('SELECT user_id FROM players WHERE ref_code=?', (ref_code,))
        row = c.fetchone()
        if row and row[0] != uid:
            c.execute('INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?,?)', (row[0], uid))
            if c.rowcount:
                changes['referrer_id'] = row[0]
        conn.commit(); conn.close()
    if changes:
        update_player(uid, **changes)

def credit_deposit(dep_id, user_id, amount):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('SELECT COALESCE(asset,"TON") FROM deposits WHERE id=?', (dep_id,))
    asset_row = c.fetchone()
    currency = (asset_row[0] if asset_row else 'TON').upper()
    balance_col = 'stars_balance' if currency == 'STARS' else 'balance'
    c.execute('UPDATE deposits SET status=? WHERE id=? AND status!=?', ('paid', dep_id, 'paid'))
    changed = c.rowcount
    if changed:
        c.execute(f'UPDATE players SET {balance_col}={balance_col}+? WHERE user_id=?', (amount, user_id))
        c.execute('INSERT INTO transactions (user_id, type, amount, currency) VALUES (?,?,?,?)', (user_id, 'deposit', amount, currency))
        c.execute('SELECT referrer_id FROM players WHERE user_id=?', (user_id,))
        row = c.fetchone()
        if row and row[0]:
            reward = round(amount * REF_REWARD_RATE, 6)
            c.execute(f'UPDATE players SET {balance_col}={balance_col}+?, ref_balance=COALESCE(ref_balance,0)+? WHERE user_id=?', (reward, reward, row[0]))
            c.execute('INSERT INTO transactions (user_id, type, amount, currency) VALUES (?,?,?,?)', (row[0], 'referral_bonus', reward, currency))
    conn.commit(); conn.close()
    return bool(changed)

def get_ton_usd_rate():
    if time.time() - ton_usd_rate.get('updated_at', 0) < 300 and ton_usd_rate.get('value'):
        return float(ton_usd_rate['value'])
    try:
        r = requests.get(COINGECKO_TON_URL, timeout=8)
        js = r.json()
        price = float(js['the-open-network']['usd'])
        if price > 0:
            ton_usd_rate.update(value=price, updated_at=time.time())
    except Exception as e:
        logger.warning(f'CoinGecko TON rate error: {e}')
    return float(ton_usd_rate.get('value') or 3.0)

def convert_crypto_to_ton(asset, amount):
    asset = (asset or 'TON').upper()
    amount = float(amount or 0)
    if asset == 'USDT':
        return round(amount / get_ton_usd_rate(), 6)
    return round(amount, 6)

def notify_user(user_id, text, reply_markup=None):
    try:
        payload = {'chat_id': user_id, 'text': text, 'parse_mode': 'HTML'}
        if reply_markup:
            payload['reply_markup'] = json.dumps(reply_markup)
        requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage', json=payload, timeout=8)
    except Exception as e:
        logger.error(f'Telegram notify error: {e}')

def coingecko_rate_loop():
    while True:
        get_ton_usd_rate()
        time.sleep(300)

def generate_crash_point():
    r = random.random()
    if r < 0.42: return round(random.uniform(1.0, 1.45), 2)
    elif r < 0.74: return round(random.uniform(1.45, 2.4), 2)
    elif r < 0.94: return round(random.uniform(2.4, 5.0), 2)
    elif r < 0.992: return round(random.uniform(5.0, 12.0), 2)
    return round(random.uniform(12.0, 25.0), 2)

@dataclass
class GameState:
    status: str = 'countdown'
    current_multiplier: float = 1.0
    countdown: int = 10
    crash_point: float = 0.0
    last_results: list = field(default_factory=list)
    bets: dict = field(default_factory=dict)
    pending_bets: dict = field(default_factory=dict)

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
    update_player(uid, username=data.get('username',''), first_name=data.get('first_name',''), last_name=data.get('last_name',''), avatar_url=data.get('avatar_url',''))
    ensure_referral(uid, data.get('ref_code'))
    p = get_player(uid)
    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id=?',(uid,)); ref_count=c.fetchone()[0]; conn.close()
    return jsonify({'status':'ok','balance':p['balance'],'wallet':p['wallet_address'],'first_name':p['first_name'],'last_name':p['last_name'],'ref_code':p['ref_code'],'ref_count':ref_count,'ref_balance':p.get('ref_balance') or 0,
        'stars_balance':p.get('stars_balance') or 0,'is_admin':uid==ADMIN_USER_ID,'is_banned':bool(p.get('is_banned') or 0),
        'game_state':{'status':game_state.status,'multiplier':game_state.current_multiplier,'countdown':game_state.countdown,'last_results':game_state.last_results,'crash_point':game_state.crash_point if game_state.status=='crashed' else None},
        'has_bet':uid in game_state.bets or uid in game_state.pending_bets,'current_bet':(game_state.bets.get(uid) or game_state.pending_bets.get(uid) or {}).get('amount',0)})

@app.route('/api/save_wallet', methods=['POST'])
def save_wallet():
    data = request.json; uid = data.get('user_id'); addr = data.get('address','')
    if addr and len(addr)>10: update_player(uid, wallet_address=addr)
    return jsonify({'status':'ok','wallet':addr})

@app.route('/api/place_bet', methods=['POST'])
def place_bet():
    data = request.json; uid = data.get('user_id'); amt = float(data.get('amount',0)); auto_cashout = float(data.get('auto_cashout') or 0)
    currency = (data.get('currency') or 'TON').upper()
    if currency not in ('TON', 'STARS'): return jsonify({'status':'error','message':'Invalid currency'})
    p = get_player(uid)
    if not p or p.get('is_banned'): return jsonify({'status':'error','message':'Пользователь заблокирован'})
    balance_key = 'stars_balance' if currency == 'STARS' else 'balance'
    if game_state.status != 'countdown': return jsonify({'status':'error','message':'Ставки принимаются только во время отсчета'})
    if amt<=0 or amt>float(p.get(balance_key) or 0): return jsonify({'status':'error','message':'Недостаточно средств'})
    if uid in game_state.bets or uid in game_state.pending_bets: return jsonify({'status':'error','message':'Already bet'})
    update_player(uid, **{balance_key: float(p.get(balance_key) or 0)-amt})
    game_state.bets[uid]={'amount':amt,'currency':currency,'cashed_out':False,'multiplier':0,'auto_cashout':auto_cashout if auto_cashout >= 1.1 else 0}
    return jsonify({'status':'ok','balance':p['balance'] if currency == 'STARS' else p['balance']-amt,'stars_balance':(float(p.get('stars_balance') or 0)-amt) if currency == 'STARS' else float(p.get('stars_balance') or 0)})

@app.route('/api/cashout', methods=['POST'])
def cashout():
    data = request.json; uid = data.get('user_id')
    if uid not in game_state.bets: return jsonify({'status':'error','message':'No bet'})
    bet=game_state.bets[uid]
    if bet['cashed_out'] or game_state.status!='flying': return jsonify({'status':'error','message':'Cannot cashout'})
    winnings=bet['amount']*game_state.current_multiplier
    p=get_player(uid); balance_key = 'stars_balance' if bet.get('currency') == 'STARS' else 'balance'; update_player(uid, **{balance_key: float(p.get(balance_key) or 0)+winnings})
    bet['cashed_out']=True; bet['multiplier']=game_state.current_multiplier
    fresh=get_player(uid); return jsonify({'status':'ok','balance':fresh.get('balance') or 0,'stars_balance':fresh.get('stars_balance') or 0,'multiplier':game_state.current_multiplier,'winnings':winnings,'currency':bet.get('currency','TON')})

@app.route('/api/create_stars_invoice', methods=['POST'])
def create_stars_invoice():
    data = request.json or {}; uid = int(data.get('user_id') or 0); amount = int(data.get('amount') or 0)
    if amount < 1: return jsonify({'status':'error','message':'Минимум 1 звезда'})
    p = get_player(uid)
    if not p or p.get('is_banned'): return jsonify({'status':'error','message':'Пользователь заблокирован'}), 403
    payload = json.dumps({'user_id': uid, 'amount': amount, 'nonce': secrets.token_hex(8)})
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('INSERT INTO deposits (user_id, amount_game, amount_ton, invoice_id, method, asset) VALUES (?,?,?,?,?,?)', (uid, amount, amount, payload, 'telegram_stars', 'STARS'))
    dep_id = c.lastrowid; conn.commit(); conn.close()
    body = {
        'title': 'Пополнение TopGift',
        'description': f'Пополнение баланса на {amount} Telegram Stars',
        'payload': payload,
        'provider_token': '',
        'currency': 'XTR',
        'prices': [{'label': 'Telegram Stars', 'amount': amount}]
    }
    try:
        r = requests.post(f'https://api.telegram.org/bot{TOKEN}/createInvoiceLink', json=body, timeout=10)
        js = r.json()
        if r.status_code == 200 and js.get('ok'):
            c = sqlite3.connect(DB_PATH).cursor()
            conn = c.connection
            c.execute('UPDATE deposits SET pay_url=? WHERE id=?', (js['result'], dep_id)); conn.commit(); conn.close()
            return jsonify({'status':'ok','invoiceLink':js['result'],'deposit_id':dep_id})
        logger.error(f'Stars invoice error: {js}')
    except Exception as e:
        logger.error(f'Stars invoice exception: {e}')
    return jsonify({'status':'error','message':'Не удалось создать счёт Stars'})

@app.route('/api/create_deposit', methods=['POST'])
def create_deposit():
    data = request.json; uid = data.get('user_id'); amt = float(data.get('amount',0))
    if amt < 1: return jsonify({'status':'error','message':'Минимум 1 звезда'})
    comment = f"topgift:{uid}:{secrets.token_hex(5)}"
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('INSERT INTO deposits (user_id, amount_game, amount_ton, invoice_id, method, asset) VALUES (?,?,?,?,?,?)', (uid, amt, amt, comment, 'stars', 'STARS'))
    dep_id = c.lastrowid; conn.commit(); conn.close()
    payload = base64.b64encode(comment.encode()).decode()
    return jsonify({'status':'ok','deposit_id':dep_id,'admin_wallet':ADMIN_WALLET,'amount_ton':amt,'payload':payload})

@app.route('/api/create_crypto_deposit', methods=['POST'])
def create_crypto_deposit():
    data = request.json; uid = data.get('user_id'); amt = float(data.get('amount',0))
    coin = (data.get('coin') or 'TON').upper()
    if coin not in ('TON', 'USDT') or amt <= 0:
        return jsonify({'status':'error','message':'Invalid'})
    headers = {'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN}
    payload = {'amount': str(amt), 'currency_type': 'crypto', 'asset': coin, 'description': f'TopGift deposit {uid}', 'payload': f'{uid}:{secrets.token_hex(6)}'}
    try:
        r = requests.post(f'{CRYPTOBOT_API}/createInvoice', headers=headers, json=payload, timeout=10)
        js = r.json()
        if r.status_code == 200 and js.get('ok'):
            inv=js['result']; pay_url=inv.get('mini_app_invoice_url') or inv.get('web_app_invoice_url') or inv.get('bot_invoice_url') or inv.get('pay_url')
            conn=sqlite3.connect(DB_PATH); c=conn.cursor()
            c.execute('INSERT INTO deposits (user_id, amount_game, amount_ton, invoice_id, pay_url, method, asset) VALUES (?,?,?,?,?,?,?)', (uid, amt, convert_crypto_to_ton(coin, amt), str(inv['invoice_id']), pay_url, 'cryptobot', coin))
            dep_id=c.lastrowid; conn.commit(); conn.close()
            return jsonify({'status':'ok','deposit_id':dep_id,'pay_url':pay_url,'invoice_id':inv['invoice_id']})
    except Exception as e:
        logger.error(f'CryptoBot error: {e}')
    return jsonify({'status':'error','message':'CryptoBot error'})

@app.route('/api/deposit_status', methods=['POST'], endpoint='deposit_status_api')
def check_deposit_status():
    req = request.json; dep_id = req.get('deposit_id')
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT user_id, amount_game, amount_ton, invoice_id, status, method, COALESCE(asset,'TON') FROM deposits WHERE id=?", (dep_id,))
    row = c.fetchone(); conn.close()
    if not row: return jsonify({'status':'not_found'})
    user_id, amount_game, amount_ton, invoice_id, status, method, asset = row
    if status == 'paid': return jsonify({'status':'paid'})
    if method == 'telegram_stars':
        return jsonify({'status':'pending'})
    if method == 'cryptobot':
        headers = {'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN}
        try:
            r = requests.get(f'{CRYPTOBOT_API}/getInvoices', headers=headers, params={'invoice_ids': invoice_id}, timeout=10)
            js = r.json()
            items = js.get('result', {}).get('items', []) if js.get('ok') else []
            if items and items[0].get('status') == 'paid':
                credit_deposit(dep_id, user_id, convert_crypto_to_ton(items[0].get('asset') or asset, items[0].get('amount') or amount_game))
                return jsonify({'status':'paid'})
        except Exception as e: logger.error(f'Status check error: {e}')
    else:
        try:
            r=requests.get(f'{TONAPI_URL}/blockchain/accounts/{ADMIN_WALLET}/transactions', headers={'Authorization': f'Bearer {TONAPI_KEY}'}, params={'limit':20}, timeout=10)
            if r.status_code == 200 and invoice_id in r.text:
                credit_deposit(dep_id, user_id, amount_game)
                return jsonify({'status':'paid'})
        except Exception as e: logger.error(f'TON status error: {e}')
    return jsonify({'status':'pending'})

@app.route('/api/referral', methods=['POST'])
def referral():
    uid=(request.json or {}).get('user_id')
    p=get_player(uid); ensure_referral(uid); p=get_player(uid)
    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id=?',(uid,)); count=c.fetchone()[0]; conn.close()
    return jsonify({'status':'ok','ref_code':p['ref_code'],'ref_count':count,'ref_balance':p.get('ref_balance') or 0,'link':f'https://t.me/{BOT_USERNAME}?start={p["ref_code"]}'})


@app.route('/api/create_withdrawal', methods=['POST'])
def create_withdrawal():
    data = request.json or {}; uid = data.get('user_id'); amt = float(data.get('amount', 0))
    if amt <= 0: return jsonify({'status':'error','message':'Invalid amount'})
    p = get_player(uid)
    if not p or amt > float(p.get('balance') or 0): return jsonify({'status':'error','message':'Недостаточно средств'})
    update_player(uid, balance=float(p['balance']) - amt)
    spend_id = f'wd_{uid}_{secrets.token_hex(8)}'
    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('INSERT INTO withdrawals (user_id, amount, transfer_id, status) VALUES (?,?,?,?)', (uid, amt, spend_id, 'pending')); wid=c.lastrowid; conn.commit(); conn.close()
    headers = {'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN}
    payload = {'user_id': uid, 'asset': 'TON', 'amount': str(round(amt, 6)), 'spend_id': spend_id, 'comment': 'TopGift withdrawal'}
    try:
        r = requests.post(f'{CRYPTOBOT_API}/transfer', headers=headers, json=payload, timeout=12)
        js = r.json()
        if r.status_code == 200 and js.get('ok'):
            conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('UPDATE withdrawals SET status=? WHERE id=?', ('paid', wid)); c.execute('INSERT INTO transactions (user_id, type, amount) VALUES (?,?,?)', (uid, 'withdrawal', -amt)); conn.commit(); conn.close()
            return jsonify({'status':'ok','balance':float(p['balance'])-amt})
        raise RuntimeError(js.get('error') or js)
    except Exception as e:
        logger.error(f'CryptoBot transfer error: {e}')
        fresh = get_player(uid) or {'balance': 0}
        update_player(uid, balance=float(fresh.get('balance') or 0) + amt)
        conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('UPDATE withdrawals SET status=? WHERE id=?', ('failed', wid)); conn.commit(); conn.close()
        notify_user(uid, '⚡️ <b>Ошибка вывода</b>\nСредства возвращены на ваш баланс', {'inline_keyboard': [[{'text':'Поддержка','url':'https://t.me/killowcode'}]]})
        return jsonify({'status':'error','message':'Ошибка вывода','balance':float(fresh.get('balance') or 0)+amt})

@app.route('/api/transactions', methods=['POST'])
def transactions():
    data = request.json; uid = data.get('user_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT type, amount, created_at, COALESCE(currency,"TON") FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 30', (uid,))
    txs = [{'type': r[0], 'amount': r[1], 'date': r[2], 'currency': r[3]} for r in c.fetchall()]
    c.execute('SELECT amount_game, status, created_at FROM deposits WHERE user_id=? ORDER BY created_at DESC LIMIT 30', (uid,))
    deps = [{'amount_game': r[0], 'status': r[1], 'date': r[2]} for r in c.fetchall()]
    conn.close()
    return jsonify({'transactions': txs, 'deposits': deps})



def admin_required(uid):
    return int(uid or 0) == ADMIN_USER_ID

@app.route('/api/admin/player', methods=['POST'])
def admin_player():
    data=request.json or {}; admin_id=data.get('admin_id'); target=int(data.get('user_id') or 0)
    if not admin_required(admin_id): return jsonify({'status':'error','message':'Forbidden'}), 403
    p=get_player(target)
    if not p: return jsonify({'status':'error','message':'Пользователь не найден'})
    return jsonify({'status':'ok','player':{'user_id':target,'name':public_player(p),'balance':p.get('balance') or 0,'stars_balance':p.get('stars_balance') or 0,'is_banned':bool(p.get('is_banned') or 0)}})

@app.route('/api/admin/ban', methods=['POST'])
def admin_ban():
    data=request.json or {}; admin_id=data.get('admin_id'); target=int(data.get('user_id') or 0); banned=1 if data.get('banned') else 0
    if not admin_required(admin_id): return jsonify({'status':'error','message':'Forbidden'}), 403
    update_player(target, is_banned=banned)
    return jsonify({'status':'ok','is_banned':bool(banned)})

@app.route('/api/admin/balance', methods=['POST'])
def admin_balance():
    data=request.json or {}; admin_id=data.get('admin_id'); target=int(data.get('user_id') or 0); amount=float(data.get('amount') or 0); action=data.get('action'); currency=(data.get('currency') or 'TON').upper()
    if not admin_required(admin_id): return jsonify({'status':'error','message':'Forbidden'}), 403
    if currency not in ('TON','STARS') or amount <= 0 or action not in ('add','subtract'): return jsonify({'status':'error','message':'Invalid'})
    p=get_player(target) or {}; key='stars_balance' if currency=='STARS' else 'balance'; current=float(p.get(key) or 0); new_balance=current+amount if action=='add' else max(0,current-amount)
    update_player(target, **{key:new_balance})
    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('INSERT INTO transactions (user_id,type,amount,currency) VALUES (?,?,?,?)',(target,'admin_'+action, amount if action=='add' else -amount, currency)); conn.commit(); conn.close()
    return jsonify({'status':'ok','balance':new_balance,'currency':currency})

@app.route('/api/admin/transactions', methods=['POST'])
def admin_transactions():
    data=request.json or {}; admin_id=data.get('admin_id'); target=int(data.get('user_id') or 0)
    if not admin_required(admin_id): return jsonify({'status':'error','message':'Forbidden'}), 403
    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('SELECT type,amount,created_at,COALESCE(currency,"TON") FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 80',(target,)); txs=[{'type':r[0],'amount':r[1],'date':r[2],'currency':r[3]} for r in c.fetchall()]; conn.close()
    return jsonify({'status':'ok','transactions':txs})

@app.route('/api/set_lang', methods=['POST'])
def set_lang(): return jsonify({'status': 'ok'})

@app.route('/api/set_notify', methods=['POST'])
def set_notify(): return jsonify({'status': 'ok'})

@app.route('/api/game_state')
def get_game_state():
    players_list=[]
    for uid,bet in game_state.bets.items():
        p=get_player(uid) or {}
        players_list.append({'name':public_player(p),'avatar':p.get('avatar_url') or '','bet':bet['amount'],'currency':bet.get('currency','TON'),'cashed_out':bet['cashed_out'],'multiplier':bet['multiplier'] or (game_state.crash_point if game_state.status in ('exploding','crashed') and not bet['cashed_out'] else game_state.current_multiplier),'payout':(bet['amount']*(bet['multiplier'] or 0)) if bet['cashed_out'] else 0})
    return jsonify({'status':game_state.status,'multiplier':game_state.current_multiplier,'countdown':game_state.countdown,'last_results':game_state.last_results,'crash_point':game_state.crash_point if game_state.status=='crashed' else None,'players':players_list})

def run_game_loop():
    while True:
        game_state.status='countdown'; game_state.bets = game_state.pending_bets; game_state.pending_bets = {}
        for i in range(10,0,-1): game_state.countdown=i; game_state.status='countdown'; time.sleep(1)
        game_state.crash_point=generate_crash_point(); game_state.current_multiplier=1.0; game_state.status='flying'
        st=time.time()
        while game_state.current_multiplier<game_state.crash_point:
            elapsed=time.time()-st
            game_state.current_multiplier=round(1.0 + 0.06 * (pow(1.38, elapsed) - 1.0), 2)
            for uid, bet in list(game_state.bets.items()):
                target = float(bet.get('auto_cashout') or 0)
                if target and not bet['cashed_out'] and game_state.current_multiplier >= target:
                    winnings = bet['amount'] * target
                    p = get_player(uid)
                    balance_key = 'stars_balance' if bet.get('currency') == 'STARS' else 'balance'
                    update_player(uid, **{balance_key: float(p.get(balance_key) or 0) + winnings})
                    bet['cashed_out'] = True
                    bet['multiplier'] = target
            if game_state.current_multiplier>=game_state.crash_point:
                game_state.current_multiplier=game_state.crash_point; break
            time.sleep(max(0.02,0.06-game_state.current_multiplier*0.002))
        game_state.status='exploding'
        game_state.last_results.insert(0,round(game_state.crash_point,2))
        if len(game_state.last_results)>8: game_state.last_results=game_state.last_results[:8]
        time.sleep(3.2)
        game_state.status='crashed'
        time.sleep(1)
        game_state.current_multiplier=1.0

def deposit_checker():
    while True:
        time.sleep(3)

def run_flask(): app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)

async def run_bot():
    bot=Bot(token=TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
    dp=Dispatcher()
    @dp.message(Command('start'))
    async def cmd_start(message: types.Message):
        ref=(message.text.split(maxsplit=1)[1].strip() if message.text and len(message.text.split(maxsplit=1))>1 else '')
        update_player(message.from_user.id, username=message.from_user.username or '', first_name=message.from_user.first_name or '', last_name=message.from_user.last_name or '')
        ensure_referral(message.from_user.id, ref)
        p = get_player(message.from_user.id) or {}
        if p.get('is_banned'):
            await message.answer('❌\n<b>Вы заблокированы в боте</b>\nдля выяснения обстоятельств обратитесь в поддержку')
            return
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Play TopGift (RU)", web_app=WebAppInfo(url=WEBAPP_URL))]])
        await message.answer("Welcome to TopGift! Start winning real Telegram Gifts right now!", reply_markup=kb)

    @dp.pre_checkout_query()
    async def pre_checkout(query: types.PreCheckoutQuery):
        await bot.answer_pre_checkout_query(query.id, ok=True)

    @dp.message(lambda message: bool(message.successful_payment))
    async def successful_payment(message: types.Message):
        payment = message.successful_payment
        if payment.currency != 'XTR':
            return
        try:
            payload = json.loads(payment.invoice_payload)
        except Exception:
            payload = {'user_id': message.from_user.id, 'amount': payment.total_amount}
        uid = int(payload.get('user_id') or message.from_user.id)
        amount = int(payment.total_amount or payload.get('amount') or 0)
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute('SELECT id,status FROM deposits WHERE invoice_id=?', (payment.invoice_payload,))
        row = c.fetchone()
        if row and row[1] != 'paid':
            dep_id = row[0]
            c.execute('UPDATE deposits SET status=?, telegram_payment_charge_id=? WHERE id=?', ('paid', payment.telegram_payment_charge_id, dep_id))
            c.execute('UPDATE players SET stars_balance=COALESCE(stars_balance,0)+? WHERE user_id=?', (amount, uid))
            c.execute('INSERT INTO transactions (user_id,type,amount,currency) VALUES (?,?,?,?)', (uid, 'stars_deposit', amount, 'STARS'))
            conn.commit(); conn.close()
            award_referral(uid, amount, 'STARS')
        else:
            conn.close()
        await message.answer(f'✅ Пополнение на {amount} Stars зачислено')
    await dp.start_polling(bot)

if __name__=='__main__':
    init_db()
    threading.Thread(target=run_game_loop, daemon=True).start()
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=deposit_checker, daemon=True).start()
    threading.Thread(target=coingecko_rate_loop, daemon=True).start()
    asyncio.run(run_bot())
