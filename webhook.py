import time
import requests
from flask import Flask, request
from pymongo import MongoClient
from datetime import datetime, timedelta
from dateutil.parser import parse
import threading

LINE_ACCESS_TOKEN = "LineToken"
MONGO_URI = "mongoDB uri"
DB_NAME = "mushroom_db"
COLLECTION_NAME = "mushroom_data"

app = Flask(__name__)

client = MongoClient(MONGO_URI)
collection = client[DB_NAME][COLLECTION_NAME]

# Quick Reply Buttons ปุ้มกด
QUICK_REPLY_ITEMS = [
    {"type": "action", "action": {"type": "message", "label": "สถานะเห็ด", "text": "สถานะเห็ด"}},
    {"type": "action", "action": {"type": "message", "label": "อุณหภูมิ", "text": "อุณหภูมิ"}},
    {"type": "action", "action": {"type": "message", "label": "ความชื้น", "text": "ความชื้น"}},
    {"type": "action", "action": {"type": "message", "label": "ช่วยเหลือ", "text": "ช่วยเหลือ"}},
]

def send_line_reply(reply_token, text):
    """
    ส่งข้อความตอบกลับพร้อม Quick Reply
    """
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    data = {
        "replyToken": reply_token,
        "messages": [{
            "type": "text",
            "text": text,
            "quickReply": {"items": QUICK_REPLY_ITEMS}
        }]
    }
    try:
        response = requests.post(url, json=data, headers=headers)
        print(f"LINE reply status: {response.status_code} {response.text}")
    except Exception as e:
        print(f"Error sending LINE reply: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print("Received event:", data)

    for event in data.get("events", []):
        if event.get("type") == "message":
            user_text = event["message"]["text"].strip().lower()
            reply_token = event["replyToken"]

            if user_text == "สถานะเห็ด":
                handle_status(reply_token)

            elif user_text in ["อุณหภูมิ", "ความชื้น"]:
                handle_latest_env(reply_token, user_text)

            elif user_text in ["อุณหภูมิย้อนหลัง", "ความชื้นย้อนหลัง"]:
                handle_env_history(reply_token, user_text)

            elif user_text == "ช่วยเหลือ":
                send_line_reply(reply_token, "☎️ ติดต่อสอบถามได้ที่: 0948741544")

            else:
                send_line_reply(reply_token,
                    "คำสั่งที่สามารถใช้ได้:\n"
                    "- สถานะเห็ด\n"
                    "- อุณหภูมิ\n"
                    "- ความชื้น\n"
                    "- อุณหภูมิย้อนหลัง\n"
                    "- ความชื้นย้อนหลัง\n"
                    "- ช่วยเหลือ"
                )
    return "OK", 200

def handle_status(reply_token):
    timestamps = collection.distinct("timestamp")
    if not timestamps or len(timestamps) < 2:
        send_line_reply(reply_token, "❌ ไม่มีข้อมูลเห็ดเพียงพอในฐานข้อมูล")
        return
    
    timestamps_dt = sorted([parse(ts) for ts in timestamps], reverse=True)
    target_ts = timestamps_dt[1]  # เลือก timestamp ก่อนล่าสุด

    start_time = target_ts - timedelta(milliseconds=100)
    end_time = target_ts + timedelta(milliseconds=100)

    latest_docs = list(collection.find({
        "timestamp": {"$gte": start_time.isoformat(), "$lte": end_time.isoformat()}
    }))

    if not latest_docs:
        send_line_reply(reply_token, "❌ ไม่พบข้อมูลเห็ดในช่วงเวลาที่กำหนด")
        return

    mature_count = sum(1 for doc in latest_docs if doc.get("maturity_status", "").lower() == "mature")
    immature_count = len(latest_docs) - mature_count

    total = mature_count + immature_count
    if total == 0:
        reply_text = "❌ ไม่พบข้อมูลสถานะเห็ด"
    elif mature_count == total:
        reply_text = f"🍄 พร้อมเก็บทุกดอก\n⏰ เวลา: {target_ts.strftime('%Y-%m-%d %H:%M:%S')}"
    else:
        reply_text = (
            f"🍄 พร้อมเก็บ: {mature_count} ดอก\n"
            f"⏳ ยังไม่พร้อม: {immature_count} ดอก\n"
            f"⏰ เวลา: {target_ts.strftime('%Y-%m-%d %H:%M:%S')}"
        )
    send_line_reply(reply_token, reply_text)

def handle_latest_env(reply_token, user_text):
    latest_doc = collection.find_one(sort=[("timestamp", -1)])
    if not latest_doc:
        send_line_reply(reply_token, "❌ ไม่มีข้อมูลล่าสุด")
        return

    target_ts_str = latest_doc["timestamp"]
    target_ts = parse(target_ts_str)
    docs = list(collection.find({"timestamp": target_ts_str}))
    if not docs:
        send_line_reply(reply_token, "❌ ไม่มีข้อมูลล่าสุด")
        return

    avg_temp = sum(float(doc.get("temperature_c", 0)) for doc in docs) / len(docs)
    avg_humidity = sum(float(doc.get("humidity_percent", 0)) for doc in docs) / len(docs)

    if user_text == "อุณหภูมิ":
        reply_text = f"🌡️ อุณหภูมิเฉลี่ย ({target_ts.strftime('%Y-%m-%d %H:%M:%S')}): {avg_temp:.2f}°C"
    else:
        reply_text = f"💦 ความชื้นเฉลี่ย ({target_ts.strftime('%Y-%m-%d %H:%M:%S')}): {avg_humidity:.2f}%"

    send_line_reply(reply_token, reply_text)

def handle_env_history(reply_token, user_text):
    now = datetime.utcnow()
    past_3_days = now - timedelta(days=3)

    docs = list(collection.find({
        "timestamp": {"$gte": past_3_days.isoformat(), "$lte": now.isoformat()}
    }))

    if not docs:
        send_line_reply(reply_token, f"❌ ไม่มีข้อมูล{user_text}ย้อนหลัง 3 วัน")
        return

    if user_text == "อุณหภูมิย้อนหลัง":
        values = [float(doc.get("temperature_c", 0)) for doc in docs]
        metric_name = "อุณหภูมิ (°C)"
    else:
        values = [float(doc.get("humidity_percent", 0)) for doc in docs]
        metric_name = "ความชื้น (%)"

    avg_val = sum(values) / len(values)
    max_val = max(values)
    min_val = min(values)

    reply_text = (
        f"📊 {metric_name}ย้อนหลัง 3 วัน\n"
        f"เฉลี่ย: {avg_val:.2f}\n"
        f"สูงสุด: {max_val:.2f}\n"
        f"ต่ำสุด: {min_val:.2f}"
    )
    send_line_reply(reply_token, reply_text)


# ค่าอุณหภูมิและความชื้น
TEMP_HIGH_THRESHOLD = 35.0
TEMP_LOW_THRESHOLD = 15.0
HUMIDITY_HIGH_THRESHOLD = 80.0
HUMIDITY_LOW_THRESHOLD = 50.0
ALERT_INTERVAL = 600  # วินาทีระหว่างแจ้งเตือนซ้ำ

last_alert_time = None

def send_line_broadcast(message_text):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "messages": [{"type": "text", "text": message_text}]
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        print("LINE broadcast:", response.status_code, response.text)
    except Exception as e:
        print("❌ Error broadcasting to LINE:", e)

def check_environment():
    global last_alert_time
    while True:
        try:
            latest_doc = collection.find_one(sort=[("timestamp", -1)])
            if not latest_doc:
                print("ℹ️ No document found.")
                time.sleep(ALERT_INTERVAL)
                continue

            ts_str = latest_doc.get("timestamp")
            timestamp = parse(ts_str)
            if last_alert_time and (timestamp - last_alert_time).total_seconds() < ALERT_INTERVAL:
                time.sleep(ALERT_INTERVAL)
                continue

            temp = float(latest_doc.get("temperature_c", 0))
            humidity = float(latest_doc.get("humidity_percent", 0))

            alerts = []

            if temp > TEMP_HIGH_THRESHOLD:
                alerts.append(f"🔥 อุณหภูมิสูงเกิน {TEMP_HIGH_THRESHOLD}°C\n🌡️ ตอนนี้: {temp:.1f}°C")
            elif temp < TEMP_LOW_THRESHOLD:
                alerts.append(f"❄️ อุณหภูมิต่ำเกิน {TEMP_LOW_THRESHOLD}°C\n🌡️ ตอนนี้: {temp:.1f}°C")

            if humidity > HUMIDITY_HIGH_THRESHOLD:
                alerts.append(f"💧 ความชื้นสูงเกิน {HUMIDITY_HIGH_THRESHOLD}%\n💦 ตอนนี้: {humidity:.1f}%")
            elif humidity < HUMIDITY_LOW_THRESHOLD:
                alerts.append(f"🌫️ ความชื้นต่ำเกิน {HUMIDITY_LOW_THRESHOLD}%\n💦 ตอนนี้: {humidity:.1f}%")

            if alerts:
                msg = "⚠️ แจ้งเตือนสภาพแวดล้อมไม่เหมาะสม\n\n" + "\n\n".join(alerts) + f"\n\n⏰ เวลา: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
                send_line_broadcast(msg)
                last_alert_time = timestamp

        except Exception as e:
            print("❌ Error in check_environment:", e)

        time.sleep(ALERT_INTERVAL)

# เริ่มเธรด background สำหรับระบบแจ้งเตือน
threading.Thread(target=check_environment, daemon=True).start()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
