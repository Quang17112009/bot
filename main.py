import telebot
import requests
import time
import json
import os
import random
import string
import sys 
from datetime import datetime, timedelta
from threading import Thread, Event, Lock

from flask import Flask, request

# --- Cấu hình Bot (ĐẶT TRỰC TIẾP TẠI ĐÂY) ---
BOT_TOKEN = "7820739987:AAE_eU2JPZH7u6KnDRq31_l4tn64AD_8f6s" 
ADMIN_IDS = [6915752059] 

DATA_FILE = 'user_data.json'
CAU_PATTERNS_FILE = 'cau_patterns.json'
CODES_FILE = 'codes.json'

# --- Khởi tạo Flask App và Telegram Bot ---
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN)

# Global flags và objects
bot_enabled = True
bot_disable_reason = "Không có"
bot_disable_admin_id = None
prediction_stop_event = Event() 
bot_initialized = False 
bot_init_lock = Lock() 

# Global sets for patterns and codes
CAU_XAU = set()
CAU_DEP = set()
GENERATED_CODES = {} 

# --- Quản lý dữ liệu người dùng, mẫu cầu và code ---
user_data = {}

def load_user_data():
    global user_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try:
                user_data = json.load(f)
                print(f"DEBUG: Tải {len(user_data)} bản ghi người dùng từ {DATA_FILE}")
            except json.JSONDecodeError:
                print(f"LỖI: Lỗi đọc {DATA_FILE}. Khởi tạo lại dữ liệu người dùng.")
                user_data = {}
            except Exception as e:
                print(f"LỖI: Lỗi không xác định khi tải {DATA_FILE}: {e}")
                user_data = {}
    else:
        user_data = {}
        print(f"DEBUG: File {DATA_FILE} không tồn tại. Khởi tạo dữ liệu người dùng rỗng.")
    sys.stdout.flush()

def save_user_data(data):
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        # print(f"DEBUG: Đã lưu {len(data)} bản ghi người dùng vào {DATA_FILE}")
    except Exception as e:
        print(f"LỖI: Không thể lưu dữ liệu người dùng vào {DATA_FILE}: {e}")
    sys.stdout.flush()

def load_cau_patterns():
    global CAU_XAU, CAU_DEP
    if os.path.exists(CAU_PATTERNS_FILE):
        with open(CAU_PATTERNS_FILE, 'r') as f:
            try:
                data = json.load(f)
                CAU_DEP.update(data.get('dep', []))
                CAU_XAU.update(data.get('xau', []))
                print(f"DEBUG: Tải {len(CAU_DEP)} mẫu cầu đẹp và {len(CAU_XAU)} mẫu cầu xấu từ {CAU_PATTERNS_FILE}")
            except json.JSONDecodeError:
                print(f"LỖI: Lỗi đọc {CAU_PATTERNS_FILE}. Khởi tạo lại mẫu cầu.")
                CAU_DEP = set()
                CAU_XAU = set()
            except Exception as e:
                print(f"LỖI: Lỗi không xác định khi tải {CAU_PATTERNS_FILE}: {e}")
                CAU_DEP = set()
                CAU_XAU = set()
    else:
        CAU_DEP = set()
        CAU_XAU = set()
        print(f"DEBUG: File {CAU_PATTERNS_FILE} không tồn tại. Khởi tạo mẫu cầu rỗng.")
    sys.stdout.flush()

def save_cau_patterns():
    try:
        with open(CAU_PATTERNS_FILE, 'w') as f:
            json.dump({'dep': list(CAU_DEP), 'xau': list(CAU_XAU)}, f, indent=4)
        # print(f"DEBUG: Đã lưu mẫu cầu: Cầu đẹp: {len(CAU_DEP)}, Cầu xấu: {len(CAU_XAU)}")
    except Exception as e:
        print(f"LỖI: Không thể lưu mẫu cầu vào {CAU_PATTERNS_FILE}: {e}")
    sys.stdout.flush()

def load_codes():
    global GENERATED_CODES
    if os.path.exists(CODES_FILE):
        with open(CODES_FILE, 'r') as f:
            try:
                GENERATED_CODES = json.load(f)
                print(f"DEBUG: Tải {len(GENERATED_CODES)} mã code từ {CODES_FILE}")
            except json.JSONDecodeError:
                print(f"LỖI: Lỗi đọc {CODES_FILE}. Khởi tạo lại mã code.")
                GENERATED_CODES = {}
            except Exception as e:
                print(f"LỖI: Lỗi không xác định khi tải {CODES_FILE}: {e}")
                GENERATED_CODES = {}
    else:
        GENERATED_CODES = {}
        print(f"DEBUG: File {CODES_FILE} không tồn tại. Khởi tạo mã code rỗng.")
    sys.stdout.flush()

def save_codes():
    try:
        with open(CODES_FILE, 'w') as f:
            json.dump(GENERATED_CODES, f, indent=4)
        # print(f"DEBUG: Đã lưu {len(GENERATED_CODES)} mã code vào {CODES_FILE}")
    except Exception as e:
        print(f"LỖI: Không thể lưu mã code vào {CODES_FILE}: {e}")
    sys.stdout.flush()

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
        return True, f"✅ Tài khoản của bạn còn hạn đến: `{expiry_date_str}` ({days} ngày {hours} giờ {minutes} phút {seconds} giây)."
    else:
        return False, "❌ Tài khoản của bạn đã hết hạn."

# --- Logic dự đoán Tài Xỉu ---
def tinh_tai_xiu(dice):
    total = sum(dice)
    return "Tài" if total >= 11 else "Xỉu", total

def du_doan_theo_xi_ngau_va_lich_su(dice_list, tx_history):
    # dice_list should be recent dice results (e.g., last 20)
    # tx_history should be 'T' or 'X' for recent results

    if not dice_list:
        return "Đợi thêm dữ liệu", "Không đủ dữ liệu xúc xắc để dự đoán."

    # --- Prediction based on the latest dice ---
    d1, d2, d3 = dice_list[-1]
    total = d1 + d2 + d3

    result_list = []
    for d in [d1, d2, d3]:
        tmp = d + total
        # Điều chỉnh lại logic tmp nếu cần, giữ theo logic cũ của bạn
        if tmp in [4, 5]:
            tmp -= 4
        elif tmp >= 6:
            tmp -= 6
        result_list.append("Tài" if tmp % 2 == 0 else "Xỉu")

    primary_prediction = max(set(result_list), key=result_list.count)

    # --- Enhance prediction using historical patterns (last 20 sessions) ---
    # Start considering patterns after at least 5 sessions for meaningful patterns
    if len(tx_history) >= 5: 
        # Check various lengths of recent patterns from 5 up to 20
        # Iterate from longest pattern to shortest for stronger matches first
        for length in range(min(len(tx_history), 20), 4, -1): # From min(current_len, 20) down to 5
            current_cau_pattern = ''.join(tx_history[-length:])
            
            if is_cau_dep(current_cau_pattern):
                return primary_prediction, f"AI Cầu đẹp ({current_cau_pattern}) → Giữ nguyên kết quả"
            elif is_cau_xau(current_cau_pattern):
                # Reverse prediction if it's a "bad" pattern
                reversed_prediction = "Xỉu" if primary_prediction == "Tài" else "Tài"
                return reversed_prediction, f"AI Cầu xấu ({current_cau_pattern}) → Đảo chiều kết quả"
    
    return primary_prediction, "AI Dự đoán theo xí ngầu (chưa đủ hoặc không rõ mẫu cầu)"


# --- Cập nhật mẫu cầu động ---
def update_cau_patterns(new_cau, prediction_correct):
    global CAU_DEP, CAU_XAU
    if prediction_correct:
        CAU_DEP.add(new_cau)
        if new_cau in CAU_XAU:
            CAU_XAU.remove(new_cau)
            print(f"DEBUG: Xóa mẫu cầu '{new_cau}' khỏi cầu xấu.")
    else:
        CAU_XAU.add(new_cau)
        if new_cau in CAU_DEP:
            CAU_DEP.remove(new_cau)
            print(f"DEBUG: Xóa mẫu cầu '{new_cau}' khỏi cầu đẹp.")
    save_cau_patterns()
    sys.stdout.flush()

def is_cau_xau(cau_str):
    return cau_str in CAU_XAU

def is_cau_dep(cau_str):
    return cau_str in CAU_DEP and cau_str not in CAU_XAU # Đảm bảo không trùng cầu xấu

# --- Lấy dữ liệu từ API ---
def lay_du_lieu():
    try:
        # Thay thế bằng URL API THỰC TẾ của bạn
        # Dựa trên định dạng JSON bạn cung cấp, API của bạn có thể trả về dữ liệu trực tiếp mà không cần đường dẫn phụ
        response = requests.get("https://1.bot/GetNewLottery/LT_Taixiu", timeout=10) 
        response.raise_for_status() 
        data = response.json()
        
        # Kiểm tra cấu trúc JSON bạn đã cung cấp: {"state":1,"data":{...}}
        if data.get("state") != 1 or "data" not in data:
            print(f"DEBUG: API trả về state không thành công hoặc thiếu trường 'data': {data.get('state')} cho {response.url}. Phản hồi đầy đủ: {data}")
            sys.stdout.flush()
            return None
        
        actual_data = data.get("data") # Lấy phần 'data' bên trong JSON
        
        if not all(k in actual_data for k in ["ID", "Expect", "OpenCode"]):
            print(f"DEBUG: Dữ liệu API trong trường 'data' không đầy đủ (thiếu ID, Expect, hoặc OpenCode). Dữ liệu: {actual_data}")
            sys.stdout.flush()
            return None

        print(f"DEBUG: Data fetched from API ({response.url}): {data}")
        sys.stdout.flush()
        return actual_data # Trả về phần actual_data để xử lý

    except requests.exceptions.Timeout:
        print(f"LỖI: Hết thời gian chờ khi lấy dữ liệu từ API: {response.url}")
        sys.stdout.flush()
        return None
    except requests.exceptions.ConnectionError as e:
        print(f"LỖI: Lỗi kết nối khi lấy dữ liệu từ API: {response.url} - {e}")
        sys.stdout.flush()
        return None
    except requests.exceptions.RequestException as e:
        print(f"LỖI: Lỗi HTTP hoặc Request khác khi lấy dữ liệu từ API: {response.url} - {e}")
        sys.stdout.flush()
        return None
    except json.JSONDecodeError:
        print(f"LỖI: Lỗi giải mã JSON từ API ({response.url}). Phản hồi không phải JSON hợp lệ hoặc trống.")
        print(f"DEBUG: Phản hồi thô nhận được: {response.text}")
        sys.stdout.flush()
        return None
    except Exception as e:
        print(f"LỖI: Lỗi không xác định khi lấy dữ liệu API ({response.url}): {e}")
        sys.stdout.flush()
        return None

# --- Logic chính của Bot dự đoán (chạy trong luồng riêng) ---
def prediction_loop(stop_event: Event):
    last_id = None
    tx_history = [] 
    dice_history = [] 
    
    print("LOG: Luồng dự đoán đã khởi động.")
    sys.stdout.flush()

    while not stop_event.is_set():
        if not bot_enabled:
            print(f"LOG: Bot dự đoán đang tạm dừng. Lý do: {bot_disable_reason}")
            sys.stdout.flush()
            time.sleep(10) 
            continue

        data = lay_du_lieu()
        if not data:
            print("LOG: ❌ Không lấy được dữ liệu từ API hoặc dữ liệu không hợp lệ. Đang chờ phiên mới...")
            sys.stdout.flush()
            time.sleep(5)
            continue

        # Đảm bảo lấy đúng các trường từ `actual_data` đã được trả về từ `lay_du_lieu()`
        issue_id = data.get("ID")
        expect = data.get("Expect")
        open_code = data.get("OpenCode")

        # Kiểm tra lại một lần nữa để đảm bảo dữ liệu cần thiết không bị None
        if not all([issue_id, expect, open_code]):
            print(f"LOG: Dữ liệu API không đầy đủ (thiếu ID, Expect, hoặc OpenCode) cho phiên {expect}. Bỏ qua phiên này. Dữ liệu: {data}")
            sys.stdout.flush()
            time.sleep(5)
            continue

        if issue_id != last_id:
            try:
                dice = tuple(map(int, open_code.split(",")))
                if len(dice) != 3: 
                    raise ValueError("OpenCode không chứa 3 giá trị xúc xắc.")
            except ValueError as e:
                print(f"LỖI: Lỗi phân tích OpenCode: '{open_code}'. {e}. Bỏ qua phiên này.")
                sys.stdout.flush()
                last_id = issue_id 
                time.sleep(5)
                continue
            except Exception as e:
                print(f"LỖI: Lỗi không xác định khi xử lý OpenCode '{open_code}': {e}. Bỏ qua phiên này.")
                sys.stdout.flush()
                last_id = issue_id
                time.sleep(5)
                continue
            
            ket_qua_tx, tong = tinh_tai_xiu(dice)

            # Update history for 20 sessions
            if len(tx_history) >= 20:
                tx_history.pop(0)
            tx_history.append("T" if ket_qua_tx == "Tài" else "X")

            if len(dice_history) >= 20:
                dice_history.pop(0)
            dice_history.append(dice)


            next_expect = str(int(expect) + 1).zfill(len(expect))
            
            # Use the enhanced prediction function
            du_doan, ly_do = du_doan_theo_xi_ngau_va_lich_su(dice_history, tx_history)
            
            # --- Dynamic pattern learning based on the actual result ---
            if len(tx_history) >= 5: 
                # For pattern learning, consider a range of pattern lengths from 5 to 20
                for length in range(5, min(len(tx_history) + 1, 21)):
                    current_learning_cau = ''.join(tx_history[-length:])
                    # Logic để xác định prediction_correct cần dùng `du_doan` (dự đoán của bot) 
                    # và `ket_qua_tx` (kết quả thực tế của phiên vừa qua)
                    prediction_correct = (du_doan == "Tài" and ket_qua_tx == "Tài") or \
                                         (du_doan == "Xỉu" and ket_qua_tx == "Xỉu")
                    update_cau_patterns(current_learning_cau, prediction_correct)
                    print(f"DEBUG: Cập nhật mẫu cầu: '{current_learning_cau}' (length {length}) - Chính xác: {prediction_correct}")
                    sys.stdout.flush()


            # Gửi tin nhắn dự đoán tới tất cả người dùng có quyền truy cập
            for user_id_str, user_info in list(user_data.items()): 
                user_id = int(user_id_str)
                is_sub, sub_message = check_subscription(user_id)
                if is_sub:
                    try:
                        prediction_message = (
                            "🎮 **KẾT QUẢ PHIÊN HIỆN TẠI** 🎮\n"
                            f"Phiên: `{expect}` | Kết quả: **{ket_qua_tx}** (Tổng: **{tong}**)\n\n"
                            f"**Dự đoán cho phiên tiếp theo:**\n"
                            f"🔢 Phiên: `{next_expect}`\n"
                            f"🤖 Dự đoán: **{du_doan}**\n"
                            f"📌 Lý do: _{ly_do}_\n"
                            f"⚠️ **Hãy đặt cược sớm trước khi phiên kết thúc!**"
                        )
                        bot.send_message(user_id, prediction_message, parse_mode='Markdown')
                        print(f"DEBUG: Đã gửi dự đoán cho user {user_id_str}")
                        sys.stdout.flush()
                    except telebot.apihelper.ApiTelegramException as e:
                        print(f"LỖI: Lỗi Telegram API khi gửi tin nhắn cho user {user_id}: {e}")
                        sys.stdout.flush()
                        if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                            print(f"CẢNH BÁO: Người dùng {user_id} đã chặn bot hoặc bị vô hiệu hóa. Có thể xem xét xóa khỏi danh sách.")
                            sys.stdout.flush()
                            # if user_id_str in user_data:
                            #     del user_data[user_id_str] 
                            #     save_user_data(user_data)
                    except Exception as e:
                        print(f"LỖI: Lỗi không xác định khi gửi tin nhắn cho user {user_id}: {e}")
                        sys.stdout.flush()

            print("-" * 50)
            print("LOG: Phiên {} -> {}. Kết quả: {} ({}). Dự đoán: {}. Lý do: {}".format(expect, next_expect, ket_qua_tx, tong, du_doan, ly_do))
            print("-" * 50)
            sys.stdout.flush()

            last_id = issue_id

        time.sleep(5) 
    print("LOG: Luồng dự đoán đã dừng.")
    sys.stdout.flush()

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
                     "Chào mừng bạn đến với **BOT DỰ ĐOÁN TÀI XỈU SUNWIN**!\n"
                     "Hãy dùng lệnh /help để xem danh sách các lệnh hỗ trợ.", 
                     parse_mode='Markdown')
    else:
        user_data[user_id]['username'] = username 
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
        "🔸 `/dudoan`: Bắt đầu nhận dự đoán từ bot.\n"
        "🔸 `/maucau`: Hiển thị các mẫu cầu bot đã thu thập (xấu/đẹp).\n"
        "🔸 `/code <mã_code>`: Nhập mã code để gia hạn tài khoản.\n\n"
    )
    
    if is_ctv(message.chat.id):
        help_text += (
            "**Lệnh Admin/CTV:**\n"
            "🔹 `/full <id>`: Xem thông tin người dùng (để trống ID để xem của bạn).\n"
            "🔹 `/giahan <id> <số ngày/giờ>`: Gia hạn tài khoản người dùng. Ví dụ: `/giahan 12345 1 ngày` hoặc `/giahan 12345 24 giờ`.\n\n"
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
        "📊 **BOT SUNWIN XIN THÔNG BÁO BẢNG GIÁ SUN BOT** 📊\n\n"
        "💸 **20k**: 1 Ngày\n"
        "💸 **50k**: 1 Tuần\n"
        "💸 **80k**: 2 Tuần\n"
        "💸 **130k**: 1 Tháng\n\n"
        "🤖 BOT SUN TỈ Lệ **85-92%**\n"
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
    
    admin_id = ADMIN_IDS[0] 
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
    user_id = message.chat.id
    is_sub, sub_message = check_subscription(user_id)
    
    if not is_sub:
        bot.reply_to(message, sub_message + "\nVui lòng liên hệ Admin @heheviptool hoặc @Besttaixiu999 để được hỗ trợ.", parse_mode='Markdown')
        return
    
    if not bot_enabled:
        bot.reply_to(message, f"❌ Bot dự đoán hiện đang tạm dừng bởi Admin. Lý do: `{bot_disable_reason}`", parse_mode='Markdown')
        return

    bot.reply_to(message, "✅ Bạn đang có quyền truy cập. Bot sẽ tự động gửi dự đoán các phiên mới nhất tại đây.")

@bot.message_handler(commands=['maucau'])
def show_cau_patterns(message):
    if not is_ctv(message.chat.id): 
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    dep_patterns = "\n".join(sorted(list(CAU_DEP))) if CAU_DEP else "Không có"
    xau_patterns = "\n".join(sorted(list(CAU_XAU))) if CAU_XAU else "Không có"

    pattern_text = (
        "📚 **CÁC MẪU CẦU ĐÃ THU THẬP** 📚\n\n"
        "**🟢 Cầu Đẹp:**\n"
        f"```\n{dep_patterns}\n```\n\n"
        "**🔴 Cầu Xấu:**\n"
        f"```\n{xau_patterns}\n```\n"
        "*(Các mẫu cầu này được bot tự động học hỏi theo thời gian.)*"
    )
    bot.reply_to(message, pattern_text, parse_mode='Markdown')

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

    current_expiry_str = user_data.get(user_id, {}).get('expiry_date')
    if current_expiry_str:
        current_expiry_date = datetime.strptime(current_expiry_str, '%Y-%m-%d %H:%M:%S')
        if datetime.now() > current_expiry_date:
            new_expiry_date = datetime.now()
        else:
            new_expiry_date = current_expiry_date
    else:
        new_expiry_date = datetime.now() 

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
                 f"Ngày hết hạn mới: `{user_data[user_id]['expiry_date']}`", 
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
        f"**THÔNG TIN NGƯỜI DÙNG**\n"
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
    unit = args[2].lower() 
    
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
        new_expiry_date = datetime.now() 

    if unit == 'ngày':
        new_expiry_date += timedelta(days=value)
    elif unit == 'giờ':
        new_expiry_date += timedelta(hours=value)
    
    user_data[target_user_id_str]['expiry_date'] = new_expiry_date.strftime('%Y-%m-%d %H:%M:%S')
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
            print(f"CẢNH BÁO: Không thể thông báo gia hạn cho user {target_user_id_str}: Người dùng đã chặn bot.")
        else:
            print(f"LỖI: Không thể thông báo gia hạn cho user {target_user_id_str}: {e}")
        sys.stdout.flush()

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
            time.sleep(0.1) 
        except telebot.apihelper.ApiTelegramException as e:
            print(f"LỖI: Không thể gửi thông báo cho user {user_id_str}: {e}")
            sys.stdout.flush()
            fail_count += 1
            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                print(f"CẢNH BÁO: Người dùng {user_id_str} đã chặn bot hoặc bị vô hiệu hóa. Có thể xem xét xóa khỏi user_data.")
                sys.stdout.flush()
        except Exception as e:
            print(f"LỖI: Lỗi không xác định khi gửi thông báo cho user {user_id_str}: {e}")
            sys.stdout.flush()
            fail_count += 1
            
    bot.reply_to(message, f"Đã gửi thông báo đến {success_count} người dùng. Thất bại: {fail_count}.")
    save_user_data(user_data) 

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
    sys.stdout.flush()
    
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
    sys.stdout.flush()
    
@bot.message_handler(commands=['taocode'])
def generate_code_command(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if len(args) < 2 or len(args) > 3: 
        bot.reply_to(message, "Cú pháp sai. Ví dụ:\n"
                              "`/taocode <giá_trị> <ngày/giờ> <số_lượng>`\n"
                              "Ví dụ: `/taocode 1 ngày 5` (tạo 5 code 1 ngày)\n"
                              "Hoặc: `/taocode 24 giờ` (tạo 1 code 24 giờ)", parse_mode='Markdown')
        return
    
    try:
        value = int(args[0])
        unit = args[1].lower()
        quantity = int(args[2]) if len(args) == 3 else 1 
        
        if unit not in ['ngày', 'giờ']:
            bot.reply_to(message, "Đơn vị không hợp lệ. Chỉ chấp nhận `ngày` hoặc `giờ`.", parse_mode='Markdown')
            return
        if value <= 0 or quantity <= 0:
            bot.reply_to(message, "Giá trị hoặc số lượng phải lớn hơn 0.", parse_mode='Markdown')
            return

        generated_codes_list = []
        for _ in range(quantity):
            new_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8)) 
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
            print("LOG: Đang khởi tạo luồng bot và dự đoán...")
            sys.stdout.flush()
            # Load initial data
            load_user_data()
            load_cau_patterns()
            load_codes()

            # Start prediction loop in a separate thread
            prediction_thread = Thread(target=prediction_loop, args=(prediction_stop_event,))
            prediction_thread.daemon = True 
            prediction_thread.start()
            print("LOG: Luồng dự đoán đã khởi động.")
            sys.stdout.flush()

            # Start bot polling in a separate thread
            polling_thread = Thread(target=bot.infinity_polling, kwargs={'none_stop': True})
            polling_thread.daemon = True 
            polling_thread.start()
            print("LOG: Luồng Telegram bot polling đã khởi động.")
            sys.stdout.flush()
            
            bot_initialized = True

# --- Điểm khởi chạy chính cho Gunicorn/Render ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"LOG: Khởi động Flask app trên cổng {port}")
    sys.stdout.flush()
    app.run(host='0.0.0.0', port=port, debug=False)
