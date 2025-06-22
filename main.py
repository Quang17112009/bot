import sqlite3
import time
import threading
import json
import requests
import os
import logging
from datetime import datetime, timedelta
import uuid
import random
import re
from flask import Flask # Import Flask cho keep-alive

# CẤU HÌNH CHÍNH
USER_STATES = {}  # Lưu trữ trạng thái từng người dùng
PREDICTION_HISTORY = []
ADMIN_ACTIVE = True
BOT_VERSION = "5.1" # Cập nhật phiên bản bot

# === CẤU HÌNH TELEGRAM ===
# Gắn trực tiếp BOT_TOKEN vào đây
BOT_TOKEN = "7820739987:AAE_eU2JPZH7u6KnDRq31_l4tn64AD_8f6s" 
if not BOT_TOKEN:
    print("!!! Lỗi: Vui lòng cấu hình biến môi trường BOT_TOKEN !!!")
    logging.error("Lỗi: Vui lòng cấu hình biến môi trường BOT_TOKEN")
    exit(1)

# === CẤU HÌNH API ===
TAIXIU_API_URL = "https://wanglinapiws.up.railway.app/api/taixiu"
LAST_FETCHED_SESSION_ID = None # Lưu trữ ID phiên cuối cùng đã xử lý

# === BIỂU TƯỢNG EMOJI ===
EMOJI = {
    "dice": "🎲", "money": "💰", "chart": "📊", "clock": "⏱️", "bell": "🔔", "rocket": "🚀",
    "warning": "⚠️", "trophy": "🏆", "fire": "🔥", "up": "📈", "down": "📉", "right": "↪️",
    "left": "↩️", "check": "✅", "cross": "❌", "star": "⭐", "medal": "🏅", "id": "🆔",
    "sum": "🧮", "prediction": "🔮", "trend": "📶", "history": "🔄", "pattern": "🧩",
    "settings": "⚙️", "vip": "💎", "team": "👥", "ae": "🔷", "key": "🔑", "admin": "🛡️",
    "play": "▶️", "pause": "⏸️", "add": "➕", "list": "📜", "delete": "🗑️",
    "infinity": "♾️", "calendar": "📅", "streak": "🔥", "analysis": "🔍",
    "heart": "❤️", "diamond": "♦️", "spade": "♠️", "club": "♣️", "luck": "🍀",
    "money_bag": "💰", "crown": "👑", "shield": "🛡", "zap": "⚡", "target": "🎯",
    "broadcast": "📢", "info": "ℹ️", "users": "👤"
}

# === THUẬT TOÁN PATTERN ANALYSIS NÂNG CAO ===
PATTERN_DATA = {
    # Các pattern cơ bản
    "tttt": {"tai": 73, "xiu": 27}, "xxxx": {"tai": 27, "xiu": 73},
    "tttttt": {"tai": 83, "xiu": 17}, "xxxxxx": {"tai": 17, "xiu": 83},
    "ttttx": {"tai": 40, "xiu": 60}, "xxxxt": {"tai": 60, "xiu": 40},
    "ttttttx": {"tai": 30, "xiu": 70}, "xxxxxxt": {"tai": 70, "xiu": 30},
    "ttxx": {"tai": 62, "xiu": 38}, "xxtt": {"tai": 38, "xiu": 62},
    "ttxxtt": {"tai": 32, "xiu": 68}, "xxttxx": {"tai": 68, "xiu": 32},
    "txx": {"tai": 60, "xiu": 40}, "xtt": {"tai": 40, "xiu": 60},
    "txxtx": {"tai": 63, "xiu": 37}, "xttxt": {"tai": 37, "xiu": 63},
    "tttxt": {"tai": 60, "xiu": 40}, "xxxtx": {"tai": 40, "xiu": 60},
    "tttxx": {"tai": 60, "xiu": 40}, "xxxtt": {"tai": 40, "xiu": 60},
    "txxt": {"tai": 60, "xiu": 40}, "xttx": {"tai": 40, "xiu": 60},
    "ttxxttx": {"tai": 30, "xiu": 70}, "xxttxxt": {"tai": 70, "xiu": 30},
    
    # Bổ sung pattern cầu lớn (chuỗi dài)
    "tttttttt": {"tai": 88, "xiu": 12}, "xxxxxxxx": {"tai": 12, "xiu": 88},
    "tttttttx": {"tai": 25, "xiu": 75}, "xxxxxxxxt": {"tai": 75, "xiu": 25},
    "tttttxxx": {"tai": 35, "xiu": 65}, "xxxxtttt": {"tai": 65, "xiu": 35},
    "ttttxxxx": {"tai": 30, "xiu": 70}, "xxxxtttx": {"tai": 70, "xiu": 30},
    
    # Pattern đặc biệt cho Sunwin
    "txtxtx": {"tai": 68, "xiu": 32}, "xtxtxt": {"tai": 32, "xiu": 68},
    "ttxtxt": {"tai": 55, "xiu": 45}, "xxtxtx": {"tai": 45, "xiu": 55},
    "txtxxt": {"tai": 60, "xiu": 40}, "xtxttx": {"tai": 40, "xiu": 60},
    
    # Thêm các pattern mới nâng cao
    "ttx": {"tai": 65, "xiu": 35}, "xxt": {"tai": 35, "xiu": 65},
    "txt": {"tai": 58, "xiu": 42}, "xtx": {"tai": 42, "xiu": 58},
    "tttx": {"tai": 70, "xiu": 30}, "xxxt": {"tai": 30, "xiu": 70},
    "ttxt": {"tai": 63, "xiu": 37}, "xxtx": {"tai": 37, "xiu": 63},
    "txxx": {"tai": 25, "xiu": 75}, "xttt": {"tai": 75, "xiu": 25},
    "tttxx": {"tai": 60, "xiu": 40}, "xxxtt": {"tai": 40, "xiu": 60},
    "ttxtx": {"tai": 62, "xiu": 38}, "xxtxt": {"tai": 38, "xiu": 62},
    "ttxxt": {"tai": 55, "xiu": 45}, "xxttx": {"tai": 45, "xiu": 55},
    "ttttx": {"tai": 40, "xiu": 60}, "xxxxt": {"tai": 60, "xiu": 40},
    "tttttx": {"tai": 30, "xiu": 70}, "xxxxxt": {"tai": 70, "xiu": 30},
    "ttttttx": {"tai": 25, "xiu": 75}, "xxxxxxt": {"tai": 75, "xiu": 25},
    "tttttttx": {"tai": 20, "xiu": 80}, "xxxxxxxt": {"tai": 80, "xiu": 20},
    "ttttttttx": {"tai": 15, "xiu": 85}, "xxxxxxxxt": {"tai": 85, "xiu": 15},
    
    # Pattern đặc biệt zigzag
    "txtx": {"tai": 52, "xiu": 48}, "xtxt": {"tai": 48, "xiu": 52},
    "txtxt": {"tai": 53, "xiu": 47}, "xtxtx": {"tai": 47, "xiu": 53},
    "txtxtx": {"tai": 55, "xiu": 45}, "xtxtxt": {"tai": 45, "xiu": 55},
    "txtxtxt": {"tai": 57, "xiu": 43}, "xtxtxtx": {"tai": 43, "xiu": 57},
    
    # Pattern đặc biệt kết hợp
    "ttxxttxx": {"tai": 38, "xiu": 62}, "xxttxxtt": {"tai": 62, "xiu": 38},
    "ttxxxttx": {"tai": 45, "xiu": 55}, "xxttxxxt": {"tai": 55, "xiu": 45},
    "ttxtxttx": {"tai": 50, "xiu": 50}, "xxtxtxxt": {"tai": 50, "xiu": 50},
    
    # Thêm các pattern mới cực ngon
    "ttxttx": {"tai": 60, "xiu": 40}, "xxtxxt": {"tai": 40, "xiu": 60},
    "ttxxtx": {"tai": 58, "xiu": 42}, "xxtxxt": {"tai": 42, "xiu": 58},
    "ttxtxtx": {"tai": 62, "xiu": 38}, "xxtxtxt": {"tai": 38, "xiu": 62},
    "ttxxtxt": {"tai": 55, "xiu": 45}, "xxtxttx": {"tai": 45, "xiu": 55},
    "ttxtxxt": {"tai": 65, "xiu": 35}, "xxtxttx": {"tai": 35, "xiu": 65},
    "ttxtxttx": {"tai": 70, "xiu": 30}, "xxtxtxxt": {"tai": 30, "xiu": 70},
    "ttxxtxtx": {"tai": 68, "xiu": 32}, "xxtxtxtx": {"tai": 32, "xiu": 68},
    "ttxtxxtx": {"tai": 72, "xiu": 28}, "xxtxtxxt": {"tai": 28, "xiu": 72},
    "ttxxtxxt": {"tai": 75, "xiu": 25}, "xxtxtxxt": {"tai": 25, "xiu": 75},
}

# Dữ liệu thống kê cầu lớn từ Sunwin
BIG_STREAK_DATA = {
    "Tài": {
        "3": {"next_tai": 65, "next_xiu": 35},
        "4": {"next_tai": 70, "next_xiu": 30},
        "5": {"next_tai": 75, "next_xiu": 25},
        "6": {"next_tai": 80, "next_xiu": 20},
        "7": {"next_tai": 85, "next_xiu": 15},
        "8": {"next_tai": 88, "next_xiu": 12},
        "9": {"next_tai": 90, "next_xiu": 10},
        "10+": {"next_tai": 92, "next_xiu": 8}
    },
    "Xỉu": {
        "3": {"next_tai": 35, "next_xiu": 65},
        "4": {"next_tai": 30, "next_xiu": 70},
        "5": {"next_tai": 25, "next_xiu": 75},
        "6": {"next_tai": 20, "next_xiu": 80},
        "7": {"next_tai": 15, "next_xiu": 85},
        "8": {"next_tai": 12, "next_xiu": 88},
        "9": {"next_tai": 10, "next_xiu": 90},
        "10+": {"next_tai": 8, "next_xiu": 92}
    }
}

# Dữ liệu thống kê theo tổng điểm
SUM_STATS = {
    "3-10": {"tai": 0, "xiu": 100},  # Xỉu 100%
    "11": {"tai": 15, "xiu": 85},
    "12": {"tai": 25, "xiu": 75},
    "13": {"tai": 40, "xiu": 60},
    "14": {"tai": 50, "xiu": 50},
    "15": {"tai": 60, "xiu": 40},
    "16": {"tai": 75, "xiu": 25},
    "17": {"tai": 85, "xiu": 15},
    "18": {"tai": 100, "xiu": 0}     # Tài 100%
}

def find_closest_pattern(input_pattern_oldest_first):
    best_key_match = None
    longest_len = 0
    if not input_pattern_oldest_first:
        return None
    
    # Ưu tiên tìm pattern dài nhất khớp với lịch sử
    for key in sorted(PATTERN_DATA.keys(), key=len, reverse=True):
        if input_pattern_oldest_first.endswith(key):
            return key
    
    return None

def analyze_big_streak(history):
    if len(history) < 2:
        return None, 0
    
    current_streak = 1
    current_result = history[0]["result"]
    
    for i in range(1, len(history)):
        if history[i]["result"] == current_result:
            current_streak += 1
        else:
            break
    
    if current_streak >= 3:  # Xét cầu từ 3 nút trở lên
        streak_key = str(current_streak) if current_streak <= 9 else "10+"
        stats = BIG_STREAK_DATA[current_result].get(streak_key, None)
        if stats:
            if stats["next_tai"] > stats["next_xiu"]:
                return "Tài", stats["next_tai"]
            else:
                return "Xỉu", stats["next_xiu"]
    return None, 0

def analyze_sum_trend(history):
    if not history:
        return None, 0
    
    last_sum = history[0]["total"]
    
    # Chuyển đổi tổng điểm thành chuỗi key cho SUM_STATS
    sum_key = str(last_sum)
    if last_sum >= 3 and last_sum <= 10:
        sum_key = "3-10"
    elif last_sum >= 18: # Đảm bảo tổng 18 là Tài 100%
        sum_key = "18"
    
    sum_stats = SUM_STATS.get(sum_key, None)
    
    if sum_stats:
        if sum_stats["tai"] == 100:
            return "Tài", 95
        elif sum_stats["xiu"] == 100:
            return "Xỉu", 95
        elif sum_stats["tai"] > sum_stats["xiu"]:
            return "Tài", sum_stats["tai"]
        else:
            return "Xỉu", sum_stats["xiu"]
    
    return None, 0

def pattern_predict(history):
    if not history:
        return "Tài", 50  # Dự đoán mặc định nếu không có lịch sử
    
    # 1. Phân tích cầu lớn trước (ưu tiên cao nhất)
    streak_prediction, streak_confidence = analyze_big_streak(history)
    if streak_prediction and streak_confidence > 75:
        return streak_prediction, streak_confidence
    
    # 2. Phân tích theo tổng điểm (ưu tiên thứ hai)
    sum_prediction, sum_confidence = analyze_sum_trend(history)
    if sum_prediction and sum_confidence > 80:
        return sum_prediction, sum_confidence
    
    # 3. Phân tích pattern thông thường
    elements = [("t" if s["result"] == "Tài" else "x") for s in history[:15]]  # Xét 15 phiên gần nhất
    current_pattern_str = "".join(reversed(elements))
    closest_pattern_key = find_closest_pattern(current_pattern_str)
    
    if closest_pattern_key:
        data = PATTERN_DATA[closest_pattern_key]
        if data["tai"] == data["xiu"]:
            # Nếu tỷ lệ bằng nhau, xét tổng điểm gần nhất
            last_session = history[0]
            if last_session["total"] >= 11:
                return "Tài", 55
            else:
                return "Xỉu", 55
        else:
            prediction = "Tài" if data["tai"] > data["xiu"] else "Xỉu"
            confidence = max(data["tai"], data["xiu"])
            return prediction, confidence
    else:
        # Nếu không tìm thấy pattern phù hợp, dựa vào tổng điểm gần nhất
        last_session = history[0]
        if last_session["total"] >= 11:
            return "Tài", 55
        else:
            return "Xỉu", 55

# === LOGGING ===
logging.basicConfig(filename="bot_detailed_log.txt", level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

def send_telegram(chat_id, message, parse_mode="Markdown", disable_web_page_preview=True):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": message, "parse_mode": parse_mode, "disable_web_page_preview": disable_web_page_preview}
    try:
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        logging.info(f"Telegram response to {chat_id}: {response.status_code} - {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"{EMOJI['warning']} Lỗi gửi Telegram đến {chat_id}: {e}")
        log_message(f"Lỗi gửi Telegram đến {chat_id}: {e}")

def send_telegram_with_buttons(chat_id, message, buttons, parse_mode="Markdown"):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    reply_markup = {"inline_keyboard": buttons}
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
        "reply_markup": json.dumps(reply_markup)
    }
    try:
        response = requests.post(url, json=data, timeout=10)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        logging.info(f"Telegram response with buttons to {chat_id}: {response.status_code} - {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"{EMOJI['warning']} Lỗi gửi Telegram với nút đến {chat_id}: {e}")
        log_message(f"Lỗi gửi Telegram với nút đến {chat_id}: {e}")

def init_db():
    conn = sqlite3.connect("taixiu.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS sessions
                 (session_id TEXT PRIMARY KEY, dice TEXT, total INTEGER, result TEXT, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS keys
                 (key_value TEXT PRIMARY KEY, created_at TEXT, created_by INTEGER,
                  prefix TEXT, max_uses INTEGER, current_uses INTEGER DEFAULT 0,
                  expiry_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins
                 (chat_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_states
                 (chat_id INTEGER PRIMARY KEY, is_active INTEGER DEFAULT 0, key_value TEXT)''')
    try:
        c.execute("ALTER TABLE keys ADD COLUMN expiry_date TEXT")
        conn.commit()
        print("Đã thêm cột expiry_date vào bảng keys.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Cột expiry_date đã tồn tại trong bảng keys.")
        else:
            print(f"Lỗi khi thêm cột expiry_date: {e}")
    conn.commit()
    conn.close()

def get_db_connection():
    return sqlite3.connect("taixiu.db")

def is_admin(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT chat_id FROM admins WHERE chat_id = ?", (chat_id,))
    result = c.fetchone()
    conn.close()
    return result is not None

def add_admin_to_db(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO admins (chat_id) VALUES (?)", (chat_id,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def remove_admin_from_db(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE chat_id = ?", (chat_id,))
    rows_deleted = c.rowcount
    conn.commit()
    conn.close()
    return rows_deleted > 0

def get_all_admins_from_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT chat_id FROM admins")
    admins = [row[0] for row in c.fetchall()]
    conn.close()
    return admins

def add_key_to_db(key_value, created_by, prefix, max_uses, expiry_date):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO keys (key_value, created_at, created_by, prefix, max_uses, expiry_date) VALUES (?, ?, ?, ?, ?, ?)",
                  (key_value, time.strftime("%Y-%m-%d %H:%M:%S"), created_by, prefix, max_uses, expiry_date))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_all_keys_from_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT key_value, created_at, created_by, prefix, max_uses, current_uses, expiry_date FROM keys")
    keys = c.fetchall()
    conn.close()
    return keys

def delete_key_from_db(key_value):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM keys WHERE key_value = ?", (key_value,))
    rows_deleted = c.rowcount
    conn.commit()
    conn.close()
    return rows_deleted > 0

def is_key_valid(key):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT key_value, max_uses, current_uses, expiry_date FROM keys WHERE key_value = ?", (key,))
    result = c.fetchone()
    conn.close()
    if result:
        key_value, max_uses, current_uses, expiry_date_str = result
        if expiry_date_str:
            expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d %H:%M:%S")
            if datetime.now() > expiry_date:
                return False
        if max_uses == -1:
            return True
        return current_uses < max_uses
    return False

def increment_key_usage(key):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE keys SET current_uses = current_uses + 1 WHERE key_value = ?", (key,))
    conn.commit()
    conn.close()

def update_user_state(chat_id, is_active, key_value=None):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        if key_value:
            c.execute("INSERT OR REPLACE INTO user_states (chat_id, is_active, key_value) VALUES (?, ?, ?)",
                      (chat_id, 1 if is_active else 0, key_value))
        else:
            c.execute("UPDATE user_states SET is_active = ? WHERE chat_id = ?",
                      (1 if is_active else 0, chat_id))
        conn.commit()
        return True
    except Exception as e:
        print(f"Lỗi khi cập nhật trạng thái người dùng: {e}")
        return False
    finally:
        conn.close()

def get_user_state(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT is_active, key_value FROM user_states WHERE chat_id = ?", (chat_id,))
    result = c.fetchone()
    conn.close()
    if result:
        return {"is_active": bool(result[0]), "key_value": result[1]}
    return None

def get_all_active_users():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT chat_id FROM user_states WHERE is_active = 1")
    active_users = [row[0] for row in c.fetchall()]
    conn.close()
    return active_users

def update_db(data):
    if not data:
        return []
    conn = get_db_connection()
    c = conn.cursor()
    new_sessions = []
    # Data from API is a single object, not a list.
    # Convert keys to match internal session structure
    session_data = {
        "session_id": str(data["Phien"]),
        "dice": [data["Xuc_xac_1"], data["Xuc_xac_2"], data["Xuc_xac_3"]],
        "total": data["Tong"],
        "result": data["Ket_qua"]
    }
    dice_str = ",".join(map(str, session_data["dice"]))
    
    # Check if session_id already exists
    c.execute("SELECT session_id FROM sessions WHERE session_id = ?", (session_data["session_id"],))
    if c.fetchone() is None:
        c.execute('''INSERT INTO sessions (session_id, dice, total, result, timestamp)
                     VALUES (?, ?, ?, ?, ?)''',
                  (session_data["session_id"], dice_str, session_data["total"], session_data["result"],
                   time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        new_sessions.append(session_data)
    conn.close()
    return new_sessions


def get_last_sessions(limit):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"SELECT session_id, dice, total, result FROM sessions ORDER BY timestamp DESC LIMIT {limit}")
    results = c.fetchall()
    conn.close()
    sessions = []
    for result in results:
        dice = list(map(int, result[1].split(",")))
        sessions.append({"session_id": result[0], "dice": dice, "total": result[2], "result": result[3]})
    return sessions

def log_message(message):
    with open("bot_log.txt", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
    logging.info(message)

def analyze_trend():
    last_sessions = get_last_sessions(15)  # Tăng số phiên phân tích lên 15
    if len(last_sessions) < 5:
        return f"{EMOJI['warning']} Chưa đủ dữ liệu để phân tích xu hướng"
    
    tai_count = sum(1 for s in last_sessions if s["result"] == "Tài")
    xiu_count = len(last_sessions) - tai_count
    
    # Phân tích cầu lớn
    current_streak = 1
    current_result = last_sessions[0]["result"] if last_sessions else None
    
    if current_result:
        for i in range(1, len(last_sessions)):
            if last_sessions[i]["result"] == current_result:
                current_streak += 1
            else:
                break
    
    streak_info = ""
    if current_result and current_streak >= 3:
        streak_info = f" | {EMOJI['streak']} Cầu {current_result} {current_streak} nút"
    
    # Phân tích tổng điểm
    sum_analysis = ""
    last_sum = last_sessions[0]["total"] if last_sessions else 0
    if 3 <= last_sum <= 10:
        sum_analysis = f" | {EMOJI['down']} Tổng thấp ({last_sum})"
    elif 17 <= last_sum <= 18:
        sum_analysis = f" | {EMOJI['up']} Tổng cao ({last_sum})"
    
    if tai_count > xiu_count:
        return f"{EMOJI['up']} Xu hướng Tài ({tai_count}/{len(last_sessions)}){streak_info}{sum_analysis}"
    elif xiu_count > tai_count:
        return f"{EMOJI['down']} Xu hướng Xỉu ({xiu_count}/{len(last_sessions)}){streak_info}{sum_analysis}"
    else:
        return f"{EMOJI['right']} Xu hướng cân bằng{streak_info}{sum_analysis}"

def should_send_prediction(chat_id):
    user_state = get_user_state(chat_id)
    return ADMIN_ACTIVE and (user_state and user_state["is_active"])

def send_prediction_update(session):
    dice = "-".join(map(str, session["dice"]))
    total = session["total"]
    result = session["result"]
    session_id = session["session_id"]
    
    try:
        next_session_id = str(int(session_id) + 1)
    except ValueError:
        next_session_id = "Không xác định" # Fallback if session_id is not a number
        
    history = get_last_sessions(20)  # Tăng số phiên phân tích lên 20
    
    # Phân tích nâng cao
    prediction, confidence = pattern_predict(history)
    current_time = time.strftime("%H:%M:%S %d/%m/%Y")
    trend = analyze_trend()
    
    result_display = f"{EMOJI['money']} *TÀI*" if result == "Tài" else f"{EMOJI['cross']} *XỈU*"
    prediction_display = f"{EMOJI['fire']} *TÀI*" if prediction == "Tài" else f"{EMOJI['cross']} *XỈU*"
    
    if confidence > 85:
        confidence_level = f"{EMOJI['star']} *RẤT CAO* (Cầu mạnh)"
    elif confidence > 75:
        confidence_level = f"{EMOJI['star']} *RẤT CAO*"
    elif confidence > 65:
        confidence_level = f"{EMOJI['check']} *CAO*"
    elif confidence > 55:
        confidence_level = f"{EMOJI['right']} *TRUNG BÌNH*"
    else:
        confidence_level = f"{EMOJI['warning']} *THẤP*"
    
    # Thêm phân tích pattern
    elements = [("t" if s["result"] == "Tài" else "x") for s in history[:15]]
    current_pattern = "".join(reversed(elements))
    pattern_analysis = f"\n{EMOJI['pattern']} *Pattern:* `{current_pattern[-15:] if len(current_pattern) > 15 else current_pattern}`"
    
    # Thêm phân tích tổng điểm
    last_sum = history[0]["total"] if history else 0
    sum_analysis = ""
    if 3 <= last_sum <= 10:
        sum_analysis = f"\n{EMOJI['down']} *Tổng điểm gần nhất:* `{last_sum}` (Xu hướng Xỉu)"
    elif 17 <= last_sum <= 18:
        sum_analysis = f"\n{EMOJI['up']} *Tổng điểm gần nhất:* `{last_sum}` (Xu hướng Tài)"
    
    # Tạo message đẹp hơn với định dạng phong cách
    message = (
        f"{EMOJI['diamond']} *SUNWIN VIP - PHÂN TÍCH CHUẨN XÁC* {EMOJI['diamond']}\n"
        f"══════════════════════════\n"
        f"{EMOJI['id']} *Phiên:* `{session_id}`\n"
        f"{EMOJI['dice']} *Xúc xắc:* `{dice}`\n"
        f"{EMOJI['sum']} *Tổng điểm:* `{total}` | *Kết quả:* {result_display}\n"
        f"──────────────────────────\n"
        f"{EMOJI['prediction']} *Dự đoán phiên {next_session_id}:* {prediction_display}\n"
        f"{EMOJI['chart']} *Độ tin cậy:* {confidence_level} ({confidence:.1f}%)\n"
        f"{EMOJI['target']} *Khuyến nghị:* Đặt cược `{prediction}`\n"
        f"{pattern_analysis}{sum_analysis}\n"
        f"{EMOJI['clock']} *Thời gian:* `{current_time}`\n"
        f"{EMOJI['trend']} *Xu hướng:* `{trend}`\n"
        f"══════════════════════════\n"
        f"{EMOJI['team']} *Hệ thống phân tích nâng cao* {EMOJI['team']}\n"
        f"{EMOJI['vip']} *Uy tín - Chính xác - Hiệu quả* {EMOJI['vip']}"
    )
    
    # Gửi đến người dùng đang active
    active_users = get_all_active_users()
    
    for user_id in active_users:
        if should_send_prediction(user_id):
            try:
                send_telegram(user_id, message)
                time.sleep(0.1)  # Giới hạn tốc độ gửi
            except Exception as e:
                log_message(f"Lỗi khi gửi dự đoán đến {user_id}: {str(e)}")
    
    log_message(f"Đã gửi dự đoán đến {len(active_users)} người dùng")
    if not ADMIN_ACTIVE:
        log_message("Bot đang tạm dừng gửi dự đoán (do admin).")

def fetch_and_process_data():
    global LAST_FETCHED_SESSION_ID
    try:
        response = requests.get(TAIXIU_API_URL, timeout=10)
        response.raise_for_status()  # Raise an HTTPError for bad responses (4xx or 5xx)
        data = response.json()

        # Kiểm tra nếu ID phiên hiện tại lớn hơn ID phiên cuối cùng đã xử lý
        current_session_id = str(data.get("Phien"))
        if current_session_id and (LAST_FETCHED_SESSION_ID is None or int(current_session_id) > int(LAST_FETCHED_SESSION_ID)):
            new_sessions = update_db(data) # update_db đã được điều chỉnh để nhận một đối tượng, không phải danh sách
            for session in new_sessions: # new_sessions sẽ chứa 0 hoặc 1 phiên mới
                send_prediction_update(session)
            LAST_FETCHED_SESSION_ID = current_session_id
            print(f"{EMOJI['check']} Đã xử lý phiên mới: {current_session_id}")
            log_message(f"Đã xử lý phiên mới từ API: {current_session_id}")
        else:
            # print(f"{EMOJI['info']} Không có phiên mới hoặc phiên trùng lặp. Phiên hiện tại: {current_session_id}. Phiên cuối đã xử lý: {LAST_FETCHED_SESSION_ID}")
            pass # Bỏ qua log này để tránh spam log khi không có phiên mới
            
    except requests.exceptions.RequestException as e:
        error_msg = f"{EMOJI['warning']} Lỗi kết nối API: {e}"
        print(error_msg)
        log_message(error_msg)
    except json.JSONDecodeError as e:
        error_msg = f"{EMOJI['warning']} Lỗi giải mã JSON từ API: {e}"
        print(error_msg)
        log_message(error_msg)
    except Exception as e:
        error_msg = f"{EMOJI['warning']} Lỗi không xác định khi lấy dữ liệu API: {e}"
        print(error_msg)
        log_message(error_msg)

def background_fetch_task():
    initial_delay = 5 # Đợi một chút khi khởi động
    time.sleep(initial_delay)
    
    # Khởi tạo LAST_FETCHED_SESSION_ID từ phiên cuối cùng trong DB
    last_session_in_db = get_last_sessions(1)
    if last_session_in_db:
        global LAST_FETCHED_SESSION_ID
        LAST_FETCHED_SESSION_ID = last_session_in_db[0]["session_id"]
        print(f"Khởi tạo LAST_FETCHED_SESSION_ID từ DB: {LAST_FETCHED_SESSION_ID}")

    fetch_interval = 5 # Lấy dữ liệu mỗi 5 giây
    while True:
        fetch_and_process_data()
        time.sleep(fetch_interval)

def broadcast_message(chat_id, message_text):
    if not is_admin(chat_id):
        send_telegram(chat_id, f"{EMOJI['warning']} Chỉ admin mới có quyền sử dụng lệnh này.")
        return
    
    if not message_text:
        send_telegram(chat_id, f"{EMOJI['warning']} Vui lòng nhập nội dung tin nhắn. Sử dụng: /broadcast [tin nhắn]")
        return
    
    # Lấy tất cả người dùng đã từng tương tác với bot
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT chat_id FROM user_states")
    all_users = [row[0] for row in c.fetchall()]
    conn.close()
    
    if not all_users:
        send_telegram(chat_id, f"{EMOJI['warning']} Không có người dùng nào trong hệ thống.")
        return
    
    success_count = 0
    fail_count = 0
    
    # Gửi thông báo đến từng người dùng
    for user_id in all_users:
        try:
            send_telegram(user_id, f"{EMOJI['broadcast']} *THÔNG BÁO TỪ ADMIN*\n══════════════════════════\n{message_text}\n══════════════════════════\n{EMOJI['info']} Đây là tin nhắn tự động")
            success_count += 1
            time.sleep(0.3)  # Giới hạn tốc độ gửi
        except Exception as e:
            log_message(f"Lỗi khi gửi broadcast đến {user_id}: {str(e)}")
            fail_count += 1
    
    # Gửi báo cáo kết quả cho admin
    report_message = (
        f"{EMOJI['broadcast']} *BÁO CÁO GỬI THÔNG BÁO*\n"
        f"══════════════════════════\n"
        f"{EMOJI['users']} *Tổng người dùng:* {len(all_users)}\n"
        f"{EMOJI['check']} *Gửi thành công:* {success_count}\n"
        f"{EMOJI['warning']} *Gửi thất bại:* {fail_count}\n"
        f"══════════════════════════\n"
        f"{EMOJI['info']} Nội dung đã gửi:\n{message_text}"
    )
    
    send_telegram(chat_id, report_message)
    log_message(f"Admin {chat_id} đã gửi broadcast đến {len(all_users)} người dùng. Thành công: {success_count}, Thất bại: {fail_count}")

def handle_telegram_updates():
    global ADMIN_ACTIVE
    offset = 0
    while True:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        params = {"offset": offset, "timeout": 30}
        try:
            response = requests.get(url, params=params, timeout=40)
            response.raise_for_status()
            updates = response.json()["result"]
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    message = update["message"]
                    chat_id = message["chat"]["id"]
                    text = message.get("text")

                    if text:
                        if text.startswith("/start"):
                            welcome_message = (
                                f"{EMOJI['diamond']} *SUNWIN VIP - CHÀO MỪNG BẠN* {EMOJI['diamond']}\n"
                                f"══════════════════════════\n"
                                f"{EMOJI['rocket']} *BOT PHÂN TÍCH TÀI XỈU CHUẨN XÁC*\n"
                                f"{EMOJI['vip']} Phiên bản: {BOT_VERSION}\n"
                                f"══════════════════════════\n"
                                f"{EMOJI['bell']} *Hướng dẫn sử dụng:*\n"
                                f"- Nhập `/key <key_của_bạn>` để kích hoạt bot\n"
                                f"- `/chaybot` để bật nhận thông báo\n"
                                f"- `/tatbot` để tắt nhận thông báo\n"
                                f"- `/lichsu` để xem lịch sử 10 phiên gần nhất\n"
                                f"══════════════════════════\n"
                                f"{EMOJI['team']} *Liên hệ admin để mua key VIP* {EMOJI['team']}"
                            )
                            
                            buttons = [
                                [{"text": f"{EMOJI['key']} Hướng dẫn kích hoạt", "callback_data": "help_activate"}],
                                [{"text": f"{EMOJI['money_bag']} Liên hệ mua key", "url": "https://t.me/truongdong1920"}]
                            ]
                            
                            send_telegram_with_buttons(chat_id, welcome_message, buttons)
                            
                            user_state = get_user_state(chat_id)
                            if not user_state or not user_state.get("key_value"):
                                pass
                            else:
                                key_to_check = user_state["key_value"]
                                if is_key_valid(key_to_check):
                                    update_user_state(chat_id, True)
                                    # Không increment_key_usage ở đây để tránh đếm 2 lần khi user /start lại
                                    send_telegram(chat_id, f"{EMOJI['check']} Bot đã được kích hoạt cho bạn. Nhận thông báo dự đoán tự động.")
                                else:
                                    send_telegram(chat_id, f"{EMOJI['warning']} Key của bạn đã hết lượt sử dụng hoặc đã hết hạn.")
                                    update_user_state(chat_id, False)

                        elif text.startswith("/key"):
                            parts = text.split()
                            if len(parts) == 2:
                                key = parts[1]
                                if is_key_valid(key):
                                    # Kiểm tra xem user đã có key hợp lệ khác chưa để tránh ghi đè key
                                    current_user_state = get_user_state(chat_id)
                                    if current_user_state and current_user_state["key_value"] == key:
                                        send_telegram(chat_id, f"{EMOJI['info']} Key này đã được kích hoạt cho bạn.")
                                    else:
                                        update_user_state(chat_id, True, key)
                                        increment_key_usage(key) # Chỉ tăng khi nhập key mới
                                        
                                        # Lấy thông tin key
                                        conn = get_db_connection()
                                        c = conn.cursor()
                                        c.execute("SELECT prefix, max_uses, current_uses, expiry_date FROM keys WHERE key_value = ?", (key,))
                                        key_info = c.fetchone()
                                        conn.close()
                                        
                                        if key_info:
                                            prefix, max_uses, current_uses_after_increment, expiry_date = key_info
                                            uses_left = f"{max_uses - current_uses_after_increment} lần" if max_uses != -1 else "không giới hạn"
                                            expiry_info = f"hết hạn {expiry_date}" if expiry_date else "vĩnh viễn"
                                            
                                            success_message = (
                                                f"{EMOJI['check']} *KÍCH HOẠT THÀNH CÔNG*\n"
                                                f"══════════════════════════\n"
                                                f"{EMOJI['key']} *Loại key:* `{prefix}`\n"
                                                f"{EMOJI['chart']} *Số lần còn lại:* `{uses_left}`\n"
                                                f"{EMOJI['calendar']} *Thời hạn:* `{expiry_info}`\n"
                                                f"══════════════════════════\n"
                                                f"{EMOJI['bell']} Gõ `/chaybot` để bắt đầu nhận dự đoán!"
                                            )
                                            send_telegram(chat_id, success_message)
                                        else:
                                            send_telegram(chat_id, f"{EMOJI['key']} Key hợp lệ. Bot đã được kích hoạt cho bạn.")
                                else:
                                    send_telegram(chat_id, f"{EMOJI['warning']} Key không hợp lệ hoặc đã hết lượt sử dụng/hết hạn. Vui lòng kiểm tra lại.")
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Sử dụng: `/key <your_key>`")

                        elif text.startswith("/chaybot"):
                            user_state = get_user_state(chat_id)
                            if user_state and user_state.get("key_value") and is_key_valid(user_state["key_value"]):
                                update_user_state(chat_id, True)
                                
                                # Kiểm tra lịch sử 5 phiên gần nhất
                                last_sessions = get_last_sessions(5)
                                if last_sessions:
                                    last_result = last_sessions[0]["result"]
                                    streak = 1
                                    for i in range(1, len(last_sessions)):
                                        if last_sessions[i]["result"] == last_result:
                                            streak += 1
                                        else:
                                            break
                                    
                                    streak_info = f"\n{EMOJI['streak']} *Cầu hiện tại:* {last_result} {streak} nút" if streak >= 3 else ""
                                else:
                                    streak_info = ""
                                
                                message = (
                                    f"{EMOJI['check']} *BOT ĐÃ ĐƯỢC BẬT*\n"
                                    f"══════════════════════════\n"
                                    f"{EMOJI['bell']} Bạn sẽ nhận thông báo dự đoán tự động.{streak_info}\n"
                                    f"══════════════════════════\n"
                                    f"{EMOJI['warning']} Lưu ý: Đây là công cụ hỗ trợ, không đảm bảo 100% chính xác."
                                )
                                send_telegram(chat_id, message)
                                
                                print(f"{EMOJI['play']} Bot đã được bật cho người dùng {chat_id}.")
                                log_message(f"Bot đã được bật cho người dùng {chat_id}.")
                            elif is_admin(chat_id):
                                ADMIN_ACTIVE = True
                                send_telegram(chat_id, f"{EMOJI['play']} Bot đã được bật cho tất cả người dùng (admin).")
                                print(f"{EMOJI['play']} Bot đã được bật bởi admin.")
                                log_message("Bot đã được bật bởi admin.")
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Bạn cần kích hoạt bot bằng key trước hoặc bạn không có quyền sử dụng lệnh này.")

                        elif text.startswith("/tatbot"):
                            user_state = get_user_state(chat_id)
                            if user_state and user_state.get("key_value"):
                                update_user_state(chat_id, False)
                                send_telegram(chat_id, f"{EMOJI['pause']} Bot đã được tắt cho bạn. Bạn sẽ không nhận thông báo nữa.")
                                print(f"{EMOJI['pause']} Bot đã được tắt cho người dùng {chat_id}.")
                                log_message(f"Bot đã được tắt cho người dùng {chat_id}.")
                            elif is_admin(chat_id):
                                ADMIN_ACTIVE = False
                                send_telegram(chat_id, f"{EMOJI['pause']} Bot đã được tắt cho tất cả người dùng (admin).")
                                print(f"{EMOJI['pause']} Bot đã được tắt bởi admin.")
                                log_message("Bot đã được tắt bởi admin.")
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Bạn cần kích hoạt bot bằng key trước hoặc bạn không có quyền sử dụng lệnh này.")

                        elif text.startswith("/lichsu"):
                            last_sessions = get_last_sessions(10)
                            if last_sessions:
                                sessions_info = []
                                for i, session in enumerate(last_sessions):
                                    dice_str = "-".join(map(str, session["dice"]))
                                    sessions_info.append(
                                        f"{EMOJI['id']} *Phiên {session['session_id']}*: "
                                        f"{dice_str} | Tổng: `{session['total']}` | "
                                        f"{'Tài' if session['result'] == 'Tài' else 'Xỉu'}"
                                    )
                                
                                # Phân tích xu hướng
                                tai_count = sum(1 for s in last_sessions if s["result"] == "Tài")
                                xiu_count = len(last_sessions) - tai_count
                                
                                message = (
                                    f"{EMOJI['history']} *LỊCH SỬ 10 PHIÊN GẦN NHẤT*\n"
                                    f"══════════════════════════\n"
                                    + "\n".join(sessions_info) +
                                    f"\n══════════════════════════\n"
                                    f"{EMOJI['chart']} *Thống kê:* Tài: {tai_count} | Xỉu: {xiu_count}\n"
                                    f"{EMOJI['trend']} *Xu hướng:* {'Tài' if tai_count > xiu_count else 'Xỉu' if xiu_count > tai_count else 'Cân bằng'}"
                                )
                                send_telegram(chat_id, message)
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Chưa có dữ liệu lịch sử.")

                        elif text.startswith("/taokey"):
                            if is_admin(chat_id):
                                parts = text.split()
                                if len(parts) >= 2:
                                    prefix = parts[1]
                                    limit_str = "unlimited"
                                    time_str = "vĩnh viễn"

                                    if len(parts) >= 3:
                                        limit_str = parts[2].lower()
                                    if len(parts) >= 4:
                                        time_str = " ".join(parts[3:]).lower()

                                    max_uses = -1
                                    if limit_str.isdigit():
                                        max_uses = int(limit_str)
                                    elif limit_str == "unlimited" or limit_str == "voihan": # Thêm "voihan" cho tiện
                                        max_uses = -1
                                    else:
                                        send_telegram(chat_id, f"{EMOJI['warning']} Giới hạn dùng không hợp lệ. Nhập số hoặc 'unlimited' (hoặc 'voihan').")
                                        continue

                                    expiry_date = None
                                    if time_str and time_str != "vĩnh viễn" and time_str != "unlimited":
                                        time_parts = time_str.split()
                                        if len(time_parts) >= 2 and time_parts[0].isdigit():
                                            time_value = int(time_parts[0])
                                            time_unit = " ".join(time_parts[1:])

                                            now = datetime.now()
                                            if "ngày" in time_unit:
                                                expiry_date = now + timedelta(days=time_value)
                                            elif "tuần" in time_unit:
                                                expiry_date = now + timedelta(weeks=time_value)
                                            elif "tháng" in time_unit:
                                                expiry_date = now + timedelta(days=time_value * 30)
                                            elif "năm" in time_unit:
                                                expiry_date = now + timedelta(days=time_value * 365)

                                            if expiry_date:
                                                expiry_date = expiry_date.strftime("%Y-%m-%d %H:%M:%S")
                                            else:
                                                send_telegram(chat_id, f"{EMOJI['warning']} Đơn vị thời gian không hợp lệ. Ví dụ: '30 ngày', '1 tuần', '6 tháng', '1 năm'.")
                                                continue
                                        else:
                                            send_telegram(chat_id, f"{EMOJI['warning']} Định dạng thời gian không hợp lệ. Ví dụ: '30 ngày', '1 tuần', 'vĩnh viễn'.")
                                            continue

                                    new_key_value = f"{prefix}-{str(uuid.uuid4())[:8]}"
                                    if add_key_to_db(new_key_value, chat_id, prefix, max_uses, expiry_date):
                                        uses_display = f"{max_uses} lần" if max_uses != -1 else f"{EMOJI['infinity']} không giới hạn"
                                        expiry_display = f"{EMOJI['calendar']} {expiry_date}" if expiry_date else f"{EMOJI['infinity']} vĩnh viễn"
                                        send_telegram(chat_id, f"{EMOJI['add']} Đã tạo key '{new_key_value}'. Giới hạn: {uses_display}, Thời hạn: {expiry_display}.")
                                        log_message(f"Admin {chat_id} đã tạo key '{new_key_value}' với giới hạn {max_uses}, thời hạn {expiry_date}.")
                                    else:
                                        send_telegram(chat_id, f"{EMOJI['warning']} Không thể tạo key (có thể đã tồn tại).")
                                else:
                                    send_telegram(chat_id, f"{EMOJI['warning']} Sử dụng: `/taokey <tên_key> [giới_hạn_dùng/unlimited] [thời_gian (ví dụ: 30 ngày, 1 tuần, vĩnh viễn)]`. Các tham số giới hạn và thời gian là tùy chọn (mặc định là không giới hạn).")
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Chỉ admin mới có quyền sử dụng lệnh này.")

                        elif text.startswith("/lietkekey"):
                            if is_admin(chat_id):
                                keys_data = get_all_keys_from_db()
                                if keys_data:
                                    keys_list = []
                                    for key in keys_data:
                                        key_value, created_at, created_by, prefix, max_uses, current_uses, expiry_date = key
                                        uses_left = f"{current_uses}/{max_uses}" if max_uses != -1 else f"{current_uses}/{EMOJI['infinity']}"
                                        expiry_display = expiry_date if expiry_date else f"{EMOJI['infinity']}"
                                        keys_list.append(f"- `{key_value}` (Prefix: {prefix}, Dùng: {uses_left}, Hết hạn: {expiry_display})")
                                    
                                    keys_str = "\n".join(keys_list)
                                    message = (
                                        f"{EMOJI['list']} *DANH SÁCH KEY*\n"
                                        f"══════════════════════════\n"
                                        f"{keys_str}\n"
                                        f"══════════════════════════\n"
                                        f"{EMOJI['info']} Tổng số key: {len(keys_data)}"
                                    )
                                    send_telegram(chat_id, message)
                                else:
                                    send_telegram(chat_id, f"{EMOJI['list']} Không có key nào trong hệ thống.")
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Chỉ admin mới có quyền sử dụng lệnh này.")

                        elif text.startswith("/xoakey"):
                            if is_admin(chat_id):
                                parts = text.split()
                                if len(parts) == 2:
                                    key_to_delete = parts[1]
                                    if delete_key_from_db(key_to_delete):
                                        send_telegram(chat_id, f"{EMOJI['delete']} Đã xóa key `{key_to_delete}`.")
                                        log_message(f"Admin {chat_id} đã xóa key {key_to_delete}.")
                                    else:
                                        send_telegram(chat_id, f"{EMOJI['warning']} Không tìm thấy key `{key_to_delete}`.")
                                else:
                                    send_telegram(chat_id, f"{EMOJI['warning']} Sử dụng: `/xoakey <key_cần_xóa>`")
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Chỉ admin mới có quyền sử dụng lệnh này.")

                        elif text.startswith("/themadmin"):
                            if is_admin(chat_id):
                                parts = text.split()
                                if len(parts) == 2 and parts[1].isdigit():
                                    new_admin_id = int(parts[1])
                                    if add_admin_to_db(new_admin_id):
                                        send_telegram(chat_id, f"{EMOJI['admin']} Đã thêm admin ID `{new_admin_id}`.")
                                        log_message(f"Admin {chat_id} đã thêm admin {new_admin_id}.")
                                    else:
                                        send_telegram(chat_id, f"{EMOJI['warning']} Admin ID `{new_admin_id}` đã tồn tại.")
                                else:
                                    send_telegram(chat_id, f"{EMOJI['warning']} Sử dụng: `/themadmin <telegram_id>` (telegram_id phải là số).")
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Chỉ admin mới có quyền sử dụng lệnh này.")

                        elif text.startswith("/xoaadmin"):
                            if is_admin(chat_id):
                                parts = text.split()
                                if len(parts) == 2 and parts[1].isdigit():
                                    admin_to_remove = int(parts[1])
                                    if remove_admin_from_db(admin_to_remove):
                                        send_telegram(chat_id, f"{EMOJI['admin']} Đã xóa admin ID `{admin_to_remove}`.")
                                        log_message(f"Admin {chat_id} đã xóa admin {admin_to_remove}.")
                                    else:
                                        send_telegram(chat_id, f"{EMOJI['warning']} Không tìm thấy admin ID `{admin_to_remove}`.")
                                else:
                                    send_telegram(chat_id, f"{EMOJI['warning']} Sử dụng: `/xoaadmin <telegram_id>` (telegram_id phải là số).")
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Chỉ admin mới có quyền sử dụng lệnh này.")

                        elif text.startswith("/danhsachadmin"):
                            if is_admin(chat_id):
                                admins = get_all_admins_from_db()
                                if admins:
                                    admin_list_str = "\n".join([f"- `{admin_id}`" for admin_id in admins])
                                    message = (
                                        f"{EMOJI['admin']} *DANH SÁCH ADMIN*\n"
                                        f"══════════════════════════\n"
                                        f"{admin_list_str}\n"
                                        f"══════════════════════════\n"
                                        f"{EMOJI['info']} Tổng số admin: {len(admins)}"
                                    )
                                    send_telegram(chat_id, message)
                                else:
                                    send_telegram(chat_id, f"{EMOJI['admin']} Hiện tại không có admin nào.")
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Chỉ admin mới có quyền sử dụng lệnh này.")

                        elif text.startswith("/broadcast"):
                            if is_admin(chat_id):
                                message_text = text[len("/broadcast"):].strip()
                                if message_text:
                                    # Xác nhận trước khi gửi
                                    confirm_buttons = [
                                        [{"text": f"{EMOJI['check']} Xác nhận gửi", "callback_data": f"broadcast_confirm:{message_text}"}],
                                        [{"text": f"{EMOJI['cross']} Hủy bỏ", "callback_data": "broadcast_cancel"}]
                                    ]
                                    send_telegram_with_buttons(
                                        chat_id,
                                        f"{EMOJI['broadcast']} *XÁC NHẬN GỬI THÔNG BÁO*\n══════════════════════════\nBạn có chắc muốn gửi thông báo này đến tất cả người dùng?\n\nNội dung:\n{message_text}",
                                        confirm_buttons
                                    )
                                else:
                                    send_telegram(chat_id, f"{EMOJI['warning']} Vui lòng nhập nội dung tin nhắn. Sử dụng: /broadcast [tin nhắn]")
                            else:
                                send_telegram(chat_id, f"{EMOJI['warning']} Chỉ admin mới có quyền sử dụng lệnh này.")

                        elif text.startswith("/help") or text.startswith("/trogiup"):
                            help_message = (
                                f"{EMOJI['bell']} *HƯỚNG DẪN SỬ DỤNG BOT*\n"
                                f"══════════════════════════\n"
                                f"{EMOJI['key']} *Lệnh cơ bản:*\n"
                                f"- `/start`: Hiển thị thông tin chào mừng\n"
                                f"- `/key <key>`: Nhập key để kích hoạt bot\n"
                                f"- `/chaybot`: Bật nhận thông báo\n"
                                f"- `/tatbot`: Tắt nhận thông báo\n"
                                f"- `/lichsu`: Xem lịch sử 10 phiên gần nhất\n"
                                f"\n{EMOJI['admin']} *Lệnh admin:*\n"
                                f"- `/taokey <tên_key> [giới_hạn] [thời_gian]`: Tạo key mới\n"
                                f"- `/lietkekey`: Liệt kê tất cả key\n"
                                f"- `/xoakey <key>`: Xóa key\n"
                                f"- `/themadmin <id>`: Thêm admin\n"
                                f"- `/xoaadmin <id>`: Xóa admin\n"
                                f"- `/danhsachadmin`: Xem danh sách admin\n"
                                f"- `/broadcast [tin nhắn]`: Gửi thông báo đến tất cả người dùng\n"
                                f"══════════════════════════\n"
                                f"{EMOJI['team']} Liên hệ admin để được hỗ trợ thêm"
                            )
                            send_telegram(chat_id, help_message)

                elif "callback_query" in update:
                    callback_query = update["callback_query"]
                    callback_data = callback_query.get("data", "")
                    chat_id = callback_query["message"]["chat"]["id"]
                    
                    if callback_data.startswith("broadcast_confirm:"):
                        if is_admin(chat_id):
                            message_text = callback_data[len("broadcast_confirm:"):]
                            send_telegram(chat_id, f"{EMOJI['broadcast']} Đang gửi thông báo đến tất cả người dùng...")
                            broadcast_message(chat_id, message_text)
                        else:
                            send_telegram(chat_id, f"{EMOJI['warning']} Bạn không có quyền thực hiện hành động này.")
                    
                    elif callback_data == "broadcast_cancel":
                        send_telegram(chat_id, f"{EMOJI['cross']} Đã hủy gửi thông báo.")
                    
                    elif callback_data == "help_activate":
                        help_activate_message = (
                            f"{EMOJI['key']} *HƯỚNG DẪN KÍCH HOẠT BOT*\n"
                            f"══════════════════════════\n"
                            f"1. Liên hệ admin để mua key VIP\n"
                            f"2. Nhập lệnh `/key <key_của_bạn>` để kích hoạt\n"
                            f"3. Nhập `/chaybot` để bắt đầu nhận dự đoán\n"
                            f"══════════════════════════\n"
                            f"{EMOJI['warning']} Mỗi key có giới hạn sử dụng và thời hạn nhất định\n"
                            f"{EMOJI['team']} Liên hệ: @truongdong1920 để được hỗ trợ"
                        )
                        send_telegram(chat_id, help_activate_message)

        except requests.exceptions.RequestException as e:
            print(f"{EMOJI['warning']} Lỗi khi lấy updates từ Telegram: {e}")
            time.sleep(5)
        except json.JSONDecodeError as e:
            print(f"{EMOJI['warning']} Lỗi giải mã JSON từ Telegram: {e}")
            time.sleep(5)
        except Exception as e:
            print(f"{EMOJI['warning']} Lỗi không xác định trong handle_telegram_updates: {e}")
            time.sleep(5)

# Flask app for keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask_app():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def main():
    init_db()

    # Thêm admin mặc định nếu chưa có
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM admins")
    if c.fetchone()[0] == 0:
        print(f"{EMOJI['admin']} Thêm admin đầu tiên với ID: 6381480476, 5934991279 và 6915752059")
        c.execute("INSERT OR IGNORE INTO admins (chat_id) VALUES (?)", (6381480476,))
        c.execute("INSERT OR IGNORE INTO admins (chat_id) VALUES (?)", (5934991279,))
        c.execute("INSERT OR IGNORE INTO admins (chat_id) VALUES (?)", (6915752059,)) # Thêm ID admin mới
        conn.commit()
    conn.close()

    print(f"\n{EMOJI['diamond']} {'*'*20} {EMOJI['diamond']}")
    print(f"{EMOJI['rocket']} *SUNWIN VIP - BOT TÀI XỈU CHUẨN XÁC* {EMOJI['rocket']}")
    print(f"{EMOJI['diamond']} {'*'*20} {EMOJI['diamond']}\n")
    print(f"{EMOJI['settings']} Phiên bản: {BOT_VERSION}")
    print(f"{EMOJI['chart']} Hệ thống phân tích nâng cao")
    print(f"{EMOJI['team']} Phát triển bởi AE HTDD Team\n")
    print(f"{EMOJI['bell']} Bot đã sẵn sàng hoạt động!")

    # Start background tasks
    threading.Thread(target=background_fetch_task, daemon=True).start()
    threading.Thread(target=handle_telegram_updates, daemon=True).start()
    threading.Thread(target=run_flask_app, daemon=True).start() # Start Flask app for keep-alive

    try:
        while True:
            time.sleep(1) # Main thread keeps alive
    except KeyboardInterrupt:
        print(f"\n{EMOJI['warning']} Đang dừng bot...")
        conn = get_db_connection()
        conn.close()
        print(f"{EMOJI['check']} Bot đã dừng an toàn")

if __name__ == "__main__":
    main()

