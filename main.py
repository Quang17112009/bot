import sqlite3
import threading
import time
import requests
import json
import re
from datetime import datetime
import os

# --- CẤU HÌNH BOT ---
BOT_TOKEN = '7820739987:AAE_eU2JPZH7u6KnDRq31_l4tn64AD_8f6s' # <-- THAY THẾ BẰNG TOKEN BOT CỦA BẠN
ADMIN_IDS = [6915752059] # <-- THAY THẾ BẰNG ID TELEGRAM CỦA ADMIN (có thể là nhiều ID)
DB_NAME = 'bot_data.db'
API_FETCH_INTERVAL = 30 # Thời gian chờ giữa các lần fetch API (giây)
RECENT_HISTORY_FETCH_LIMIT = 200 # Số phiên lịch sử tối đa để phân tích mẫu cầu và thống kê
CAU_MIN_LENGTH = 7 # Độ dài tối thiểu của cầu để được nhận diện (5-7 là hợp lý)
AUTO_SEND_HISTORY_INTERVAL = 10 # Số phiên MỚI được thêm vào DB trước khi tự động gửi lịch sử cho admin

# Khóa để đồng bộ hóa truy cập database
DB_LOCK = threading.Lock()

# Các cấu hình game
GAME_CONFIGS = {
    'Luckywin': {
        # Bạn có thể cần thay đổi api_url nếu đây là API bạn đang dùng.
        # Hiện tại, cấu trúc JSON bạn đưa ra không phải là kết quả trực tiếp từ API của Luckywin (api.luckywin.bet/api/v1/game/get-xocdia-history)
        # mà có vẻ là một wrapper hoặc một nguồn dữ liệu khác.
        'api_url': 'https://1.bot/GetNewLottery/LT_Taixiu', # Giữ nguyên nếu bạn vẫn muốn fetch từ đây và chỉ ví dụ JSON, hoặc thay đổi nếu nguồn JSON bạn đưa ra là từ API khác
        'parse_func': lambda api_response_data: {
            # Giả định api_response_data là phần "data" trong JSON bạn cung cấp
            # (tức là api_response_data sẽ là {"ID":725487,"Expect":"2506220541", ...})
            'Phien': api_response_data.get('Expect'),
            'OpenCode_str': api_response_data.get('OpenCode'), # Lưu chuỗi "4,5,2" để phân tích
            'TableName': api_response_data.get('TableName') # Giữ lại để debug hoặc kiểm tra nếu cần
        },
        'display_name': 'Luckywin 🎲'
    }
}

# Biến toàn cục để đếm số phiên mới được thêm vào database
new_sessions_count = 0

# --- CÁC HÀM TIỆN ÍCH (giữ nguyên) ---
def escape_markdown_v2(text):
    """Thoát các ký tự đặc biệt trong chuỗi để sử dụng với MarkdownV2 của Telegram."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', str(text))

def send_telegram_message(chat_id, text, parse_mode='MarkdownV2', disable_web_page_preview=True):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
        'disable_web_page_preview': disable_web_page_preview
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"LỖI: Không thể gửi tin nhắn đến chat ID {chat_id}: {e}")
        if response and response.status_code == 400:
            print(f"Mô tả lỗi: {response.json().get('description')}")

def send_telegram_document(chat_id, document_path, caption=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        with open(document_path, 'rb') as doc_file:
            files = {'document': doc_file}
            data = {'chat_id': chat_id}
            if caption:
                data['caption'] = caption
            response = requests.post(url, files=files, data=data)
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"LỖI: Không thể gửi file đến chat ID {chat_id}: {e}")
    except FileNotFoundError:
        print(f"LỖI: Không tìm thấy file để gửi: {document_path}")
    except Exception as e:
        print(f"LỖI không xác định khi gửi tài liệu: {e}")

def download_file(file_id, file_path):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile"
    response = requests.get(url, params={'file_id': file_id})
    response.raise_for_status()
    file_info = response.json()['result']
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info['file_path']}"
    
    with requests.get(file_url, stream=True) as r:
        r.raise_for_status()
        with open(file_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return file_path

# --- QUẢN LÝ DATABASE (giữ nguyên) ---
def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS game_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_name TEXT NOT NULL,
                phien TEXT UNIQUE NOT NULL,
                ket_qua TEXT NOT NULL,
                tong INTEGER,
                xuc_xac_1 INTEGER,
                xuc_xac_2 INTEGER,
                xuc_xac_3 INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learned_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_name TEXT NOT NULL,
                pattern_type TEXT NOT NULL, -- 'dep' or 'xau'
                pattern_string TEXT NOT NULL UNIQUE,
                count INTEGER DEFAULT 1
            )
        ''')
        conn.commit()
        conn.close()

def save_game_result(game_name, phien, ket_qua, tong, xuc_xac_1, xuc_xac_2, xuc_xac_3):
    global new_sessions_count
    with DB_LOCK:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO game_history (game_name, phien, ket_qua, tong, xuc_xac_1, xuc_xac_2, xuc_xac_3) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (game_name, phien, ket_qua, tong, xuc_xac_1, xuc_xac_2, xuc_xac_3)
            )
            conn.commit()
            print(f"DEBUG: Đã phát hiện và lưu phiên MỚI: {game_name} - Phiên {phien}, Kết quả {ket_qua}")
            new_sessions_count += 1
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            print(f"LỖI: Không thể lưu kết quả phiên {phien} vào DB: {e}")
            return False
        finally:
            conn.close()

def get_recent_history(game_name, limit=200, include_phien=False):
    with DB_LOCK:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT phien, ket_qua, tong, xuc_xac_1, xuc_xac_2, xuc_xac_3 FROM game_history WHERE game_name = ? ORDER BY phien DESC LIMIT ?",
            (game_name, limit)
        )
        history_raw = cursor.fetchall()
        conn.close()
        history_raw.reverse()

        if include_phien:
            return [(p[0], p[1]) for p in history_raw]
        return [p[1] for p in history_raw]

def save_learned_pattern(game_name, pattern_type, pattern_string):
    with DB_LOCK:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO learned_patterns (game_name, pattern_type, pattern_string, count) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(pattern_string) DO UPDATE SET count = count + 1",
                (game_name, pattern_type, pattern_string)
            )
            conn.commit()
        except Exception as e:
            print(f"LỖI: Error saving learned pattern to DB: {e}")
        finally:
            conn.close()

def load_cau_patterns_from_db():
    global LEARNED_PATTERNS
    with DB_LOCK:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT game_name, pattern_type, pattern_string, count FROM learned_patterns")
        patterns = cursor.fetchall()
        conn.close()

        LEARNED_PATTERNS = {game_name: {'dep': {}, 'xau': {}} for game_name in GAME_CONFIGS.keys()}
        for game_name, p_type, p_string, count in patterns:
            if game_name in LEARNED_PATTERNS and p_type in LEARNED_PATTERNS[game_name]:
                LEARNED_PATTERNS[game_name][p_type][p_string] = count
    print("DEBUG: Đã tải các mẫu cầu từ DB.")

# --- LOGIC DỰ ĐOÁN (NÂNG CẤP) (giữ nguyên) ---
def analyze_cau_patterns_advanced(history):
    # Luôn ưu tiên các mẫu dài và mạnh nhất
    
    # 1. Cầu Bệt (ưu tiên cao nhất nếu đủ dài và đang tiếp diễn)
    for result_type in ['T', 'X']:
        current_bich_length = 0
        for i in range(len(history) - 1, -1, -1):
            if history[i] == result_type:
                current_bich_length += 1
            else:
                break
        if current_bich_length >= CAU_MIN_LENGTH:
            # Nếu đang bệt và phiên cuối cùng là loại đó, dự đoán tiếp tục bệt
            if history[-1] == result_type:
                return {
                    'prediction': result_type,
                    'reason': f"Cầu bệt {result_type} dài {current_bich_length} phiên.",
                    'confidence': 'Cao'
                }

    # 2. Cầu Zigzag (xen kẽ)
    if len(history) >= CAU_MIN_LENGTH:
        is_zigzag = True
        for i in range(1, CAU_MIN_LENGTH):
            if history[-i] == history[-(i+1)]: # Kiểm tra xen kẽ
                is_zigzag = False
                break
        if is_zigzag:
            current_zigzag_length = 0
            for i in range(len(history) - 1, 0, -1):
                if history[i] != history[i-1]:
                    current_zigzag_length += 1
                else:
                    break
            current_zigzag_length += 1 # Tính cả phiên cuối cùng
            if current_zigzag_length >= CAU_MIN_LENGTH:
                prediction = 'X' if history[-1] == 'T' else 'T' # Dự đoán ngược lại phiên cuối
                return {
                    'prediction': prediction,
                    'reason': f"Cầu zigzag dài {current_zigzag_length} phiên.",
                    'confidence': 'Trung bình'
                }
    
    # 3. Mẫu 1-2-1 (TXXT -> X) và XTTX -> T
    if len(history) >= 4:
        last_4 = history[-4:]
        if last_4 == ['T', 'X', 'X', 'T']:
            return {'prediction': 'X', 'reason': "Mẫu 1-2-1", 'confidence': 'Trung bình'}
        if last_4 == ['X', 'T', 'T', 'X']:
            return {'prediction': 'T', 'reason': "Mẫu 1-2-1", 'confidence': 'Trung bình'}

    # 4. Mẫu 2-1-2 (TTXTT -> X) và XXTXX -> T
    if len(history) >= 5:
        last_5 = history[-5:]
        if last_5 == ['T', 'T', 'X', 'T', 'T']:
            return {'prediction': 'X', 'reason': "Mẫu 2-1-2", 'confidence': 'Trung bình'}
        if last_5 == ['X', 'X', 'T', 'X', 'X']:
            return {'prediction': 'T', 'reason': "Mẫu 2-1-2", 'confidence': 'Trung bình'}

    # 5. Mẫu 1-1-2-2 (TTXX -> T) và XXTT -> X
    if len(history) >= 4:
        last_4 = history[-4:]
        if last_4 == ['T', 'T', 'X', 'X']:
            return {'prediction': 'T', 'reason': "Mẫu 1-1-2-2", 'confidence': 'Thấp-Trung bình'}
        if last_4 == ['X', 'X', 'T', 'T']:
            return {'prediction': 'X', 'reason': "Mẫu 1-1-2-2", 'confidence': 'Thấp-Trung bình'}

    # 6. Mẫu 1-3-1 (TXTXT -> X) và XTXTX -> T (Dự đoán đảo chiều sau 1-3-1)
    if len(history) >= 5:
        last_5 = history[-5:]
        if last_5 == ['T', 'X', 'T', 'X', 'T']:
            return {'prediction': 'X', 'reason': "Mẫu 1-3-1. Dự đoán đảo chiều.", 'confidence': 'Thấp-Trung bình'}
        if last_5 == ['X', 'T', 'X', 'T', 'X']:
            return {'prediction': 'T', 'reason': "Mẫu 1-3-1. Dự đoán đảo chiều.", 'confidence': 'Thấp-Trung bình'}

    # 7. Thống kê tỷ lệ trong cửa sổ gần nhất (20 phiên hoặc 10 phiên)
    if len(history) >= 20: 
        recent_20 = history[-20:]
        count_T_20 = recent_20.count('T')
        count_X_20 = recent_20.count('X')
        
        if count_T_20 / len(recent_20) >= 0.65: # Nếu Tài chiếm >= 65% trong 20 phiên
            return {'prediction': 'T', 'reason': "Tỷ lệ Tài cao trong 20 phiên gần nhất (>65%).", 'confidence': 'Thấp-Trung bình'}
        if count_X_20 / len(recent_20) >= 0.65: # Nếu Xỉu chiếm >= 65% trong 20 phiên
            return {'prediction': 'X', 'reason': "Tỷ lệ Xỉu cao trong 20 phiên gần nhất (>65%).", 'confidence': 'Thấp-Trung bình'}
        
    if len(history) >= 10:
        recent_10 = history[-10:]
        count_T_10 = recent_10.count('T')
        count_X_10 = recent_10.count('X')
        if count_T_10 >= 7: # 70% Tài trong 10 phiên gần nhất
            return {'prediction': 'T', 'reason': "Xu hướng Tài trong 10 phiên gần nhất (>70%).", 'confidence': 'Thấp'}
        if count_X_10 >= 7: # 70% Xỉu trong 10 phiên gần nhất
            return {'prediction': 'X', 'reason': "Xu hướng Xỉu trong 10 phiên gần nhất (>70%).", 'confidence': 'Thấp'}

    return None

def get_prediction_for_user(game_name):
    history = get_recent_history(game_name, limit=RECENT_HISTORY_FETCH_LIMIT)
    
    if len(history) < CAU_MIN_LENGTH:
        return "KHÔNG CHẮC CHẮN", "Không đủ lịch sử để phân tích mẫu.", "Rất thấp"
    
    prediction_info = analyze_cau_patterns_advanced(history)

    if prediction_info:
        return (
            prediction_info['prediction'],
            prediction_info['reason'],
            prediction_info['confidence']
        )
    else:
        last_result = history[-1]
        predicted_result = 'X' if last_result == 'T' else 'T'
        return predicted_result, "Không có mẫu/thống kê rõ ràng. Dự đoán đảo ngược kết quả gần nhất.", "Thấp"


# --- THU THẬP DỮ LIỆU & GỬI DỰ ĐOÁN ---

def process_game_api_fetch(game_name, config):
    try:
        response = requests.get(config['api_url'], timeout=10)
        response.raise_for_status()
        full_api_data = response.json() # Toàn bộ phản hồi API

        # Luckywin trả về list, lấy phần tử đầu tiên
        # Hoặc nếu API của bạn trả về cấu trúc {"state":1,"data":{...}}, bạn cần truy cập full_api_data.get('data')
        
        # Điều chỉnh logic này để phù hợp với cấu trúc JSON bạn đã cung cấp:
        if isinstance(full_api_data, dict) and 'data' in full_api_data and isinstance(full_api_data['data'], dict):
            latest_game_data_raw = full_api_data['data']
        elif isinstance(full_api_data, list) and full_api_data:
            latest_game_data_raw = full_api_data[0] # Nếu API vẫn trả về list như Luckywin gốc
        else:
            print(f"LỖI: Dữ liệu từ API {game_name} không đúng cấu trúc mong đợi (không có 'data' dict hoặc không phải list): {full_api_data}")
            return

        parsed_data = config['parse_func'](latest_game_data_raw)

        phien = parsed_data.get('Phien')
        open_code_str = parsed_data.get('OpenCode_str') # Lấy chuỗi "4,5,2"
        
        if phien and open_code_str:
            # Phân tích OpenCode_str để lấy xúc xắc và tính tổng
            dice_values = []
            try:
                dice_values_str = [d.strip() for d in open_code_str.split(',')]
                for d_str in dice_values_str:
                    if d_str.isdigit():
                        dice_values.append(int(d_str))
                    else:
                        print(f"CẢNH BÁO: Giá trị xúc xắc không phải số: {d_str} trong {open_code_str}")
                        dice_values = [] # Reset nếu có lỗi để tránh tính tổng sai
                        break
            except Exception as e:
                print(f"LỖI: Không thể phân tích OpenCode '{open_code_str}': {e}")
                dice_values = [] # Đảm bảo không có giá trị sai

            tong = sum(dice_values) if dice_values else None # Tính tổng
            
            # Xác định kết quả T/X từ tổng (Giả định quy tắc Tài Xỉu truyền thống 11-17 Tài, 4-10 Xỉu)
            ket_qua = None
            if tong is not None:
                if tong >= 11:
                    ket_qua = 'T'
                elif tong >= 4: # Tổng từ 4 đến 10 là Xỉu
                    ket_qua = 'X'
                # Chưa xử lý Bão (Tài Xỉu 3 con xúc xắc thường có Bão khi 3 con giống nhau)
                # Bạn có thể thêm logic cho Bão ở đây nếu muốn bot dự đoán cả Bão.
                # if len(dice_values) == 3 and dice_values[0] == dice_values[1] == dice_values[2]:
                #    ket_qua = 'B' # 'Bão'

            xuc_xac_1 = dice_values[0] if len(dice_values) > 0 else None
            xuc_xac_2 = dice_values[1] if len(dice_values) > 1 else None
            xuc_xac_3 = dice_values[2] if len(dice_values) > 2 else None

            if ket_qua: # Chỉ lưu nếu xác định được kết quả T/X
                is_new_session = save_game_result(game_name, phien, ket_qua, tong, xuc_xac_1, xuc_xac_2, xuc_xac_3)
                if is_new_session:
                    recent_history_for_learning = get_recent_history(game_name, limit=RECENT_HISTORY_FETCH_LIMIT)
                    learn_new_patterns(recent_history_for_learning, game_name)
                    predicted_result, reason, confidence = get_prediction_for_user(game_name)
                    
                    recent_history_for_msg = get_recent_history(game_name, limit=15, include_phien=True)
                    recent_history_formatted = [f"\\#{escape_markdown_v2(p[0])}: {escape_markdown_v2(p[1])}" for p in recent_history_for_msg]
                    
                    prev_phien_result_display = ""
                    # Để hiển thị kết quả phiên vừa qua, ta cần lấy dữ liệu của phiên hiện tại sau khi nó đã được lưu
                    current_phien_data = get_session_data(game_name, phien)
                    
                    if current_phien_data:
                        # current_phien_data = (phien, ket_qua, tong, xuc_xac_1, xuc_xac_2, xuc_xac_3, timestamp)
                        tong_val_display = escape_markdown_v2(str(current_phien_data[2])) if current_phien_data[2] is not None else 'N/A'
                        
                        xuc_xac_parts_display = [current_phien_data[3], current_phien_data[4], current_phien_data[5]]
                        xuc_xac_str_display = " ".join([escape_markdown_v2(str(d)) for d in xuc_xac_parts_display if d is not None])
                        if not xuc_xac_str_display: xuc_xac_str_display = 'N/A'

                        prev_phien_result_display = (
                            f"Kết quả phiên trước \\(\\#{escape_markdown_v2(str(current_phien_data[0]))}\\): "
                            f"{xuc_xac_str_display} = {tong_val_display} \\({escape_markdown_v2(current_phien_data[1])}\\)"
                        )
                    
                    message = (
                        f"🎲 Dự đoán {escape_markdown_v2(config['display_name'])} 🎲\n"
                        f"---\n"
                        f"✨ Phiên hiện tại: \\# {escape_markdown_v2(phien)}\n"
                        f"➡️ {prev_phien_result_display}\n"
                        f"---\n"
                        f"🎯 Dự đoán: {escape_markdown_v2(predicted_result)}\n"
                        f"💡 Lý do: {escape_markdown_v2(reason)}\n"
                        f"📊 Độ tin cậy: {escape_markdown_v2(confidence)}\n"
                        f"---\n"
                        f"📈 Lịch sử gần đây \\({len(recent_history_for_msg)} phiên\\):\n"
                        f"{' '.join(recent_history_formatted)}\n\n"
                        f"⚠️ Lưu ý: Dự đoán chỉ mang tính chất tham khảo, không đảm bảo 100% chính xác\\!"
                    )
                    for admin_id in ADMIN_IDS:
                        send_telegram_message(admin_id, message)
            else:
                print(f"DEBUG: Phiên {phien} của {game_name} đã tồn tại hoặc không xác định được kết quả T/X.")
        else:
            print(f"LỖI: Dữ liệu từ API {game_name} không đầy đủ (thiếu Phiên hoặc OpenCode_str): {parsed_data}")

    except requests.exceptions.RequestException as e:
        print(f"LỖI: Lỗi khi fetch API {game_name}: {e}")
    except json.JSONDecodeError:
        print(f"LỖI: Không thể phân tích JSON từ API {game_name}. Phản hồi có thể không phải JSON hợp lệ.")
    except Exception as e:
        print(f"LỖI: Xảy ra lỗi không xác định khi xử lý {game_name}: {e}")

# Hàm get_session_data (giữ nguyên)
def get_session_data(game_name, phien):
    with DB_LOCK:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT phien, ket_qua, tong, xuc_xac_1, xuc_xac_2, xuc_xac_3, timestamp FROM game_history WHERE game_name = ? AND phien = ?",
            (game_name, phien)
        )
        data = cursor.fetchone()
        conn.close()
        return data

# Hàm learn_new_patterns (giữ nguyên)
def learn_new_patterns(history, game_name):
    if len(history) < CAU_MIN_LENGTH + 1:
        return
    
    new_cau_dep = {}
    new_cau_xau = {}

    for result_type in ['T', 'X']:
        for length in range(CAU_MIN_LENGTH, len(history)):
            pattern_segment = history[-length-1:-1]
            next_result = history[-1]

            if all(r == result_type for r in pattern_segment):
                pattern_string = "".join(pattern_segment)
                if next_result == result_type:
                    new_cau_dep[pattern_string] = new_cau_dep.get(pattern_string, 0) + 1
                else:
                    new_cau_xau[pattern_string] = new_cau_xau.get(pattern_string, 0) + 1

    if len(history) >= CAU_MIN_LENGTH + 1:
        is_zigzag = True
        for i in range(1, CAU_MIN_LENGTH):
            if history[-(i+1)] == history[-i]:
                is_zigzag = False
                break
        
        if is_zigzag:
            pattern_segment = history[-CAU_MIN_LENGTH-1:-1]
            next_result = history[-1]
            predicted_next_for_zigzag = 'X' if pattern_segment[-1] == 'T' else 'T'
            
            pattern_string = "".join(pattern_segment)
            if next_result == predicted_next_for_zigzag:
                new_cau_dep[pattern_string] = new_cau_dep.get(pattern_string, 0) + 1
            else:
                new_cau_xau[pattern_string] = new_cau_xau.get(pattern_string, 0) + 1

    for pattern_string, count in new_cau_dep.items():
        LEARNED_PATTERNS[game_name]['dep'][pattern_string] = LEARNED_PATTERNS[game_name]['dep'].get(pattern_string, 0) + count
        save_learned_pattern(game_name, 'dep', pattern_string)

    for pattern_string, count in new_cau_xau.items():
        LEARNED_PATTERNS[game_name]['xau'][pattern_string] = LEARNED_PATTERNS[game_name]['xau'].get(pattern_string, 0) + count
        save_learned_pattern(game_name, 'xau', pattern_string)


# --- XỬ LÝ LỆNH TELEGRAM (giữ nguyên) ---
def handle_telegram_updates(update):
    if 'message' not in update:
        return

    message = update['message']
    chat_id = message['chat']['id']
    text = message.get('text', '')
    user_id = message['from']['id']

    if user_id not in ADMIN_IDS:
        send_telegram_message(chat_id, escape_markdown_v2("Bạn không có quyền sử dụng lệnh này\\."), parse_mode='MarkdownV2')
        return

    if text.startswith('/status_bot'):
        with DB_LOCK:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()

            message_parts = []
            total_history = 0
            total_learned_dep = 0
            total_learned_xau = 0

            for game_name in GAME_CONFIGS.keys():
                cursor.execute("SELECT COUNT(*) FROM game_history WHERE game_name = ?", (game_name,))
                game_history_count = cursor.fetchone()[0]
                total_history += game_history_count

                dep_count = sum(LEARNED_PATTERNS[game_name]['dep'].values())
                xau_count = sum(LEARNED_PATTERNS[game_name]['xau'].values())
                total_learned_dep += dep_count
                total_learned_xau += xau_count

                message_parts.append(
                    f"*{escape_markdown_v2(game_name)}:*\n"
                    f"  Phiên lịch sử: {escape_markdown_v2(game_history_count)}\n"
                    f"  Mẫu cầu đẹp: {escape_markdown_v2(dep_count)}\n"
                    f"  Mẫu cầu xấu: {escape_markdown_v2(xau_count)}\n"
                )

            message_parts.append(
                f"\n*Tổng cộng:*\n"
                f"  Tổng phiên lịch sử: {escape_markdown_v2(total_history)}\n"
                f"  Tổng mẫu cầu đẹp: {escape_markdown_v2(total_learned_dep)}\n"
                f"  Tổng mẫu cầu xấu: {escape_markdown_v2(total_learned_xau)}\n"
            )

            conn.close()
            send_telegram_message(chat_id, "\n".join(message_parts), parse_mode='MarkdownV2')

    elif text.startswith('/trichcau'):
        output_file_name = f"learned_patterns_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(output_file_name, 'w', encoding='utf-8') as f:
            f.write(f"--- Mẫu cầu đã học ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---\n\n")
            for game_name, patterns_type in LEARNED_PATTERNS.items():
                f.write(f"=== {game_name} ===\n")
                f.write("--- Cầu Đẹp ---\n")
                if patterns_type['dep']:
                    for pattern, count in patterns_type['dep'].items():
                        f.write(f"{pattern}: {count}\n")
                else:
                    f.write("Không có mẫu cầu đẹp nào được học.\n")
                
                f.write("\n--- Cầu Xấu ---\n")
                if patterns_type['xau']:
                    for pattern, count in patterns_type['xau'].items():
                        f.write(f"{pattern}: {count}\n")
                else:
                    f.write("Không có mẫu cầu xấu nào được học.\n")
                f.write("\n")
        
        for admin_id in ADMIN_IDS:
            send_telegram_document(admin_id, output_file_name, caption="File mẫu cầu đã học.")
        try:
            os.remove(output_file_name)
        except OSError as e:
            print(f"LỖI: Không thể xóa file {output_file_name}: {e}")

    elif text.startswith('/nhapcau'):
        send_telegram_message(chat_id, escape_markdown_v2("Vui lòng gửi file lịch sử \\(\\.txt\\) đã được bot trích xuất\\. Tôi sẽ cố gắng nhập lại dữ liệu\\."), parse_mode='MarkdownV2')
    
    elif 'document' in message and user_id in ADMIN_IDS:
        document = message['document']
        file_name = document['file_name']
        file_id = document['file_id']
        
        if file_name.startswith('history_export_') and file_name.endswith('.txt'):
            send_telegram_message(chat_id, escape_markdown_v2("Đang tải xuống và xử lý file lịch sử, vui lòng đợi\\.\\.\\."), parse_mode='MarkdownV2')
            local_file_path = f"/tmp/{file_name}"
            try:
                download_file(file_id, local_file_path)
                imported_count = import_history_from_file(local_file_path)
                send_telegram_message(chat_id, escape_markdown_v2(f"Đã nhập thành công {imported_count} phiên từ file '{escape_markdown_v2(file_name)}'\\.\\\nBot sẽ tải lại các mẫu cầu để học từ lịch sử mới\\."), parse_mode='MarkdownV2')
                load_cau_patterns_from_db() 
            except Exception as e:
                send_telegram_message(chat_id, escape_markdown_v2(f"LỖI khi xử lý file lịch sử: {escape_markdown_v2(str(e))}\\. Vui lòng kiểm tra định dạng file\\."), parse_mode='MarkdownV2')
                print(f"LỖI: Exception khi xử lý file lịch sử: {e}")
            finally:
                if os.path.exists(local_file_path):
                    try:
                        os.remove(local_file_path)
                    except OSError as e:
                        print(f"LỖI: Không thể xóa file tạm thời {local_file_path}: {e}")
        else:
            send_telegram_message(chat_id, escape_markdown_v2("File không phải là file lịch sử hợp lệ \\(ví dụ: không bắt đầu bằng 'history_export\\_' và kết thúc bằng '\\.txt'\\)\\. Vui lòng gửi đúng file lịch sử được xuất ra bởi bot\\."), parse_mode='MarkdownV2')
    
    elif text.startswith('/start'):
        send_telegram_message(chat_id, escape_markdown_v2("Chào mừng bạn đến với Bot Dự đoán Tài Xỉu\\.\nSử dụng /status_bot để xem trạng thái và /trichcau để trích xuất mẫu cầu\\.\nNếu bạn muốn nhập lịch sử, hãy dùng lệnh /nhapcau và sau đó gửi file lịch sử dưới dạng tệp đính kèm\\."), parse_mode='MarkdownV2')

# Hàm import_history_from_file (giữ nguyên)
def import_history_from_file(file_path):
    imported_count = 0
    with DB_LOCK:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                next(f) 
                next(f)
                game_name = None
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    if line.startswith("===") and line.endswith("==="):
                        game_name_match = re.match(r'===\s*(.*?)\s*===', line)
                        if game_name_match:
                            game_name = game_name_match.group(1)
                        continue
                    
                    if game_name and line.startswith("Phiên:"):
                        match = re.match(r'Phiên:\s*(\d+),\s*Kết quả:\s*([TX]),\s*Tổng:\s*(\d+|N/A),\s*Xúc xắc:\s*([\d,N/A\s]+)', line)
                        if match:
                            phien = match.group(1)
                            ket_qua = match.group(2)
                            tong_str = match.group(3)
                            xuc_xac_str = match.group(4)
                            
                            tong = int(tong_str) if tong_str.isdigit() else None
                            
                            xuc_xac_parts_raw = [x.strip() for x in xuc_xac_str.split(',')]
                            xuc_xac_1 = int(xuc_xac_parts_raw[0]) if xuc_xac_parts_raw[0].isdigit() else None
                            xuc_xac_2 = int(xuc_xac_parts_raw[1]) if len(xuc_xac_parts_raw) > 1 and xuc_xac_parts_raw[1].isdigit() else None
                            xuc_xac_3 = int(xuc_xac_parts_raw[2]) if len(xuc_xac_parts_raw) > 2 and xuc_xac_parts_raw[2].isdigit() else None
                            
                            try:
                                cursor.execute(
                                    "INSERT OR IGNORE INTO game_history (game_name, phien, ket_qua, tong, xuc_xac_1, xuc_xac_2, xuc_xac_3) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (game_name, phien, ket_qua, tong, xuc_xac_1, xuc_xac_2, xuc_xac_3)
                                )
                                if cursor.rowcount > 0:
                                    imported_count += 1
                            except Exception as e:
                                print(f"LỖI: Không thể nhập phiên {phien} cho {game_name}: {e}")
                        else:
                            print(f"CẢNH BÁO: Không khớp định dạng dòng lịch sử: {line}")
            conn.commit()
            return imported_count
        except FileNotFoundError:
            raise Exception("File lịch sử không tồn tại.")
        except Exception as e:
            conn.rollback()
            raise Exception(f"Lỗi khi đọc hoặc phân tích file: {e}")
        finally:
            conn.close()

# Hàm auto_send_history_if_needed (giữ nguyên)
def auto_send_history_if_needed():
    global new_sessions_count
    if new_sessions_count >= AUTO_SEND_HISTORY_INTERVAL:
        print(f"DEBUG: Đã thu thập đủ {AUTO_SEND_HISTORY_INTERVAL} phiên mới. Đang gửi lịch sử...")
        output_file_name = f"history_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        with open(output_file_name, 'w', encoding='utf-8') as f:
            f.write(f"--- Lịch sử phiên game ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---\n\n")
            with DB_LOCK:
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                for game_name in GAME_CONFIGS.keys():
                    f.write(f"=== {game_name} ===\n")
                    cursor.execute(
                        "SELECT phien, ket_qua, tong, xuc_xac_1, xuc_xac_2, xuc_xac_3 FROM game_history WHERE game_name = ? ORDER BY phien ASC",
                        (game_name,)
                    )
                    history_data = cursor.fetchall()
                    if history_data:
                        for row in history_data:
                            tong_val = row[2] if row[2] is not None else 'N/A'
                            
                            temp_dice = [str(d) for d in [row[3], row[4], row[5]] if d is not None]
                            xuc_xac_str = ','.join(temp_dice) if temp_dice else 'N/A'
                                
                            f.write(f"Phiên: {row[0]}, Kết quả: {row[1]}, Tổng: {tong_val}, Xúc xắc: {xuc_xac_str}\n")
                    else:
                        f.write("Không có lịch sử cho game này.\n")
                    f.write("\n")
                conn.close()

        for admin_id in ADMIN_IDS:
            send_telegram_document(admin_id, output_file_name, caption=f"Lịch sử {new_sessions_count} phiên mới nhất.")
        
        new_sessions_count = 0
        try:
            os.remove(output_file_name)
        except OSError as e:
            print(f"LỖI: Không thể xóa file {output_file_name}: {e}")

# --- MAIN LOOP (giữ nguyên) ---
def main_loop():
    while True:
        for game_name, config in GAME_CONFIGS.items():
            process_game_api_fetch(game_name, config)
            auto_send_history_if_needed()
        time.sleep(API_FETCH_INTERVAL)

if __name__ == '__main__':
    init_db()
    load_cau_patterns_from_db()
    threading.Thread(target=main_loop).start()

    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {'offset': last_update_id + 1, 'timeout': 60}
            response = requests.get(url, params=params, timeout=70)
            response.raise_for_status()
            updates = response.json().get('result', [])

            for update in updates:
                last_update_id = update['update_id']
                threading.Thread(target=handle_telegram_updates, args=(update,)).start()
        except requests.exceptions.Timeout:
            pass
        except requests.exceptions.RequestException as e:
            print(f"LỖI: Lỗi khi lấy update từ Telegram: {e}")
            time.sleep(5)
        except Exception as e:
            print(f"LỖI: Lỗi không xác định trong vòng lặp getUpdates: {e}")
            time.sleep(5)
