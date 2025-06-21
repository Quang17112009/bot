import telebot
import requests
import json
import time
import threading
from datetime import datetime, timedelta
import os
import sys
import sqlite3
import hashlib # Để tạo key ngẫu nhiên và an toàn hơn

# --- Cấu hình Bot và Admin ---
# THAY THẾ BẰNG BOT_TOKEN CỦA BẠN (Lấy từ BotFather, KHÔNG PHẢI TOKEN MẪU)
BOT_TOKEN = "7820739987:AAE_eU2JPZH7u6KnDRq31_l4tn64AD_8f6s" 
# THAY THAY BẰNG ID TELEGRAM CỦA BẠN (VD: [123456789])
ADMIN_IDS = [6915752059] 
bot = telebot.TeleBot(BOT_TOKEN)

# --- Cấu hình Game ---
GAME_CONFIGS = {
    'luckywin': {'api_url': 'https://luckywin01.com/api/web/getLogs?game_code=TAIXIU', 'game_name_vi': 'Luckywin', 'history_table': 'luckywin_history'},
    'hitclub': {'api_url': 'https://apphit.club/api/web/getLogs?game_code=TAIXIU', 'game_name_vi': 'Hit Club', 'history_table': 'hitclub_history'},
    'sunwin': {'api_url': 'https://sunwin.ist/api/web/getLogs?game_code=TAIXIU', 'game_name_vi': 'Sunwin', 'history_table': 'sunwin_history'}
}

# --- Biến Toàn Cục và Cấu Hình Lưu Trữ ---
LAST_FETCHED_IDS = {game: 0 for game in GAME_CONFIGS.keys()}
CHECK_INTERVAL_SECONDS = 5 # Kiểm tra API mỗi 5 giây
CAU_DEP = {game: set() for game in GAME_CONFIGS.keys()}
CAU_XAU = {game: set() for game in GAME_CONFIGS.keys()}
CAU_MIN_LENGTH = 5 # Độ dài tối thiểu của mẫu cầu để phân loại
RECENT_HISTORY_FETCH_LIMIT = 50 # Số phiên lịch sử gần nhất để lấy từ DB phục vụ việc học mẫu cầu

TEMP_DIR = 'temp_bot_files' # Thư mục để lưu file tạm thời
DB_NAME = 'bot_data.db' # Tên file database SQLite

# Tạo thư mục nếu chưa tồn tại
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# Biến tạm để lưu trạng thái chờ file của admin (cho lệnh /nhapcau)
waiting_for_cau_file = {} # {admin_id: True}

# --- Hàm Hỗ Trợ Chung ---
def is_admin(user_id):
    """Kiểm tra xem user_id có phải là admin hay không."""
    return user_id in ADMIN_IDS

def get_db_connection():
    """Tạo và trả về kết nối đến cơ sở dữ liệu SQLite."""
    conn = sqlite3.connect(DB_NAME)
    return conn

def init_db():
    """Khởi tạo các bảng cần thiết trong cơ sở dữ liệu nếu chúng chưa tồn tại."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Bảng mẫu cầu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cau_patterns (
            game_name TEXT NOT NULL,
            pattern TEXT NOT NULL,
            type TEXT NOT NULL, -- 'dep' or 'xau'
            PRIMARY KEY (game_name, pattern, type)
        )
    ''')

    # Bảng lịch sử cho mỗi game
    for game_name, config in GAME_CONFIGS.items():
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {config['history_table']} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phien INTEGER UNIQUE NOT NULL,
                result_tx TEXT NOT NULL,
                total_point INTEGER NOT NULL,
                dice1 INTEGER NOT NULL,
                dice2 INTEGER NOT NULL,
                dice3 INTEGER NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
    
    # Bảng quản lý key truy cập
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS access_keys (
            key_value TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            user_id INTEGER, -- User ID đã sử dụng key này (NULL nếu chưa dùng)
            activated_at TEXT, -- Thời điểm key được kích hoạt bởi user_id
            is_active INTEGER NOT NULL DEFAULT 1 -- 1 là active, 0 là deactivated bởi admin
        )
    ''')

    conn.commit()
    conn.close()

# --- Quản lý Mẫu Cầu (Sử dụng SQLite) ---
def load_cau_patterns_from_db():
    """Tải tất cả mẫu cầu từ database vào biến toàn cục CAU_DEP và CAU_XAU."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for game_name in GAME_CONFIGS.keys():
        CAU_DEP[game_name].clear()
        CAU_XAU[game_name].clear()

        cursor.execute("SELECT pattern FROM cau_patterns WHERE game_name = ? AND type = 'dep'", (game_name,))
        for row in cursor.fetchall():
            CAU_DEP[game_name].add(row[0])
        
        cursor.execute("SELECT pattern FROM cau_patterns WHERE game_name = ? AND type = 'xau'", (game_name,))
        for row in cursor.fetchall():
            CAU_XAU[game_name].add(row[0])
            
    conn.close()
    print(f"DEBUG: Đã tải mẫu cầu từ DB. Tổng cầu đẹp: {sum(len(v) for v in CAU_DEP.values())}, Tổng cầu xấu: {sum(len(v) for v in CAU_XAU.values())}")
    sys.stdout.flush()

def save_cau_patterns_to_db():
    """Lưu tất cả mẫu cầu từ biến toàn cục CAU_DEP và CAU_XAU vào database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Xóa tất cả mẫu cũ để tránh trùng lặp và cập nhật lại
    cursor.execute("DELETE FROM cau_patterns")

    # Thêm mẫu cầu đẹp
    for game_name, patterns in CAU_DEP.items():
        if patterns:
            data = [(game_name, pattern, 'dep') for pattern in patterns]
            cursor.executemany("INSERT INTO cau_patterns (game_name, pattern, type) VALUES (?, ?, ?)", data)
    
    # Thêm mẫu cầu xấu
    for game_name, patterns in CAU_XAU.items():
        if patterns:
            data = [(game_name, pattern, 'xau') for pattern in patterns]
            cursor.executemany("INSERT INTO cau_patterns (game_name, pattern, type) VALUES (?, ?, ?)", data)
            
    conn.commit()
    conn.close()

# --- Lịch sử Phiên Game (Sử dụng SQLite) ---
def save_game_result(game_name, phien, result_tx, total_point, dice1, dice2, dice3):
    """Lưu kết quả của một phiên game vào bảng lịch sử tương ứng trong database."""
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
    except sqlite3.IntegrityError:
        pass # Phiên đã tồn tại, bỏ qua
    except Exception as e:
        print(f"LỖI: Không thể lưu kết quả phiên {phien} cho {game_name} vào DB: {e}")
        sys.stdout.flush()
    finally:
        conn.close()

def get_recent_history_tx(game_name, limit=RECENT_HISTORY_FETCH_LIMIT):
    """Lấy N ký tự 'T', 'X', 'B' của các phiên gần nhất từ database, theo thứ tự cũ đến mới."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"SELECT result_tx FROM {GAME_CONFIGS[game_name]['history_table']} ORDER BY phien DESC LIMIT ?", (limit,))
    history = [row[0] for row in cursor.fetchall()]
    conn.close()
    return history[::-1] # Đảo ngược để có thứ tự từ cũ đến mới

# --- Logic Học và Dự Đoán ---
def classify_and_learn_cau(game_name):
    """
    Học các mẫu cầu 'đẹp' hoặc 'xấu' dựa trên lịch sử phiên và lưu vào database.
    Mẫu cầu được xem xét là chuỗi CAU_MIN_LENGTH ký tự ('T', 'X', 'B').
    """
    recent_history_tx = get_recent_history_tx(game_name, limit=RECENT_HISTORY_FETCH_LIMIT)
    
    if len(recent_history_tx) < CAU_MIN_LENGTH + 1:
        return 
    
    for i in range(len(recent_history_tx) - CAU_MIN_LENGTH):
        pattern_to_classify = "".join(recent_history_tx[i : i + CAU_MIN_LENGTH])
        actual_result_for_pattern = recent_history_tx[i + CAU_MIN_LENGTH]

        is_bet = (pattern_to_classify.count('T') == CAU_MIN_LENGTH) or \
                 (pattern_to_classify.count('X') == CAU_MIN_LENGTH) or \
                 (pattern_to_classify.count('B') == CAU_MIN_LENGTH)
        
        is_ziczac = True
        for j in range(CAU_MIN_LENGTH - 1):
            if pattern_to_classify[j] == pattern_to_classify[j+1]:
                is_ziczac = False
                break
        
        if 'B' in pattern_to_classify and (pattern_to_classify.count('B') != CAU_MIN_LENGTH):
            is_bet = False
            is_ziczac = False 

        if is_bet:
            expected_result = pattern_to_classify[-1]
            if actual_result_for_pattern == expected_result:
                if pattern_to_classify not in CAU_XAU[game_name]:
                     CAU_DEP[game_name].add(pattern_to_classify)
            else:
                if pattern_to_classify in CAU_DEP[game_name]:
                     CAU_DEP[game_name].remove(pattern_to_classify)
                CAU_XAU[game_name].add(pattern_to_classify)
        elif is_ziczac:
            if pattern_to_classify[-1] == 'T': expected_result = 'X'
            elif pattern_to_classify[-1] == 'X': expected_result = 'T'
            else: expected_result = actual_result_for_pattern

            if actual_result_for_pattern == expected_result:
                if pattern_to_classify not in CAU_XAU[game_name]:
                     CAU_DEP[game_name].add(pattern_to_classify)
            else:
                if pattern_to_classify in CAU_DEP[game_name]:
                     CAU_DEP[game_name].remove(pattern_to_classify)
                CAU_XAU[game_name].add(pattern_to_classify)
        else:
            pass # Hiện tại không phân loại các mẫu không rõ ràng

    save_cau_patterns_to_db()

def make_prediction_for_game(game_name):
    """Đưa ra dự đoán cho phiên tiếp theo dựa trên các mẫu cầu đã học."""
    recent_history_tx = get_recent_history_tx(game_name, limit=CAU_MIN_LENGTH)
    
    if len(recent_history_tx) < CAU_MIN_LENGTH:
        return "Chưa đủ lịch sử để dự đoán mẫu cầu.", "N/A"
    
    current_cau_for_prediction = "".join(recent_history_tx[-CAU_MIN_LENGTH:])
    
    prediction_text = f"📊 Mẫu cầu hiện tại: **{current_cau_for_prediction}**\n"
    predicted_value = "N/A"

    if current_cau_for_prediction in CAU_DEP[game_name]:
        predicted_value = current_cau_for_prediction[-1] 
        prediction_text += f"✅ Phát hiện mẫu cầu đẹp. Khả năng cao ra: **{predicted_value}**\n"
    elif current_cau_for_prediction in CAU_XAU[game_name]:
        # Dự đoán ngược lại hoặc theo một logic khác cho cầu xấu
        if current_cau_for_prediction[-1] == 'T': predicted_value = 'X'
        elif current_cau_for_prediction[-1] == 'X': predicted_value = 'T'
        else: predicted_value = 'T' # Nếu là 'B', dự đoán T
        prediction_text += f"❌ Phát hiện mẫu cầu xấu. Khả năng cao ra: **{predicted_value}** (Dự đoán ngược)\n"
    else:
        # Nếu không có trong cầu đẹp/xấu, dự đoán dựa trên xác suất hoặc xu hướng đơn giản
        prediction_text += "🧐 Chưa có mẫu cầu rõ ràng để dự đoán.\n"
        # VD: Dự đoán ngược lại kết quả phiên cuối cùng
        if recent_history_tx[-1] == 'T': predicted_value = 'X'
        elif recent_history_tx[-1] == 'X': predicted_value = 'T'
        else: predicted_value = 'T' # Nếu là 'B', dự đoán T
        prediction_text += f"👉 Khả năng cao ra: **{predicted_value}** (Dựa trên phiên gần nhất)\n"

    return prediction_text, predicted_value

# --- Logic Xử lý Game ---
def process_game_api_fetch(game_name, config):
    """Kết nối API, xử lý dữ liệu phiên mới, lưu vào DB."""
    url = config['api_url']
    game_name_vi = config['game_name_vi']

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data and 'logs' in data and data['logs']:
            latest_log = data['logs'][0]
            phien = latest_log.get('phien')
            result_points = latest_log.get('result_points')
            dices = latest_log.get('dices')

            if phien and phien > LAST_FETCHED_IDS[game_name]:
                LAST_FETCHED_IDS[game_name] = phien

                if result_points is not None and dices and len(dices) == 3:
                    total_point = sum(dices)
                    result_tx = 'T' if total_point >= 11 else 'X'
                    if dices[0] == dices[1] == dices[2]:
                        result_tx = 'B'

                    save_game_result(game_name, phien, result_tx, total_point, dices[0], dices[1], dices[2])
                    classify_and_learn_cau(game_name)

                    # Gửi thông báo kết quả phiên mới và dự đoán cho phiên tiếp theo đến Admin
                    prediction_message_part, _ = make_prediction_for_game(game_name)
                    
                    full_message = f"🔔 **{game_name_vi} - Phiên mới kết thúc!**\n\n"
                    full_message += prediction_message_part # Phần dự đoán
                    full_message += f"\n⚡ **Kết quả phiên {phien}**: "
                    for i, dice in enumerate(dices):
                        full_message += f"[{dice}] "
                        if i < 2: full_message += "+ "
                    full_message += f"= **{total_point}** ({result_tx})"
                    
                    for admin_id in ADMIN_IDS:
                        try:
                            bot.send_message(admin_id, full_message, parse_mode='Markdown')
                        except telebot.apihelper.ApiTelegramException as e:
                            print(f"LỖI: Không thể gửi tin nhắn đến admin {admin_id}: {e}")
                        
                    print(f"DEBUG: Đã xử lý và gửi thông báo cho {game_name_vi} phiên {phien}.")
                    sys.stdout.flush()
                else:
                    print(f"LỖI: Thiếu dữ liệu result_points hoặc dices từ API {game_name_vi} cho phiên {phien}.")
                    sys.stdout.flush()

    except requests.exceptions.RequestException as e:
        print(f"LỖI: Không thể kết nối hoặc lấy dữ liệu từ {game_name_vi} API: {e}")
        sys.stdout.flush()
    except json.JSONDecodeError as e:
        print(f"LỖI: Không thể giải mã JSON từ {game_name_vi} API: {e}")
        sys.stdout.flush()
    except Exception as e:
        print(f"LỖI: Xảy ra lỗi không xác định khi xử lý {game_name_vi}: {e}")
        sys.stdout.flush()

def check_apis_loop():
    """Vòng lặp liên tục kiểm tra API của các game."""
    while True:
        for game_name, config in GAME_CONFIGS.items():
            process_game_api_fetch(game_name, config)
        time.sleep(CHECK_INTERVAL_SECONDS)

# --- Quản lý Key Truy Cập ---
def generate_key(length_days):
    """Tạo một key ngẫu nhiên và lưu vào DB với thời hạn sử dụng."""
    key_value = hashlib.sha256(os.urandom(24)).hexdigest()[:16] # Key 16 ký tự hex
    created_at = datetime.now()
    expires_at = created_at + timedelta(days=length_days)

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
        # Key đã tồn tại, thử tạo lại (rất hiếm)
        return generate_key(length_days) 
    except Exception as e:
        print(f"LỖI: Không thể tạo key: {e}")
        sys.stdout.flush()
        return None, None
    finally:
        conn.close()

def get_user_active_key(user_id):
    """Lấy key đang hoạt động của người dùng."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT key_value, expires_at FROM access_keys 
        WHERE user_id = ? AND is_active = 1 AND expires_at > ?
    ''', (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    key_info = cursor.fetchone()
    conn.close()
    return key_info # (key_value, expires_at) or None

def activate_key_for_user(key_value, user_id):
    """Kích hoạt key cho một user_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    activated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Kiểm tra key có tồn tại, chưa được kích hoạt và còn hạn không
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

        # Kích hoạt key
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
    """Kiểm tra xem người dùng có quyền truy cập (key còn hạn) hay không."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Lấy key của user và kiểm tra hạn sử dụng
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
        return True, f"Key của bạn còn hạn **{days_left} ngày {hours_left} giờ**."
    else:
        return False, "Bạn không có key hợp lệ hoặc key đã hết hạn. Vui lòng `/kichhoat <key_của_bạn>` để sử dụng bot."

# Middleware để kiểm tra quyền truy cập cho các lệnh yêu cầu key
def require_access(func):
    def wrapper(message):
        if is_admin(message.chat.id): # Admin luôn có quyền
            func(message)
            return

        has_access, reason = check_user_access(message.chat.id)
        if has_access:
            func(message)
        else:
            bot.reply_to(message, reason, parse_mode='Markdown')
    return wrapper

# --- Các Lệnh của Bot ---
@bot.message_handler(commands=['start', 'help'])
def show_help(message):
    """Hiển thị tin nhắn trợ giúp và các lệnh có sẵn."""
    help_text = (
        "Xin chào! Tôi là bot dự đoán Tài Xỉu.\n"
        "Để sử dụng các tính năng dự đoán, bạn cần có key truy cập.\n\n"
        "--- Lệnh chung ---\n"
        "`/kichhoat <key_của_bạn>`: Kích hoạt key truy cập.\n"
        "`/kiemtrakey`: Kiểm tra trạng thái và thời hạn key của bạn.\n"
        "`/du_doan <tên_game>`: Xem dự đoán cho game (ví dụ: `/du_doan luckywin`).\n"
        "`/status_bot`: Xem trạng thái bot và thống kê mẫu cầu (chỉ admin).\n\n"
    )
    
    if is_admin(message.chat.id):
        help_text += (
            "--- 👑 Lệnh dành cho Admin 👑 ---\n"
            "👑 `/taokey <số_ngày>`: Tạo một key mới có thời hạn (ví dụ: `/taokey 30`).\n"
            "👑 `/keys`: Xem danh sách các key đã tạo.\n"
            "👑 `/trichcau`: Trích xuất toàn bộ dữ liệu mẫu cầu đã học ra file TXT.\n"
            "👑 `/nhapcau`: Nhập lại dữ liệu mẫu cầu đã học từ file TXT bạn gửi lên.\n"
            "👑 `/reset_patterns`: Đặt lại toàn bộ mẫu cầu đã học (cần xác nhận).\n"
            "👑 `/history <tên_game> <số_lượng>`: Lấy lịch sử N phiên của game (ví dụ: `/history luckywin 10`).\n"
        )
    else:
        help_text += "Liên hệ admin để được cấp key truy cập."
        
    bot.reply_to(message, help_text, parse_mode='Markdown')

# Lệnh mới để người dùng kích hoạt key
@bot.message_handler(commands=['kichhoat'])
def activate_key(message):
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Vui lòng nhập key của bạn. Cú pháp: `/kichhoat <key_của_bạn>`")
        return
    
    key_value = args[1]
    user_id = message.chat.id

    # Kiểm tra xem user đã có key active chưa
    existing_key_info = get_user_active_key(user_id)
    if existing_key_info:
        bot.reply_to(message, f"Bạn đã có một key đang hoạt động: `{existing_key_info[0]}`. Hạn sử dụng đến {existing_key_info[1]}.", parse_mode='Markdown')
        return

    success, msg = activate_key_for_user(key_value, user_id)
    if success:
        bot.reply_to(message, f"🎉 {msg} Bạn đã có thể sử dụng bot!")
    else:
        bot.reply_to(message, f"⚠️ Kích hoạt thất bại: {msg}")

# Lệnh mới để kiểm tra key của người dùng
@bot.message_handler(commands=['kiemtrakey'])
def check_key_status(message):
    has_access, reason = check_user_access(message.chat.id)
    if has_access:
        bot.reply_to(message, f"✅ Key của bạn đang hoạt động. {reason}", parse_mode='Markdown')
    else:
        bot.reply_to(message, f"⚠️ Key của bạn không hợp lệ hoặc đã hết hạn. {reason}", parse_mode='Markdown')

# Lệnh dự đoán, áp dụng middleware kiểm tra quyền
@bot.message_handler(commands=['du_doan'])
@require_access
def get_prediction_for_user(message):
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Vui lòng chọn game muốn dự đoán. Cú pháp: `/du_doan <tên_game>`\nCác game hỗ trợ: luckywin, hitclub, sunwin")
        return
    
    game_input = args[1].lower()
    
    matched_game_key = None
    for key, config in GAME_CONFIGS.items():
        if game_input == key or game_input == config['game_name_vi'].lower().replace(' ', ''):
            matched_game_key = key
            break
    
    if not matched_game_key:
        bot.reply_to(message, f"Không tìm thấy game: '{game_input}'. Các game hỗ trợ: {', '.join([config['game_name_vi'] for config in GAME_CONFIGS.values()])}")
        return

    prediction_text, _ = make_prediction_for_game(matched_game_key)
    bot.reply_to(message, f"**Dự đoán {GAME_CONFIGS[matched_game_key]['game_name_vi']} cho phiên tiếp theo:**\n\n{prediction_text}", parse_mode='Markdown')


# Lệnh cũ /status đổi tên thành /status_bot để tránh nhầm lẫn và chỉ admin dùng
@bot.message_handler(commands=['status_bot'])
def show_status_bot(message):
    """Hiển thị trạng thái hiện tại của bot và thống kê mẫu cầu."""
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    status_message = "📊 **THỐNG KÊ BOT DỰ ĐOÁN** 📊\n\n"

    total_dep_patterns = 0
    total_xau_patterns = 0

    conn = get_db_connection()
    cursor = conn.cursor()

    for game_name, config in GAME_CONFIGS.items():
        status_message += f"**{config['game_name_vi']}**:\n"
        
        dep_count = len(CAU_DEP.get(game_name, set()))
        xau_count = len(CAU_XAU.get(game_name, set()))
        status_message += f"  - Mẫu cầu đẹp: {dep_count}\n"
        status_message += f"  - Mẫu cầu xấu: {xau_count}\n"
        total_dep_patterns += dep_count
        total_xau_patterns += xau_count;

        cursor.execute(f"SELECT COUNT(*) FROM {config['history_table']}")
        total_history = cursor.fetchone()[0]
        status_message += f"  - Tổng lịch sử phiên trong DB: {total_history}\n\n"
    
    # Thống kê Keys
    cursor.execute("SELECT COUNT(*) FROM access_keys")
    total_keys = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM access_keys WHERE user_id IS NOT NULL AND expires_at > ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    active_keys = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM access_keys WHERE user_id IS NULL AND expires_at > ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    unused_keys = cursor.fetchone()[0]

    conn.close()

    status_message += f"**Tổng cộng các mẫu cầu đã học (trong RAM):**\n"
    status_message += f"  - Cầu đẹp: {total_dep_patterns}\n"
    status_message += f"  - Cầu xấu: {total_xau_patterns}\n\n"
    status_message += f"**Thống kê Key Truy Cập:**\n"
    status_message += f"  - Tổng số key đã tạo: {total_keys}\n"
    status_message += f"  - Key đang hoạt động: {active_keys}\n"
    status_message += f"  - Key chưa dùng (còn hạn): {unused_keys}\n"
    
    bot.reply_to(message, status_message, parse_mode='Markdown')

@bot.message_handler(commands=['reset_patterns'])
def reset_patterns_confirmation(message):
    """Yêu cầu xác nhận trước khi xóa toàn bộ mẫu cầu đã học."""
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("✅ Xác nhận Reset", callback_data="confirm_reset_patterns"))
    bot.reply_to(message, "Bạn có chắc chắn muốn xóa toàn bộ mẫu cầu đã học không? Hành động này không thể hoàn tác và bot sẽ phải học lại từ đầu.", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "confirm_reset_patterns")
def confirm_reset_patterns(call):
    """Xử lý xác nhận xóa mẫu cầu."""
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Bạn không có quyền thực hiện hành động này.")
        return

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cau_patterns")
        conn.commit()
        conn.close()

        global CAU_DEP, CAU_XAU
        CAU_DEP = {game: set() for game in GAME_CONFIGS.keys()}
        CAU_XAU = {game: set() for game in GAME_CONFIGS.keys()}

        bot.answer_callback_query(call.id, "Đã reset toàn bộ mẫu cầu!")
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, 
                              text="✅ Toàn bộ mẫu cầu đã được xóa và reset trong database và bộ nhớ bot.")
        print("DEBUG: Đã reset toàn bộ mẫu cầu từ DB và RAM.")
        sys.stdout.flush()
    except Exception as e:
        bot.answer_callback_query(call.id, "Lỗi khi reset mẫu cầu.")
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, 
                              text=f"Lỗi khi reset mẫu cầu: {e}")
        print(f"LỖI: Lỗi khi reset mẫu cầu: {e}")
        sys.stdout.flush()

@bot.message_handler(commands=['history'])
def get_game_history(message):
    """Lấy và hiển thị lịch sử N phiên của một game cụ thể từ database."""
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Cú pháp: `/history <tên_game> <số_lượng_phiên>`\nVí dụ: `/history luckywin 10`", parse_mode='Markdown')
        return

    game_input = args[1].lower()
    limit_str = args[2] 
    
    matched_game_key = None
    for key, config in GAME_CONFIGS.items():
        if game_input == key or game_input == config['game_name_vi'].lower().replace(' ', ''):
            matched_game_key = key
            break

    if not matched_game_key:
        bot.reply_to(message, f"Không tìm thấy game: '{game_input}'. Các game hỗ trợ: {', '.join([config['game_name_vi'] for config in GAME_CONFIGS.values()])}")
        return

    try:
        limit = int(limit_str)
        if limit <= 0 or limit > 200:
            bot.reply_to(message, "Số lượng phiên phải là số nguyên dương và không quá 200.")
            return
    except ValueError:
        bot.reply_to(message, "Số lượng phiên phải là một số hợp lệ.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(f"SELECT phien, total_point, result_tx, dice1, dice2, dice3 FROM {GAME_CONFIGS[matched_game_key]['history_table']} ORDER BY phien DESC LIMIT ?", (limit,))
        history_records = cursor.fetchall()
        conn.close()

        if not history_records:
            bot.reply_to(message, f"Không có lịch sử cho game **{GAME_CONFIGS[matched_game_key]['game_name_vi']}** trong database.", parse_mode='Markdown')
            return
        
        history_message = f"**Lịch sử {limit} phiên gần nhất của {GAME_CONFIGS[matched_game_key]['game_name_vi']}**:\n\n"
        for record in reversed(history_records): # Đảo ngược để hiển thị từ cũ đến mới
            phien, total_point, result_tx, d1, d2, d3 = record
            history_message += f"**#{phien}**: [{d1} {d2} {d3}] = **{total_point}** ({result_tx})\n"
        
        bot.reply_to(message, history_message, parse_mode='Markdown')

    except Exception as e:
        bot.reply_to(message, f"Đã xảy ra lỗi khi lấy lịch sử: {e}")
        print(f"LỖI: Lỗi khi lấy lịch sử game: {e}")
        sys.stdout.flush()

# --- Chức năng Trích xuất dữ liệu mẫu cầu ra file TXT ---
@bot.message_handler(commands=['trichcau'])
def extract_cau_patterns(message):
    """Trích xuất toàn bộ dữ liệu mẫu cầu đã học ra file TXT và gửi cho admin."""
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    all_patterns_content = ""
    for game_name, config in GAME_CONFIGS.items():
        all_patterns_content += f"===== Mẫu cầu cho {config['game_name_vi']} =====\n\n"
        
        dep_patterns = sorted(list(CAU_DEP.get(game_name, set())))
        xau_patterns = sorted(list(CAU_XAU.get(game_name, set())))
        
        all_patterns_content += "--- Cầu Đẹp ---\n"
        if dep_patterns:
            all_patterns_content += "\n".join(dep_patterns) + "\n\n"
        else:
            all_patterns_content += "Không có mẫu cầu đẹp.\n\n"

        all_patterns_content += "--- Cầu Xấu ---\n"
        if xau_patterns:
            all_patterns_content += "\n".join(xau_patterns) + "\n\n"
        else:
            all_patterns_content += "Không có mẫu cầu xấu.\n\n"
        
        all_patterns_content += "\n"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"cau_patterns_{timestamp}.txt"
    file_path = os.path.join(TEMP_DIR, file_name)

    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(all_patterns_content)
        
        with open(file_path, 'rb') as f_to_send:
            bot.send_document(message.chat.id, f_to_send, caption="Đây là toàn bộ dữ liệu mẫu cầu đã học của bot. Bạn có thể sử dụng file này với lệnh `/nhapcau` để khôi phục.")
        
        os.remove(file_path)
        print(f"DEBUG: Đã gửi và xóa file '{file_name}'.")
        sys.stdout.flush()

    except Exception as e:
        bot.reply_to(message, f"Đã xảy ra lỗi khi trích xuất hoặc gửi file: {e}")
        print(f"LỖI: Lỗi khi trích xuất mẫu cầu: {e}")
        sys.stdout.flush()

# --- Chức năng Nhập dữ liệu mẫu cầu từ file TXT ---
@bot.message_handler(commands=['nhapcau'])
def ask_for_cau_file(message):
    """Yêu cầu admin gửi file TXT chứa dữ liệu mẫu cầu."""
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    waiting_for_cau_file[message.chat.id] = True
    bot.reply_to(message, "Vui lòng gửi file `.txt` chứa dữ liệu mẫu cầu bạn muốn bot tải lại. Đảm bảo định dạng file giống file bot đã trích xuất bằng lệnh `/trichcau`.")

@bot.message_handler(content_types=['document'])
def handle_document_for_cau_patterns(message):
    """Xử lý file TXT được gửi bởi admin để tải lại mẫu cầu."""
    user_id = message.chat.id
    if user_id not in ADMIN_IDS or not waiting_for_cau_file.get(user_id):
        return

    if message.document.mime_type != 'text/plain' or not message.document.file_name.endswith('.txt'):
        bot.reply_to(message, "File bạn gửi không phải là file `.txt` hợp lệ. Vui lòng gửi lại file `.txt`.")
        waiting_for_cau_file[user_id] = False
        return

    temp_file_path = None
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        temp_file_path = os.path.join(TEMP_DIR, message.document.file_name)
        with open(temp_file_path, 'wb') as f:
            f.write(downloaded_file)

        new_cau_dep = {game: set() for game in GAME_CONFIGS.keys()}
        new_cau_xau = {game: set() for game in GAME_CONFIGS.keys()}
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
                    if "Không có mẫu cầu" not in line:
                        if current_section == 'dep':
                            new_cau_dep[current_game].add(line)
                        elif current_section == 'xau':
                            new_cau_xau[current_game].add(line)
        
        global CAU_DEP, CAU_XAU
        CAU_DEP = new_cau_dep
        CAU_XAU = new_cau_xau
        save_cau_patterns_to_db()

        bot.reply_to(message, "✅ Đã tải lại dữ liệu mẫu cầu thành công từ file của bạn!")
        print(f"DEBUG: Đã tải lại mẫu cầu từ file '{message.document.file_name}'.")
        sys.stdout.flush()

    except Exception as e:
        bot.reply_to(message, f"Đã xảy ra lỗi khi xử lý file hoặc tải lại dữ liệu: {e}")
        print(f"LỖI: Lỗi khi nhập mẫu cầu từ file: {e}")
        sys.stdout.flush()
    finally:
        waiting_for_cau_file[user_id] = False
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)

# --- Lệnh Admin tạo key ---
@bot.message_handler(commands=['taokey'])
def create_new_key(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Cú pháp: `/taokey <số_ngày_sử_dụng>` (ví dụ: `/taokey 30`)", parse_mode='Markdown')
        return
    
    try:
        days = int(args[1])
        if days <= 0 or days > 3650: # Giới hạn 10 năm
            bot.reply_to(message, "Số ngày sử dụng phải là số nguyên dương và không quá 3650 ngày (10 năm).")
            return
        
        key_value, expires_at = generate_key(days)
        if key_value:
            bot.reply_to(message, 
                         f"🔑 **Đã tạo key mới thành công!**\n\n"
                         f"Key: `{key_value}`\n"
                         f"Hạn sử dụng: **{expires_at.strftime('%Y-%m-%d %H:%M:%S')}**\n\n"
                         f"Hãy gửi key này cho người dùng và hướng dẫn họ dùng lệnh `/kichhoat {key_value}`", 
                         parse_mode='Markdown')
        else:
            bot.reply_to(message, "Đã xảy ra lỗi khi tạo key.")
    except ValueError:
        bot.reply_to(message, "Số ngày sử dụng phải là một số nguyên hợp lệ.")
    except Exception as e:
        bot.reply_to(message, f"Đã xảy ra lỗi không xác định: {e}")

# --- Lệnh Admin xem danh sách keys ---
@bot.message_handler(commands=['keys'])
def list_keys(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT key_value, created_at, expires_at, user_id, activated_at, is_active FROM access_keys ORDER BY created_at DESC")
        keys = cursor.fetchall()
        conn.close()

        if not keys:
            bot.reply_to(message, "Chưa có key nào được tạo.")
            return
        
        key_list_message = "🔑 **Danh sách các Key truy cập** 🔑\n\n"
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
                    status = f"🟢 Đang dùng bởi `{user_id}` (còn {remaining.days} ngày)"
            else:
                expires_dt = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
                if expires_dt < datetime.now():
                    status = "⚪ Hết hạn (chưa dùng)"
                else:
                    status = "🔵 Chưa dùng"
            
            key_list_message += f"`{key_value}` - {status}\n"
            key_list_message += f"  _Tạo: {created_at}_"
            if user_id:
                key_list_message += f" _- Kích hoạt: {activated_at}_"
            key_list_message += f" _- HSD: {expires_at_str}_\n\n"
            
        # Chia nhỏ tin nhắn nếu quá dài
        if len(key_list_message) > 4096:
            for i in range(0, len(key_list_message), 4000):
                bot.reply_to(message, key_list_message[i:i+4000], parse_mode='Markdown')
        else:
            bot.reply_to(message, key_list_message, parse_mode='Markdown')

    except Exception as e:
        bot.reply_to(message, f"Đã xảy ra lỗi khi lấy danh sách key: {e}")
        print(f"LỖI: Lỗi khi lấy danh sách key: {e}")
        sys.stdout.flush()

# --- Khởi động Bot ---
def start_bot_threads():
    """Khởi tạo database, tải mẫu cầu và bắt đầu các luồng xử lý bot."""
    # Khởi tạo Database và tải mẫu cầu khi bot khởi động
    init_db()
    load_cau_patterns_from_db()

    # Khởi tạo luồng kiểm tra API
    # Hàm check_apis_loop đã được định nghĩa ở trên
    api_checker_thread = threading.Thread(target=check_apis_loop) 
    api_checker_thread.daemon = True # Đặt daemon thread để nó tự kết thúc khi chương trình chính kết thúc
    api_checker_thread.start()

    # Bắt đầu bot lắng nghe tin nhắn
    print("Bot đang khởi động và sẵn sàng nhận lệnh...")
    sys.stdout.flush()
    bot.polling(none_stop=True)

if __name__ == "__main__":
    start_bot_threads()
