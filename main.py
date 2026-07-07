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
        "ALTER TABLE players ADD COLUMN lang TEXT DEFAULT 'ru'",
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
    c.execute('''CREATE TABLE IF NOT EXISTS promo_codes (
        code TEXT PRIMARY KEY, max_activations INTEGER, ton_amount REAL DEFAULT 0, stars_amount REAL DEFAULT 0,
        active INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS promo_activations (
        code TEXT, user_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(code,user_id)
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
    asset = (asset_row[0] if asset_row else 'TON').upper()
    currency = 'STARS' if asset == 'STARS' else 'TON'
    balance_col = 'stars_balance' if currency == 'STARS' else 'balance'
    c.execute('UPDATE deposits SET status=? WHERE id=? AND status!=?', ('paid', dep_id, 'paid'))
    changed = c.rowcount
    if changed:
        c.execute(f'UPDATE players SET {balance_col}={balance_col}+? WHERE user_id=?', (amount, user_id))
        c.execute('INSERT INTO transactions (user_id, type, amount, currency) VALUES (?,?,?,?)', (user_id, 'deposit', amount, currency))
    conn.commit(); conn.close()
    return bool(changed)


def award_referral(user_id, amount, currency='TON'):
    currency = (currency or 'TON').upper()
    balance_col = 'stars_balance' if currency == 'STARS' else 'balance'
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('SELECT referrer_id FROM players WHERE user_id=?', (user_id,))
    row = c.fetchone()
    if row and row[0]:
        reward = round(float(amount or 0) * REF_REWARD_RATE, 6)
        if reward > 0:
            referrer_id = row[0]
            c.execute(f'UPDATE players SET {balance_col}={balance_col}+?, ref_balance=COALESCE(ref_balance,0)+? WHERE user_id=?', (reward, reward, referrer_id))
            c.execute('INSERT INTO transactions (user_id, type, amount, currency) VALUES (?,?,?,?)', (referrer_id, 'referral_bonus', reward, currency))
            conn.commit(); conn.close()
            amount_text = f'{reward:.0f}' if currency == 'STARS' else f'{reward:.2f}'
            notify_user(referrer_id, f'<tg-emoji emoji-id="5280818098960611598">🤑</tg-emoji> <b>На ваш баланс начислено <code>{amount_text}</code><tg-emoji emoji-id="5359719332542718652">💎</tg-emoji> за ставку реферала</b>')
            return
    conn.commit(); conn.close()

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

MAX_CRASH_MULTIPLIER = 25.0
TARGET_RTP = 0.92

def make_round_seeds():
    server_seed = secrets.token_hex(32)
    client_seed = secrets.token_hex(32)
    salt = secrets.token_hex(16)
    server_seed_hash = hashlib.sha256(server_seed.encode()).hexdigest()
    return server_seed, client_seed, salt, server_seed_hash

def generate_crash_point(server_seed=None, client_seed=None, salt=None, bonus_round=False):
    seed = f'{server_seed or secrets.token_hex(16)}:{client_seed or ""}:{salt or ""}'.encode()
    digest = hmac.new(str(salt or 'topgift').encode(), seed, hashlib.sha256).hexdigest()
    roll = int(digest[:13], 16) / float(0xFFFFFFFFFFFFF)
    if roll < (1.0 - TARGET_RTP):
        return 1.00
    point = TARGET_RTP / max(1e-12, 1.0 - roll)
    return round(max(1.0, min(MAX_CRASH_MULTIPLIER, point)), 2)

def multiplier_tick_delay(multiplier):
    if multiplier < 2.0:
        return 0.08
    if multiplier < 3.0:
        return 0.05
    if multiplier < 5.0:
        return 0.045
    return max(0.012, 0.045 - (int(multiplier) - 4) * 0.003)

@dataclass
class GameState:
    status: str = 'countdown'
    current_multiplier: float = 1.0
    countdown: int = 10
    crash_point: float = 0.0
    last_results: list = field(default_factory=list)
    bets: dict = field(default_factory=dict)
    pending_bets: dict = field(default_factory=dict)
    server_seed: str = ''
    server_seed_hash: str = ''
    client_seed: str = ''
    salt: str = ''
    revealed_server_seed: str = ''
    revealed_salt: str = ''
    force_crash: bool = False

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
        'stars_balance':p.get('stars_balance') or 0,'lang':p.get('lang') or 'ru','is_admin':uid==ADMIN_USER_ID,'is_banned':bool(p.get('is_banned') or 0),
        'game_state':{'status':game_state.status,'multiplier':game_state.current_multiplier,'countdown':game_state.countdown,'last_results':game_state.last_results,'crash_point':game_state.crash_point if game_state.status=='crashed' else None},
        'has_bet':uid in game_state.bets or uid in game_state.pending_bets,'current_bet':(game_state.bets.get(uid) or game_state.pending_bets.get(uid) or {}).get('amount',0)})


@app.route('/api/balance', methods=['POST'])
def balance_api():
    uid=(request.json or {}).get('user_id')
    p=get_player(uid)
    if not p: return jsonify({'status':'error','message':'Пользователь не найден'}), 404
    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id=?',(uid,)); ref_count=c.fetchone()[0]; conn.close()
    return jsonify({'status':'ok','balance':p.get('balance') or 0,'stars_balance':p.get('stars_balance') or 0,'ref_balance':p.get('ref_balance') or 0,'ref_count':ref_count})

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
    min_bet = 10 if currency == 'STARS' else 0.1
    if amt < min_bet: return jsonify({'status':'error','message':'Минимальная ставка 10 звезд' if currency == 'STARS' else 'Минимальная ставка 0.1 TON'})
    if amt>float(p.get(balance_key) or 0): return jsonify({'status':'error','message':'Недостаточно средств'})
    if uid in game_state.bets or uid in game_state.pending_bets: return jsonify({'status':'error','message':'Already bet'})
    update_player(uid, **{balance_key: float(p.get(balance_key) or 0)-amt})
    award_referral(uid, amt, currency)
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
    if coin != 'TON' or amt <= 0:
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
    if status == 'paid':
        p=get_player(user_id) or {}; return jsonify({'status':'paid','balance':p.get('balance') or 0,'stars_balance':p.get('stars_balance') or 0,'ref_balance':p.get('ref_balance') or 0})
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
                p=get_player(user_id) or {}; return jsonify({'status':'paid','balance':p.get('balance') or 0,'stars_balance':p.get('stars_balance') or 0,'ref_balance':p.get('ref_balance') or 0})
        except Exception as e: logger.error(f'Status check error: {e}')
    else:
        try:
            r=requests.get(f'{TONAPI_URL}/blockchain/accounts/{ADMIN_WALLET}/transactions', headers={'Authorization': f'Bearer {TONAPI_KEY}'}, params={'limit':20}, timeout=10)
            if r.status_code == 200 and invoice_id in r.text:
                credit_deposit(dep_id, user_id, amount_game)
                p=get_player(user_id) or {}; return jsonify({'status':'paid','balance':p.get('balance') or 0,'stars_balance':p.get('stars_balance') or 0,'ref_balance':p.get('ref_balance') or 0})
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
    data = request.json or {}; uid = data.get('user_id'); amt = float(data.get('amount', 0)); currency = (data.get('currency') or 'TON').upper()
    if currency == 'STARS': return jsonify({'status':'error','message':'Вывод звезд пока недоступен'})
    if currency != 'TON' or amt <= 0: return jsonify({'status':'error','message':'Invalid amount'})
    p = get_player(uid)
    balance_key = 'stars_balance' if currency == 'STARS' else 'balance'
    debit_amount = convert_crypto_to_ton('USDT', amt) if currency == 'USDT' else amt
    if not p or debit_amount > float(p.get(balance_key) or 0): return jsonify({'status':'error','message':'Недостаточно средств'})
    update_player(uid, **{balance_key: float(p.get(balance_key) or 0) - debit_amount})
    spend_id = f'wd_{uid}_{secrets.token_hex(8)}'
    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('INSERT INTO withdrawals (user_id, amount, transfer_id, status) VALUES (?,?,?,?)', (uid, amt, spend_id, 'pending')); wid=c.lastrowid; c.execute('INSERT INTO transactions (user_id, type, amount, currency) VALUES (?,?,?,?)', (uid, 'withdrawal_request', -amt, currency)); conn.commit(); conn.close()
    if currency == 'STARS':
        fresh = get_player(uid) or {}
        return jsonify({'status':'ok','balance':fresh.get('balance') or 0,'stars_balance':fresh.get('stars_balance') or 0})
    headers = {'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN}
    payload = {'user_id': uid, 'asset': currency, 'amount': str(round(amt, 6)), 'spend_id': spend_id, 'comment': 'TopGift withdrawal'}
    try:
        r = requests.post(f'{CRYPTOBOT_API}/transfer', headers=headers, json=payload, timeout=12)
        js = r.json()
        if r.status_code == 200 and js.get('ok'):
            conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('UPDATE withdrawals SET status=? WHERE id=?', ('paid', wid)); c.execute('INSERT INTO transactions (user_id, type, amount, currency) VALUES (?,?,?,?)', (uid, 'withdrawal', -amt, currency)); conn.commit(); conn.close()
            fresh=get_player(uid) or {}; return jsonify({'status':'ok','balance':fresh.get('balance') or 0,'stars_balance':fresh.get('stars_balance') or 0})
        raise RuntimeError(js.get('error') or js)
    except Exception as e:
        logger.error(f'CryptoBot transfer error: {e}')
        fresh = get_player(uid) or {'balance': 0}
        update_player(uid, **{balance_key: float(fresh.get(balance_key) or 0) + debit_amount})
        conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('UPDATE withdrawals SET status=? WHERE id=?', ('failed', wid)); conn.commit(); conn.close()
        notify_user(uid, '⚡️ <b>Ошибка вывода</b>\nСредства возвращены на ваш баланс', {'inline_keyboard': [[{'text':'Поддержка','url':'https://t.me/killowcode'}]]})
        fresh=get_player(uid) or {}; return jsonify({'status':'error','message':'Произошла ошибка при выводе, попробуйте позже','balance':fresh.get('balance') or 0,'stars_balance':fresh.get('stars_balance') or 0})

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
    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('SELECT type,amount,created_at,COALESCE(currency,"TON") FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 5',(target,)); txs=[{'type':r[0],'amount':r[1],'date':r[2],'currency':r[3]} for r in c.fetchall()]; conn.close()
    return jsonify({'status':'ok','transactions':txs})


@app.route('/api/promo/activate', methods=['POST'])
def promo_activate():
    data=request.json or {}; uid=int(data.get('user_id') or 0); code=(data.get('code') or '').strip().upper()
    if not code: return jsonify({'status':'error','message':'not_found'})
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    c.execute('SELECT code,max_activations,ton_amount,stars_amount,active FROM promo_codes WHERE code=?',(code,)); row=c.fetchone()
    if not row or not row[4]: conn.close(); return jsonify({'status':'error','message':'not_found'})
    c.execute('SELECT 1 FROM promo_activations WHERE code=? AND user_id=?',(code,uid))
    if c.fetchone(): conn.close(); return jsonify({'status':'error','message':'already_used'})
    c.execute('SELECT COUNT(*) FROM promo_activations WHERE code=?',(code,)); used=c.fetchone()[0]
    if used >= int(row[1] or 0): conn.close(); return jsonify({'status':'error','message':'exhausted'})
    c.execute('INSERT INTO promo_activations (code,user_id) VALUES (?,?)',(code,uid))
    c.execute('UPDATE players SET balance=COALESCE(balance,0)+?, stars_balance=COALESCE(stars_balance,0)+? WHERE user_id=?',(float(row[2] or 0),float(row[3] or 0),uid))
    if row[2]: c.execute('INSERT INTO transactions (user_id,type,amount,currency) VALUES (?,?,?,?)',(uid,'promo',float(row[2]),'TON'))
    if row[3]: c.execute('INSERT INTO transactions (user_id,type,amount,currency) VALUES (?,?,?,?)',(uid,'promo',float(row[3]),'STARS'))
    conn.commit(); conn.close(); p=get_player(uid) or {}
    return jsonify({'status':'ok','balance':p.get('balance') or 0,'stars_balance':p.get('stars_balance') or 0})

@app.route('/api/admin/promos', methods=['POST'])
def admin_promos():
    data=request.json or {}; admin_id=data.get('admin_id')
    if not admin_required(admin_id): return jsonify({'status':'error','message':'Forbidden'}),403
    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('SELECT p.code,p.max_activations,p.ton_amount,p.stars_amount,COUNT(a.user_id) FROM promo_codes p LEFT JOIN promo_activations a ON a.code=p.code WHERE p.active=1 GROUP BY p.code ORDER BY p.created_at DESC')
    rows=[{'code':r[0],'max':r[1],'ton':r[2],'stars':r[3],'used':r[4]} for r in c.fetchall()]; conn.close(); return jsonify({'status':'ok','promos':rows})

@app.route('/api/admin/promo_create', methods=['POST'])
def admin_promo_create():
    data=request.json or {}; admin_id=data.get('admin_id')
    if not admin_required(admin_id): return jsonify({'status':'error','message':'Forbidden'}),403
    code=(data.get('code') or '').strip().upper(); uses=int(data.get('uses') or 0); ton=float(data.get('ton') or 0); stars=float(data.get('stars') or 0)
    if not code or uses<1 or (ton<=0 and stars<=0): return jsonify({'status':'error','message':'Invalid'})
    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('INSERT OR REPLACE INTO promo_codes (code,max_activations,ton_amount,stars_amount,active) VALUES (?,?,?,?,1)',(code,uses,ton,stars)); conn.commit(); conn.close(); return jsonify({'status':'ok'})

@app.route('/api/admin/promo_delete', methods=['POST'])
def admin_promo_delete():
    data=request.json or {}; admin_id=data.get('admin_id')
    if not admin_required(admin_id): return jsonify({'status':'error','message':'Forbidden'}),403
    code=(data.get('code') or '').strip().upper(); conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('UPDATE promo_codes SET active=0 WHERE code=?',(code,)); conn.commit(); conn.close(); return jsonify({'status':'ok'})

@app.route('/api/admin/crash', methods=['POST'])
def admin_crash():
    data=request.json or {}; admin_id=data.get('admin_id')
    if not admin_required(admin_id): return jsonify({'status':'error','message':'Forbidden'}), 403
    if game_state.status != 'flying': return jsonify({'status':'error','message':'Раунд сейчас не летит'})
    game_state.force_crash = True
    game_state.crash_point = min(game_state.crash_point or game_state.current_multiplier, max(1.0, game_state.current_multiplier))
    return jsonify({'status':'ok','multiplier':game_state.current_multiplier})

@app.route('/api/set_lang', methods=['POST'])
def set_lang():
    data=request.json or {}; uid=data.get('user_id'); lang=(data.get('lang') or 'ru').lower()
    if lang not in ('ru','en'): lang='ru'
    update_player(uid, lang=lang)
    return jsonify({'status':'ok','lang':lang})

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
        game_state.status='countdown'; game_state.bets = game_state.pending_bets; game_state.pending_bets = {}; game_state.force_crash = False
        game_state.server_seed, game_state.client_seed, game_state.salt, game_state.server_seed_hash = make_round_seeds()
        game_state.revealed_server_seed = ''; game_state.revealed_salt = ''
        for i in range(10,0,-1): game_state.countdown=i; game_state.status='countdown'; time.sleep(1)
        bonus_round = not game_state.bets
        game_state.crash_point=generate_crash_point(game_state.server_seed, game_state.client_seed, game_state.salt, bonus_round); game_state.current_multiplier=1.0; game_state.status='flying'
        while game_state.current_multiplier<game_state.crash_point:
            game_state.current_multiplier=round(min(game_state.crash_point, game_state.current_multiplier + 0.01), 2)

            for uid, bet in list(game_state.bets.items()):
                target = float(bet.get('auto_cashout') or 0)
                if target and not bet['cashed_out'] and game_state.current_multiplier >= target:
                    winnings = bet['amount'] * target
                    p = get_player(uid)
                    balance_key = 'stars_balance' if bet.get('currency') == 'STARS' else 'balance'
                    update_player(uid, **{balance_key: float(p.get(balance_key) or 0) + winnings})
                    bet['cashed_out'] = True
                    bet['multiplier'] = target
            if game_state.force_crash or game_state.current_multiplier>=game_state.crash_point:
                game_state.current_multiplier=game_state.crash_point; break
            time.sleep(multiplier_tick_delay(game_state.current_multiplier))
        game_state.revealed_server_seed = game_state.server_seed
        game_state.revealed_salt = game_state.salt
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
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Play", web_app=WebAppInfo(url=WEBAPP_URL))]])
        await message.answer("<b>Welcome to UP! №1 Crash Game in Telegram</b>", reply_markup=kb)

    admin_sessions = {}

    def admin_panel_kb():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Пополнить', callback_data='adm:add'), InlineKeyboardButton(text='Снять', callback_data='adm:subtract')],
            [InlineKeyboardButton(text='Забанить', callback_data='adm:ban'), InlineKeyboardButton(text='Разбанить', callback_data='adm:unban')],
            [InlineKeyboardButton(text='Транзакции', callback_data='adm:tx'), InlineKeyboardButton(text='Крашнуть', callback_data='adm:crash')],
            [InlineKeyboardButton(text='Рассылка', callback_data='adm:broadcast')],
        ])

    def currency_kb(action):
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='TON', callback_data=f'admcur:{action}:TON'), InlineKeyboardButton(text='Звезды', callback_data=f'admcur:{action}:STARS')]])

    @dp.message(Command('admin'))
    async def cmd_admin(message: types.Message):
        if message.from_user.id != ADMIN_USER_ID:
            return
        await message.answer('<b>⚙️ Админ панель TopGift</b>\n\n<blockquote><i>Выберите действие ниже. Для пополнений и снятий бот пошагово запросит валюту, Telegram ID и сумму.</i></blockquote>', reply_markup=admin_panel_kb())

    @dp.callback_query(lambda c: c.data and c.data.startswith('adm:'))
    async def admin_action(cb: types.CallbackQuery):
        if cb.from_user.id != ADMIN_USER_ID:
            await cb.answer('Нет доступа', show_alert=True); return
        action = cb.data.split(':', 1)[1]
        if action in ('add', 'subtract'):
            admin_sessions[cb.from_user.id] = {'action': action, 'step': 'currency'}
            await cb.message.answer('<b>💰 Баланс пользователя</b>\n<blockquote><i>Выберите валюту для операции.</i></blockquote>', reply_markup=currency_kb(action))
        elif action in ('ban', 'unban', 'tx'):
            admin_sessions[cb.from_user.id] = {'action': action, 'step': 'user_id'}
            await cb.message.answer('<b>👤 Введите Telegram ID пользователя</b>\n<blockquote><i>Например: <code>8374183799</code></i></blockquote>')
        elif action == 'broadcast':
            admin_sessions[cb.from_user.id] = {'action': action, 'step': 'text'}
            await cb.message.answer('<b>📣 Рассылка</b>\n<blockquote><i>Отправьте текст сообщения. Можно использовать HTML: &lt;b&gt;, &lt;i&gt;, &lt;blockquote&gt; и переносы строк.</i></blockquote>')
        elif action == 'crash':
            if game_state.status == 'flying':
                game_state.force_crash = True
                game_state.crash_point = min(game_state.crash_point or game_state.current_multiplier, max(1.0, game_state.current_multiplier))
                await cb.message.answer('<b>💥 Ракетка крашнута</b>\n<blockquote><i>Все незабранные ставки проигрывают.</i></blockquote>')
            else:
                await cb.message.answer('<b>⏳ Раунд сейчас не летит</b>\n<blockquote><i>Краш доступен только во время полёта.</i></blockquote>')
        await cb.answer()

    @dp.callback_query(lambda c: c.data and c.data.startswith('admcur:'))
    async def admin_currency(cb: types.CallbackQuery):
        if cb.from_user.id != ADMIN_USER_ID:
            await cb.answer('Нет доступа', show_alert=True); return
        _, action, currency = cb.data.split(':')
        admin_sessions[cb.from_user.id] = {'action': action, 'step': 'user_id', 'currency': currency}
        await cb.message.answer(f'<b>✅ Выбрано: {currency}</b>\n<blockquote><i>Введите Telegram ID пользователя.</i></blockquote>')
        await cb.answer()

    @dp.message(lambda m: m.from_user and m.from_user.id == ADMIN_USER_ID and m.from_user.id in admin_sessions)
    async def admin_dialog(message: types.Message):
        sess = admin_sessions.get(message.from_user.id, {})
        action, step = sess.get('action'), sess.get('step')
        text = (message.text or '').strip()
        try:
            if step == 'user_id':
                sess['user_id'] = int(text); sess['step'] = 'amount' if action in ('add', 'subtract') else 'confirm'
                if action in ('add', 'subtract'):
                    await message.answer('<b>✍️ Введите сумму</b>\n<blockquote><i>Можно использовать точку или запятую.</i></blockquote>')
                elif action in ('ban', 'unban'):
                    update_player(sess['user_id'], is_banned=1 if action == 'ban' else 0)
                    admin_sessions.pop(message.from_user.id, None)
                    await message.answer('<b>✅ Готово</b>\n<blockquote><i>Статус пользователя обновлён.</i></blockquote>', reply_markup=admin_panel_kb())
                elif action == 'tx':
                    conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('SELECT type,amount,created_at,COALESCE(currency,"TON") FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 5',(sess['user_id'],)); rows=c.fetchall(); conn.close()
                    body='<b>🧾 Последние транзакции</b>\n' + ('\n'.join([f'<blockquote><b>{r[0]}</b> — <code>{r[1]} {r[3]}</code>\n<i>{r[2]}</i></blockquote>' for r in rows]) or '<blockquote><i>Транзакций нет</i></blockquote>')
                    admin_sessions.pop(message.from_user.id, None)
                    await message.answer(body, reply_markup=admin_panel_kb())
            elif step == 'amount':
                amount = float(text.replace(',', '.')); currency=sess.get('currency','TON'); key='stars_balance' if currency=='STARS' else 'balance'
                p=get_player(sess['user_id']) or {}; current=float(p.get(key) or 0); new_balance=current+amount if action=='add' else max(0,current-amount)
                update_player(sess['user_id'], **{key:new_balance})
                conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('INSERT INTO transactions (user_id,type,amount,currency) VALUES (?,?,?,?)',(sess['user_id'],'admin_'+action, amount if action=='add' else -amount, currency)); conn.commit(); conn.close()
                admin_sessions.pop(message.from_user.id, None)
                await message.answer((f'<b>✅ Баланс обновлён</b>\n<blockquote><i>Новый баланс {currency}:</i> <code>{new_balance:.0f}</code></blockquote>' if currency=='STARS' else f'<b>✅ Баланс обновлён</b>\n<blockquote><i>Новый баланс {currency}:</i> <code>{new_balance:.2f}</code></blockquote>'), reply_markup=admin_panel_kb())
            elif step == 'text' and action == 'broadcast':
                conn=sqlite3.connect(DB_PATH); c=conn.cursor(); c.execute('SELECT user_id FROM players WHERE COALESCE(is_banned,0)=0'); users=[r[0] for r in c.fetchall()]; conn.close()
                sent=0
                for uid in users:
                    try:
                        await bot.send_message(uid, text); sent += 1
                    except Exception:
                        pass
                admin_sessions.pop(message.from_user.id, None)
                await message.answer(f'<b>✅ Рассылка завершена</b>\n<blockquote><i>Отправлено:</i> <code>{sent}</code></blockquote>', reply_markup=admin_panel_kb())
        except Exception as e:
            await message.answer(f'<b>❌ Ошибка</b>\n<blockquote><i>{e}</i>\nПопробуйте ещё раз или откройте /admin.</blockquote>')

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
