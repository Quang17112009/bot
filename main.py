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
import re # Thêm import cho regex

# Thêm import cho Flask và Thread
from flask import Flask
from threading import Thread

# --- Cấu hình Bot và Admin ---
# THAY THẾ BẰNG BOT_TOKEN CỦA BẠN (Lấy từ BotFather, KHÔNG PHẢI TOKEN MẪU)
BOT_TOKEN = "7820739987:AAE_eU2JPZH7u6KnDRq31_l4tn64AD_8f6s" 
# THAY THẾ BẰNG ID TELEGRAM CỦA BẠN (VD: [123456789, 987654321])
# Admin ID có thể lấy từ bot @userinfobot trên Telegram
ADMIN_IDS = [6915752059] 
bot = telebot.TeleBot(BOT_TOKEN)

# --- Cấu hình Game ---
# LƯU Ý: Các URL API dưới đây là các URL bạn đã cung cấp. 
# Nếu các URL này không trả về định dạng JSON hợp lệ hoặc không đúng như mong đợi, 
# bạn cần thay đổi chúng sang các API tương ứng hoặc điều chỉnh phần xử lý JSON.
GAME_CONFIGS = {
    'luckywin': {'api_url': 'https://1.bot/GetNewLottery/LT_Taixiu', 'game_name_vi': 'Luckywin', 'history_table': 'luckywin_history'},
    'hitclub': {'api_url': 'https://apihitclub.up.railway.app/api/taixiu', 'game_name_vi': 'Hit Club', 'history_table': 'hitclub_history'},
    'sunwin': {'api_url': 'https://wanglinapiws.up.railway.app/api/taixiu', 'game_name_vi': 'Sunwin', 'history_table': 'sunwin_history'}
}

# --- Biến Toàn Cục và Cấu Hình Lưu Trữ ---
LAST_FETCHED_IDS = {game: None for game in GAME_CONFIGS.keys()} # Lưu Expect string hoặc Phien int
CHECK_INTERVAL_SECONDS = 5 # Kiểm tra API mỗi 5 giây

# CAU_DEP và CAU_XAU sẽ lưu trữ các mẫu cầu đã học.
# Format: {game_name: {pattern_string: confidence_or_length}}
# 'confidence_or_length' có thể là độ dài của cầu đó hoặc một giá trị tin cậy khác.
LEARNED_PATTERNS = {game: {'dep': {}, 'xau': {}} for game in GAME_CONFIGS.keys()} 

CAU_MIN_LENGTH = 5 # Độ dài tối thiểu của mẫu cầu để phân loại
RECENT_HISTORY_FETCH_LIMIT = 200 # Đã tăng lên 200 phiên để bot học nhiều hơn

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

    # Bảng mẫu cầu đã học
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS learned_patterns_db (
            game_name TEXT NOT NULL,
            pattern TEXT NOT NULL,
            pattern_type TEXT NOT NULL, -- e.g., 'bet_T', 'zigzag_TX', '1-2-1', 'statistical'
            result_sequence TEXT NOT NULL, -- The sequence of T/X/B used to identify this pattern
            classification_type TEXT NOT NULL, -- 'dep' or 'xau'
            confidence REAL, -- A numerical value (e.g., length of pattern)
            last_seen_phien TEXT,
            PRIMARY KEY (game_name, pattern_type, result_sequence, classification_type)
        )
    ''')

    # Bảng lịch sử cho mỗi game
    for game_name, config in GAME_CONFIGS.items():
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {config['history_table']} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phien TEXT UNIQUE NOT NULL, -- Lưu số phiên hoặc Expect string
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
    """Tải tất cả mẫu cầu từ database vào biến toàn cục LEARNED_PATTERNS."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for game_name in GAME_CONFIGS.keys():
        LEARNED_PATTERNS[game_name]['dep'].clear()
        LEARNED_PATTERNS[game_name]['xau'].clear()

        cursor.execute("SELECT pattern_type, result_sequence, confidence FROM learned_patterns_db WHERE game_name = ? AND classification_type = 'dep'", (game_name,))
        for row in cursor.fetchall():
            LEARNED_PATTERNS[game_name]['dep'][row[1]] = {'type': row[0], 'confidence': row[2]} # Store result_sequence as key
        
        cursor.execute("SELECT pattern_type, result_sequence, confidence FROM learned_patterns_db WHERE game_name = ? AND classification_type = 'xau'", (game_name,))
        for row in cursor.fetchall():
            LEARNED_PATTERNS[game_name]['xau'][row[1]] = {'type': row[0], 'confidence': row[2]} # Store result_sequence as key
            
    conn.close()
    print(f"DEBUG: Đã tải mẫu cầu từ DB. Tổng cầu đẹp: {sum(len(v['dep']) for v in LEARNED_PATTERNS.values())}, Tổng cầu xấu: {sum(len(v['xau']) for v in LEARNED_PATTERNS.values())}")
    sys.stdout.flush()

def save_learned_pattern_to_db(game_name, pattern_type, result_sequence, classification_type, confidence, last_seen_phien):
    """Lưu hoặc cập nhật một mẫu cầu đã học vào database."""
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
        return True # Trả về True nếu thêm mới thành công
    except sqlite3.IntegrityError:
        return False # Phiên đã tồn tại, không thêm mới
    except Exception as e:
        print(f"LỖI: Không thể lưu kết quả phiên {phien} cho {game_name} vào DB: {e}")
        sys.stdout.flush()
        return False
    finally:
        conn.close()

def get_recent_history(game_name, limit=RECENT_HISTORY_FETCH_LIMIT, include_phien=False):
    """
    Lấy N kết quả của các phiên gần nhất từ database.
    Mặc định trả về list các chuỗi 'T', 'X', 'B'.
    Nếu include_phien=True, trả về list các tuple (phien, result_tx).
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if include_phien:
        cursor.execute(f"SELECT phien, result_tx FROM {GAME_CONFIGS[game_name]['history_table']} ORDER BY id DESC LIMIT ?", (limit,))
        history = cursor.fetchall()
    else:
        cursor.execute(f"SELECT result_tx FROM {GAME_CONFIGS[game_name]['history_table']} ORDER BY id DESC LIMIT ?", (limit,))
        history = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    return history[::-1] # Đảo ngược để có thứ tự từ cũ đến mới

# --- Logic Học và Dự Đoán ---

def analyze_and_learn_patterns(game_name, history_results):
    """
    Phân tích các mẫu cầu (bệt, zigzag, 1-2-1, 2-1-2) từ lịch sử kết quả.
    Lưu trữ vào LEARNED_PATTERNS và cập nhật DB.
    """
    if len(history_results) < CAU_MIN_LENGTH + 1:
        return 

    # Tạm thời clear cache mẫu học để học lại với dữ liệu mới
    # Nếu muốn giữ lại các mẫu đã học và chỉ cập nhật, bạn có thể bỏ dòng này
    # Tuy nhiên, để đảm bảo tính nhất quán với dữ liệu lịch sử mới, 
    # việc học lại thường là cần thiết.
    # LEARNED_PATTERNS[game_name]['dep'].clear()
    # LEARNED_PATTERNS[game_name]['xau'].clear()

    # Để tối ưu, chỉ xóa và học lại các mẫu cũ nếu chúng không còn xuất hiện hoặc thay đổi
    # Hiện tại giữ nguyên logic clear và học lại để đơn giản.

    newly_learned_dep = {}
    newly_learned_xau = {}

    for i in range(len(history_results) - CAU_MIN_LENGTH):
        current_sequence = "".join(history_results[i : i + CAU_MIN_LENGTH])
        actual_next_result = history_results[i + CAU_MIN_LENGTH]
        
        pattern_type = 'unknown'
        predicted_result = 'N/A'
        
        # Check for bệt
        if len(set(current_sequence)) == 1 and 'B' not in current_sequence: # Only T or X
            pattern_type = f'bet_{current_sequence[0]}'
            predicted_result = current_sequence[0]
            
        # Check for zigzag (TX, XT alternating)
        elif 'B' not in current_sequence and all(current_sequence[j] != current_sequence[j+1] for j in range(CAU_MIN_LENGTH - 1)):
            pattern_type = f'zigzag_{current_sequence[0]}{current_sequence[1]}'
            predicted_result = 'T' if current_sequence[-1] == 'X' else 'X'
        
        # Check for 1-2-1 (e.g., TXTTXT...) - Requires more complex logic
        # Simplified: Check if current_sequence matches a 1-2-1 pattern 
        # (e.g., TXT... if length is odd, TXTT... if length is even)
        # A more robust 1-2-1 check would be: A B A B A B...
        elif 'B' not in current_sequence and CAU_MIN_LENGTH >= 3:
            is_121 = True
            for j in range(len(current_sequence) - 1):
                if current_sequence[j] == current_sequence[j+1]: # Should always alternate
                    is_121 = False
                    break
            if is_121:
                pattern_type = '1-2-1'
                predicted_result = 'T' if current_sequence[-1] == 'X' else 'X' # Dự đoán ngược lại
        
        # Check for 2-1-2 (e.g., TTXTTX...) - Simplified detection
        # A more robust 2-1-2 check would be: A A B A A B...
        elif 'B' not in current_sequence and CAU_MIN_LENGTH >= 3: # Cần ít nhất 3 cho TTX
            is_212 = False
            # Check for TTX pattern (or XXT) repeating
            if len(current_sequence) >= 3:
                # Example: TTX, XXT
                if (current_sequence[0] == current_sequence[1] and current_sequence[1] != current_sequence[2]) or \
                   (current_sequence[0] != current_sequence[1] and current_sequence[1] == current_sequence[2]):
                   # This is a starting segment, check if it continues
                   is_212 = True
                   segment_length = 3
                   for k in range(segment_length, len(current_sequence), segment_length):
                       if len(current_sequence) - k >= segment_length:
                           if not (current_sequence[k] == current_sequence[k+1] and current_sequence[k+1] != current_sequence[k+2]) and \
                              not (current_sequence[k] != current_sequence[k+1] and current_sequence[k+1] == current_sequence[k+2]):
                               is_212 = False
                               break
                       else: # Remaining part is shorter than a full segment
                           # Just check if it follows the pattern so far
                           if current_sequence[k] != current_sequence[k-segment_length]: # if TTX... (T != X)
                               is_212 = False
                               break
            
            if is_212:
                 pattern_type = '2-1-2'
                 # Dự đoán tiếp theo của TTX sẽ là T, của XXT sẽ là X
                 # Nghĩa là dự đoán giống với kết quả đầu tiên của segment tiếp theo
                 predicted_result = current_sequence[0] 


        # Nếu tìm thấy một mẫu cầu được định nghĩa
        if predicted_result != 'N/A' and pattern_type != 'unknown':
            if actual_next_result == predicted_result:
                # Nếu mẫu dự đoán đúng, thêm vào cầu đẹp
                newly_learned_dep[current_sequence] = {'type': pattern_type, 'confidence': CAU_MIN_LENGTH}
            else:
                # Nếu mẫu dự đoán sai, thêm vào cầu xấu
                newly_learned_xau[current_sequence] = {'type': pattern_type, 'confidence': CAU_MIN_LENGTH}
        
    # Cập nhật LEARNED_PATTERNS với các mẫu mới học
    LEARNED_PATTERNS[game_name]['dep'].update(newly_learned_dep)
    LEARNED_PATTERNS[game_name]['xau'].update(newly_learned_xau)

    # Lưu lại toàn bộ các mẫu đã học vào DB
    conn = get_db_connection()
    cursor = conn.cursor()
    # Xóa các mẫu cũ của game này trước khi thêm mới để tránh trùng lặp và cập nhật chính xác
    cursor.execute("DELETE FROM learned_patterns_db WHERE game_name = ?", (game_name,))
    
    for pattern_seq, data in LEARNED_PATTERNS[game_name]['dep'].items():
        save_learned_pattern_to_db(game_name, data['type'], pattern_seq, 'dep', data['confidence'], None)
    for pattern_seq, data in LEARNED_PATTERNS[game_name]['xau'].items():
        save_learned_pattern_to_db(game_name, data['type'], pattern_seq, 'xau', data['confidence'], None)
    
    conn.commit()
    conn.close()

def make_prediction_for_game(game_name):
    """
    Đưa ra dự đoán cho phiên tiếp theo dựa trên các mẫu cầu đã học và thống kê.
    Ưu tiên mẫu cầu đẹp, sau đó đến thống kê.
    """
    recent_history_tx = get_recent_history(game_name, limit=RECENT_HISTORY_FETCH_LIMIT) # Lấy đủ lịch sử cho thống kê và mẫu dài
    
    prediction = None
    reason = "Không có mẫu rõ ràng."
    confidence = "Thấp"
    
    # Lấy chuỗi lịch sử ngắn gọn để so khớp mẫu
    # Đảm bảo đủ độ dài CAU_MIN_LENGTH để so khớp
    if len(recent_history_tx) < CAU_MIN_LENGTH:
        return None, "Không đủ lịch sử để phân tích mẫu.", "Rất thấp", ""

    current_sequence_for_match = "".join(recent_history_tx[-CAU_MIN_LENGTH:])

    # 1. Ưu tiên dựa trên MẪU CẦU ĐẸP
    if current_sequence_for_match in LEARNED_PATTERNS[game_name]['dep']:
        pattern_data = LEARNED_PATTERNS[game_name]['dep'][current_sequence_for_match]
        
        # Simple prediction based on pattern type
        if pattern_data['type'].startswith('bet_'):
            prediction = pattern_data['type'][-1] # T hoặc X
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
            # Dự đoán tiếp theo của TTX sẽ là T, của XXT sẽ là X
            # Nghĩa là dự đoán giống với kết quả đầu tiên của segment tiếp theo
            prediction = current_sequence_for_match[0]
            reason = f"Theo cầu 2-1-2 dài {pattern_data['confidence']}+."
            confidence = "Cao"
        
        return prediction, reason, confidence, current_sequence_for_match
    
    # 2. Nếu không có mẫu đẹp, kiểm tra MẪU CẦU XẤU
    if current_sequence_for_match in LEARNED_PATTERNS[game_name]['xau']:
        pattern_data = LEARNED_PATTERNS[game_name]['xau'][current_sequence_for_match]
        prediction = 'N/A' # Khi cầu xấu, không nên dự đoán mà nên khuyên ngừng
        reason = f"⚠️ Phát hiện mẫu cầu không ổn định: {pattern_data['type']}. Nên cân nhắc tạm dừng."
        confidence = "Rất thấp"
        return prediction, reason, confidence, current_sequence_for_match
        
    # 3. Nếu không có mẫu rõ ràng (đẹp/xấu), dựa vào THỐNG KÊ ĐƠN GIẢN
    if len(recent_history_tx) >= 10: # Cần ít nhất 10 phiên cho thống kê
        num_T = recent_history_tx.count('T')
        num_X = recent_history_tx.count('X')
        num_B = recent_history_tx.count('B')
        
        total_tx = num_T + num_X
        if total_tx > 0:
            ratio_T = num_T / total_tx
            ratio_X = num_X / total_tx

            if ratio_T > 0.6: # Nếu Tài chiếm hơn 60%
                prediction = 'T'
                reason = f"Thống kê {num_T}/{total_tx} phiên gần nhất là Tài. Khả năng cao tiếp tục Tài."
                confidence = "Trung bình"
            elif ratio_X > 0.6: # Nếu Xỉu chiếm hơn 60%
                prediction = 'X'
                reason = f"Thống kê {num_X}/{total_tx} phiên gần nhất là Xỉu. Khả năng cao tiếp tục Xỉu."
                confidence = "Trung bình"
            elif num_B > 0 and num_B / len(recent_history_tx) > 0.05: # Nếu bão xuất hiện khá thường xuyên
                prediction = 'B' # Có thể dự đoán bão
                reason = f"Bão xuất hiện {num_B}/{len(recent_history_tx)} phiên gần nhất. Có thể bão tiếp."
                confidence = "Trung bình"

    # 4. Fallback: Nếu vẫn không có dự đoán, flip ngược lại kết quả cuối cùng
    if not prediction and len(recent_history_tx) > 0:
        last_result = recent_history_tx[-1]
        prediction = 'T' if last_result == 'X' else 'X'
        reason = f"Không có mẫu/thống kê rõ ràng. Dự đoán đảo ngược kết quả gần nhất ({last_result})."
        confidence = "Thấp"
    
    return prediction, reason, confidence, current_sequence_for_match

def format_prediction_message(game_name_vi, phien_id_next, prev_phien_id, prev_result, dices, total_point, prediction, reason, confidence, recent_history_formatted):
    """Định dạng tin nhắn dự đoán cho Telegram."""
    emoji_map = {
        'T': '📈', 'X': '📉', 'B': '🌪️', 
        'Cao': '🚀', 'Trung bình': '👍', 'Thấp': '🐌', 'Rất thấp': '🚨'
    }
    
    prediction_emoji = emoji_map.get(prediction, '🤔')
    confidence_emoji = emoji_map.get(confidence, '')

    message = (
        f"🎲 *Dự đoán {game_name_vi}* 🎲\n"
        f"---\n"
        f"✨ **Phiên hiện tại:** `# {phien_id_next}`\n"
        f"➡️ **Kết quả phiên trước (`#{prev_phien_id}`):** [{dices[0]} {dices[1]} {dices[2]}] = **{total_point}** ({prev_result})\n"
        f"---\n"
        f"🎯 **Dự đoán:** {prediction_emoji} **{prediction or 'KHÔNG CHẮC CHẮN'}**\n"
        f"💡 **Lý do:** _{reason}_\n"
        f"📊 **Độ tin cậy:** {confidence_emoji} _{confidence}_\n"
        f"---\n"
        f"📈 **Lịch sử gần đây ({len(recent_history_formatted)} phiên):**\n"
        f"`{' '.join(recent_history_formatted)}`\n"
        f"\n"
        f"⚠️ _Lưu ý: Dự đoán chỉ mang tính chất tham khảo, không đảm bảo 100% chính xác. Hãy chơi có trách nhiệm!_"
    )
    return message

# --- Logic Xử lý Game (ĐÃ SỬA LỖI TUPLLE ASSIGNMENT) ---
def process_game_api_fetch(game_name, config):
    """Kết nối API, xử lý dữ liệu phiên mới, lưu vào DB."""
    url = config['api_url']
    game_name_vi = config['game_name_vi']

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status() # Sẽ raise HTTPError cho các mã lỗi 4xx/5xx
        data = response.json()

        phien = None
        total_point = None
        dice1 = None
        dice2 = None
        dice3 = None
        result_tx_from_api = ''
        
        # --- Lấy thông tin phiên trước để hiển thị trong thông báo ---
        # Lấy bản ghi chi tiết của phiên cuối cùng từ DB
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT phien, total_point, dice1, dice2, dice3, result_tx FROM {config['history_table']} ORDER BY id DESC LIMIT 1")
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
        
        # --- Phân tích dữ liệu từ các API khác nhau ---
        if game_name == 'luckywin':
            # Specific parsing for Luckywin API response
            if data.get('state') == 1 and 'data' in data:
                game_data = data['data']
                phien = game_data.get('Expect') # This is the "Phien" for Luckywin
                open_code = game_data.get('OpenCode')
                
                # Luckywin thường không có 'Ket_qua' trực tiếp, phải tính từ OpenCode
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

        elif game_name in ['hitclub', 'sunwin']:
            # Parsing for Hitclub and Sunwin API response
            phien = data.get('Phien')
            total_point = data.get('Tong')
            dice1 = data.get('Xuc_xac_1')
            dice2 = data.get('Xuc_xac_2')
            dice3 = data.get('Xuc_xac_3')
            result_tx_from_api = data.get('Ket_qua', '').upper() 
        
        # --- Kiểm tra dữ liệu và xử lý phiên mới ---
        if phien is not None and total_point is not None and \
           dice1 is not None and dice2 is not None and dice3 is not None and \
           result_tx_from_api in ['T', 'X', 'B']: # Đảm bảo có kết quả T/X/B hợp lệ
            
            # So sánh với phiên cuối cùng đã xử lý (đây là điểm quan trọng để xác định phiên mới)
            is_new_phien = save_game_result(game_name, phien, result_tx_from_api, total_point, dice1, dice2, dice3)

            if is_new_phien:
                print(f"DEBUG: Đã lưu phiên mới: {game_name_vi} - Phiên {phien}, Kết quả: {result_tx_from_api}")
                sys.stdout.flush()
                
                # Sau khi lưu phiên mới, tiến hành học lại mẫu cầu với dữ liệu cập nhật
                recent_history_tx_for_learning = get_recent_history(game_name, limit=RECENT_HISTORY_FETCH_LIMIT)
                analyze_and_learn_patterns(game_name, recent_history_tx_for_learning)
                
                # Thực hiện dự đoán cho phiên tiếp theo
                prediction, reason, confidence, current_sequence = make_prediction_for_game(game_name)

                # Lấy lịch sử gần nhất để hiển thị trong tin nhắn (10-15 phiên)
                # Lấy lại lịch sử sau khi đã lưu phiên mới để đảm bảo hiển thị đúng phiên mới
                recent_history_for_msg = get_recent_history(game_name, limit=15, include_phien=True)
                recent_history_formatted = [f"#{p[0]}:{p[1]}" for p in recent_history_for_msg]
                
                # Gửi tin nhắn dự đoán
                # phien_id_next là số phiên tiếp theo
                # prev_phien_id, prev_result_tx, prev_dices, prev_total_point là thông tin của phiên vừa kết thúc
                formatted_message = format_prediction_message(
                    game_name_vi, 
                    phien, # Luckywin Expect là số phiên tiếp theo, Hitclub/Sunwin Phien là phiên hiện tại
                    phien, # Dùng phien hiện tại làm prev_phien_id vì là phiên vừa kết thúc
                    result_tx_from_api, 
                    [dice1, dice2, dice3], # Dùng dice hiện tại vì nó là của phiên vừa kết thúc
                    total_point,           # Dùng total_point hiện tại vì nó là của phiên vừa kết thúc
                    prediction, reason, confidence, recent_history_formatted
                )
                
                # Gửi tới tất cả các admin
                for admin_id in ADMIN_IDS:
                    try:
                        bot.send_message(admin_id, formatted_message, parse_mode='Markdown')
                    except telebot.apihelper.ApiTelegramException as e:
                        print(f"LỖI: Không thể gửi tin nhắn đến admin {admin_id}: {e}")
                        sys.stdout.flush()
                    
                print(f"DEBUG: Đã xử lý và gửi thông báo cho {game_name_vi} phiên {phien}.")
                sys.stdout.flush()
            # else: Phiên này đã được xử lý hoặc là phiên cũ hơn, không làm gì.
        else:
            print(f"LỖỖI: Dữ liệu từ API {game_name_vi} không đầy đủ hoặc không hợp lệ: {data}")
            sys.stdout.flush()

    except requests.exceptions.Timeout:
        print(f"LỖI: Hết thời gian chờ khi kết nối đến API {game_name_vi}.")
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
    """Vòng lặp liên tục kiểm tra API của các game."""
    # Khởi tạo LAST_FETCHED_IDS với phiên cuối cùng trong DB cho mỗi game khi bắt đầu loop
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

# --- Keep-alive cho Render (SỬA ĐỔI MỚI) ---
app = Flask(__name__)

@app.route('/')
def home():
    """Endpoint cho Render Health Check."""
    return "Bot is running!", 200

def run_web_server():
    """Chạy Flask web server trong một luồng riêng."""
    # Lấy cổng từ biến môi trường của Render
    port = int(os.environ.get('PORT', 10000)) # Mặc định port 10000 nếu không tìm thấy biến môi trường PORT
    print(f"DEBUG: Starting Flask web server on port {port}")
    sys.stdout.flush()
    # Sử dụng `debug=False` trong môi trường production
    # host='0.0.0.0' để server có thể truy cập được từ bên ngoài container
    app.run(host='0.0.0.0', port=port, debug=False) 

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
        "`/du_doan <tên_game>`: Xem dự đoán cho game (ví dụ: `/du_doan luckywin`).\n\n"
    )
    
    if is_admin(message.chat.id):
        help_text += (
            "--- 👑 Lệnh dành cho Admin 👑 ---\n"
            "👑 `/taokey <số_ngày>`: Tạo một key mới có thời hạn (ví dụ: `/taokey 30`).\n"
            "👑 `/keys`: Xem danh sách các key đã tạo.\n"
            "👑 `/status_bot`: Xem trạng thái bot và thống kê mẫu cầu.\n"
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

    # Để đưa ra dự đoán, cần lấy thông tin phiên cuối cùng đã lưu trong DB cho game đó
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
        bot.reply_to(message, "Chưa có dữ liệu lịch sử cho game này để dự đoán. Vui lòng chờ bot thu thập thêm dữ liệu.")
        return
    
    prediction, reason, confidence, _ = make_prediction_for_game(matched_game_key)
    
    # Lấy lịch sử 15 phiên gần nhất để hiển thị
    recent_history_for_msg = get_recent_history(matched_game_key, limit=15, include_phien=True)
    recent_history_formatted = [f"#{p[0]}:{p[1]}" for p in recent_history_for_msg]

    formatted_message = format_prediction_message(
        GAME_CONFIGS[matched_game_key]['game_name_vi'], 
        "Tiếp theo", # Hoặc bạn có thể tính phien_id_next nếu API cung cấp
        prev_phien_id, prev_result_tx, prev_dices, prev_total_point, 
        prediction, reason, confidence, recent_history_formatted
    )
    
    bot.reply_to(message, formatted_message, parse_mode='Markdown')

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
        
        # Lấy số lượng từ DB để đảm bảo chính xác nhất
        cursor.execute("SELECT COUNT(*) FROM learned_patterns_db WHERE game_name = ? AND classification_type = 'dep'", (game_name,))
        dep_count_db = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM learned_patterns_db WHERE game_name = ? AND classification_type = 'xau'", (game_name,))
        xau_count_db = cursor.fetchone()[0]

        status_message += f"  - Mẫu cầu đẹp (trong DB): {dep_count_db}\n"
        status_message += f"  - Mẫu cầu xấu (trong DB): {xau_count_db}\n"
        total_dep_patterns += dep_count_db
        total_xau_patterns += xau_count_db

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

    status_message += f"**Tổng cộng các mẫu cầu đã học (từ DB):**\n"
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
        cursor.execute("DELETE FROM learned_patterns_db") # Xóa từ bảng mới
        conn.commit()
        conn.close()

        # Sau khi xóa DB, cũng clear biến global LEARNED_PATTERNS
        global LEARNED_PATTERNS
        LEARNED_PATTERNS = {game: {'dep': {}, 'xau': {}} for game in GAME_CONFIGS.keys()}

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
        cursor.execute(f"SELECT phien, total_point, result_tx, dice1, dice2, dice3 FROM {GAME_CONFIGS[matched_game_key]['history_table']} ORDER BY id DESC LIMIT ?", (limit,))
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
        
        # Tải từ DB để đảm bảo dữ liệu là mới nhất
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
            all_patterns_content += "Không có mẫu cầu đẹp.\n\n"

        all_patterns_content += "--- Cầu Xấu ---\n"
        if xau_patterns_db:
            all_patterns_content += "\n".join(xau_patterns_db) + "\n\n"
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

        # Khởi tạo các dict tạm để lưu mẫu mới đọc từ file
        new_cau_dep = {game: {} for game in GAME_CONFIGS.keys()} 
        new_cau_xau = {game: {} for game in GAME_CONFIGS.keys()}
        current_game = None
        current_section = None # 'dep' hoặc 'xau'

        with open(temp_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("===== Mẫu cầu cho"):
                    for game_key, config in GAME_CONFIGS.items():
                        if config['game_name_vi'] in line: # Tìm tên game tiếng Việt trong dòng
                            current_game = game_key
                            break
                    current_section = None # Reset section khi chuyển game
                elif line == "--- Cầu Đẹp ---":
                    current_section = 'dep'
                elif line == "--- Cầu Xấu ---":
                    current_section = 'xau'
                elif line and current_game and current_section:
                    if "Không có mẫu cầu" not in line and not line.startswith("===") and not line.startswith("---"):
                        pattern_seq = line
                        # Cố gắng suy luận lại loại mẫu khi nhập
                        pattern_type = 'manual_import'
                        if len(set(pattern_seq)) == 1:
                            pattern_type = f'bet_{pattern_seq[0]}'
                        elif len(pattern_seq) >= 2 and all(pattern_seq[j] != pattern_seq[j+1] for j in range(len(pattern_seq) - 1)):
                            pattern_type = f'zigzag_{pattern_seq[0]}{pattern_seq[1]}'
                        elif len(pattern_seq) >= 3 and all(pattern_seq[j] != pattern_seq[j+1] for j in range(len(pattern_seq) - 1)): # 1-2-1
                             pattern_type = '1-2-1'
                        # Thêm logic cho 2-1-2 nếu cần, nhưng phức tạp hơn để suy luận từ chuỗi đơn giản
                        
                        if current_section == 'dep':
                            new_cau_dep[current_game][pattern_seq] = {'type': pattern_type, 'confidence': len(pattern_seq)}
                        elif current_section == 'xau':
                            new_cau_xau[current_game][pattern_seq] = {'type': pattern_type, 'confidence': len(pattern_seq)}
        
        # Cập nhật biến global LEARNED_PATTERNS
        global LEARNED_PATTERNS
        for game_key in GAME_CONFIGS.keys():
            LEARNED_PATTERNS[game_key]['dep'] = new_cau_dep.get(game_key, {})
            LEARNED_PATTERNS[game_key]['xau'] = new_cau_xau.get(game_key, {})
        
        # Xóa tất cả các mẫu cũ trong DB và lưu lại các mẫu mới nhập
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM learned_patterns_db") # Xóa toàn bộ
        for g_name, data_types in LEARNED_PATTERNS.items():
            for c_type, patterns_dict in data_types.items():
                for p_seq, p_data in patterns_dict.items():
                    save_learned_pattern_to_db(g_name, p_data['type'], p_seq, c_type, p_data['confidence'], None)
        conn.commit()
        conn.close()

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
    load_cau_patterns_from_db() # Tải mẫu cầu đã học vào bộ nhớ

    # Khởi tạo luồng web server cho Render (keep-alive)
    web_server_thread = Thread(target=run_web_server)
    web_server_thread.daemon = True # Đặt daemon thread để nó tự kết thúc khi chương trình chính kết thúc
    web_server_thread.start()
    print("DEBUG: Đã khởi động luồng web server.")
    sys.stdout.flush()

    # Khởi tạo luồng kiểm tra API
    api_checker_thread = threading.Thread(target=check_apis_loop) 
    api_checker_thread.daemon = True # Đặt daemon thread để nó tự kết thúc khi chương trình chính kết thúc
    api_checker_thread.start()
    print("DEBUG: Đã khởi động luồng kiểm tra API.")
    sys.stdout.flush()

    # Bắt đầu bot lắng nghe tin nhắn
    print("Bot đang khởi động và sẵn sàng nhận lệnh...")
    sys.stdout.flush()
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        print(f"LỖI: Bot polling dừng đột ngột: {e}")
        sys.stdout.flush()
        # Trong môi trường Render, khi bot polling dừng, dịch vụ có thể sẽ dừng luôn.
        # Render sẽ tự động thử khởi động lại nếu dịch vụ bị crash.

if __name__ == "__main__":
    start_bot_threads()
