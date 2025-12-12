import os
import asyncio
from datetime import datetime
from quart import Quart, render_template, request, jsonify
from telethon import TelegramClient as TelethonClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from pyrogram import Client as PyrogramClient
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid
from motor.motor_asyncio import AsyncIOMotorClient
import random
import string

app = Quart(__name__)

# --- DATABASE CONNECTION (MongoDB) ---
# Replace with your actual MongoDB URL from Atlas
MONGO_URL = os.environ.get("MONGO_URL", "mongodb+srv://YOUR_USER:YOUR_PASS@cluster.mongodb.net/?retryWrites=true&w=majority")
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["StringGenBot"]
collection = db["history"]

# In-Memory Storage (Note: On server restart, this clears. Good for security.)
TEMP_CLIENTS = {}

def generate_id():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=16))

@app.route('/')
async def index():
    return await render_template('index.html')

@app.route('/api/send_otp', methods=['POST'])
async def send_otp():
    data = await request.get_json()
    session_id = generate_id()
    
    try:
        if data['lib'] == 'telethon':
            client = TelethonClient(
                StringSession(), 
                data['api_id'], 
                data['api_hash'], 
                device_model="StringGen Glass",
                app_version=data['version']
            )
            await client.connect()
            
            if not await client.is_user_authorized():
                await client.send_code_request(data['phone'])
                TEMP_CLIENTS[session_id] = {'client': client, 'lib': 'telethon', 'phone': data['phone'], 'api_id': data['api_id']}
            else:
                return jsonify({'error': 'Account already logged in!'}), 400

        elif data['lib'] == 'pyrogram':
            client = PyrogramClient(
                f"sess_{session_id}", 
                data['api_id'], 
                data['api_hash'], 
                in_memory=True,
                device_model="StringGen Glass",
                app_version=data['version']
            )
            await client.connect()
            sent_code = await client.send_code(data['phone'])
            TEMP_CLIENTS[session_id] = {
                'client': client, 
                'lib': 'pyrogram', 
                'phone': data['phone'], 
                'ph_hash': sent_code.phone_code_hash,
                'api_id': data['api_id']
            }

        return jsonify({'status': 'success', 'session_id': session_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/verify_otp', methods=['POST'])
async def verify_otp():
    data = await request.get_json()
    session_id = data.get('session_id')
    
    if session_id not in TEMP_CLIENTS:
        return jsonify({'error': 'Session expired. Please refresh.'}), 400

    sess_data = TEMP_CLIENTS[session_id]
    client = sess_data['client']
    
    try:
        if sess_data['lib'] == 'telethon':
            try:
                await client.sign_in(sess_data['phone'], data['code'])
            except SessionPasswordNeededError:
                return jsonify({'status': '2fa_required'})
            except PhoneCodeInvalidError:
                return jsonify({'error': 'Invalid OTP Code'})

        elif sess_data['lib'] == 'pyrogram':
            try:
                await client.sign_in(sess_data['phone'], sess_data['ph_hash'], data['code'])
            except SessionPasswordNeeded:
                return jsonify({'status': '2fa_required'})
            except PhoneCodeInvalid:
                return jsonify({'error': 'Invalid OTP Code'})

        return await generate_string(session_id)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/verify_password', methods=['POST'])
async def verify_password():
    data = await request.get_json()
    session_id = data.get('session_id')

    if session_id not in TEMP_CLIENTS:
        return jsonify({'error': 'Session expired.'}), 400

    sess_data = TEMP_CLIENTS[session_id]
    client = sess_data['client']

    try:
        if sess_data['lib'] == 'telethon':
            await client.sign_in(password=data['password'])
        elif sess_data['lib'] == 'pyrogram':
            await client.check_password(data['password'])
        
        return await generate_string(session_id)

    except Exception as e:
        return jsonify({'error': 'Incorrect Password'}), 400

async def generate_string(session_id):
    sess_data = TEMP_CLIENTS[session_id]
    client = sess_data['client']
    
    session_string = ""
    
    # Generate String
    if sess_data['lib'] == 'telethon':
        session_string = client.session.save()
        msg_text = f"**Telethon Session Generated** 🔮\n\n`{session_string}`\n\nGenerated via GlassGen Web."
        await client.send_message('me', msg_text)
        await client.disconnect()
    
    elif sess_data['lib'] == 'pyrogram':
        session_string = await client.export_session_string()
        msg_text = f"**Pyrogram Session Generated** 🔮\n\n`{session_string}`\n\nGenerated via GlassGen Web."
        await client.send_message('me', msg_text)
        await client.disconnect()

    # Save Log to MongoDB
    await collection.insert_one({
        "type": sess_data['lib'],
        "api_id": sess_data['api_id'],
        "date": datetime.now()
    })

    del TEMP_CLIENTS[session_id]
    return jsonify({'status': 'success', 'string': session_string})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
  
