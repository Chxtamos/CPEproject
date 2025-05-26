import cv2
import time
import board
import adafruit_dht
from ultralytics import YOLO
from deep_sort_realtime.deepsort_tracker import DeepSort
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta
from DFRobot_RaspberryPi_A02YYUW import DFRobot_A02_Distance as DistanceSensor
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306 # ใช้ driver สำหรับ SSD1306


I2C_ADDRESS = 0x3C # Address ของจอ OLED
OLED_WIDTH = 128
OLED_HEIGHT = 32 

Serial Interface (I2C) 
print("Initializing I2C serial interface for OLED...")
oled_device = None
try:
    oled_serial = i2c(port=1, address=I2C_ADDRESS)
    oled_device = ssd1306(oled_serial, width=OLED_WIDTH, height=OLED_HEIGHT)
    print("OLED display initialized successfully.")
    # เคลียร์จอ OLED ตอนเริ่ม
    with canvas(oled_device) as draw:
        draw.rectangle(oled_device.bounding_box, outline="black", fill="black")
except Exception as e:
    print(f"Error initializing OLED display: {e}")
    print("OLED display will not be used.")


sensor_board = DistanceSensor()
sensor_board.set_dis_range(0, 4500)

def get_distance_mm():
    distance = sensor_board.getDistance()
    if sensor_board.last_operate_status == sensor_board.STA_OK:
        return distance
    else:
        print("[⚠️] อ่านระยะไม่สำเร็จ - ใช้ค่าปริยาย 100 mm")
        return 100

# โหลดโมเดล YOLOv8 
model = YOLO(r"input your model")

# สร้าง tracker DeepSort
tracker = DeepSort(max_age=5)

thai_timezone = timezone(timedelta(hours=7))

# ตั้งค่าเซ็นเซอร์ DHT11 ที่ขา GPIO17
dht_device = adafruit_dht.DHT11(board.D17)

# เชื่อมต่อ MongoDB
client = MongoClient("input your uri")
db = client["mushroom_db"]
collection = db["mushroom_data"]

# เปิดกล้อง
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Unable to open camera!")
    exit()

# พารามิเตอร์กล้อง
sensor_width_mm = 8.46666582 
image_width_px = 640          
focal_length_mm = 3.2         

last_log_time = 0
log_interval = 3  # วินาที

#เพิ่มตัวแปรสำหรับควบคุมการอัปเดตจอ OLED 
last_oled_update_time = 0
oled_update_interval = 1


locked_ids = {}
next_locked_id = 1
latest_mushroom_data = {}

frame_count = 0

print("Starting mushroom detection and sensor reading...")

#เพิ่มตัวแปรสำหรับเก็บค่า T/H ล่าสุด
current_temp = None
current_humidity = None

def read_dht_sensor(dht_device):
    try:
        temperature_c = dht_device.temperature
        humidity = dht_device.humidity
        if temperature_c is None or humidity is None:
            raise RuntimeError("Sensor returned None")
        return temperature_c, humidity
    except RuntimeError as error:
        print(f"[Warning] DHT11 อ่านค่าไม่สำเร็จ: {error}")
        return None, None

try:
    while True:
        frame_count += 1
        ret, frame = cap.read()
        if not ret:
            print("Camera read error.")
            time.sleep(1)
            continue

        print(f"[Info] อ่านภาพรอบที่ {frame_count}")

        # ปรับขนาดภาพให้ตรงกับโมเดล
        frame = cv2.resize(frame, (640, 384))
        height, width, _ = frame.shape

        # อ่านระยะจากเซ็นเซอร์ A02YYUW
        distance_mm = get_distance_mm()

        # ตรวจจับเห็ดด้วย YOLO
        results = model(frame)[0]

        detections = []
        for r in results.boxes.data.cpu().numpy():
            x1, y1, x2, y2, score, class_id = r
            class_id = int(class_id)
            if class_id == 0 and score > 0.3:
                x1c = max(0, int(x1))
                y1c = max(0, int(y1))
                x2c = min(width - 1, int(x2))
                y2c = min(height - 1, int(y2))
                w = x2c - x1c
                h = y2c - y1c
                if w <= 0 or h <= 0:
                    continue
                bbox = [x1c, y1c, w, h]
                detections.append((bbox, score, class_id))

        # ติดตามด้วย DeepSort
        tracks = tracker.update_tracks(detections, frame=frame)
        now = time.time()

        for track in tracks:
            if not track.is_confirmed():
                continue

            track_id = track.track_id
            if track_id not in locked_ids:
                locked_ids[track_id] = next_locked_id
                next_locked_id += 1
            locked_id = locked_ids[track_id]

            x1, y1, x2, y2 = map(int, track.to_tlbr())
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            width_box_px = x2 - x1
            height_box_px = y2 - y1

            pixel_size_mm = sensor_width_mm / image_width_px  
            bbox_width_mm = width_box_px * pixel_size_mm
            object_real_width_mm = (bbox_width_mm * distance_mm) / focal_length_mm
            object_real_width_cm = object_real_width_mm / 10

            maturity_label = "mature" if 1.5 <= object_real_width_cm <= 2 else "immature"

            label_text = f'ID:{locked_id} Size:{object_real_width_cm:.2f}cm ({maturity_label})'

            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame, label_text, (x1, y1 - 10),
                                 cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            latest_mushroom_data[locked_id] = {
                "mushroom_id": int(locked_id),
                "maturity_status": maturity_label,
                "real_size_cm": object_real_width_cm,
                "timestamp": datetime.now(tz=thai_timezone).isoformat(),
                "camera_id": "cam01",
                "location": "shelf_3"
            }

            print(f"Track ID: {track_id} (Locked ID: {locked_id}), Center: ({center_x}, {center_y}), "
                          f"Size(px): ({width_box_px}x{height_box_px}), Real Width: {object_real_width_cm:.2f} cm")

        # ถ้าถึงเวลา log และมีข้อมูล
        if now - last_log_time >= log_interval and latest_mushroom_data:
            print(f"เวลาเกิน {log_interval} วิ - เตรียมส่งข้อมูลเห็ดจำนวน {len(latest_mushroom_data)}")

            temperature_c, humidity = read_dht_sensor(dht_device)
            # อัปเดตค่า ล่าสุดเพื่อใช้แสดงบน OLED
            current_temp = temperature_c
            current_humidity = humidity
           

            if temperature_c is not None and humidity is not None:
                print(f"อ่านค่าได้ Temp: {temperature_c}°C, Humidity: {humidity}%")
                docs_to_insert = []
                for data in latest_mushroom_data.values():
                    doc = data.copy()
                    doc["temperature_c"] = temperature_c
                    doc["humidity_percent"] = humidity
                    docs_to_insert.append(doc)
                try:
                    result = collection.insert_many(docs_to_insert)
                    if result.acknowledged:
                        print(f"[✅] ส่งข้อมูลสำเร็จ จำนวน {len(result.inserted_ids)} รายการ")
                    else:
                        print("[⚠️] MongoDB ไม่ตอบรับการบันทึกข้อมูล")
                    for d in docs_to_insert:
                        print(d)
                except Exception as e:
                    print(f"[❌] เกิดข้อผิดพลาดในการส่งข้อมูลเข้า MongoDB: {e}")
            else:
                print("[Warning] ข้ามบันทึกข้อมูลเพราะเซ็นเซอร์อ่านค่าไม่สมบูรณ์")

            last_log_time = now

        # เพิ่มส่วนแสดงผลบน OLED สำหรับอุณหภูมิและความชื้นเท่านั้น
        if oled_device is not None and now - last_oled_update_time >= oled_update_interval:
            with canvas(oled_device) as draw:
                # อุณหภูมิ
                temp_str = f"Temp: {current_temp:.1f} C" if current_temp is not None else "Temp: N/A C"
                draw.text((0, 0), temp_str, fill="white")

                # ความชื้น
                humi_str = f"Humi: {current_humidity:.1f} %" if current_humidity is not None else "Humi: N/A %"
                
                # ตำแหน่ง y สำหรับบรรทัดที่ 2
                if OLED_HEIGHT == 32:
                    # สำหรับ 128x32, วางบรรทัดที่ 2 ที่ y=16 (กลางๆ จอ)
                    draw.text((0, 16), humi_str, fill="white") 
                elif OLED_HEIGHT == 64:
                    # สำหรับ 128x64, วางบรรทัดที่ 2 ที่ y=20 (เว้นระยะจากบรรทัดแรก)
                    draw.text((0, 20), humi_str, fill="white")

            last_oled_update_time = now # อัปเดตเวลาที่ OLED ล่าสุด
    

        time.sleep(0.05)

except KeyboardInterrupt:
    print("Program interrupted by user")

finally:
    cap.release()
    # เพิ่มส่วนเคลียร์จอ OLED เมื่อโปรแกรมหยุด
    if oled_device is not None:
        with canvas(oled_device) as draw:
            draw.rectangle(oled_device.bounding_box, outline="black", fill="black")
    # ---