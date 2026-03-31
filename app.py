import os
import uuid
import base64
import sqlite3
import hashlib
import time
from datetime import datetime, timedelta
from io import BytesIO
from flask import Flask, render_template, send_from_directory, jsonify, request, make_response
import requests
import qrcode

app = Flask(__name__, template_folder=os.path.abspath('.'))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_FOLDER = os.path.join(BASE_DIR, 'media')
DB_PATH = os.path.join(BASE_DIR, 'bastion_tracking.db')

PLUMIFY_API_TOKEN = "oI9madY2L6gDfIToH4o7GQjnYlvpOX7aJD0GtH8BdWGHyIerGgm8s5XMTJ8x"
PLUMIFY_ENDPOINT = f"https://api.atomopay.com.br/api/public/v1/transactions?api_token={PLUMIFY_API_TOKEN}"

META_PIXEL_ID = "1271724644401924"
META_CAPI_ACCESS_TOKEN = "EAAY3pJasrWUBRHjNp1JhJ9HZCAJ1zjjmWF3SqYVWZCkvgBqhG47ZAWHgtCiyMlZCrCrR2hnq4IZB7C6O9nRbiFZBB0C51oSGQrEDYZCGabW29EYwjCDuoNHWBr1hDDzkPGLbLRE1eJZAgHR3G6L5XN68rjmptmZAAc3fZBnt3xLh8GUFFlskHousgYyXPj4bbV3qt6TwZDZD"

PAID_STATUSES = {"paid", "approved", "completed", "confirmed", "paid_out", "finished", "success", "settled", "captured", "accredited", "credited", "confirmed_payment"}
FAILED_STATUSES = {"canceled", "cancelled", "refunded", "chargeback", "reversed", "voided", "failed", "expired", "denied"}

if not os.path.exists(MEDIA_FOLDER):
    os.makedirs(MEDIA_FOLDER)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_tracking (
            id TEXT PRIMARY KEY,
            ip_address TEXT,
            user_agent TEXT,
            generate_count INTEGER DEFAULT 0,
            last_generated DATETIME,
            blocked_until DATETIME
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def send_purchase_capi(transaction_id, value=20.96, currency="BRL", email=None, phone=None):
    def hash_value(value):
        if not value:
            return None
        return hashlib.sha256(value.encode('utf-8')).hexdigest().lower()
    payload = {
        "data": [{
            "event_name": "Purchase",
            "event_time": int(time.time()),
            "action_source": "website",
            "event_id": transaction_id,
            "user_data": {
                "em": hash_value(email),
                "ph": hash_value(phone)
            },
            "custom_data": {
                "currency": currency,
                "value": value,
                "content_ids": ["premium-access"],
                "content_type": "product",
                "transaction_id": transaction_id
            }
        }]
    }
    url = f"https://graph.facebook.com/v20.0/{META_PIXEL_ID}/events?access_token={META_CAPI_ACCESS_TOKEN}"
    try:
        requests.post(url, json=payload)
    except:
        pass

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/media/<path:filename>')
def serve_video(filename):
    return send_from_directory(MEDIA_FOLDER, filename)

@app.route('/api/create-payment', methods=['POST'])
def create_payment():
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    user_agent = request.headers.get('User-Agent', '')
    tracking_id = request.cookies.get('bastion_session_id')
    if not tracking_id:
        tracking_id = str(uuid.uuid4())
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM user_tracking WHERE id = ? OR ip_address = ?', (tracking_id, client_ip))
    user_record = cursor.fetchone()
    if user_record:
        tracking_id = user_record['id']
        blocked_until = user_record['blocked_until']
        if blocked_until:
            blocked_time = datetime.fromisoformat(blocked_until)
            if datetime.now() < blocked_time:
                conn.close()
                return jsonify({"success": False, "error": "Acesso restrito temporariamente pelo administrador.", "code": "BLOCKED"}), 403
    payload = request.get_json(silent=True) or {}
    customer_data = payload.get("customer", {
        "name": "BASTIONSTREAM LTDA",
        "email": "cliente@exemplo.com",
        "phone_number": "11999999999",
        "document": "09115751031",
        "street_name": "Rua das Flores",
        "number": "123",
        "complement": "Apt 45",
        "neighborhood": "Centro",
        "city": "Rio de Janeiro",
        "state": "RJ",
        "zip_code": "20040020"
    })
    api_payload = {
        "amount": 2000,
        "offer_hash": "zfglddzogu",
        "payment_method": "pix",
        "installments": 1,
        "customer": customer_data,
        "cart": [
            {
                "product_hash": "spdgalirqt",
                "title": "Acesso Premium BastionStream (6 meses)",
                "cover": None,
                "price": 2000,
                "quantity": 1,
                "operation_type": 1,
                "tangible": False
            }
        ],
        "expire_in_days": 1,
        "transaction_origin": "api"
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    try:
        response = requests.post(PLUMIFY_ENDPOINT, json=api_payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        resp = data if isinstance(data, dict) else {}
        d = resp.get("data") if isinstance(resp.get("data", None), dict) else resp
        pix = d.get("pix") or {}
        emv = pix.get("pix_qr_code") or pix.get("copy_and_paste") or pix.get("emv") or d.get("pix_qr_code") or d.get("copy_and_paste") or d.get("emv")
        h = d.get("hash") or pix.get("hash") or d.get("transaction_hash") or d.get("id_hash") or str(uuid.uuid4())
        if emv:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=4,
            )
            qr.add_data(emv)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
            base64_image = f"data:image/png;base64,{img_str}"
            now_str = datetime.now().isoformat()
            if user_record:
                cursor.execute('UPDATE user_tracking SET generate_count = generate_count + 1, last_generated = ?, ip_address = ?, user_agent = ? WHERE id = ?', (now_str, client_ip, user_agent, tracking_id))
            else:
                cursor.execute('INSERT INTO user_tracking (id, ip_address, user_agent, generate_count, last_generated) VALUES (?, ?, ?, 1, ?)', (tracking_id, client_ip, user_agent, now_str))
            conn.commit()
            conn.close()
            api_response = jsonify({
                "success": True,
                "transaction_id": h,
                "pix_copy_paste": emv,
                "qr_code_base64": base64_image
            })
            res = make_response(api_response)
            res.set_cookie('bastion_session_id', tracking_id, max_age=31536000, httponly=True)
            return res
        else:
            conn.close()
            return jsonify({
                "success": False,
                "error": "Falha ao extrair a linha digitável do Pix na resposta da API.",
                "details": data
            }), 400
    except requests.exceptions.RequestException as e:
        conn.close()
        return jsonify({
            "success": False,
            "error": "Erro de comunicação com a API de pagamento.",
            "details": str(e)
        }), 502
    except Exception as e:
        conn.close()
        return jsonify({
            "success": False,
            "error": "Erro interno no servidor.",
            "details": str(e)
        }), 500

@app.route('/api/webhook/plumify', methods=['POST'])
def plumify_webhook():
    data = request.get_json(silent=True) or {}
    transaction_hash = data.get('data', {}).get('hash') or data.get('hash') or data.get('transaction_id') or data.get('id')
    status = data.get('status') or data.get('data', {}).get('status') or data.get('event') or data.get('payment_status')
    status_lower = str(status).lower() if status else ""
    if status_lower in PAID_STATUSES and transaction_hash:
        customer = data.get('data', {}).get('customer') or data.get('customer') or {}
        email = customer.get('email')
        phone = customer.get('phone_number')
        send_purchase_capi(transaction_hash, value=20.96, currency="BRL", email=email, phone=phone)
    return jsonify({"success": True}), 200

@app.route('/seyzalel_panel')
def seyzalel_panel():
    return render_template('yourdata.html')

@app.route('/api/admin/tracking-data', methods=['GET'])
def get_tracking_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM user_tracking ORDER BY last_generated DESC')
    rows = cursor.fetchall()
    conn.close()
    data = []
    for row in rows:
        data.append(dict(row))
    return jsonify({"success": True, "data": data})

@app.route('/api/admin/block-user', methods=['POST'])
def block_user():
    payload = request.get_json(silent=True) or {}
    tracking_id = payload.get('id')
    hours = payload.get('hours', 0)
    if not tracking_id:
        return jsonify({"success": False, "error": "ID não fornecido"}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    if hours > 0:
        blocked_until = (datetime.now() + timedelta(hours=hours)).isoformat()
        cursor.execute('UPDATE user_tracking SET blocked_until = ? WHERE id = ?', (blocked_until, tracking_id))
    else:
        cursor.execute('UPDATE user_tracking SET blocked_until = NULL WHERE id = ?', (tracking_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
