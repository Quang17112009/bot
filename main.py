import telebot
import requests
import time
import json
import os
import random
import string
from datetime import datetime, timedelta
from threading import Thread, Event, Lock
from flask import Flask, request

# --- Cấu hình Bot (ĐẶT TRỰC TIẾP TẠY ĐÂY) ---
# THAY THẾ 'YOUR_BOT_TOKEN_HERE' BẰNG TOKEN THẬT CỦA BẠN
BOT_TOKEN = "8118428622:AAFR4sxqk20-TiDxPSzM74O7UYDbRz3anp8" 
# THAY THẾ BẰNG ID ADMIN THẬT CỦA BẠN. Có thể có nhiều ID, cách nhau bởi dấu phẩy.
ADMIN_IDS = [6915752059] # Ví dụ: [6915752059, 123456789]

DATA_FILE = 'user_data.json'
CAU_PATTERNS_FILE = 'cau_patterns.json'
CODES_FILE = 'codes.json'

# CHANGES: Cấu hình cho nhiều game, 'sunwin' đổi thành 'luckywin'
GAME_CONFIGS = {
    "luckywin": { # Đã đổi từ "sunwin" thành "luckywin"
        "api_url": "https://1.bot/GetNewLottery/LT_Taixiu",
        "name": "Luckywin", # Đã đổi từ "Sunwin" thành "Luckywin"
        "pattern_prefix": "L", # Tiền tố để phân biệt mẫu cầu nếu cần, hoặc để trống
        "tx_history_length": 7 # Chiều dài lịch sử cầu để học mẫu
    },
    "hitclub": {
        "api_url": "https://apihitclub.up.railway.app/api/taixiu",
        "name": "Hit Club",
        "pattern_prefix": "H", # Tiền tố để phân biệt mẫu cầu
        "tx_history_length": 7 # Chiều dài lịch sử cầu để học mẫu
    }
}

# --- Khởi tạo Flask App và Telegram Bot ---
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN)

# Global flags và objects
bot_enabled = True
bot_disable_reason = "Không có"
bot_disable_admin_id = None
prediction_stop_event = Event() # Để kiểm soát luồng dự đoán
bot_initialized = False # Cờ để đảm bảo bot chỉ được khởi tạo một lần
bot_init_lock = Lock() # Khóa để tránh race condition khi khởi tạo

# Global data structures
user_data = {}
# CHANGES: CAU_PATTERNS giờ là dictionary của dictionaries, mỗi game có bộ mẫu riêng
CAU_PATTERNS = {} # {game_name: {pattern_string: confidence_score (float)}}
GENERATED_CODES = {} # {code: {"value": 1, "type": "day", "used_by": null, "used_time": null}}

# CHANGES: Quản lý trạng thái riêng biệt cho mỗi game (last_id, tx_history)
game_states = {}
for game_id in GAME_CONFIGS.keys():
    game_states[game_id] = {
        "last_id": None,
        "tx_history": []
    }

# --- Quản lý dữ liệu người dùng, mẫu cầu và code ---
def load_user_data():
    global user_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try:
                user_data = json.load(f)
            except json.JSONDecodeError:
                print(f"Lỗi đọc {DATA_FILE}. Khởi tạo lại dữ liệu người dùng.")
                user_data = {}
    else:
        user_data = {}
    print(f"Loaded {len(user_data)} user records from {DATA_FILE}")

def save_user_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def load_cau_patterns():
    global CAU_PATTERNS
    if os.path.exists(CAU_PATTERNS_FILE):
        with open(CAU_PATTERNS_FILE, 'r') as f:
            try:
                CAU_PATTERNS = json.load(f)
            except json.JSONDecodeError:
                print(f"Lỗi đọc {CAU_PATTERNS_FILE}. Khởi tạo lại mẫu cầu.")
                CAU_PATTERNS = {}
    else:
        CAU_PATTERNS = {}
    
    # CHANGES: Đảm bảo mỗi game có một entry trong CAU_PATTERNS
    for game_id in GAME_CONFIGS.keys():
        if game_id not in CAU_PATTERNS:
            CAU_PATTERNS[game_id] = {}

    print(f"Loaded patterns for {len(CAU_PATTERNS)} games.")

def save_cau_patterns():
    with open(CAU_PATTERNS_FILE, 'w') as f:
        json.dump(CAU_PATTERNS, f, indent=4)

def load_codes():
    global GENERATED_CODES
    if os.path.exists(CODES_FILE):
        with open(CODES_FILE, 'r') as f:
            try:
                GENERATED_CODES = json.load(f)
            except json.JSONDecodeError:
                print(f"Lỗi đọc {CODES_FILE}. Khởi tạo lại mã code.")
                GENERATED_CODES = {}
    else:
        GENERATED_CODES = {}
    print(f"Loaded {len(GENERATED_CODES)} codes from {CODES_FILE}")

def save_codes():
    with open(CODES_FILE, 'w') as f:
        json.dump(GENERATED_CODES, f, indent=4)

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_ctv(user_id):
    return is_admin(user_id) or (str(user_id) in user_data and user_data[str(user_id)].get('is_ctv'))

def check_subscription(user_id):
    user_id_str = str(user_id)
    if is_admin(user_id) or is_ctv(user_id):
        return True, "Bạn là Admin/CTV, quyền truy cập vĩnh viễn."

    if user_id_str not in user_data or user_data[user_id_str].get('expiry_date') is None:
        return False, "⚠️ Bạn chưa đăng ký hoặc tài khoản chưa được gia hạn."

    expiry_date_str = user_data[user_id_str]['expiry_date']
    expiry_date = datetime.strptime(expiry_date_str, '%Y-%m-%d %H:%M:%S')

    if datetime.now() < expiry_date:
        remaining_time = expiry_date - datetime.now()
        days = remaining_time.days
        hours = remaining_time.seconds // 3600
        minutes = (remaining_time.seconds % 3600) // 60
        seconds = remaining_time.seconds % 60
        return True, f"✅ Tài khoản của bạn còn hạn đến: `{expiry_date_str}` ({days} ngày {hours} phút {minutes} giây)."
    else:
        return False, "❌ Tài khoản của bạn đã hết hạn."

# --- Logic dự đoán Tài Xỉu ---
def du_doan_theo_xi_ngau(dice_list):
    if not dice_list:
        return "Đợi thêm dữ liệu"
    d1, d2, d3 = dice_list[-1]
    total = d1 + d2 + d3

    # Một phương pháp dự đoán đơn giản dựa trên tổng và từng con xúc xắc
    # Có thể phức tạp hơn với các quy tắc khác
    results = []
    for d in [d1, d2, d3]:
        tmp = d + total
        while tmp > 6: # Đảm bảo nằm trong phạm vi 1-6
            tmp -= 6
        if tmp % 2 == 0:
            results.append("Tài")
        else:
            results.append("Xỉu")

    # Chọn kết quả xuất hiện nhiều nhất, nếu hòa thì ưu tiên Tài
    tai_count = results.count("Tài")
    xiu_count = results.count("Xỉu")
    if tai_count >= xiu_count:
        return "Tài"
    else:
        return "Xỉu"


def tinh_tai_xiu(dice):
    total = sum(dice)
    # Tài: 11-17, Xỉu: 4-10. Loại trừ bộ ba đồng nhất (ví dụ 1-1-1 là Xỉu, 6-6-6 là Tài)
    # Tuy nhiên, trong context này, tổng xí ngầu đã bao gồm cả bộ ba đồng nhất.
    # Quy tắc chuẩn của Tài Xỉu thường là nếu ra bộ ba đồng nhất (3 con xúc xắc giống nhau) thì cái đó sẽ là "bão", và tiền cược sẽ trả lại hoặc là nhà cái thắng.
    # Trong code này, tôi giả định chỉ cần tính tổng là đủ, không xử lý đặc biệt cho bộ ba đồng nhất.
    if total >= 11:
        return "Tài", total
    else:
        return "Xỉu", total

# --- Cập nhật mẫu cầu động và độ tin cậy ---
def update_cau_patterns(game_id, pattern_str, prediction_correct):
    global CAU_PATTERNS
    # Tăng/giảm confidence score. Giả sử 1.0 là điểm khởi đầu, min 0.1, max 5.0
    initial_confidence = 1.0
    increase_factor = 0.2
    decrease_factor = 0.5 # Giảm nhiều hơn khi sai để nhanh chóng loại bỏ mẫu xấu

    # CHANGES: Truy cập mẫu cầu theo game_id
    current_confidence = CAU_PATTERNS[game_id].get(pattern_str, initial_confidence)

    if prediction_correct:
        new_confidence = min(current_confidence + increase_factor, 5.0)
    else:
        new_confidence = max(current_confidence - decrease_factor, 0.1)
    
    CAU_PATTERNS[game_id][pattern_str] = new_confidence
    save_cau_patterns()
    # print(f"Cập nhật mẫu cầu '{pattern_str}' cho {game_id}: Confidence mới = {new_confidence:.2f}")

def get_pattern_prediction_adjustment(game_id, pattern_str):
    """
    Trả về yếu tố điều chỉnh dự đoán dựa trên confidence score của mẫu cầu.
    Nếu confidence cao (> ngưỡng), có thể tin tưởng. Nếu thấp (< ngưỡng), có thể đảo chiều.
    """
    # CHANGES: Truy cập mẫu cầu theo game_id
    confidence = CAU_PATTERNS[game_id].get(pattern_str, 1.0)
    
    if confidence >= 2.5: # Ngưỡng để coi là cầu đẹp đáng tin
        return "giữ nguyên"
    elif confidence <= 0.5: # Ngưỡng để coi là cầu xấu, cần đảo chiều
        return "đảo chiều"
    else:
        return "theo xí ngầu" # Không đủ độ tin cậy để điều chỉnh

# --- Lấy dữ liệu từ API ---
# CHANGES: Hàm lấy dữ liệu chung, nhận game_id
def lay_du_lieu(game_id):
    config = GAME_CONFIGS.get(game_id)
    if not config:
        print(f"Cấu hình game '{game_id}' không tồn tại.")
        return None

    api_url = config["api_url"]
    try:
        response = requests.get(api_url)
        response.raise_for_status() # Báo lỗi nếu status code là lỗi HTTP
        data = response.json()
        
        # CHANGES: Xử lý định dạng API khác nhau. 'sunwin' đổi thành 'luckywin'
        if game_id == "luckywin": # Đã đổi từ "sunwin" thành "luckywin"
            if data.get("state") != 1:
                return None
            return {
                "ID": data.get("data", {}).get("ID"),
                "Expect": data.get("data", {}).get("Expect"),
                "OpenCode": data.get("data", {}).get("OpenCode")
            }
        elif game_id == "hitclub":
            # Example: {"Ket_qua":"Xiu","Phien":2611929,"Tong":10,"Xuc_xac_1":6,"Xuc_xac_2":3,"Xuc_xac_3":1,"id":"djtuancon"}
            if not all(k in data for k in ["Phien", "Xuc_xac_1", "Xuc_sac_2", "Xuc_xac_3"]): # Sửa lỗi chính tả Xuc_sac_2
                 print(f"Dữ liệu Hit Club không đầy đủ: {data}")
                 return None
            return {
                "ID": data.get("Phien"), # Dùng Phien làm ID duy nhất
                "Expect": data.get("Phien"),
                "Xuc_xac_1": data.get("Xuc_xac_1"),
                "Xuc_xac_2": data.get("Xuc_xac_2"), # Thêm các xúc xắc riêng để xử lý
                "Xuc_xac_3": data.get("Xuc_xac_3"),
                "OpenCode": f"{data.get('Xuc_xac_1')},{data.get('Xuc_xac_2')},{data.get('Xuc_xac_3')}"
            }
        else:
            return None # Game không được hỗ trợ

    except requests.exceptions.RequestException as e:
        print(f"Lỗi khi lấy dữ liệu từ API {api_url}: {e}")
        return None
    except json.JSONDecodeError:
        print(f"Lỗi giải mã JSON từ API {api_url}. Phản hồi không phải JSON hợp lệ.")
        return None


# --- Logic chính của Bot dự đoán (chạy trong luồng riêng) ---
def prediction_loop(stop_event: Event):
    print("Prediction loop started.")
    while not stop_event.is_set():
        if not bot_enabled:
            time.sleep(10) # Ngủ lâu hơn khi bot bị tắt
            continue

        for game_id, config in GAME_CONFIGS.items():
            current_game_state = game_states[game_id]
            
            data = lay_du_lieu(game_id)
            if not data:
                # print(f"❌ Không lấy được dữ liệu từ API hoặc dữ liệu không hợp lệ cho {config['name']}.")
                time.sleep(5) # Vẫn sleep 5s để tránh spam API ngay cả khi lỗi
                continue

            issue_id = data.get("ID")
            expect = data.get("Expect")
            open_code = data.get("OpenCode")

            if not all([issue_id, expect, open_code]):
                # print(f"Dữ liệu API {config['name']} không đầy đủ (thiếu ID, Expect, hoặc OpenCode). Bỏ qua phiên này.")
                current_game_state["last_id"] = issue_id # Vẫn cập nhật last_id để không lặp lại lỗi
                time.sleep(5)
                continue

            if issue_id != current_game_state["last_id"]:
                try:
                    dice = tuple(map(int, open_code.split(",")))
                except ValueError:
                    print(f"Lỗi phân tích OpenCode cho {config['name']}: '{open_code}'. Bỏ qua phiên này.")
                    current_game_state["last_id"] = issue_id
                    time.sleep(5)
                    continue
                
                ket_qua_tx, tong = tinh_tai_xiu(dice)

                # Lưu lịch sử phiên Tài Xỉu cho game cụ thể
                tx_history_for_game = current_game_state["tx_history"]
                tx_history_length = config["tx_history_length"]

                if len(tx_history_for_game) >= tx_history_length:
                    tx_history_for_game.pop(0)
                tx_history_for_game.append("T" if ket_qua_tx == "Tài" else "X")
                current_game_state["tx_history"] = tx_history_for_game # Cập nhật lại vào state

                # CHANGES: Tính next_expect tùy thuộc vào game_id
                if game_id == "luckywin": # Đã đổi từ "sunwin" thành "luckywin"
                    next_expect = str(int(expect) + 1).zfill(len(expect))
                elif game_id == "hitclub":
                    next_expect = str(int(expect) + 1) # Phien của Hit Club không có leading zeros
                else:
                    next_expect = str(int(expect) + 1) # Fallback

                du_doan_co_so = du_doan_theo_xi_ngau([dice]) # Dự đoán ban đầu theo xí ngầu
                du_doan_cuoi_cung = du_doan_co_so
                ly_do = ""
                current_cau_str = ""

                # --- Logic điều chỉnh dự đoán (chỉ áp dụng mẫu cầu động) ---
                if len(tx_history_for_game) == tx_history_length:
                    current_cau_str = ''.join(tx_history_for_game)
                    # CHANGES: Lấy điều chỉnh theo game_id
                    pattern_adjustment = get_pattern_prediction_adjustment(game_id, current_cau_str)

                    if pattern_adjustment == "giữ nguyên":
                        ly_do = f"AI Cầu đẹp ({current_cau_str}) → Giữ nguyên kết quả"
                    elif pattern_adjustment == "đảo chiều":
                        du_doan_cuoi_cung = "Xỉu" if du_doan_co_so == "Tài" else "Tài" # Đảo chiều
                        ly_do = f"AI Cầu xấu ({current_cau_str}) → Đảo chiều kết quả"
                    else:
                        ly_do = f"AI Không rõ/Đang học mẫu cầu ({current_cau_str}) → Dự đoán theo xí ngầu"
                else:
                    ly_do = f"AI Dự đoán theo xí ngầu (chưa đủ lịch sử cầu cho {tx_history_length} ký tự)"


                # Cập nhật độ tin cậy của mẫu cầu dựa trên kết quả thực tế
                if len(tx_history_for_game) == tx_history_length:
                    prediction_correct = (du_doan_cuoi_cung == "Tài" and ket_qua_tx == "Tài") or \
                                         (du_doan_cuoi_cung == "Xỉu" and ket_qua_tx == "Xỉu")
                    # CHANGES: Cập nhật mẫu cầu theo game_id
                    update_cau_patterns(game_id, current_cau_str, prediction_correct)

                # Gửi tin nhắn dự đoán tới tất cả người dùng có quyền truy cập
                for user_id_str, user_info in list(user_data.items()): # Dùng list() để tránh lỗi khi user_data thay đổi
                    user_id = int(user_id_str)
                    is_sub, sub_message = check_subscription(user_id)
                    if is_sub:
                        try:
                            prediction_message = (
                                f"🎮 **KẾT QUẢ PHIÊN HIỆN TẠI ({config['name']})** 🎮\n"
                                f"Phiên: `{expect}` | Kết quả: **{ket_qua_tx}** (Tổng: **{tong}**)\n\n"
                                f"**Dự đoán cho phiên tiếp theo:**\n"
                                f"🔢 Phiên: `{next_expect}`\n"
                                f"🤖 Dự đoán: **{du_doan_cuoi_cung}**\n"
                                f"📌 Lý do: _{ly_do}_\n"
                                f"⚠️ **Hãy đặt cược sớm trước khi phiên kết thúc!**"
                            )
                            bot.send_message(user_id, prediction_message, parse_mode='Markdown')
                        except telebot.apihelper.ApiTelegramException as e:
                            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                                pass
                            else:
                                print(f"Lỗi gửi tin nhắn cho user {user_id}: {e}")
                        except Exception as e:
                            print(f"Lỗi không xác định khi gửi tin nhắn cho user {user_id}: {e}")

                print("-" * 50)
                print(f"🎮 KẾT QUẢ VÀ DỰ ĐOÁN CHO {config['name']}")
                print("Phiên hiện tại: {} (Tổng: {})".format(ket_qua_tx, tong))
                print("🔢 Phiên: {} → {}".format(expect, next_expect))
                print("🤖 Dự đoán: {}".format(du_doan_cuoi_cung))
                print("📌 Lý do: {}".format(ly_do))
                print("Lịch sử TX: {}. ".format(''.join(tx_history_for_game)))
                print("-" * 50)

                current_game_state["last_id"] = issue_id
        
        # CHANGES: Thời gian nghỉ giữa các lần kiểm tra (giả định 1 phút 15 giây / 2 game = mỗi game được check 37.5 giây 1 lần)
        # Giảm thời gian sleep để không bị lỡ phiên quá lâu cho game 75s/phiên.
        # Ở đây tôi đặt là 10 giây để đảm bảo bot check mỗi game đủ nhanh.
        time.sleep(10) 
    print("Prediction loop stopped.")

# --- Xử lý lệnh Telegram ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name
    
    if user_id not in user_data:
        user_data[user_id] = {
            'username': username,
            'expiry_date': None,
            'is_ctv': False
        }
        save_user_data(user_data)
        bot.reply_to(message, 
                     "Chào mừng bạn đến với **BOT DỰ ĐOÁN TÀI XỈU**!\n"
                     "Hãy dùng lệnh /help để xem danh sách các lệnh hỗ trợ.", 
                     parse_mode='Markdown')
    else:
        user_data[user_id]['username'] = username # Cập nhật username nếu có thay đổi
        save_user_data(user_data)
        bot.reply_to(message, "Bạn đã khởi động bot rồi. Dùng /help để xem các lệnh.")

@bot.message_handler(commands=['help'])
def show_help(message):
    help_text = (
        "🤖 **DANH SÁCH LỆNH HỖ TRỢ** 🤖\n\n"
        "**Lệnh người dùng:**\n"
        "🔸 `/start`: Khởi động bot và thêm bạn vào hệ thống.\n"
        "🔸 `/help`: Hiển thị danh sách các lệnh.\n"
        "🔸 `/support`: Thông tin hỗ trợ Admin.\n"
        "🔸 `/gia`: Xem bảng giá dịch vụ.\n"
        "🔸 `/gopy <nội dung>`: Gửi góp ý/báo lỗi cho Admin.\n"
        "🔸 `/nap`: Hướng dẫn nạp tiền.\n"
        "🔸 `/dudoan`: Bắt đầu nhận dự đoán từ bot (mặc định Luckywin).\n" # Đổi tên
        "🔸 `/dudoan_hitclub`: Bắt đầu nhận dự đoán từ bot (Hit Club).\n"
        "🔸 `/maucau [tên game]`: Hiển thị các mẫu cầu bot đã thu thập (xấu/đẹp). (ví dụ: `/maucau luckywin` hoặc `/maucau hitclub`)\n" # Đổi tên
        "🔸 `/code <mã_code>`: Nhập mã code để gia hạn tài khoản.\n\n"
    )
    
    if is_ctv(message.chat.id):
        help_text += (
            "**Lệnh Admin/CTV:**\n"
            "🔹 `/full <id>`: Xem thông tin người dùng (để trống ID để xem của bạn).\n"
            "🔹 `/giahan <id> <số ngày/giờ>`: Gia hạn tài khoản người dùng. Ví dụ: `/giahan 12345 1 ngày` hoặc `/giahan 12345 24 giờ`.\n"
            "🔹 `/nhapcau <tên game>`: Nhập các mẫu cầu từ văn bản cho bot. (ví dụ: `/nhapcau luckywin` sau đó gửi text)\n\n" # Đổi tên
        )
    
    if is_admin(message.chat.id):
        help_text += (
            "**Lệnh Admin Chính:**\n"
            "👑 `/ctv <id>`: Thêm người dùng làm CTV.\n"
            "👑 `/xoactv <id>`: Xóa người dùng khỏi CTV.\n"
            "👑 `/tb <nội dung>`: Gửi thông báo đến tất cả người dùng.\n"
            "👑 `/tatbot <lý do>`: Tắt mọi hoạt động của bot dự đoán.\n"
            "👑 `/mokbot`: Mở lại hoạt động của bot dự đoán.\n"
            "👑 `/taocode <giá trị> <ngày/giờ> <số lượng>`: Tạo mã code gia hạn. Ví dụ: `/taocode 1 ngày 5` (tạo 5 code 1 ngày).\n"
        )
    
    bot.reply_to(message, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['support'])
def show_support(message):
    bot.reply_to(message, 
        "Để được hỗ trợ, vui lòng liên hệ Admin:\n"
        "@heheviptool hoặc @Besttaixiu999"
    )

@bot.message_handler(commands=['gia'])
def show_price(message):
    price_text = (
        "📊 **BOT LUCKYWIN XIN THÔNG BÁO BẢNG GIÁ LUCKYWIN BOT** 📊\n\n" # Đổi tên
        "💸 **20k**: 1 Ngày\n"
        "💸 **50k**: 1 Tuần\n"
        "💸 **80k**: 2 Tuần\n"
        "💸 **130k**: 1 Tháng\n\n"
        "🤖 BOT LUCKYWIN TỈ Lệ **85-92%**\n" # Đổi tên
        "⏱️ ĐỌC 24/24\n\n"
        "Vui Lòng ib @heheviptool hoặc @Besttaixiu999 Để Gia Hạn"
    )
    bot.reply_to(message, price_text, parse_mode='Markdown')

@bot.message_handler(commands=['gopy'])
def send_feedback(message):
    feedback_text = telebot.util.extract_arguments(message.text)
    if not feedback_text:
        bot.reply_to(message, "Vui lòng nhập nội dung góp ý. Ví dụ: `/gopy Bot dự đoán rất chuẩn!`", parse_mode='Markdown')
        return
    
    admin_id = ADMIN_IDS[0] # Gửi cho Admin đầu tiên trong danh sách
    user_name = message.from_user.username or message.from_user.first_name
    bot.send_message(admin_id, 
                     f"📢 **GÓP Ý MỚI TỪ NGƯỜI DÙNG** 📢\n\n"
                     f"**ID:** `{message.chat.id}`\n"
                     f"**Tên:** @{user_name}\n\n"
                     f"**Nội dung:**\n`{feedback_text}`",
                     parse_mode='Markdown')
    bot.reply_to(message, "Cảm ơn bạn đã gửi góp ý! Admin đã nhận được.")

@bot.message_handler(commands=['nap'])
def show_deposit_info(message):
    user_id = message.chat.id
    deposit_text = (
        "⚜️ **NẠP TIỀN MUA LƯỢT** ⚜️\n\n"
        "Để mua lượt, vui lòng chuyển khoản đến:\n"
        "- Ngân hàng: **MB BANK**\n"
        "- Số tài khoản: **0939766383**\n"
        "- Tên chủ TK: **Nguyen Huynh Nhut Quang**\n\n"
        "**NỘI DUNG CHUYỂN KHOẢN (QUAN TRỌNG):**\n"
        "`mua luot {user_id}`\n\n"
        f"❗️ Nội dung bắt buộc của bạn là:\n"
        f"`mua luot {user_id}`\n\n"
        "(Vui lòng sao chép đúng nội dung trên để được cộng lượt tự động)\n"
        "Sau khi chuyển khoản, vui lòng chờ 1-2 phút. Nếu có sự cố, hãy dùng lệnh /support."
    )
    bot.reply_to(message, deposit_text, parse_mode='Markdown')

@bot.message_handler(commands=['dudoan'])
def start_prediction_command(message):
    # CHANGES: Mặc định là Luckywin
    game_id = "luckywin" # Đã đổi từ "sunwin" thành "luckywin"
    user_id = message.chat.id
    is_sub, sub_message = check_subscription(user_id)
    
    if not is_sub:
        bot.reply_to(message, sub_message + "\nVui lòng liên hệ Admin @heheviptool hoặc @Besttaixiu999 để được hỗ trợ.", parse_mode='Markdown')
        return
    
    if not bot_enabled:
        bot.reply_to(message, f"❌ Bot dự đoán hiện đang tạm dừng bởi Admin. Lý do: `{bot_disable_reason}`", parse_mode='Markdown')
        return

    bot.reply_to(message, f"✅ Bạn đang có quyền truy cập. Bot sẽ tự động gửi dự đoán các phiên mới nhất của **{GAME_CONFIGS[game_id]['name']}** tại đây.")

# CHANGES: Lệnh dự đoán riêng cho Hit Club
@bot.message_handler(commands=['dudoan_hitclub'])
def start_prediction_hitclub_command(message):
    game_id = "hitclub" 
    user_id = message.chat.id
    is_sub, sub_message = check_subscription(user_id)
    
    if not is_sub:
        bot.reply_to(message, sub_message + "\nVui lòng liên hệ Admin @heheviptool hoặc @Besttaixiu999 để được hỗ trợ.", parse_mode='Markdown')
        return
    
    if not bot_enabled:
        bot.reply_to(message, f"❌ Bot dự đoán hiện đang tạm dừng bởi Admin. Lý do: `{bot_disable_reason}`", parse_mode='Markdown')
        return

    bot.reply_to(message, f"✅ Bạn đang có quyền truy cập. Bot sẽ tự động gửi dự đoán các phiên mới nhất của **{GAME_CONFIGS[game_id]['name']}** tại đây.")


@bot.message_handler(commands=['maucau'])
def show_cau_patterns_command(message):
    if not is_ctv(message.chat.id): # Chỉ Admin/CTV mới được xem mẫu cầu chi tiết
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or args[0].lower() not in GAME_CONFIGS:
        bot.reply_to(message, "Vui lòng chỉ định tên game (luckywin hoặc hitclub). Ví dụ: `/maucau luckywin` hoặc `/maucau hitclub`", parse_mode='Markdown') # Đổi tên
        return
    
    game_id = args[0].lower()
    game_name = GAME_CONFIGS[game_id]['name']

    # CHANGES: Lấy mẫu cầu cho game cụ thể
    game_patterns = CAU_PATTERNS.get(game_id, {})

    if not game_patterns:
        pattern_text = f"📚 **CÁC MẪU CẦU ĐÃ THU THUẬT CHO {game_name}** 📚\n\nKhông có mẫu cầu nào được thu thập."
    else:
        # Sắp xếp các mẫu cầu theo confidence score giảm dần
        sorted_patterns = sorted(game_patterns.items(), key=lambda item: item[1], reverse=True)
        dep_patterns_list = []
        xau_patterns_list = []

        for pattern, confidence in sorted_patterns:
            if confidence >= 2.5: # Ngưỡng "đẹp"
                dep_patterns_list.append(f"{pattern} ({confidence:.2f})")
            elif confidence <= 0.5: # Ngưỡng "xấu"
                xau_patterns_list.append(f"{pattern} ({confidence:.2f})")
            # Các mẫu ở giữa không rõ ràng thì không liệt kê vào đây

        dep_patterns_str = "\n".join(dep_patterns_list) if dep_patterns_list else "Không có"
        xau_patterns_str = "\n".join(xau_patterns_list) if xau_patterns_list else "Không có"

        pattern_text = (
            f"📚 **CÁC MẪU CẦU ĐÃ THU THUẬT CHO {game_name}** 📚\n\n"
            "**🟢 Cầu Đẹp (Confidence >= 2.5):**\n"
            f"```\n{dep_patterns_str}\n```\n\n"
            "**🔴 Cầu Xấu (Confidence <= 0.5):**\n"
            f"```\n{xau_patterns_str}\n```\n"
            "*(Các mẫu cầu này được bot tự động học hỏi theo thời gian. Số trong ngoặc là điểm tin cậy)*"
        )
    bot.reply_to(message, pattern_text, parse_mode='Markdown')

# CHANGES: Thêm lệnh /nhapcau để nhập mẫu cầu từ text
@bot.message_handler(commands=['nhapcau'])
def prompt_import_patterns(message):
    if not is_ctv(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or args[0].lower() not in GAME_CONFIGS:
        bot.reply_to(message, "Vui lòng chỉ định tên game (luckywin hoặc hitclub) để nhập cầu. Ví dụ: `/nhapcau luckywin`", parse_mode='Markdown') # Đổi tên
        return
    
    game_id = args[0].lower()
    game_name = GAME_CONFIGS[game_id]['name']

    markup = telebot.types.ForceReply(selective=True)
    msg = bot.reply_to(message, f"Vui lòng dán văn bản chứa mẫu cầu {game_name} (theo định dạng /maucau) vào đây:", reply_markup=markup)
    bot.register_next_step_handler(msg, import_patterns_from_text, game_id)

def import_patterns_from_text(message, game_id):
    if not is_ctv(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    input_text = message.text
    new_patterns_count = 0
    updated_patterns_count = 0
    
    # Regex để tìm kiếm các mẫu cầu và confidence score
    # Ví dụ: XTTXXXT (0.50)
    import re
    pattern_regex = re.compile(r'([TX]+)\s+\((\d+\.\d+)\)')

    lines = input_text.split('\n')
    
    # CHANGES: Lấy mẫu cầu hiện tại của game cụ thể
    current_game_patterns = CAU_PATTERNS.get(game_id, {})

    for line in lines:
        match = pattern_regex.search(line)
        if match:
            pattern_str = match.group(1)
            confidence = float(match.group(2))
            
            if pattern_str in current_game_patterns:
                updated_patterns_count += 1
            else:
                new_patterns_count += 1
            
            current_game_patterns[pattern_str] = confidence
    
    # CHANGES: Cập nhật CAU_PATTERNS toàn cục
    CAU_PATTERNS[game_id] = current_game_patterns
    save_cau_patterns()

    bot.reply_to(message, 
                 f"✅ Đã nhập mẫu cầu cho **{GAME_CONFIGS[game_id]['name']}** thành công!\n"
                 f"Mới: {new_patterns_count} mẫu. Cập nhật: {updated_patterns_count} mẫu.",
                 parse_mode='Markdown')


@bot.message_handler(commands=['code'])
def use_code(message):
    code_str = telebot.util.extract_arguments(message.text)
    user_id = str(message.chat.id)

    if not code_str:
        bot.reply_to(message, "Vui lòng nhập mã code. Ví dụ: `/code ABCXYZ`", parse_mode='Markdown')
        return
    
    if code_str not in GENERATED_CODES:
        bot.reply_to(message, "❌ Mã code không tồn tại hoặc đã hết hạn.")
        return

    code_info = GENERATED_CODES[code_str]
    if code_info.get('used_by') is not None:
        bot.reply_to(message, "❌ Mã code này đã được sử dụng rồi.")
        return

    # Apply extension
    current_expiry_str = user_data.get(user_id, {}).get('expiry_date')
    if current_expiry_str:
        current_expiry_date = datetime.strptime(current_expiry_str, '%Y-%m-%d %H:%M:%S')
        # If current expiry is in the past, start from now
        if datetime.now() > current_expiry_date:
            new_expiry_date = datetime.now()
        else:
            new_expiry_date = current_expiry_date
    else:
        new_expiry_date = datetime.now() # Start from now if no previous expiry

    value = code_info['value']
    if code_info['type'] == 'ngày':
        new_expiry_date += timedelta(days=value)
    elif code_info['type'] == 'giờ':
        new_expiry_date += timedelta(hours=value)
    
    user_data.setdefault(user_id, {})['expiry_date'] = new_expiry_date.strftime('%Y-%m-%d %H:%M:%S')
    user_data[user_id]['username'] = message.from_user.username or message.from_user.first_name
    
    GENERATED_CODES[code_str]['used_by'] = user_id
    GENERATED_CODES[code_str]['used_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    save_user_data(user_data)
    save_codes()

    bot.reply_to(message, 
                 f"🎉 Bạn đã đổi mã code thành công! Tài khoản của bạn đã được gia hạn thêm **{value} {code_info['type']}**.\n"
                 f"Ngày hết hạn mới: `{user_expiry_date(user_id)}`", 
                 parse_mode='Markdown')

def user_expiry_date(user_id):
    if str(user_id) in user_data and user_data[str(user_id)].get('expiry_date'):
        return user_data[str(user_id)]['expiry_date']
    return "Không có"

# --- Lệnh Admin/CTV ---
@bot.message_handler(commands=['full'])
def get_user_info(message):
    if not is_ctv(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    target_user_id_str = str(message.chat.id)
    if args and args[0].isdigit():
        target_user_id_str = args[0]
    
    if target_user_id_str not in user_data:
        bot.reply_to(message, f"Không tìm thấy thông tin cho người dùng ID `{target_user_id_str}`.")
        return

    user_info = user_data[target_user_id_str]
    expiry_date_str = user_info.get('expiry_date', 'Không có')
    username = user_info.get('username', 'Không rõ')
    is_ctv_status = "Có" if is_ctv(int(target_user_id_str)) else "Không"

    info_text = (
        f"**THÔNG TIN NGƯỠNG DÙNG**\n"
        f"**ID:** `{target_user_id_str}`\n"
        f"**Tên:** @{username}\n"
        f"**Ngày hết hạn:** `{expiry_date_str}`\n"
        f"**Là CTV/Admin:** {is_ctv_status}"
    )
    bot.reply_to(message, info_text, parse_mode='Markdown')

@bot.message_handler(commands=['giahan'])
def extend_subscription(message):
    if not is_ctv(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if len(args) != 3 or not args[0].isdigit() or not args[1].isdigit() or args[2].lower() not in ['ngày', 'giờ']:
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/giahan <id_nguoi_dung> <số_lượng> <ngày/giờ>`\n"
                              "Ví dụ: `/giahan 12345 1 ngày` hoặc `/giahan 12345 24 giờ`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    value = int(args[1])
    unit = args[2].lower() # 'ngày' or 'giờ'
    
    if target_user_id_str not in user_data:
        user_data[target_user_id_str] = {
            'username': "UnknownUser",
            'expiry_date': None,
            'is_ctv': False
        }
        bot.send_message(message.chat.id, f"Đã tạo tài khoản mới cho user ID `{target_user_id_str}`.")

    current_expiry_str = user_data[target_user_id_str].get('expiry_date')
    if current_expiry_str:
        current_expiry_date = datetime.strptime(current_expiry_str, '%Y-%m-%d %H:%M:%S')
        if datetime.now() > current_expiry_date:
            new_expiry_date = datetime.now()
        else:
            new_expiry_date = current_expiry_date
    else:
        new_expiry_date = datetime.now() # Start from now if no previous expiry

    if unit == 'ngày':
        new_expiry_date += timedelta(days=value)
    elif unit == 'giờ':
        new_expiry_date += timedelta(hours=value)
    
    user_data[target_user_id_str]['expiry_date'] = new_expiry_date.strftime('%Y-%m-%d %H:%M:%S')
    user_data[target_user_id_str]['username'] = user_data[target_user_id_str].get('username', 'UnknownUser') # Giữ lại username nếu có
    save_user_data(user_data)
    
    bot.reply_to(message, 
                 f"Đã gia hạn thành công cho user ID `{target_user_id_str}` thêm **{value} {unit}**.\n"
                 f"Ngày hết hạn mới: `{user_data[target_user_id_str]['expiry_date']}`",
                 parse_mode='Markdown')
    
    try:
        bot.send_message(int(target_user_id_str), 
                         f"🎉 Tài khoản của bạn đã được gia hạn thêm **{value} {unit}** bởi Admin/CTV!\n"
                         f"Ngày hết hạn mới của bạn là: `{user_data[target_user_id_str]['expiry_date']}`",
                         parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
        if "bot was blocked by the user" in str(e):
            pass
        else:
            print(f"Không thể thông báo gia hạn cho user {target_user_id_str}: {e}")

# --- Lệnh Admin Chính ---
@bot.message_handler(commands=['ctv'])
def add_ctv(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/ctv <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    if target_user_id_str not in user_data:
        user_data[target_user_id_str] = {
            'username': "UnknownUser",
            'expiry_date': None,
            'is_ctv': True
        }
    else:
        user_data[target_user_id_str]['is_ctv'] = True
    
    save_user_data(user_data)
    bot.reply_to(message, f"Đã cấp quyền CTV cho user ID `{target_user_id_str}`.")
    try:
        bot.send_message(int(target_user_id_str), "🎉 Bạn đã được cấp quyền CTV!")
    except Exception:
        pass

@bot.message_handler(commands=['xoactv'])
def remove_ctv(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/xoactv <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    if target_user_id_str in user_data:
        user_data[target_user_id_str]['is_ctv'] = False
        save_user_data(user_data)
        bot.reply_to(message, f"Đã xóa quyền CTV của user ID `{target_user_id_str}`.")
        try:
            bot.send_message(int(target_user_id_str), "❌ Quyền CTV của bạn đã bị gỡ bỏ.")
        except Exception:
            pass
    else:
        bot.reply_to(message, f"Không tìm thấy người dùng có ID `{target_user_id_str}`.")

@bot.message_handler(commands=['tb'])
def send_broadcast(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    broadcast_text = telebot.util.extract_arguments(message.text)
    if not broadcast_text:
        bot.reply_to(message, "Vui lòng nhập nội dung thông báo. Ví dụ: `/tb Bot sẽ bảo trì vào 2h sáng mai.`", parse_mode='Markdown')
        return
    
    success_count = 0
    fail_count = 0
    for user_id_str in list(user_data.keys()):
        try:
            bot.send_message(int(user_id_str), f"📢 **THÔNG BÁO TỪ ADMIN** 📢\n\n{broadcast_text}", parse_mode='Markdown')
            success_count += 1
            time.sleep(0.1) # Tránh bị rate limit
        except telebot.apihelper.ApiTelegramException as e:
            fail_count += 1
            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                pass
        except Exception as e:
            print(f"Lỗi không xác định khi gửi thông báo cho user {user_id_str}: {e}")
            fail_count += 1
                
    bot.reply_to(message, f"Đã gửi thông báo đến {success_count} người dùng. Thất bại: {fail_count}.")
    save_user_data(user_data) # Lưu lại nếu có user bị xóa

@bot.message_handler(commands=['tatbot'])
def disable_bot_command(message):
    global bot_enabled, bot_disable_reason, bot_disable_admin_id
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    reason = telebot.util.extract_arguments(message.text)
    if not reason:
        bot.reply_to(message, "Vui lòng nhập lý do tắt bot. Ví dụ: `/tatbot Bot đang bảo trì.`", parse_mode='Markdown')
        return

    bot_enabled = False
    bot_disable_reason = reason
    bot_disable_admin_id = message.chat.id
    bot.reply_to(message, f"✅ Bot dự đoán đã được tắt bởi Admin `{message.from_user.username or message.from_user.first_name}`.\nLý do: `{reason}`", parse_mode='Markdown')
    
@bot.message_handler(commands=['mokbot'])
def enable_bot_command(message):
    global bot_enabled, bot_disable_reason, bot_disable_admin_id
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    if bot_enabled:
        bot.reply_to(message, "Bot dự đoán đã và đang hoạt động rồi.")
        return

    bot_enabled = True
    bot_disable_reason = "Không có"
    bot_disable_admin_id = None
    bot.reply_to(message, "✅ Bot dự đoán đã được mở lại bởi Admin.")
    
@bot.message_handler(commands=['taocode'])
def generate_code_command(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if len(args) < 2 or len(args) > 3: # Giá trị, đơn vị, số lượng (tùy chọn)
        bot.reply_to(message, "Cú pháp sai. Ví dụ:\n"
                              "`/taocode <giá_trị> <ngày/giờ> <số_lượng>`\n"
                              "Ví dụ: `/taocode 1 ngày 5` (tạo 5 code 1 ngày)\n"
                              "Hoặc: `/taocode 24 giờ` (tạo 1 code 24 giờ)", parse_mode='Markdown')
        return
    
    try:
        value = int(args[0])
        unit = args[1].lower()
        quantity = int(args[2]) if len(args) == 3 else 1 # Mặc định tạo 1 code nếu không có số lượng
        
        if unit not in ['ngày', 'giờ']:
            bot.reply_to(message, "Đơn vị không hợp lệ. Chỉ chấp nhận `ngày` hoặc `giờ`.", parse_mode='Markdown')
            return
        if value <= 0 or quantity <= 0:
            bot.reply_to(message, "Giá trị hoặc số lượng phải lớn hơn 0.", parse_mode='Markdown')
            return

        generated_codes_list = []
        for _ in range(quantity):
            new_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8)) # 8 ký tự ngẫu nhiên
            GENERATED_CODES[new_code] = {
                "value": value,
                "type": unit,
                "used_by": None,
                "used_time": None
            }
            generated_codes_list.append(new_code)
        
        save_codes()
        
        response_text = f"✅ Đã tạo thành công {quantity} mã code gia hạn **{value} {unit}**:\n\n"
        response_text += "\n".join([f"`{code}`" for code in generated_codes_list])
        response_text += "\n\n_(Các mã này chưa được sử dụng)_"
        
        bot.reply_to(message, response_text, parse_mode='Markdown')

    except ValueError:
        bot.reply_to(message, "Giá trị hoặc số lượng không hợp lệ. Vui lòng nhập số nguyên.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"Đã xảy ra lỗi khi tạo code: {e}", parse_mode='Markdown')


# --- Flask Routes cho Keep-Alive ---
@app.route('/')
def home():
    return "Bot is alive and running!"

@app.route('/health')
def health_check():
    return "OK", 200

# --- Khởi tạo bot và các luồng khi Flask app khởi động ---
@app.before_request
def start_bot_threads():
    global bot_initialized
    with bot_init_lock:
        if not bot_initialized:
            print("Initializing bot and prediction threads...")
            # Load initial data
            load_user_data()
            load_cau_patterns()
            load_codes()

            # Start prediction loop in a separate thread
            prediction_thread = Thread(target=prediction_loop, args=(prediction_stop_event,))
            prediction_thread.daemon = True
            prediction_thread.start()
            print("Prediction loop thread started.")

            # Start bot polling in a separate thread
            polling_thread = Thread(target=bot.infinity_polling, kwargs={'none_stop': True})
            polling_thread.daemon = True
            polling_thread.start()
            print("Telegram bot polling thread started.")
            
            bot_initialized = True

# --- Điểm khởi chạy chính cho Gunicorn/Render ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Flask app locally on port {port}")
    app.run(host='0.0.0.0', port=port, debug=True)

