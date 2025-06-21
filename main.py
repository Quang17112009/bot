import telebot
import requests
import json
import time
import threading
from datetime import datetime
import os
import sys
import sqlite3

# --- Cấu hình Bot và Admin ---
# THAY THẾ BẰNG BOT_TOKEN CỦA BẠN
BOT_TOKEN = "7820739987:AAE_eU2JPZH7u6KnDRq31_l4tn64AD_8f6s"
# THAY THẾ BẰNG ID TELEGRAM CỦA BẠN
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

    # Tạo bảng mẫu cầu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cau_patterns (
            game_name TEXT NOT NULL,
            pattern TEXT NOT NULL,
            type TEXT NOT NULL, -- 'dep' or 'xau'
            PRIMARY KEY (game_name, pattern, type)
        )
    ''')

    # Tạo bảng lịch sử cho mỗi game
    for game_name, config in GAME_CONFIGS.items():
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {config['history_table']} (
                id INTEGER PRIMARY KEY AUTOINCREMENT, -- Tự động tăng ID
                phien INTEGER UNIQUE NOT NULL, -- Số phiên, đảm bảo duy nhất
                result_tx TEXT NOT NULL, -- T, X, B
                total_point INTEGER NOT NULL,
                dice1 INTEGER NOT NULL,
                dice2 INTEGER NOT NULL,
                dice3 INTEGER NOT NULL,
                timestamp TEXT NOT NULL
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
        if patterns: # Chỉ chèn nếu có mẫu
            data = [(game_name, pattern, 'dep') for pattern in patterns]
            cursor.executemany("INSERT INTO cau_patterns (game_name, pattern, type) VALUES (?, ?, ?)", data)
    
    # Thêm mẫu cầu xấu
    for game_name, patterns in CAU_XAU.items():
        if patterns: # Chỉ chèn nếu có mẫu
            data = [(game_name, pattern, 'xau') for pattern in patterns]
            cursor.executemany("INSERT INTO cau_patterns (game_name, pattern, type) VALUES (?, ?, ?)", data)
            
    conn.commit()
    conn.close()
    # print("DEBUG: Đã lưu mẫu cầu vào DB.") # Có thể bỏ dòng này để tránh spam log nếu gọi thường xuyên
    # sys.stdout.flush()

# --- Lịch sử Phiên Game (Sử dụng SQLite) ---
def save_game_result(game_name, phien, result_tx, total_point, dice1, dice2, dice3):
    """Lưu kết quả của một phiên game vào bảng lịch sử tương ứng trong database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # INSERT OR IGNORE: Nếu phien đã tồn tại (do UNIQUE), bỏ qua.
        cursor.execute(f'''
            INSERT OR IGNORE INTO {GAME_CONFIGS[game_name]['history_table']} 
            (phien, result_tx, total_point, dice1, dice2, dice3, timestamp) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (phien, result_tx, total_point, dice1, dice2, dice3, timestamp))
        conn.commit()
    except sqlite3.IntegrityError:
        # Phiên đã tồn tại, không làm gì cả
        pass
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
    
    if len(recent_history_tx) < CAU_MIN_LENGTH + 1: # Cần ít nhất CAU_MIN_LENGTH + 1 phiên để có 1 mẫu và 1 kết quả
        return 
    
    # Lặp qua lịch sử để tạo và phân loại các mẫu cầu
    # Mỗi mẫu cầu có độ dài CAU_MIN_LENGTH
    for i in range(len(recent_history_tx) - CAU_MIN_LENGTH):
        pattern_to_classify = "".join(recent_history_tx[i : i + CAU_MIN_LENGTH])
        actual_result_for_pattern = recent_history_tx[i + CAU_MIN_LENGTH]

        # Xác định xem mẫu cầu có phải là cầu bệt hay zíc-zắc hay không
        is_bet = (pattern_to_classify.count('T') == CAU_MIN_LENGTH) or \
                 (pattern_to_classify.count('X') == CAU_MIN_LENGTH) or \
                 (pattern_to_classify.count('B') == CAU_MIN_LENGTH) # Thêm bão bệt
        
        is_ziczac = True
        for j in range(CAU_MIN_LENGTH - 1):
            if pattern_to_classify[j] == pattern_to_classify[j+1]:
                is_ziczac = False
                break
        
        # Nếu mẫu cầu là bão, chỉ coi là bệt nếu tất cả đều là B
        if 'B' in pattern_to_classify and (pattern_to_classify.count('B') != CAU_MIN_LENGTH):
            is_bet = False
            is_ziczac = False # Bão làm hỏng cả zíc-zắc

        if is_bet:
            expected_result = pattern_to_classify[-1] # Dự đoán sẽ tiếp tục bệt
            if actual_result_for_pattern == expected_result:
                if pattern_to_classify not in CAU_XAU[game_name]: # Nếu nó không phải cầu xấu, thêm vào đẹp
                     CAU_DEP[game_name].add(pattern_to_classify)
            else: # Dự đoán bệt sai, mẫu này là xấu
                if pattern_to_classify in CAU_DEP[game_name]: # Nếu nó từng là cầu đẹp, xóa khỏi đẹp
                     CAU_DEP[game_name].remove(pattern_to_classify)
                CAU_XAU[game_name].add(pattern_to_classify)
        elif is_ziczac:
            # Dự đoán sẽ tiếp tục zíc-zắc (ngược lại ký tự cuối)
            if pattern_to_classify[-1] == 'T': expected_result = 'X'
            elif pattern_to_classify[-1] == 'X': expected_result = 'T'
            else: expected_result = actual_result_for_pattern # Nếu là 'B', không theo zic-zac

            if actual_result_for_pattern == expected_result:
                if pattern_to_classify not in CAU_XAU[game_name]:
                     CAU_DEP[game_name].add(pattern_to_classify)
            else: # Dự đoán zíc-zắc sai, mẫu này là xấu
                if pattern_to_classify in CAU_DEP[game_name]:
                     CAU_DEP[game_name].remove(pattern_to_classify)
                CAU_XAU[game_name].add(pattern_to_classify)
        else:
            # Đối với các mẫu cầu không phải bệt/zic-zac rõ ràng,
            # bot hiện tại chưa có logic để phân loại là "đẹp" hay "xấu" một cách tự động.
            # Có thể bỏ qua hoặc thêm logic phức tạp hơn (ví dụ: học máy) ở đây.
            pass
            
    # Sau khi phân loại tất cả các mẫu trong lần học này, lưu vào DB
    save_cau_patterns_to_db()

def make_prediction(game_name):
    """Đưa ra dự đoán cho phiên tiếp theo dựa trên các mẫu cầu đã học."""
    recent_history_tx = get_recent_history_tx(game_name, limit=CAU_MIN_LENGTH)
    
    if len(recent_history_tx) < CAU_MIN_LENGTH:
        return "Chưa đủ lịch sử để dự đoán mẫu cầu."
    
    current_cau_for_prediction = "".join(recent_history_tx[-CAU_MIN_LENGTH:])
    
    prediction_text = f"📊 Mẫu cầu hiện tại: **{current_cau_for_prediction}**\n"
    
    if current_cau_for_prediction in CAU_DEP[game_name]:
        # Nếu mẫu cầu hiện tại là cầu đẹp, dự đoán tiếp tục theo mẫu
        predicted_value = current_cau_for_prediction[-1] 
        prediction_text += f"✅ Phát hiện mẫu cầu đẹp. Khả năng cao ra: **{predicted_value}**\n"
    elif current_cau_for_prediction in CAU_XAU[game_name]:
        # Nếu mẫu cầu hiện tại là cầu xấu, dự đoán ngược lại
        predicted_value = 'T' if current_cau_for_prediction[-1] == 'X' else ('X' if current_cau_for_prediction[-1] == 'T' else 'T') # Nếu là 'B', coi như ngược lại thành T (chỉ ví dụ)
        prediction_text += f"❌ Phát hiện mẫu cầu xấu. Khả năng cao ra: **{predicted_value}** (Dự đoán ngược)\n"
    else:
        prediction_text += "🧐 Chưa có mẫu cầu rõ ràng để dự đoán. Dự đoán dựa trên xác suất 50/50.\n"
        # Dự đoán ngẫu nhiên hoặc theo một xu hướng đơn giản nếu không có mẫu rõ ràng
        # Ví dụ: dự đoán ngược lại phiên trước đó nếu không có mẫu nào
        # predicted_value = 'T' if recent_history_tx[-1] == 'X' else 'X'
        # prediction_text += f"👉 Khả năng cao ra: **{predicted_value}** (Dự đoán ngược phiên trước)\n"
        # Hoặc một dự đoán mặc định, ví dụ Tài
        predicted_value = 'T' # Default prediction
        prediction_text += f"👉 Khả năng cao ra: **{predicted_value}** (Dự đoán mặc định)\n"

    return prediction_text, predicted_value # Trả về cả tin nhắn và giá trị dự đoán

# --- Logic Xử lý Game ---
def process_game(game_name, config):
    """Kết nối API, xử lý dữ liệu phiên mới, lưu vào DB và gửi dự đoán."""
    url = config['api_url']
    game_name_vi = config['game_name_vi']

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status() # Báo lỗi nếu status code là 4xx hoặc 5xx
        data = response.json()

        if data and 'logs' in data and data['logs']:
            latest_log = data['logs'][0]
            phien = latest_log.get('phien')
            result_points = latest_log.get('result_points')
            dices = latest_log.get('dices')

            if phien and phien > LAST_FETCHED_IDS[game_name]:
                # Đây là một phiên mới
                LAST_FETCHED_IDS[game_name] = phien

                if result_points is not None and dices and len(dices) == 3:
                    total_point = sum(dices)
                    result_tx = 'T' if total_point >= 11 else 'X'
                    if dices[0] == dices[1] == dices[2]: # Nếu là bão
                        result_tx = 'B'

                    # Lưu kết quả phiên mới vào database
                    save_game_result(game_name, phien, result_tx, total_point, dices[0], dices[1], dices[2])
                    
                    # Gọi hàm học mẫu cầu sau khi có dữ liệu mới
                    classify_and_learn_cau(game_name)

                    # Tạo tin nhắn dự đoán và kết quả
                    prediction_text, _ = make_prediction(game_name) # Lấy tin nhắn dự đoán
                    
                    full_message = f"🔔 **Dự đoán {game_name_vi}**\n\n"
                    full_message += prediction_text # Thêm phần dự đoán
                    full_message += f"\n⚡ **Kết quả phiên {phien}**: "
                    for i, dice in enumerate(dices):
                        full_message += f"[{dice}] "
                        if i < 2: full_message += "+ "
                    full_message += f"= **{total_point}** ({result_tx})"
                    
                    # Gửi tin nhắn đến tất cả ADMIN_IDS
                    for admin_id in ADMIN_IDS:
                        bot.send_message(admin_id, full_message, parse_mode='Markdown')
                        
                    print(f"DEBUG: Đã xử lý và gửi dự đoán cho {game_name_vi} phiên {phien}.")
                    sys.stdout.flush()
                else:
                    print(f"LỖI: Thiếu dữ liệu result_points hoặc dices từ API {game_name_vi} cho phiên {phien}.")
                    sys.stdout.flush()
            # else:
            #     print(f"DEBUG: {game_name_vi} - Chưa có phiên mới (phien {phien} <= {LAST_FETCHED_IDS[game_name]})")
            #     sys.stdout.flush()

    except requests.exceptions.RequestException as e:
        print(f"LỖI: Không thể kết nối hoặc lấy dữ liệu từ {game_name_vi} API: {e}")
        sys.stdout.flush()
    except json.JSONDecodeError as e:
        print(f"LỖI: Không thể giải mã JSON từ {game_name_vi} API: {e}")
        sys.stdout.flush()
    except Exception as e:
        print(f"LỖI: Xảy ra lỗi không xác định khi xử lý {game_name_vi}: {e}")
        sys.stdout.flush()

# --- Lặp để kiểm tra API (chạy trong một luồng riêng) ---
def check_apis_loop():
    """Vòng lặp chính để kiểm tra API của tất cả các game."""
    while True:
        for game_name, config in GAME_CONFIGS.items():
            process_game(game_name, config)
        time.sleep(CHECK_INTERVAL_SECONDS)

# --- Các Lệnh của Bot ---
@bot.message_handler(commands=['start', 'help'])
def show_help(message):
    """Hiển thị tin nhắn trợ giúp và các lệnh có sẵn."""
    help_text = (
        "Xin chào! Tôi là bot dự đoán Tài Xỉu.\n"
        "Tôi sẽ tự động gửi kết quả và dự đoán cho các game sau:\n"
    )
    for _, config in GAME_CONFIGS.items():
        help_text += f"- **{config['game_name_vi']}**\n"
    
    help_text += "\n"
    
    if is_admin(message.chat.id):
        help_text += (
            "--- 👑 Lệnh dành cho Admin 👑 ---\n"
            "👑 `/status`: Xem trạng thái bot và thống kê mẫu cầu.\n"
            "👑 `/trichcau`: Trích xuất toàn bộ dữ liệu mẫu cầu đã học ra file TXT.\n"
            "👑 `/nhapcau`: Nhập lại dữ liệu mẫu cầu đã học từ file TXT bạn gửi lên.\n"
            "👑 `/reset_patterns`: Đặt lại toàn bộ mẫu cầu đã học (cần xác nhận).\n"
            "👑 `/history <tên_game> <số_lượng>`: Lấy lịch sử N phiên của game (ví dụ: `/history luckywin 10`).\n"
        )
    else:
        help_text += "Nếu bạn có thắc mắc, hãy liên hệ admin để biết thêm chi tiết."
        
    bot.reply_to(message, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['status'])
def show_status(message):
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
        
        # Lấy số lượng mẫu cầu từ RAM (đã được load từ DB)
        dep_count = len(CAU_DEP.get(game_name, set()))
        xau_count = len(CAU_XAU.get(game_name, set()))
        status_message += f"  - Mẫu cầu đẹp: {dep_count}\n"
        status_message += f"  - Mẫu cầu xấu: {xau_count}\n"
        total_dep_patterns += dep_count
        total_xau_patterns += xau_count

        # Lấy tổng số lịch sử phiên từ DB
        cursor.execute(f"SELECT COUNT(*) FROM {config['history_table']}")
        total_history = cursor.fetchone()[0]
        status_message += f"  - Tổng lịch sử phiên trong DB: {total_history}\n\n"
    
    conn.close()

    status_message += f"**Tổng cộng các mẫu cầu đã học (trong RAM):**\n"
    status_message += f"  - Cầu đẹp: {total_dep_patterns}\n"
    status_message += f"  - Cầu xấu: {total_xau_patterns}\n"
    
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

        # Reset các biến toàn cục trong RAM
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

    # Ánh xạ tên game đầu vào với keys trong GAME_CONFIGS
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
        if limit <= 0 or limit > 200: # Giới hạn số lượng để tránh spam tin nhắn Telegram
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
        
        # Sắp xếp các mẫu để file dễ đọc hơn
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
        
        all_patterns_content += "\n" # Khoảng cách giữa các game

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"cau_patterns_{timestamp}.txt"
    file_path = os.path.join(TEMP_DIR, file_name)

    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(all_patterns_content)
        
        with open(file_path, 'rb') as f_to_send:
            bot.send_document(message.chat.id, f_to_send, caption="Đây là toàn bộ dữ liệu mẫu cầu đã học của bot. Bạn có thể sử dụng file này với lệnh `/nhapcau` để khôi phục.")
        
        os.remove(file_path) # Xóa file tạm sau khi gửi
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

    waiting_for_cau_file[message.chat.id] = True # Đặt trạng thái chờ file cho admin này
    bot.reply_to(message, "Vui lòng gửi file `.txt` chứa dữ liệu mẫu cầu bạn muốn bot tải lại. Đảm bảo định dạng file giống file bot đã trích xuất bằng lệnh `/trichcau`.")

@bot.message_handler(content_types=['document'])
def handle_document_for_cau_patterns(message):
    """Xử lý file TXT được gửi bởi admin để tải lại mẫu cầu."""
    user_id = message.chat.id
    # Chỉ xử lý nếu user là admin và đang trong trạng thái chờ file
    if user_id not in ADMIN_IDS or not waiting_for_cau_file.get(user_id):
        return

    # Kiểm tra loại file
    if message.document.mime_type != 'text/plain' or not message.document.file_name.endswith('.txt'):
        bot.reply_to(message, "File bạn gửi không phải là file `.txt` hợp lệ. Vui lòng gửi lại file `.txt`.")
        waiting_for_cau_file[user_id] = False # Reset trạng thái chờ
        return

    temp_file_path = None # Khởi tạo để đảm bảo luôn có thể xóa
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        # Lưu file tạm thời
        temp_file_path = os.path.join(TEMP_DIR, message.document.file_name)
        with open(temp_file_path, 'wb') as f:
            f.write(downloaded_file)

        # Tạo các set mới để lưu dữ liệu từ file
        new_cau_dep = {game: set() for game in GAME_CONFIGS.keys()}
        new_cau_xau = {game: set() for game in GAME_CONFIGS.keys()}
        current_game = None
        current_section = None # 'dep' or 'xau'

        with open(temp_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("===== Mẫu cầu cho"):
                    # Phát hiện game mới
                    for game_key, config in GAME_CONFIGS.items():
                        if config['game_name_vi'] in line:
                            current_game = game_key
                            break
                    current_section = None # Reset section khi chuyển game
                elif line == "--- Cầu Đẹp ---":
                    current_section = 'dep'
                elif line == "--- Cầu Xấu ---":
                    current_section = 'xau'
                elif line and current_game and current_section:
                    # Bỏ qua các dòng thông báo "Không có mẫu cầu đẹp/xấu"
                    if "Không có mẫu cầu" not in line:
                        if current_section == 'dep':
                            new_cau_dep[current_game].add(line)
                        elif current_section == 'xau':
                            new_cau_xau[current_game].add(line)
        
        # Cập nhật các biến global CAU_DEP và CAU_XAU
        global CAU_DEP, CAU_XAU
        CAU_DEP = new_cau_dep
        CAU_XAU = new_cau_xau
        save_cau_patterns_to_db() # Lưu các mẫu mới tải vào database

        bot.reply_to(message, "✅ Đã tải lại dữ liệu mẫu cầu thành công từ file của bạn!")
        print(f"DEBUG: Đã tải lại mẫu cầu từ file '{message.document.file_name}'.")
        sys.stdout.flush()

    except Exception as e:
        bot.reply_to(message, f"Đã xảy ra lỗi khi xử lý file hoặc tải lại dữ liệu: {e}")
        print(f"LỖI: Lỗi khi nhập mẫu cầu từ file: {e}")
        sys.stdout.flush()
    finally:
        waiting_for_cau_file[user_id] = False # Reset trạng thái chờ
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path) # Xóa file tạm

# --- Khởi động Bot ---
def start_bot_threads():
    """Khởi tạo database, tải mẫu cầu và bắt đầu các luồng xử lý bot."""
    # Khởi tạo Database và tải mẫu cầu khi bot khởi động
    init_db()
    load_cau_patterns_from_db()

    # Khởi tạo luồng kiểm tra API
    api_checker_thread = threading.Thread(target=check_apis_loop)
    api_checker_thread.daemon = True # Đặt daemon thread để nó tự kết thúc khi chương trình chính kết thúc
    api_checker_thread.start()

    # Bắt đầu bot lắng nghe tin nhắn
    print("Bot đang khởi động và sẵn sàng nhận lệnh...")
    sys.stdout.flush()
    bot.polling(none_stop=True)

if __name__ == "__main__":
    start_bot_threads()
