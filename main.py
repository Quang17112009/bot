import telebot
import requests
import json
import time
import threading
from datetime import datetime, timedelta
import os
import sys
import sqlite3
import hashlib
import re

# Thêm import cho Flask và Thread
from flask import Flask
from threading import Thread

# --- Cấu hình Bot và Admin ---
BOT_TOKEN = "7820739987:AAE_eU2JPZH3L_l4tn64AD_8f6s" # Đã sửa token mẫu (nếu bạn vẫn dùng token cũ, hãy cập nhật)
ADMIN_IDS = [6915752059]
bot = telebot.TeleBot(BOT_TOKEN)

# --- Cấu hình Game (CHỈ GIỮ LUCKYWIN) ---
GAME_CONFIGS = {
    'luckywin': {'api_url': 'https://1.bot/GetNewLottery/LT_Taixiu', 'game_name_vi': 'Luckywin', 'history_table': 'luckywin_history'},
    # 'hitclub': {'api_url': 'https://apihitclub.up.railway.app/api/taixiu', 'game_name_vi': 'Hit Club', 'history_table': 'hitclub_history'}, # Đã xóa
    # 'sunwin': {'api_url': 'https://wanglinapiws.up.railway.app/api/taixiu', 'game_name_vi': 'Sunwin', 'history_table': 'sunwin_history'} # Đã xóa
}

# --- Biến Toàn Cục và Cấu Hình Lưu Trữ ---
LAST_FETCHED_IDS = {game: None for game in GAME_CONFIGS.keys()}
CHECK_INTERVAL_SECONDS = 5

LEARNED_PATTERNS = {game: {'dep': {}, 'xau': {}} for game in GAME_CONFIGS.keys()}

CAU_MIN_LENGTH = 5
RECENT_HISTORY_FETCH_LIMIT = 200

TEMP_DIR = 'temp_bot_files'
DB_NAME = 'bot_data.db'

# Tạo thư mục nếu chưa tồn tại
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# Biến tạm để lưu trạng thái chờ file của admin (cho lệnh /nhapcau)
waiting_for_cau_file = {}

# Khóa để đồng bộ hóa truy cập database
DB_LOCK = threading.Lock()

# --- Hàm Hỗ Trợ Chung ---
def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS learned_patterns_db (
            game_name TEXT NOT NULL,
            pattern TEXT NOT NULL,
            pattern_type TEXT NOT NULL,
            result_sequence TEXT NOT NULL,
            classification_type TEXT NOT NULL,
            confidence REAL,
            last_seen_phien TEXT,
            PRIMARY KEY (game_name, pattern_type, result_sequence, classification_type)
        )
    ''')

    for game_name, config in GAME_CONFIGS.items():
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {config['history_table']} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phien TEXT UNIQUE NOT NULL,
                result_tx TEXT NOT NULL,
                total_point INTEGER NOT NULL,
                dice1 INTEGER NOT NULL,
                dice2 INTEGER NOT NULL,
                dice3 INTEGER NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS access_keys (
            key_value TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            user_id INTEGER,
            activated_at TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.commit()
    conn.close()

# Hàm thoát ký tự đặc biệt cho MarkdownV2
def escape_markdown_v2(text):
    """Escape special characters for Telegram MarkdownV2."""
    # List of special characters in MarkdownV2 that need to be escaped
    # _ * [ ] ( ) ~ ` > # + - = | { } . !
    # Using re.sub with a lambda function for replacement
    return re.sub(r'([_*\\[\\]()~`>#+\-={}.!|])', r'\\\1', text)

# --- Quản lý Mẫu Cầu (Sử dụng SQLite) ---
def load_cau_patterns_from_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    for game_name in GAME_CONFIGS.keys():
        LEARNED_PATTERNS[game_name]['dep'].clear()
        LEARNED_PATTERNS[game_name]['xau'].clear()

        cursor.execute("SELECT pattern_type, result_sequence, confidence FROM learned_patterns_db WHERE game_name = ? AND classification_type = 'dep'", (game_name,))
        for row in cursor.fetchall():
            LEARNED_PATTERNS[game_name]['dep'][row[1]] = {'type': row[0], 'confidence': row[2]}

        cursor.execute("SELECT pattern_type, result_sequence, confidence FROM learned_patterns_db WHERE game_name = ? AND classification_type = 'xau'", (game_name,))
        for row in cursor.fetchall():
            LEARNED_PATTERNS[game_name]['xau'][row[1]] = {'type': row[0], 'confidence': row[2]}

    conn.close()
    print(f"DEBUG: Đã tải mẫu cầu từ DB. Tổng cầu đẹp: {sum(len(v['dep']) for v in LEARNED_PATTERNS.values())}, Tổng cầu xấu: {sum(len(v['xau']) for v in LEARNED_PATTERNS.values())}")
    sys.stdout.flush()

def save_learned_pattern_to_db(game_name, pattern_type, result_sequence, classification_type, confidence, last_seen_phien):
    with DB_LOCK: # Sử dụng lock khi ghi vào DB
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO learned_patterns_db (game_name, pattern_type, result_sequence, classification_type, confidence, last_seen_phien)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_name, pattern_type, result_sequence, classification_type) DO UPDATE SET
                    confidence = EXCLUDED.confidence,
                    last_seen_phien = EXCLUDED.last_seen_phien
            ''', (game_name, pattern_type, result_sequence, classification_type, confidence, last_seen_phien))
            conn.commit()
        except Exception as e:
            print(f"Error saving learned pattern to DB: {e}")
            sys.stdout.flush()
        finally:
            conn.close()

# --- Lịch sử Phiên Game (Sử dụng SQLite) ---
def save_game_result(game_name, phien, result_tx, total_point, dice1, dice2, dice3):
    with DB_LOCK: # Sử dụng lock khi ghi vào DB
        conn = get_db_connection()
        cursor = conn.cursor()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            cursor.execute(f'''
                INSERT OR IGNORE INTO {GAME_CONFIGS[game_name]['history_table']}
                (phien, result_tx, total_point, dice1, dice2, dice3, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (phien, result_tx, total_point, dice1, dice2, dice3, timestamp))
            conn.commit()
            return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            print(f"LỖI: Không thể lưu kết quả phiên {phien} cho {game_name} vào DB: {e}")
            sys.stdout.flush()
            return False
        finally:
            conn.close()

def get_recent_history(game_name, limit=RECENT_HISTORY_FETCH_LIMIT, include_phien=False):
    with DB_LOCK: # Sử dụng lock khi đọc từ DB
        conn = get_db_connection()
        cursor = conn.cursor()

        if include_phien:
            cursor.execute(f"SELECT phien, result_tx FROM {GAME_CONFIGS[game_name]['history_table']} ORDER BY id DESC LIMIT ?", (limit,))
            history = cursor.fetchall()
        else:
            cursor.execute(f"SELECT result_tx FROM {GAME_CONFIGS[game_name]['history_table']} ORDER BY id DESC LIMIT ?", (limit,))
            history = [row[0] for row in cursor.fetchall()]

        conn.close()
        return history[::-1]

# --- Logic Học và Dự Đoán ---
def analyze_and_learn_patterns(game_name, history_results):
    if len(history_results) < CAU_MIN_LENGTH + 1:
        return

    newly_learned_dep = {}
    newly_learned_xau = {}

    for i in range(len(history_results) - CAU_MIN_LENGTH):
        current_sequence = "".join(history_results[i : i + CAU_MIN_LENGTH])
        actual_next_result = history_results[i + CAU_MIN_LENGTH]

        pattern_type = 'unknown'
        predicted_result = 'N/A'

        # Check for bệt
        if len(set(current_sequence)) == 1 and 'B' not in current_sequence:
            pattern_type = f'bet_{current_sequence[0]}'
            predicted_result = current_sequence[0]

        # Check for zigzag (TX, XT alternating)
        elif 'B' not in current_sequence and all(current_sequence[j] != current_sequence[j+1] for j in range(CAU_MIN_LENGTH - 1)):
            pattern_type = f'zigzag_{current_sequence[0]}{current_sequence[1]}'
            predicted_result = 'T' if current_sequence[-1] == 'X' else 'X'

        # Check for 1-2-1
        elif 'B' not in current_sequence and CAU_MIN_LENGTH >= 3:
            is_121 = True
            for j in range(len(current_sequence) - 1):
                if current_sequence[j] == current_sequence[j+1]:
                    is_121 = False
                    break
            if is_121:
                pattern_type = '1-2-1'
                predicted_result = 'T' if current_sequence[-1] == 'X' else 'X'

        # Check for 2-1-2
        elif 'B' not in current_sequence and CAU_MIN_LENGTH >= 3:
            is_212 = False
            if len(current_sequence) >= 3:
                if (current_sequence[0] == current_sequence[1] and current_sequence[1] != current_sequence[2]) or \
                   (current_sequence[0] != current_sequence[1] and current_sequence[1] == current_sequence[2]):
                   is_212 = True
                   segment_length = 3
                   for k in range(segment_length, len(current_sequence), segment_length):
                       if len(current_sequence) - k >= segment_length:
                           if not (current_sequence[k] == current_sequence[k+1] and current_sequence[k+1] != current_sequence[k+2]) and \
                              not (current_sequence[k] != current_sequence[k+1] and current_sequence[k+1] == current_sequence[k+2]):
                               is_212 = False
                               break
                       else:
                           if k < len(current_sequence) and current_sequence[k] != current_sequence[k-segment_length]:
                               is_212 = False
                               break
            if is_212:
                 pattern_type = '2-1-2'
                 predicted_result = current_sequence[0]

        if predicted_result != 'N/A' and pattern_type != 'unknown':
            if actual_next_result == predicted_result:
                newly_learned_dep[current_sequence] = {'type': pattern_type, 'confidence': CAU_MIN_LENGTH}
            else:
                newly_learned_xau[current_sequence] = {'type': pattern_type, 'confidence': CAU_MIN_LENGTH}

    LEARNED_PATTERNS[game_name]['dep'].update(newly_learned_dep)
    LEARNED_PATTERNS[game_name]['xau'].update(newly_learned_xau)

    with DB_LOCK: # Sử dụng lock khi ghi vào DB
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM learned_patterns_db WHERE game_name = ?", (game_name,))

        for pattern_seq, data in LEARNED_PATTERNS[game_name]['dep'].items():
            save_learned_pattern_to_db(game_name, data['type'], pattern_seq, 'dep', data['confidence'], None)
        for pattern_seq, data in LEARNED_PATTERNS[game_name]['xau'].items():
            save_learned_pattern_to_db(game_name, data['type'], pattern_seq, 'xau', data['confidence'], None)

        conn.commit()
        conn.close()

def make_prediction_for_game(game_name):
    recent_history_tx = get_recent_history(game_name, limit=RECENT_HISTORY_FETCH_LIMIT)

    prediction = None
    reason = "Không có mẫu rõ ràng."
    confidence = "Thấp"

    if len(recent_history_tx) < CAU_MIN_LENGTH:
        return None, "Không đủ lịch sử để phân tích mẫu.", "Rất thấp", ""

    current_sequence_for_match = "".join(recent_history_tx[-CAU_MIN_LENGTH:])

    if current_sequence_for_match in LEARNED_PATTERNS[game_name]['dep']:
        pattern_data = LEARNED_PATTERNS[game_name]['dep'][current_sequence_for_match]

        if pattern_data['type'].startswith('bet_'):
            prediction = pattern_data['type'][-1]
            reason = f"Theo cầu bệt {prediction} dài {pattern_data['confidence']}+."
            confidence = "Cao"
        elif pattern_data['type'].startswith('zigzag_'):
            prediction = 'T' if current_sequence_for_match[-1] == 'X' else 'X'
            reason = f"Theo cầu zigzag dài {pattern_data['confidence']}+."
            confidence = "Cao"
        elif pattern_data['type'] == '1-2-1':
            prediction = 'T' if current_sequence_for_match[-1] == 'X' else 'X'
            reason = f"Theo cầu 1-2-1 dài {pattern_data['confidence']}+."
            confidence = "Cao"
        elif pattern_data['type'] == '2-1-2':
            prediction = current_sequence_for_match[0]
            reason = f"Theo cầu 2-1-2 dài {pattern_data['confidence']}+."
            confidence = "Cao"

        return prediction, reason, confidence, current_sequence_for_match

    if current_sequence_for_match in LEARNED_PATTERNS[game_name]['xau']:
        pattern_data = LEARNED_PATTERNS[game_name]['xau'][current_sequence_for_match]
        prediction = 'N/A'
        reason = f"⚠️ Phát hiện mẫu cầu không ổn định: {pattern_data['type']}. Nên cân nhắc tạm dừng."
        confidence = "Rất thấp"
        return prediction, reason, confidence, current_sequence_for_match

    if len(recent_history_tx) >= 10:
        num_T = recent_history_tx.count('T')
        num_X = recent_history_tx.count('X')
        num_B = recent_history_tx.count('B')

        total_tx = num_T + num_X
        if total_tx > 0:
            ratio_T = num_T / total_tx
            ratio_X = num_X / total_tx

            if ratio_T > 0.6:
                prediction = 'T'
                reason = f"Thống kê {num_T}/{total_tx} phiên gần nhất là Tài. Khả năng cao tiếp tục Tài."
                confidence = "Trung bình"
            elif ratio_X > 0.6:
                prediction = 'X'
                reason = f"Thống kê {num_X}/{total_tx} phiên gần nhất là Xỉu. Khả năng cao tiếp tục Xỉu."
                confidence = "Trung bình"
            elif num_B > 0 and num_B / len(recent_history_tx) > 0.05:
                prediction = 'B'
                reason = f"Bão xuất hiện {num_B}/{len(recent_history_tx)} phiên gần nhất. Có thể bão tiếp."
                confidence = "Trung bình"

    if not prediction and len(recent_history_tx) > 0:
        last_result = recent_history_tx[-1]
        prediction = 'T' if last_result == 'X' else 'X'
        reason = f"Không có mẫu/thống kê rõ ràng. Dự đoán đảo ngược kết quả gần nhất ({last_result})."
        confidence = "Thấp"

    return prediction, reason, confidence, current_sequence_for_match

def format_prediction_message(game_name_vi, phien_id_next, prev_phien_id, prev_result, dices, total_point, prediction, reason, confidence, recent_history_formatted):
    emoji_map = {
        'T': '📈', 'X': '📉', 'B': '🌪️',
        'Cao': '🚀', 'Trung bình': '👍', 'Thấp': '🐌', 'Rất thấp': '🚨'
    }

    prediction_emoji = emoji_map.get(prediction, '🤔')
    confidence_emoji = emoji_map.get(confidence, '')

    # Thoát các ký tự đặc biệt cho MarkdownV2
    phien_id_next_escaped = escape_markdown_v2(str(phien_id_next))
    prev_phien_id_escaped = escape_markdown_v2(str(prev_phien_id))
    prev_result_escaped = escape_markdown_v2(str(prev_result))
    total_point_escaped = escape_markdown_v2(str(total_point))
    prediction_escaped = escape_markdown_v2(str(prediction or 'KHÔNG CHẮC CHẮN'))
    reason_escaped = escape_markdown_v2(reason)
    confidence_escaped = escape_markdown_v2(confidence)
    recent_history_joined_escaped = escape_markdown_v2(' '.join(recent_history_formatted))
    game_name_vi_escaped = escape_markdown_v2(game_name_vi)


    message = (
        f"🎲 *Dự đoán {game_name_vi_escaped}* 🎲\n"
        f"---\n"
        f"✨ \\*\\*Phiên hiện tại:\\*\\* \\#\\{phien_id_next_escaped}\\`\n" # Escape # và `
        f"➡️ \\*\\*Kết quả phiên trước \\(`\\#{prev_phien_id_escaped}\\`\\):\\*\\* \\[{dices[0]} {dices[1]} {dices[2]}\\] = \\*\\*{total_point_escaped}\\*\\* \\({prev_result_escaped}\\)\n"
        f"---\n"
        f"🎯 \\*\\*Dự đoán:\\*\\* {prediction_emoji} \\*\\*{prediction_escaped}\\*\\*\n"
        f"💡 \\*\\*Lý do:\\*\\* _{reason_escaped}_\n"
        f"📊 \\*\\*Độ tin cậy:\\*\\* {confidence_emoji} _{confidence_escaped}_\n"
        f"---\n"
        f"📈 \\*\\*Lịch sử gần đây \\({len(recent_history_formatted)} phiên\\):\\*\\*\n"
        f"`{recent_history_joined_escaped}`\n"
        f"\n"
        f"⚠️ _Lưu ý: Dự đoán chỉ mang tính chất tham khảo, không đảm bảo 100% chính xác\\!_" # Escape !
    )
    return message

# --- Logic Xử lý Game (ĐÃ SỬA ĐỂ CHỈ XỬ LÝ LUCKYWIN) ---
def process_game_api_fetch(game_name, config):
    url = config['api_url']
    game_name_vi = config['game_name_vi']

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        phien = None
        total_point = None
        dice1 = None
        dice2 = None
        dice3 = None
        result_tx_from_api = ''

        if game_name == 'luckywin':
            if data.get('state') == 1 and 'data' in data:
                game_data = data['data']
                phien = game_data.get('Expect') # Phien cho Luckywin là Expect
                open_code = game_data.get('OpenCode')

                if open_code:
                    try:
                        dices_str = open_code.split(',')
                        if len(dices_str) == 3:
                            dice1 = int(dices_str[0])
                            dice2 = int(dices_str[1])
                            dice3 = int(dices_str[2])
                            total_point = dice1 + dice2 + dice3
                            if dice1 == dice2 == dice3:
                                result_tx_from_api = 'B'
                            elif total_point >= 11:
                                result_tx_from_api = 'T'
                            else:
                                result_tx_from_api = 'X'
                    except ValueError:
                        print(f"LỖI: OpenCode '{open_code}' không hợp lệ cho Luckywin. Bỏ qua phiên này.")
                        sys.stdout.flush()
                        return
                else:
                    print(f"LỖI: Không tìm thấy 'OpenCode' trong dữ liệu Luckywin. Bỏ qua phiên này.")
                    sys.stdout.flush()
                    return
            else:
                print(f"LỖI: Dữ liệu Luckywin không đúng định dạng mong đợi: {data}")
                sys.stdout.flush()
                return
        # Removed Hitclub and Sunwin parsing logic

        if phien is not None and total_point is not None and \
           dice1 is not None and dice2 is not None and dice3 is not None and \
           result_tx_from_api in ['T', 'X', 'B']:

            is_new_phien_added = save_game_result(game_name, phien, result_tx_from_api, total_point, dice1, dice2, dice3)

            if is_new_phien_added:
                print(f"DEBUG: Đã phát hiện và lưu phiên MỚI: {game_name_vi} - Phiên {phien}, Kết quả: {result_tx_from_api}")
                sys.stdout.flush()

                # Lấy bản ghi chi tiết của phiên VỪA KẾT THÚC (phiên mới được thêm vào DB)
                # Sử dụng lock khi đọc DB
                with DB_LOCK:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(f"SELECT phien, total_point, dice1, dice2, dice3, result_tx FROM {config['history_table']} WHERE phien = ? LIMIT 1", (phien,))
                    current_phien_info = cursor.fetchone()
                    conn.close()

                if not current_phien_info:
                    print(f"LỖI NGHIÊM TRỌNG: Phiên {phien} được báo là đã thêm nhưng không tìm thấy ngay lập tức trong DB.")
                    sys.stdout.flush()
                    return

                prev_phien_id = current_phien_info[0]
                prev_total_point = current_phien_info[1]
                prev_dices = (current_phien_info[2], current_phien_info[3], current_phien_info[4])
                prev_result_tx = current_phien_info[5]

                recent_history_tx_for_learning = get_recent_history(game_name, limit=RECENT_HISTORY_FETCH_LIMIT)
                analyze_and_learn_patterns(game_name, recent_history_tx_for_learning)

                prediction, reason, confidence, current_sequence_for_match = make_prediction_for_game(game_name)

                recent_history_for_msg = get_recent_history(game_name, limit=15, include_phien=True)
                recent_history_formatted = [f"#{p[0]}:{p[1]}" for p in recent_history_for_msg]

                formatted_message = format_prediction_message(
                    game_name_vi,
                    phien,
                    prev_phien_id, prev_result_tx, prev_dices, prev_total_point,
                    prediction, reason, confidence, recent_history_formatted
                )

                for admin_id in ADMIN_IDS:
                    try:
                        bot.send_message(admin_id, formatted_message, parse_mode='MarkdownV2') # Đổi sang MarkdownV2
                    except telebot.apihelper.ApiTelegramException as e:
                        print(f"LỖI: Không thể gửi tin nhắn đến admin {admin_id}: {e}")
                        sys.stdout.flush()

                print(f"DEBUG: Đã xử lý và gửi thông báo dự đoán cho {game_name_vi} phiên {phien}.")
                sys.stdout.flush()
            else:
                pass
        else:
            print(f"LỖI: Dữ liệu từ API {game_name_vi} không đầy đủ hoặc không hợp lệ: {data}")
            sys.stdout.flush()

    except requests.exceptions.Timeout:
        print(f"LỖỖI: Hết thời gian chờ khi kết nối đến API {game_name_vi}.")
        sys.stdout.flush()
    except requests.exceptions.RequestException as e:
        print(f"LỖI: Không thể kết nối hoặc lấy dữ liệu từ {game_name_vi} API: {e}")
        sys.stdout.flush()
    except json.JSONDecodeError:
        print(f"LỖI: Phản hồi API {game_name_vi} không phải là JSON hợp lệ.")
        sys.stdout.flush()
    except Exception as e:
        print(f"LỖI: Xảy ra lỗi không xác định khi xử lý {game_name_vi}: {e}")
        sys.stdout.flush()

def check_apis_loop():
    with DB_LOCK: # Sử dụng lock khi đọc DB lúc khởi tạo
        conn = get_db_connection()
        cursor = conn.cursor()
        for game_name, config in GAME_CONFIGS.items():
            try:
                cursor.execute(f"SELECT phien FROM {config['history_table']} ORDER BY id DESC LIMIT 1")
                last_phien = cursor.fetchone()
                if last_phien:
                    LAST_FETCHED_IDS[game_name] = last_phien[0]
                    print(f"DEBUG: {game_name}: Đã khởi tạo LAST_FETCHED_IDS = {last_phien[0]}")
                    sys.stdout.flush()
                else:
                    print(f"DEBUG: {game_name}: Chưa có dữ liệu trong DB, LAST_FETCHED_IDS = None")
                    sys.stdout.flush()
            except sqlite3.OperationalError:
                print(f"DEBUG: Bảng '{config['history_table']}' chưa tồn tại khi khởi tạo. Sẽ tạo khi lưu.")
                sys.stdout.flush()
            except Exception as e:
                print(f"LỖI: Không thể khởi tạo LAST_FETCHED_IDS cho {game_name}: {e}")
                sys.stdout.flush()
        conn.close()

    while True:
        for game_name, config in GAME_CONFIGS.items():
            process_game_api_fetch(game_name, config)
        time.sleep(CHECK_INTERVAL_SECONDS)

# --- Keep-alive cho Render ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!", 200

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    print(f"DEBUG: Starting Flask web server on port {port}")
    sys.stdout.flush()
    app.run(host='0.0.0.0', port=port, debug=False)

# --- Quản lý Key Truy Cập ---
def generate_key(length_days):
    key_value = hashlib.sha256(os.urandom(24)).hexdigest()[:16]
    created_at = datetime.now()
    expires_at = created_at + timedelta(days=length_days)

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO access_keys (key_value, created_at, expires_at, is_active)
                VALUES (?, ?, ?, 1)
            ''', (key_value, created_at.strftime("%Y-%m-%d %H:%M:%S"), expires_at.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            return key_value, expires_at
        except sqlite3.IntegrityError:
            return generate_key(length_days)
        except Exception as e:
            print(f"LỖI: Không thể tạo key: {e}")
            sys.stdout.flush()
            return None, None
        finally:
            conn.close()

def get_user_active_key(user_id):
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT key_value, expires_at FROM access_keys
            WHERE user_id = ? AND is_active = 1 AND expires_at > ?
        ''', (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        key_info = cursor.fetchone()
        conn.close()
        return key_info

def activate_key_for_user(key_value, user_id):
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        activated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('''
            SELECT expires_at, user_id FROM access_keys
            WHERE key_value = ? AND is_active = 1
        ''', (key_value,))
        key_data = cursor.fetchone()

        if key_data:
            expires_at_str, existing_user_id = key_data
            expires_at = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")

            if existing_user_id is not None:
                conn.close()
                return False, "Key này đã được kích hoạt bởi một người dùng khác."

            if expires_at < datetime.now():
                conn.close()
                return False, "Key này đã hết hạn."

            cursor.execute('''
                UPDATE access_keys SET user_id = ?, activated_at = ?
                WHERE key_value = ?
            ''', (user_id, activated_at, key_value))
            conn.commit()
            conn.close()
            return True, "Key đã được kích hoạt thành công!"
        else:
            conn.close()
            return False, "Key không hợp lệ hoặc không tồn tại."

def check_user_access(user_id):
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT expires_at FROM access_keys
            WHERE user_id = ? AND is_active = 1 AND expires_at > ?
        ''', (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

        result = cursor.fetchone()
        conn.close()

        if result:
            expires_at_str = result[0]
            expires_at = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
            remaining_time = expires_at - datetime.now()
            days_left = remaining_time.days
            hours_left = remaining_time.seconds // 3600
            return True, f"Key của bạn còn hạn \\*\\*{days_left} ngày {hours_left} giờ\\*\\*\\."
        else:
            return False, "Bạn không có key hợp lệ hoặc key đã hết hạn\\. Vui lòng \\`/kichhoat <key_của_bạn>\\` để sử dụng bot\\." # Escape backticks

# Middleware để kiểm tra quyền truy cập cho các lệnh yêu cầu key
def require_access(func):
    def wrapper(message):
        if is_admin(message.chat.id):
            func(message)
            return

        has_access, reason = check_user_access(message.chat.id)
        if has_access:
            func(message)
        else:
            bot.reply_to(message, reason, parse_mode='MarkdownV2') # Đổi sang MarkdownV2
    return wrapper

# --- Các Lệnh của Bot ---
@bot.message_handler(commands=['start', 'help'])
def show_help(message):
    help_text = (
        "Xin chào\\! Tôi là bot dự đoán Tài Xỉu\\.\n"
        "Để sử dụng các tính năng dự đoán, bạn cần có key truy cập\\.\n\n"
        "---\\n"
        "\\`\\/kichhoat <key_của_bạn>\\`\\: Kích hoạt key truy cập\\.\n"
        "\\`\\/kiemtrakey\\`\\: Kiểm tra trạng thái và thời hạn key của bạn\\.\n"
        "\\`\\/du_doan <tên_game>\\`\\: Xem dự đoán cho game \\(ví dụ\\: \\`/du_doan luckywin`\\)\\.\n\n"
    )

    if is_admin(message.chat.id):
        help_text += (
            "---\\n"
            "👑 Lệnh dành cho Admin 👑\n"
            "👑 \\`\\/taokey <số_ngày>\\`\\: Tạo một key mới có thời hạn \\(ví dụ\\: \\`/taokey 30`\\)\\.\n"
            "👑 \\`\\/keys\\`\\: Xem danh sách các key đã tạo\\.\n"
            "👑 \\`\\/status_bot\\`\\: Xem trạng thái bot và thống kê mẫu cầu\\.\n"
            "👑 \\`\\/trichcau\\`\\: Trích xuất toàn bộ dữ liệu mẫu cầu đã học ra file TXT\\.\n"
            "👑 \\`\\/nhapcau\\`\\: Nhập lại dữ liệu mẫu cầu đã học từ file TXT bạn gửi lên\\.\n"
            "👑 \\`\\/reset_patterns\\`\\: Đặt lại toàn bộ mẫu cầu đã học \\(cần xác nhận\\)\\.\n"
            "👑 \\`\\/history <tên_game> <số_lượng>\\`\\: Lấy lịch sử N phiên của game \\(ví dụ\\: \\`/history luckywin 10`\\)\\.\n"
        )
    else:
        help_text += "Liên hệ admin để được cấp key truy cập\\."

    bot.reply_to(message, escape_markdown_v2(help_text), parse_mode='MarkdownV2') # Đổi sang MarkdownV2

@bot.message_handler(commands=['kichhoat'])
def activate_key(message):
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, escape_markdown_v2("Vui lòng nhập key của bạn\\. Cú pháp: `/kichhoat <key_của_bạn>`"), parse_mode='MarkdownV2')
        return

    key_value = args[1]
    user_id = message.chat.id

    existing_key_info = get_user_active_key(user_id)
    if existing_key_info:
        bot.reply_to(message, escape_markdown_v2(f"Bạn đã có một key đang hoạt động: `{existing_key_info[0]}`\\. Hạn sử dụng đến {existing_key_info[1]}\\."), parse_mode='MarkdownV2')
        return

    success, msg = activate_key_for_user(key_value, user_id)
    if success:
        bot.reply_to(message, escape_markdown_v2(f"🎉 {msg} Bạn đã có thể sử dụng bot\\!"), parse_mode='MarkdownV2')
    else:
        bot.reply_to(message, escape_markdown_v2(f"⚠️ Kích hoạt thất bại: {msg}"), parse_mode='MarkdownV2')

@bot.message_handler(commands=['kiemtrakey'])
def check_key_status(message):
    has_access, reason = check_user_access(message.chat.id)
    if has_access:
        bot.reply_to(message, escape_markdown_v2(f"✅ Key của bạn đang hoạt động\\. {reason}"), parse_mode='MarkdownV2')
    else:
        bot.reply_to(message, escape_markdown_v2(f"⚠️ Key của bạn không hợp lệ hoặc đã hết hạn\\. {reason}"), parse_mode='MarkdownV2')

@bot.message_handler(commands=['du_doan'])
@require_access
def get_prediction_for_user(message):
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, escape_markdown_v2("Vui lòng chọn game muốn dự đoán\\. Cú pháp: `/du_doan <tên_game>`\\nCác game hỗ trợ: luckywin"), parse_mode='MarkdownV2')
        return

    game_input = args[1].lower()

    matched_game_key = None
    for key, config in GAME_CONFIGS.items():
        if game_input == key or game_input == config['game_name_vi'].lower().replace(' ', ''):
            matched_game_key = key
            break

    if not matched_game_key:
        bot.reply_to(message, escape_markdown_v2(f"Không tìm thấy game: '{game_input}'\\. Các game hỗ trợ: {', '.join([config['game_name_vi'] for config in GAME_CONFIGS.values()])}"), parse_mode='MarkdownV2')
        return

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT phien, total_point, dice1, dice2, dice3, result_tx FROM {GAME_CONFIGS[matched_game_key]['history_table']} ORDER BY id DESC LIMIT 1")
        last_full_history_record = cursor.fetchone()
        conn.close()

    prev_phien_id = "N/A"
    prev_result_tx = "N/A"
    prev_dices = ("N/A", "N/A", "N/A")
    prev_total_point = "N/A"
    if last_full_history_record:
        prev_phien_id = last_full_history_record[0]
        prev_total_point = last_full_history_record[1]
        prev_dices = (last_full_history_record[2], last_full_history_record[3], last_full_history_record[4])
        prev_result_tx = last_full_history_record[5]
    else:
        bot.reply_to(message, escape_markdown_v2("Chưa có dữ liệu lịch sử cho game này để dự đoán\\. Vui lòng chờ bot thu thập thêm dữ liệu\\."), parse_mode='MarkdownV2')
        return

    prediction, reason, confidence, _ = make_prediction_for_game(matched_game_key)

    recent_history_for_msg = get_recent_history(matched_game_key, limit=15, include_phien=True)
    recent_history_formatted = [f"#{p[0]}:{p[1]}" for p in recent_history_for_msg]

    formatted_message = format_prediction_message(
        GAME_CONFIGS[matched_game_key]['game_name_vi'],
        prev_phien_id,
        prev_phien_id, prev_result_tx, prev_dices, prev_total_point,
        prediction, reason, confidence, recent_history_formatted
    )

    bot.reply_to(message, formatted_message, parse_mode='MarkdownV2')

@bot.message_handler(commands=['status_bot'])
def show_status_bot(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này\\.")
        return

    status_message = "📊 \\*\\*THỐNG KÊ BOT DỰ ĐOÁN\\*\\* 📊\n\n"

    total_dep_patterns = 0
    total_xau_patterns = 0

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        for game_name, config in GAME_CONFIGS.items():
            status_message += f"\\*\\*{escape_markdown_v2(config['game_name_vi'])}\\*\\*:\n"

            cursor.execute("SELECT COUNT(*) FROM learned_patterns_db WHERE game_name = ? AND classification_type = 'dep'", (game_name,))
            dep_count_db = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM learned_patterns_db WHERE game_name = ? AND classification_type = 'xau'", (game_name,))
            xau_count_db = cursor.fetchone()[0]

            status_message += f"  \\- Mẫu cầu đẹp \\(trong DB\\): {dep_count_db}\n"
            status_message += f"  \\- Mẫu cầu xấu \\(trong DB\\): {xau_count_db}\n"
            total_dep_patterns += dep_count_db
            total_xau_patterns += xau_count_db

            cursor.execute(f"SELECT COUNT(*) FROM {config['history_table']}")
            total_history = cursor.fetchone()[0]
            status_message += f"  \\- Tổng lịch sử phiên trong DB: {total_history}\n\n"

        cursor.execute("SELECT COUNT(*) FROM access_keys")
        total_keys = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM access_keys WHERE user_id IS NOT NULL AND expires_at > ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        active_keys = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM access_keys WHERE user_id IS NULL AND expires_at > ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        unused_keys = cursor.fetchone()[0]

        conn.close()

    status_message += f"\\*\\*Tổng cộng các mẫu cầu đã học \\(từ DB\\):\\*\\*\n"
    status_message += f"  \\- Cầu đẹp: {total_dep_patterns}\n"
    status_message += f"  \\- Cầu xấu: {total_xau_patterns}\n\n"
    status_message += f"\\*\\*Thống kê Key Truy Cập:\\*\\*\n"
    status_message += f"  \\- Tổng số key đã tạo: {total_keys}\n"
    status_message += f"  \\- Key đang hoạt động: {active_keys}\n"
    status_message += f"  \\- Key chưa dùng \\(còn hạn\\): {unused_keys}\n"

    bot.reply_to(message, status_message, parse_mode='MarkdownV2')

@bot.message_handler(commands=['reset_patterns'])
def reset_patterns_confirmation(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này\\.")
        return

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("✅ Xác nhận Reset", callback_data="confirm_reset_patterns"))
    bot.reply_to(message, "Bạn có chắc chắn muốn xóa toàn bộ mẫu cầu đã học không\\? Hành động này không thể hoàn tác và bot sẽ phải học lại từ đầu\\.", reply_markup=markup, parse_mode='MarkdownV2')

@bot.callback_query_handler(func=lambda call: call.data == "confirm_reset_patterns")
def confirm_reset_patterns(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Bạn không có quyền thực hiện hành động này\\.")
        return

    with DB_LOCK:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM learned_patterns_db")
            conn.commit()
            conn.close()

            global LEARNED_PATTERNS
            LEARNED_PATTERNS = {game: {'dep': {}, 'xau': {}} for game in GAME_CONFIGS.keys()}

            bot.answer_callback_query(call.id, "Đã reset toàn bộ mẫu cầu\\!")
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                  text=escape_markdown_v2("✅ Toàn bộ mẫu cầu đã được xóa và reset trong database và bộ nhớ bot\\."), parse_mode='MarkdownV2')
            print("DEBUG: Đã reset toàn bộ mẫu cầu từ DB và RAM\\.")
            sys.stdout.flush()
        except Exception as e:
            bot.answer_callback_query(call.id, "Lỗi khi reset mẫu cầu\\.")
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                  text=escape_markdown_v2(f"Lỗi khi reset mẫu cầu: {e}"), parse_mode='MarkdownV2')
            print(f"LỖI: Lỗi khi reset mẫu cầu: {e}")
            sys.stdout.flush()

@bot.message_handler(commands=['history'])
def get_game_history(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này\\.")
        return

    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, escape_markdown_v2("Cú pháp: `/history <tên_game> <số_lượng_phiên>`\\nVí dụ: `/history luckywin 10`"), parse_mode='MarkdownV2')
        return

    game_input = args[1].lower()
    limit_str = args[2]

    matched_game_key = None
    for key, config in GAME_CONFIGS.items():
        if game_input == key or game_input == config['game_name_vi'].lower().replace(' ', ''):
            matched_game_key = key
            break

    if not matched_game_key:
        bot.reply_to(message, escape_markdown_v2(f"Không tìm thấy game: '{game_input}'\\. Các game hỗ trợ: {', '.join([config['game_name_vi'] for config in GAME_CONFIGS.values()])}"), parse_mode='MarkdownV2')
        return

    try:
        limit = int(limit_str)
        if limit <= 0 or limit > 200:
            bot.reply_to(message, "Số lượng phiên phải là số nguyên dương và không quá 200\\.")
            return
    except ValueError:
        bot.reply_to(message, "Số lượng phiên phải là một số hợp lệ\\.")
        return

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(f"SELECT phien, total_point, result_tx, dice1, dice2, dice3 FROM {GAME_CONFIGS[matched_game_key]['history_table']} ORDER BY id DESC LIMIT ?", (limit,))
            history_records = cursor.fetchall()
            conn.close()

            if not history_records:
                bot.reply_to(message, escape_markdown_v2(f"Không có lịch sử cho game \\*\\*{GAME_CONFIGS[matched_game_key]['game_name_vi']}\\*\\* trong database\\."), parse_mode='MarkdownV2')
                return

            history_message = f"\\*\\*Lịch sử {limit} phiên gần nhất của {escape_markdown_v2(GAME_CONFIGS[matched_game_key]['game_name_vi'])}\\*\\*:\n\n"
            for record in reversed(history_records):
                phien, total_point, result_tx, d1, d2, d3 = record
                history_message += f"\\*\\*\\#{escape_markdown_v2(str(phien))}\\*\\*: \\[{d1} {d2} {d3}\\] = \\*\\*{total_point}\\*\\* \\({result_tx}\\)\n"

            if len(history_message) > 4096:
                for i in range(0, len(history_message), 4000):
                    bot.reply_to(message, history_message[i:i+4000], parse_mode='MarkdownV2')
            else:
                bot.reply_to(message, history_message, parse_mode='MarkdownV2')

        except Exception as e:
            bot.reply_to(message, escape_markdown_v2(f"Đã xảy ra lỗi khi lấy lịch sử: {e}"), parse_mode='MarkdownV2')
            print(f"LỖI: Lỗi khi lấy lịch sử game: {e}")
            sys.stdout.flush()

@bot.message_handler(commands=['trichcau'])
def extract_cau_patterns(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này\\.")
        return

    all_patterns_content = ""
    for game_name, config in GAME_CONFIGS.items():
        all_patterns_content += f"===== Mẫu cầu cho {config['game_name_vi']} =====\n\n"

        with DB_LOCK:
            conn = get_db_connection()
            cursor = conn.cursor()

            dep_patterns_db = []
            cursor.execute("SELECT result_sequence FROM learned_patterns_db WHERE game_name = ? AND classification_type = 'dep'", (game_name,))
            for row in cursor.fetchall():
                dep_patterns_db.append(row[0])
            dep_patterns_db.sort()

            xau_patterns_db = []
            cursor.execute("SELECT result_sequence FROM learned_patterns_db WHERE game_name = ? AND classification_type = 'xau'", (game_name,))
            for row in cursor.fetchall():
                xau_patterns_db.append(row[0])
            xau_patterns_db.sort()

            conn.close()

        all_patterns_content += "--- Cầu Đẹp ---\n"
        if dep_patterns_db:
            all_patterns_content += "\n".join(dep_patterns_db) + "\n\n"
        else:
            all_patterns_content += "Không có mẫu cầu đẹp\\.\n\n"

        all_patterns_content += "--- Cầu Xấu ---\n"
        if xau_patterns_db:
            all_patterns_content += "\n".join(xau_patterns_db) + "\n\n"
        else:
            all_patterns_content += "Không có mẫu cầu xấu\\.\n\n"

        all_patterns_content += "\n"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"cau_patterns_{timestamp}.txt"
    file_path = os.path.join(TEMP_DIR, file_name)

    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(all_patterns_content)

        with open(file_path, 'rb') as f_to_send:
            bot.send_document(message.chat.id, f_to_send, caption=escape_markdown_v2("Đây là toàn bộ dữ liệu mẫu cầu đã học của bot\\. Bạn có thể sử dụng file này với lệnh \\`/nhapcau`\\ để khôi phục\\."), parse_mode='MarkdownV2')

        os.remove(file_path)
        print(f"DEBUG: Đã gửi và xóa file '{file_name}'.")
        sys.stdout.flush()

    except Exception as e:
        bot.reply_to(message, escape_markdown_v2(f"Đã xảy ra lỗi khi trích xuất hoặc gửi file: {e}"), parse_mode='MarkdownV2')
        print(f"LỖI: Lỗi khi trích xuất mẫu cầu: {e}")
        sys.stdout.flush()

@bot.message_handler(commands=['nhapcau'])
def ask_for_cau_file(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này\\.")
        return

    waiting_for_cau_file[message.chat.id] = True
    bot.reply_to(message, escape_markdown_v2("Vui lòng gửi file \\`.txt`\\ chứa dữ liệu mẫu cầu bạn muốn bot tải lại\\. Đảm bảo định dạng file giống file bot đã trích xuất bằng lệnh \\`/trichcau`\\."), parse_mode='MarkdownV2')

@bot.message_handler(content_types=['document'])
def handle_document_for_cau_patterns(message):
    user_id = message.chat.id
    if user_id not in ADMIN_IDS or not waiting_for_cau_file.get(user_id):
        return

    if message.document.mime_type != 'text/plain' or not message.document.file_name.endswith('.txt'):
        bot.reply_to(message, "File bạn gửi không phải là file \\`.txt`\\ hợp lệ\\. Vui lòng gửi lại file \\`.txt`\\.", parse_mode='MarkdownV2')
        waiting_for_cau_file[user_id] = False
        return

    temp_file_path = None
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        temp_file_path = os.path.join(TEMP_DIR, message.document.file_name)
        with open(temp_file_path, 'wb') as f:
            f.write(downloaded_file)

        new_cau_dep = {game: {} for game in GAME_CONFIGS.keys()}
        new_cau_xau = {game: {} for game in GAME_CONFIGS.keys()}
        current_game = None
        current_section = None

        with open(temp_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("===== Mẫu cầu cho"):
                    for game_key, config in GAME_CONFIGS.items():
                        if config['game_name_vi'] in line:
                            current_game = game_key
                            break
                    current_section = None
                elif line == "--- Cầu Đẹp ---":
                    current_section = 'dep'
                elif line == "--- Cầu Xấu ---":
                    current_section = 'xau'
                elif line and current_game and current_section:
                    if "Không có mẫu cầu" not in line and not line.startswith("===") and not line.startswith("---"):
                        pattern_seq = line
                        pattern_type = 'manual_import'
                        if len(set(pattern_seq)) == 1:
                            pattern_type = f'bet_{pattern_seq[0]}'
                        elif len(pattern_seq) >= 2 and all(pattern_seq[j] != pattern_seq[j+1] for j in range(len(pattern_seq) - 1)):
                            pattern_type = f'zigzag_{pattern_seq[0]}{pattern_seq[1]}'
                        elif len(pattern_seq) >= 3 and all(pattern_seq[j] != pattern_seq[j+1] for j in range(len(pattern_seq) - 1)):
                             pattern_type = '1-2-1'

                        if current_section == 'dep':
                            new_cau_dep[current_game][pattern_seq] = {'type': pattern_type, 'confidence': len(pattern_seq)}
                        elif current_section == 'xau':
                            new_cau_xau[current_game][pattern_seq] = {'type': pattern_type, 'confidence': len(pattern_seq)}

        global LEARNED_PATTERNS
        for game_key in GAME_CONFIGS.keys():
            LEARNED_PATTERNS[game_key]['dep'] = new_cau_dep.get(game_key, {})
            LEARNED_PATTERged_game_key]['xau'] = new_cau_xau.get(game_key, {})

        with DB_LOCK:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM learned_patterns_db")
            for g_name, data_types in LEARNED_PATTERNS.items():
                for c_type, patterns_dict in data_types.items():
                    for p_seq, p_data in patterns_dict.items():
                        save_learned_pattern_to_db(g_name, p_data['type'], p_seq, c_type, p_data['confidence'], None)
            conn.commit()
            conn.close()

        bot.reply_to(message, "✅ Đã tải lại dữ liệu mẫu cầu thành công từ file của bạn\\!", parse_mode='MarkdownV2')
        print(f"DEBUG: Đã tải lại mẫu cầu từ file '{message.document.file_name}'.")
        sys.stdout.flush()

    except Exception as e:
        bot.reply_to(message, escape_markdown_v2(f"Đã xảy ra lỗi khi xử lý file hoặc tải lại dữ liệu: {e}"), parse_mode='MarkdownV2')
        print(f"LỖI: Lỗi khi nhập mẫu cầu từ file: {e}")
        sys.stdout.flush()
    finally:
        waiting_for_cau_file[user_id] = False
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)

@bot.message_handler(commands=['taokey'])
def create_new_key(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này\\.")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, escape_markdown_v2("Cú pháp: `/taokey <số_ngày_sử_dụng>` \\(ví dụ: `/taokey 30`\\)"), parse_mode='MarkdownV2')
        return

    try:
        days = int(args[1])
        if days <= 0 or days > 3650:
            bot.reply_to(message, "Số ngày sử dụng phải là số nguyên dương và không quá 3650 ngày \\(10 năm\\)\\.", parse_mode='MarkdownV2')
            return

        key_value, expires_at = generate_key(days)
        if key_value:
            bot.reply_to(message,
                         escape_markdown_v2(f"🔑 \\*\\*Đã tạo key mới thành công\\!\\*\\*\\n\\n"
                         f"Key: \\`{key_value}\\`\\n"
                         f"Hạn sử dụng: \\*\\*{expires_at.strftime('%Y-%m-%d %H:%M:%S')}\\*\\*\\n\\n"
                         f"Hãy gửi key này cho người dùng và hướng dẫn họ dùng lệnh \\`/kichhoat {key_value}`"),
                         parse_mode='MarkdownV2')
        else:
            bot.reply_to(message, "Đã xảy ra lỗi khi tạo key\\.", parse_mode='MarkdownV2')
    except ValueError:
        bot.reply_to(message, "Số ngày sử dụng phải là một số nguyên hợp lệ\\.", parse_mode='MarkdownV2')
    except Exception as e:
        bot.reply_to(message, escape_markdown_v2(f"Đã xảy ra lỗi không xác định: {e}"), parse_mode='MarkdownV2')

@bot.message_handler(commands=['keys'])
def list_keys(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này\\.")
        return

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT key_value, created_at, expires_at, user_id, activated_at, is_active FROM access_keys ORDER BY created_at DESC")
            keys = cursor.fetchall()
            conn.close()

            if not keys:
                bot.reply_to(message, "Chưa có key nào được tạo\\.", parse_mode='MarkdownV2')
                return

            key_list_message = "🔑 \\*\\*Danh sách các Key truy cập\\*\\* 🔑\n\n"
            for key in keys:
                key_value, created_at, expires_at_str, user_id, activated_at, is_active = key

                status = ""
                if not is_active:
                    status = "🚫 Đã hủy"
                elif user_id:
                    expires_dt = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
                    if expires_dt < datetime.now():
                        status = "🔴 Hết hạn"
                    else:
                        remaining = expires_dt - datetime.now()
                        status = f"🟢 Đang dùng bởi \\`{user_id}\\` \\(còn {remaining.days} ngày\\)"
                else:
                    expires_dt = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
                    if expires_dt < datetime.now():
                        status = "⚪ Hết hạn \\(chưa dùng\\)"
                    else:
                        status = "🔵 Chưa dùng"

                key_list_message += f"\\`{key_value}\\` \\- {status}\n"
                key_list_message += f"  _Tạo: {created_at}_"
                if user_id:
                    key_list_message += f" _\\- Kích hoạt: {activated_at}_"
                key_list_message += f" _\\- HSD: {expires_at_str}_\n\n"

            if len(key_list_message) > 4096:
                for i in range(0, len(key_list_message), 4000):
                    bot.reply_to(message, key_list_message[i:i+4000], parse_mode='MarkdownV2')
            else:
                bot.reply_to(message, key_list_message, parse_mode='MarkdownV2')

        except Exception as e:
            bot.reply_to(message, escape_markdown_v2(f"Đã xảy ra lỗi khi lấy danh sách key: {e}"), parse_mode='MarkdownV2')
            print(f"LỖI: Lỗi khi lấy danh sách key: {e}")
            sys.stdout.flush()

# --- Khởi động Bot ---
def start_bot_threads():
    init_db()
    load_cau_patterns_from_db()

    web_server_thread = Thread(target=run_web_server)
    web_server_thread.daemon = True
    web_server_thread.start()
    print("DEBUG: Đã khởi động luồng web server.")
    sys.stdout.flush()

    api_checker_thread = threading.Thread(target=check_apis_loop)
    api_checker_thread.daemon = True
    api_checker_thread.start()
    print("DEBUG: Đã khởi động luồng kiểm tra API.")
    sys.stdout.flush()

    print("Bot đang khởi động và sẵn sàng nhận lệnh...")
    sys.stdout.flush()
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        print(f"LỖI: Bot polling dừng đột ngột: {e}")
        sys.stdout.flush()

if __name__ == "__main__":
    start_bot_threads()
