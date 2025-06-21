import telebot
import requests
import time
import json
import os
import random
import string
import sys # Import sys for stdout.flush for immediate log output
from datetime import datetime, timedelta
from threading import Thread, Event, Lock
from flask import Flask, request

# --- Cấu hình Bot (ĐẶT TRỰC TIẾP TẠI ĐÂY) ---
BOT_TOKEN = "7820739987:AAE_eU2JPZH7u6KnDRq31_l4tn64AD_8f6s" 
ADMIN_IDS = [6915752059] # Ví dụ: [6915752059, 123456789]

DATA_FILE = 'user_data.json'
CAU_PATTERNS_FILE = 'cau_patterns.json'
CODES_FILE = 'codes.json'
BOT_STATUS_FILE = 'bot_status.json' # File để lưu trạng thái bot

# --- Khởi tạo Flask App và Telegram Bot ---
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN)

# Global flags và objects
bot_enabled = True
bot_disable_reason = "Không có"
bot_disable_admin_id = None
prediction_stop_events = {} # Dictionary of Events for each game's prediction thread
bot_initialized = False # Cờ để đảm bảo bot chỉ được khởi tạo một lần
bot_init_lock = Lock() # Khóa để tránh race condition khi khởi tạo

# Global sets for patterns and codes
# Mẫu cầu sẽ được lưu theo từng game
CAU_DEP = {} # {'luckywin': set(), 'hitclub': set(), 'sunwin': set()}
CAU_XAU = {} # {'luckywin': set(), 'hitclub': set(), 'sunwin': set()}
GENERATED_CODES = {} # {code: {"value": 1, "type": "day", "used_by": null, "used_time": null}}

# Game specific configurations
GAME_CONFIGS = {
    'luckywin': {
        'api_url': "https://1.bot/GetNewLottery/LT_Taixiu",
        'prediction_enabled': True,
        'maintenance_mode': False,
        'maintenance_reason': "",
        'game_name_vi': "Luckywin",
        'prediction_stats': {'correct': 0, 'wrong': 0, 'total': 0},
        'users_receiving': set() # Users currently receiving predictions for this game
    },
    'hitclub': {
        'api_url': "https://apihitclub.up.railway.app/api/taixiu",
        'prediction_enabled': True,
        'maintenance_mode': False,
        'maintenance_reason': "",
        'game_name_vi': "Hit Club",
        'prediction_stats': {'correct': 0, 'wrong': 0, 'total': 0},
        'users_receiving': set()
    },
    'sunwin': {
        'api_url': "https://wanglinapiws.up.railway.app/api/taixiu",
        'prediction_enabled': True,
        'maintenance_mode': False,
        'maintenance_reason': "",
        'game_name_vi': "Sunwin",
        'prediction_stats': {'correct': 0, 'wrong': 0, 'total': 0},
        'users_receiving': set()
    }
}

# --- Quản lý dữ liệu người dùng, mẫu cầu và code ---
user_data = {} # {user_id: {username, expiry_date, is_ctv, banned, ban_reason, override_maintenance, receiving_games: {'luckywin': True, 'hitclub': True, 'sunwin': True}}}

def load_json_file(filepath, default_value):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                print(f"DEBUG: Tải {len(data)} bản ghi từ {filepath}")
                return data
            except json.JSONDecodeError:
                print(f"LỖI: Lỗi đọc {filepath}. Khởi tạo lại dữ liệu.")
                return default_value
            except Exception as e:
                print(f"LỖI: Lỗi không xác định khi tải {filepath}: {e}")
                return default_value
    else:
        print(f"DEBUG: File {filepath} không tồn tại. Khởi tạo dữ liệu rỗng.")
        return default_value

def save_json_file(filepath, data):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"LỖI: Không thể lưu dữ liệu vào {filepath}: {e}")
    sys.stdout.flush()

def load_user_data():
    global user_data
    user_data = load_json_file(DATA_FILE, {})
    # Đảm bảo các trường mới tồn tại trong user_data cũ
    for user_id_str, u_data in user_data.items():
        u_data.setdefault('banned', False)
        u_data.setdefault('ban_reason', None)
        u_data.setdefault('override_maintenance', False)
        # Khởi tạo receiving_games nếu chưa có
        u_data.setdefault('receiving_games', {game: True for game in GAME_CONFIGS.keys()})
        # Cập nhật users_receiving set trong GAME_CONFIGS
        for game_name, config in GAME_CONFIGS.items():
            if u_data['receiving_games'].get(game_name, False) and not u_data['banned']:
                config['users_receiving'].add(int(user_id_str))
    save_user_data(user_data) # Lưu lại để cập nhật schema

def save_user_data(data):
    save_json_file(DATA_FILE, data)

def load_cau_patterns():
    global CAU_DEP, CAU_XAU
    loaded_patterns = load_json_file(CAU_PATTERNS_FILE, {})
    for game_name in GAME_CONFIGS.keys():
        CAU_DEP[game_name] = set(loaded_patterns.get(game_name, {}).get('dep', []))
        CAU_XAU[game_name] = set(loaded_patterns.get(game_name, {}).get('xau', []))
        print(f"DEBUG: Tải {len(CAU_DEP[game_name])} mẫu cầu đẹp và {len(CAU_XAU[game_name])} mẫu cầu xấu cho {game_name}.")
    sys.stdout.flush()

def save_cau_patterns():
    patterns_to_save = {}
    for game_name in GAME_CONFIGS.keys():
        patterns_to_save[game_name] = {
            'dep': list(CAU_DEP.get(game_name, set())),
            'xau': list(CAU_XAU.get(game_name, set()))
        }
    save_json_file(CAU_PATTERNS_FILE, patterns_to_save)

def load_codes():
    global GENERATED_CODES
    GENERATED_CODES = load_json_file(CODES_FILE, {})

def save_codes():
    save_json_file(CODES_FILE, GENERATED_CODES)

def load_bot_status():
    global bot_enabled, bot_disable_reason, bot_disable_admin_id, GAME_CONFIGS
    status = load_json_file(BOT_STATUS_FILE, {})
    bot_enabled = status.get('bot_enabled', True)
    bot_disable_reason = status.get('bot_disable_reason', "Không có")
    bot_disable_admin_id = status.get('bot_disable_admin_id')

    for game_name, config in GAME_CONFIGS.items():
        game_status = status.get('game_configs', {}).get(game_name, {})
        config['prediction_enabled'] = game_status.get('prediction_enabled', True)
        config['maintenance_mode'] = game_status.get('maintenance_mode', False)
        config['maintenance_reason'] = game_status.get('maintenance_reason', "")
        config['prediction_stats'] = game_status.get('prediction_stats', {'correct': 0, 'wrong': 0, 'total': 0})
    print("DEBUG: Tải trạng thái bot và game.")
    sys.stdout.flush()

def save_bot_status():
    status = {
        'bot_enabled': bot_enabled,
        'bot_disable_reason': bot_disable_reason,
        'bot_disable_admin_id': bot_disable_admin_id,
        'game_configs': {}
    }
    for game_name, config in GAME_CONFIGS.items():
        status['game_configs'][game_name] = {
            'prediction_enabled': config['prediction_enabled'],
            'maintenance_mode': config['maintenance_mode'],
            'maintenance_reason': config['maintenance_reason'],
            'prediction_stats': config['prediction_stats']
        }
    save_json_file(BOT_STATUS_FILE, status)
    print("DEBUG: Lưu trạng thái bot và game.")
    sys.stdout.flush()

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_ctv(user_id):
    return is_admin(user_id) or (str(user_id) in user_data and user_data[str(user_id)].get('is_ctv'))

def is_banned(user_id):
    return str(user_id) in user_data and user_data[str(user_id)].get('banned', False)

def can_override_maintenance(user_id):
    return is_admin(user_id) or (str(user_id) in user_data and user_data[str(user_id)].get('override_maintenance', False))

def check_subscription(user_id):
    user_id_str = str(user_id)
    if is_banned(user_id):
        return False, f"🚫 Bạn đã bị cấm sử dụng bot. Lý do: `{user_data[user_id_str].get('ban_reason', 'Không rõ')}`"

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
def du_doan_theo_xi_ngau(dice_list):
    if not dice_list:
        return "Đợi thêm dữ liệu"
    d1, d2, d3 = dice_list[-1]
    total = d1 + d2 + d3

    # Logic dự đoán dựa trên tổng và từng con xúc xắc
    # Đây là một ví dụ, có thể phức tạp hơn
    if total <= 10: # Xỉu
        if sum([d for d in dice_list[-1] if d % 2 == 0]) >= sum([d for d in dice_list[-1] if d % 2 != 0]):
            return "Tài"
        else:
            return "Xỉu"
    else: # Tài
        if sum([d for d in dice_list[-1] if d % 2 != 0]) >= sum([d for d in dice_list[-1] if d % 2 == 0]):
            return "Xỉu"
        else:
            return "Tài"

def tinh_tai_xiu(dice):
    total = sum(dice)
    if total == 3 or total == 18:
        return "Bão", total # Bão 3 hoặc Bão 18
    return "Tài" if total >= 11 else "Xỉu", total

# --- Cập nhật mẫu cầu động ---
def update_cau_patterns(game_name, new_cau, prediction_correct):
    global CAU_DEP, CAU_XAU
    if prediction_correct:
        CAU_DEP.setdefault(game_name, set()).add(new_cau)
        if new_cau in CAU_XAU.setdefault(game_name, set()):
            CAU_XAU[game_name].remove(new_cau)
            print(f"DEBUG: Xóa mẫu cầu '{new_cau}' khỏi cầu xấu cho {game_name}.")
    else:
        CAU_XAU.setdefault(game_name, set()).add(new_cau)
        if new_cau in CAU_DEP.setdefault(game_name, set()):
            CAU_DEP[game_name].remove(new_cau)
            print(f"DEBUG: Xóa mẫu cầu '{new_cau}' khỏi cầu đẹp cho {game_name}.")
    save_cau_patterns()
    sys.stdout.flush()

def is_cau_xau(game_name, cau_str):
    return cau_str in CAU_XAU.get(game_name, set())

def is_cau_dep(game_name, cau_str):
    return cau_str in CAU_DEP.get(game_name, set()) and cau_str not in CAU_XAU.get(game_name, set()) # Đảm bảo không trùng cầu xấu

# --- Lấy dữ liệu từ API ---
def lay_du_lieu(game_name):
    config = GAME_CONFIGS[game_name]
    url = config['api_url']
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status() 
        data = response.json()
        
        # Normalize data structure for different APIs
        if game_name == 'luckywin':
            if data.get("state") != 1:
                print(f"DEBUG: API {game_name} trả về state không thành công: {data.get('state')}. Phản hồi đầy đủ: {data}")
                sys.stdout.flush()
                return None
            return data.get("data")
        elif game_name in ['hitclub', 'sunwin']:
            # Hit Club and Sunwin API directly return the result object
            if "Ket_qua" not in data or "Phien" not in data or "Tong" not in data:
                 print(f"DEBUG: API {game_name} không có đủ trường cần thiết. Phản hồi đầy đủ: {data}")
                 sys.stdout.flush()
                 return None
            
            # Map their keys to a common format
            return {
                "ID": str(data.get("Phien")),
                "Expect": str(data.get("Phien")),
                "OpenCode": f"{data.get('Xuc_xac_1')},{data.get('Xuc_xac_2')},{data.get('Xuc_xac_3')}",
                "Ket_qua_raw": data.get("Ket_qua") # Keep raw result for debugging if needed
            }
        else:
            print(f"LỖI: Game {game_name} không được cấu hình API.")
            return None

    except requests.exceptions.Timeout:
        print(f"LỖI: Hết thời gian chờ khi lấy dữ liệu từ API {game_name}: {url}")
        sys.stdout.flush()
        return None
    except requests.exceptions.ConnectionError as e:
        print(f"LỖI: Lỗi kết nối khi lấy dữ liệu từ API {game_name}: {url} - {e}")
        sys.stdout.flush()
        return None
    except requests.exceptions.RequestException as e:
        print(f"LỖI: Lỗi HTTP hoặc Request khác khi lấy dữ liệu từ API {game_name}: {url} - {e}")
        sys.stdout.flush()
        return None
    except json.JSONDecodeError:
        print(f"LỖI: Lỗi giải mã JSON từ API {game_name} ({url}). Phản hồi không phải JSON hợp lệ hoặc trống.")
        print(f"DEBUG: Phản hồi thô nhận được từ {game_name}: {response.text}")
        sys.stdout.flush()
        return None
    except Exception as e:
        print(f"LỖI: Lỗi không xác định khi lấy dữ liệu API {game_name} ({url}): {e}")
        sys.stdout.flush()
        return None

# --- Logic chính của Bot dự đoán (chạy trong luồng riêng) ---
def prediction_loop(game_name, stop_event: Event):
    last_id = None
    tx_history = [] # Lịch sử Tài/Xỉu/Bão của 5 phiên gần nhất
    
    print(f"LOG: Luồng dự đoán cho {game_name} đã khởi động.")
    sys.stdout.flush()

    while not stop_event.is_set():
        config = GAME_CONFIGS[game_name]

        if not bot_enabled:
            # print(f"LOG: Bot dự đoán toàn cục đang tạm dừng. Lý do: {bot_disable_reason}")
            time.sleep(10) 
            continue
        
        if not config['prediction_enabled']:
            # print(f"LOG: Bot dự đoán cho {game_name} đang tạm dừng.")
            time.sleep(10)
            continue

        if config['maintenance_mode']:
            # print(f"LOG: Game {game_name} đang bảo trì. Lý do: {config['maintenance_reason']}")
            time.sleep(10)
            continue

        data = lay_du_lieu(game_name)
        if not data:
            print(f"LOG: ❌ {game_name}: Không lấy được dữ liệu từ API hoặc dữ liệu không hợp lệ. Đang chờ phiên mới...")
            sys.stdout.flush()
            time.sleep(5)
            continue

        issue_id = data.get("ID")
        expect = data.get("Expect")
        open_code = data.get("OpenCode")

        if not all([issue_id, expect, open_code]):
            print(f"LOG: {game_name}: Dữ liệu API không đầy đủ (thiếu ID, Expect, hoặc OpenCode) cho phiên {expect}. Bỏ qua phiên này. Dữ liệu: {data}")
            sys.stdout.flush()
            time.sleep(5)
            continue

        if issue_id != last_id:
            try:
                dice = tuple(map(int, open_code.split(",")))
                if len(dice) != 3: 
                    raise ValueError("OpenCode không chứa 3 giá trị xúc xắc.")
            except ValueError as e:
                print(f"LỖI: {game_name}: Lỗi phân tích OpenCode: '{open_code}'. {e}. Bỏ qua phiên này.")
                sys.stdout.flush()
                last_id = issue_id 
                time.sleep(5)
                continue
            except Exception as e:
                print(f"LỖI: {game_name}: Lỗi không xác định khi xử lý OpenCode '{open_code}': {e}. Bỏ qua phiên này.")
                sys.stdout.flush()
                last_id = issue_id
                time.sleep(5)
                continue
            
            ket_qua_tx, tong = tinh_tai_xiu(dice)

            # Lưu lịch sử 5 phiên
            if len(tx_history) >= 5:
                tx_history.pop(0)
            tx_history.append("T" if ket_qua_tx == "Tài" else ("X" if ket_qua_tx == "Xỉu" else "B")) # Thêm 'B' cho Bão

            next_expect = str(int(expect) + 1).zfill(len(expect))
            du_doan = du_doan_theo_xi_ngau([dice])

            ly_do = ""
            current_cau = ""

            if len(tx_history) < 5:
                ly_do = "AI Dự đoán theo xí ngầu (chưa đủ mẫu cầu)"
            else:
                current_cau = ''.join(tx_history)
                if is_cau_dep(game_name, current_cau):
                    ly_do = f"AI Cầu đẹp ({current_cau}) → Giữ nguyên kết quả"
                elif is_cau_xau(game_name, current_cau):
                    du_doan = "Xỉu" if du_doan == "Tài" else "Tài" # Đảo chiều
                    ly_do = f"AI Cầu xấu ({current_cau}) → Đảo chiều kết quả"
                else:
                    ly_do = f"AI Không rõ mẫu cầu ({current_cau}) → Dự đoán theo xí ngầu"
            
            # Cập nhật mẫu cầu dựa trên kết quả thực tế
            if len(tx_history) >= 5:
                # Chỉ cập nhật mẫu cầu nếu không phải là Bão
                if ket_qua_tx != "Bão":
                    prediction_correct = (du_doan == "Tài" and ket_qua_tx == "Tài") or \
                                         (du_doan == "Xỉu" and ket_qua_tx == "Xỉu")
                    update_cau_patterns(game_name, current_cau, prediction_correct)
                    print(f"DEBUG: {game_name}: Cập nhật mẫu cầu: '{current_cau}' - Chính xác: {prediction_correct}")
                else:
                    print(f"DEBUG: {game_name}: Không cập nhật mẫu cầu do là kết quả Bão.")
                sys.stdout.flush()

            # Cập nhật thống kê
            config['prediction_stats']['total'] += 1
            if du_doan == ket_qua_tx:
                config['prediction_stats']['correct'] += 1
            else:
                config['prediction_stats']['wrong'] += 1
            save_bot_status() # Save stats

            prediction_message = (
                f"🎲 **[{config['game_name_vi'].upper()}] KẾT QUẢ PHIÊN HIỆN TẠI** 🎲\n"
                f"Phiên: `{expect}` | Kết quả: **{ket_qua_tx}** (Tổng: **{tong}**)\n\n"
                f"**Dự đoán cho phiên tiếp theo:**\n"
                f"🔢 Phiên: `{next_expect}`\n"
                f"🤖 Dự đoán: **{du_doan}**\n"
                f"📌 Lý do: _{ly_do}_\n"
                f"⚠️ **Hãy đặt cược sớm trước khi phiên kết thúc!**"
            )

            # Gửi tin nhắn dự đoán tới tất cả người dùng có quyền truy cập và đang nhận dự đoán cho game này
            for user_id_int in list(config['users_receiving']): 
                user_id_str = str(user_id_int)
                is_sub, sub_message = check_subscription(user_id_int)
                
                # Check if user opted to receive predictions for this specific game
                if user_id_str in user_data and user_data[user_id_str]['receiving_games'].get(game_name, False):
                    if is_sub:
                        # Allow Admin/Override users to receive even during maintenance
                        if config['maintenance_mode'] and not can_override_maintenance(user_id_int):
                            # print(f"DEBUG: Không gửi dự đoán cho user {user_id_str} vì {game_name} đang bảo trì và không có quyền override.")
                            continue

                        try:
                            bot.send_message(user_id_int, prediction_message, parse_mode='Markdown')
                            # print(f"DEBUG: Đã gửi dự đoán {game_name} cho user {user_id_str}")
                            sys.stdout.flush()
                        except telebot.apihelper.ApiTelegramException as e:
                            print(f"LỖI: Lỗi Telegram API khi gửi tin nhắn cho user {user_id_int} ({game_name}): {e}")
                            sys.stdout.flush()
                            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                                print(f"CẢNH BÁO: Người dùng {user_id_int} đã chặn bot hoặc bị vô hiệu hóa. Xóa khỏi danh sách nhận.")
                                config['users_receiving'].discard(user_id_int)
                                user_data[user_id_str]['receiving_games'][game_name] = False # Mark as not receiving
                                save_user_data(user_data)
                        except Exception as e:
                            print(f"LỖI: Lỗi không xác định khi gửi tin nhắn cho user {user_id_int} ({game_name}): {e}")
                            sys.stdout.flush()
                    # else:
                        # print(f"DEBUG: Không gửi dự đoán {game_name} cho user {user_id_str} vì hết hạn/bị cấm.")

            print("-" * 50)
            print("LOG: {}: Phiên {} -> {}. Kết quả: {} ({}). Dự đoán: {}. Lý do: {}".format(config['game_name_vi'], expect, next_expect, ket_qua_tx, tong, du_doan, ly_do))
            print("-" * 50)
            sys.stdout.flush()

            last_id = issue_id

        time.sleep(5) # Đợi 5 giây trước khi kiểm tra phiên mới
    print(f"LOG: Luồng dự đoán cho {game_name} đã dừng.")
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
            'is_ctv': False,
            'banned': False,
            'ban_reason': None,
            'override_maintenance': False,
            'receiving_games': {game: True for game in GAME_CONFIGS.keys()} # Mặc định nhận tất cả
        }
        save_user_data(user_data)
        # Add user to all active game receiving lists
        for game_name, config in GAME_CONFIGS.items():
            config['users_receiving'].add(int(user_id))

        bot.reply_to(message, 
                     "Chào mừng bạn đến với **BOT DỰ ĐOÁN TÀI XỈU SUNWIN**!\n"
                     "Hãy dùng lệnh /help để xem danh sách các lệnh hỗ trợ.", 
                     parse_mode='Markdown')
    else:
        user_data[user_id]['username'] = username # Cập nhật username nếu có thay đổi
        # Ensure 'receiving_games' is initialized for existing users
        user_data[user_id].setdefault('receiving_games', {game: True for game in GAME_CONFIGS.keys()})
        # Add existing user to active game receiving lists if they are set to receive
        for game_name, config in GAME_CONFIGS.items():
            if user_data[user_id]['receiving_games'].get(game_name, False) and not user_data[user_id]['banned']:
                config['users_receiving'].add(int(user_id))

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
        "🔸 `/dudoan`: Bắt đầu nhận dự đoán cho Luckywin.\n"
        "🔸 `/dudoan_hitclub`: Bắt đầu nhận dự đoán cho Hit Club.\n"
        "🔸 `/dudoan_sunwin`: Bắt đầu nhận dự đoán cho Sunwin.\n"
        "🔸 `/code <mã_code>`: Nhập mã code để gia hạn tài khoản.\n"
        "🔸 `/stop [tên game]`: Tạm ngừng nhận dự đoán (để trống để tạm ngừng tất cả, hoặc chỉ định game).\n"
        "🔸 `/continue [tên game]`: Tiếp tục nhận dự đoán (để trống để tiếp tục tất cả, hoặc chỉ định game).\n\n"
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
            "👑 `/taocode <giá trị> <ngày/giờ> <số lượng>`: Tạo mã code gia hạn. Ví dụ: `/taocode 1 ngày 5`.\n"
            "👑 `/maucau <tên game>`: Hiển thị các mẫu cầu đã thu thập cho game.\n"
            "👑 `/kiemtra`: Kiểm tra thông tin tất cả người dùng bot.\n"
            "👑 `/xoahan <id>`: Xóa số ngày còn lại của người dùng.\n"
            "👑 `/ban <id> [lý do]`: Cấm người dùng sử dụng bot.\n"
            "👑 `/unban <id>`: Bỏ cấm người dùng.\n"
            "👑 `/baotri <tên game> [lý do]`: Đặt game vào trạng thái bảo trì.\n"
            "👑 `/mobaochi <tên game>`: Bỏ trạng thái bảo trì cho game.\n"
            "👑 `/override <id>`: Cấp quyền Admin/CTV vẫn nhận dự đoán khi game bảo trì.\n"
            "👑 `/unoverride <id>`: Xóa quyền Admin/CTV override bảo trì.\n"
            "👑 `/stats [tên game]`: Xem thống kê dự đoán của bot.\n"
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

# Hàm chung để xử lý lệnh /dudoan_<game>
def handle_dudoan_command(message, game_name):
    user_id = message.chat.id
    user_id_str = str(user_id)
    
    is_sub, sub_message = check_subscription(user_id)
    
    if not is_sub:
        bot.reply_to(message, sub_message + "\nVui lòng liên hệ Admin @heheviptool hoặc @Besttaixiu999 để được hỗ trợ.", parse_mode='Markdown')
        return
    
    if not bot_enabled:
        bot.reply_to(message, f"❌ Bot dự đoán toàn cục hiện đang tạm dừng bởi Admin. Lý do: `{bot_disable_reason}`", parse_mode='Markdown')
        return

    game_config = GAME_CONFIGS.get(game_name)
    if not game_config:
        bot.reply_to(message, "Game này không được hỗ trợ.", parse_mode='Markdown')
        return

    if game_config['maintenance_mode'] and not can_override_maintenance(user_id):
        bot.reply_to(message, f"❌ Game **{game_config['game_name_vi']}** hiện đang bảo trì. Lý do: `{game_config['maintenance_reason']}`", parse_mode='Markdown')
        return
    
    if not game_config['prediction_enabled']:
        bot.reply_to(message, f"❌ Dự đoán cho game **{game_config['game_name_vi']}** hiện đang tạm dừng. Vui lòng thử lại sau.", parse_mode='Markdown')
        return

    # Update user's preference to receive this game's predictions
    if user_id_str not in user_data:
        # This case should ideally not happen if /start is used first
        user_data[user_id_str] = {'username': message.from_user.username or message.from_user.first_name, 'expiry_date': None, 'is_ctv': False, 'banned': False, 'ban_reason': None, 'override_maintenance': False, 'receiving_games': {game: False for game in GAME_CONFIGS.keys()}}
    
    user_data[user_id_str]['receiving_games'][game_name] = True
    GAME_CONFIGS[game_name]['users_receiving'].add(user_id)
    save_user_data(user_data)

    bot.reply_to(message, f"✅ Bạn đang có quyền truy cập và sẽ nhận dự đoán cho game **{game_config['game_name_vi']}**.")

@bot.message_handler(commands=['dudoan'])
def start_prediction_luckywin(message):
    handle_dudoan_command(message, 'luckywin')

@bot.message_handler(commands=['dudoan_hitclub'])
def start_prediction_hitclub(message):
    handle_dudoan_command(message, 'hitclub')

@bot.message_handler(commands=['dudoan_sunwin'])
def start_prediction_sunwin(message):
    handle_dudoan_command(message, 'sunwin')

@bot.message_handler(commands=['stop'])
def stop_receiving_predictions(message):
    user_id = message.chat.id
    user_id_str = str(user_id)
    args = telebot.util.extract_arguments(message.text).lower().split()
    
    if user_id_str not in user_data:
        bot.reply_to(message, "Bạn chưa khởi động bot. Vui lòng dùng /start trước.")
        return

    if not args: # Stop all games
        for game_name, config in GAME_CONFIGS.items():
            user_data[user_id_str]['receiving_games'][game_name] = False
            config['users_receiving'].discard(user_id)
        save_user_data(user_data)
        bot.reply_to(message, "Đã tạm ngừng nhận dự đoán cho **tất cả các game**.", parse_mode='Markdown')
    else: # Stop specific game
        game_arg = args[0]
        matched_game = None
        for game_key, config in GAME_CONFIGS.items():
            if game_key == game_arg or config['game_name_vi'].lower().replace(" ", "") == game_arg.replace(" ", ""):
                matched_game = game_key
                break
        
        if matched_game:
            user_data[user_id_str]['receiving_games'][matched_game] = False
            GAME_CONFIGS[matched_game]['users_receiving'].discard(user_id)
            save_user_data(user_data)
            bot.reply_to(message, f"Đã tạm ngừng nhận dự đoán cho game **{GAME_CONFIGS[matched_game]['game_name_vi']}**.", parse_mode='Markdown')
        else:
            bot.reply_to(message, "Tên game không hợp lệ. Các game được hỗ trợ: Luckywin, Hit Club, Sunwin.", parse_mode='Markdown')

@bot.message_handler(commands=['continue'])
def continue_receiving_predictions(message):
    user_id = message.chat.id
    user_id_str = str(user_id)
    args = telebot.util.extract_arguments(message.text).lower().split()

    is_sub, sub_message = check_subscription(user_id)
    if not is_sub:
        bot.reply_to(message, sub_message + "\nVui lòng liên hệ Admin để được hỗ trợ.", parse_mode='Markdown')
        return

    if user_id_str not in user_data:
        bot.reply_to(message, "Bạn chưa khởi động bot. Vui lòng dùng /start trước.")
        return

    if not args: # Continue all games
        for game_name, config in GAME_CONFIGS.items():
            # Check for global bot enable and game maintenance
            if not bot_enabled:
                bot.reply_to(message, f"❌ Bot dự đoán toàn cục hiện đang tạm dừng bởi Admin. Lý do: `{bot_disable_reason}`", parse_mode='Markdown')
                continue
            if config['maintenance_mode'] and not can_override_maintenance(user_id):
                bot.reply_to(message, f"❌ Game **{config['game_name_vi']}** hiện đang bảo trì. Lý do: `{config['maintenance_reason']}`", parse_mode='Markdown')
                continue
            if not config['prediction_enabled']:
                bot.reply_to(message, f"❌ Dự đoán cho game **{config['game_name_vi']}** hiện đang tạm dừng. Vui lòng thử lại sau.", parse_mode='Markdown')
                continue

            user_data[user_id_str]['receiving_games'][game_name] = True
            config['users_receiving'].add(user_id)
        save_user_data(user_data)
        bot.reply_to(message, "Đã tiếp tục nhận dự đoán cho **tất cả các game** (nếu game không bảo trì và bot hoạt động).", parse_mode='Markdown')
    else: # Continue specific game
        game_arg = args[0]
        matched_game = None
        for game_key, config in GAME_CONFIGS.items():
            if game_key == game_arg or config['game_name_vi'].lower().replace(" ", "") == game_arg.replace(" ", ""):
                matched_game = game_key
                break
        
        if matched_game:
            game_config = GAME_CONFIGS[matched_game]
            if not bot_enabled:
                bot.reply_to(message, f"❌ Bot dự đoán toàn cục hiện đang tạm dừng bởi Admin. Lý do: `{bot_disable_reason}`", parse_mode='Markdown')
                return
            if game_config['maintenance_mode'] and not can_override_maintenance(user_id):
                bot.reply_to(message, f"❌ Game **{game_config['game_name_vi']}** hiện đang bảo trì. Lý do: `{game_config['maintenance_reason']}`", parse_mode='Markdown')
                return
            if not game_config['prediction_enabled']:
                bot.reply_to(message, f"❌ Dự đoán cho game **{game_config['game_name_vi']}** hiện đang tạm dừng. Vui lòng thử lại sau.", parse_mode='Markdown')
                return

            user_data[user_id_str]['receiving_games'][matched_game] = True
            game_config['users_receiving'].add(user_id)
            save_user_data(user_data)
            bot.reply_to(message, f"Đã tiếp tục nhận dự đoán cho game **{game_config['game_name_vi']}**.", parse_mode='Markdown')
        else:
            bot.reply_to(message, "Tên game không hợp lệ. Các game được hỗ trợ: Luckywin, Hit Club, Sunwin.", parse_mode='Markdown')

@bot.message_handler(commands=['maucau'])
def show_cau_patterns_command(message):
    if not is_admin(message.chat.id): # Chỉ Admin mới được xem mẫu cầu chi tiết
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    args = telebot.util.extract_arguments(message.text).lower().split()
    if not args:
        bot.reply_to(message, "Vui lòng chỉ định tên game để xem mẫu cầu. Ví dụ: `/maucau luckywin`", parse_mode='Markdown')
        return
    
    game_arg = args[0]
    matched_game = None
    for game_key, config in GAME_CONFIGS.items():
        if game_key == game_arg or config['game_name_vi'].lower().replace(" ", "") == game_arg.replace(" ", ""):
            matched_game = game_key
            break
    
    if not matched_game:
        bot.reply_to(message, "Tên game không hợp lệ. Các game được hỗ trợ: Luckywin, Hit Club, Sunwin.", parse_mode='Markdown')
        return

    dep_patterns = "\n".join(sorted(list(CAU_DEP.get(matched_game, set())))) if CAU_DEP.get(matched_game) else "Không có"
    xau_patterns = "\n".join(sorted(list(CAU_XAU.get(matched_game, set())))) if CAU_XAU.get(matched_game) else "Không có"

    pattern_text = (
        f"📚 **CÁC MẪU CẦU ĐÃ THU THẬP CHO {GAME_CONFIGS[matched_game]['game_name_vi'].upper()}** 📚\n\n"
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
    # Ensure 'receiving_games' is initialized for this user
    user_data[user_id].setdefault('receiving_games', {game: True for game in GAME_CONFIGS.keys()})
    
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
    banned_status = "Có" if user_info.get('banned') else "Không"
    ban_reason = f"Lý do: `{user_info.get('ban_reason')}`" if user_info.get('banned') and user_info.get('ban_reason') else ""
    override_status = "Có" if user_info.get('override_maintenance') else "Không"

    receiving_games_status = []
    for game_name, is_receiving in user_info.get('receiving_games', {}).items():
        receiving_games_status.append(f"{GAME_CONFIGS[game_name]['game_name_vi']}: {'✅' if is_receiving else '❌'}")
    receiving_games_text = "\n".join(receiving_games_status) if receiving_games_status else "Không cài đặt"


    info_text = (
        f"**THÔNG TIN NGƯỜI DÙNG**\n"
        f"**ID:** `{target_user_id_str}`\n"
        f"**Tên:** @{username}\n"
        f"**Ngày hết hạn:** `{expiry_date_str}`\n"
        f"**Là CTV/Admin:** {is_ctv_status}\n"
        f"**Bị cấm:** {banned_status} {ban_reason}\n"
        f"**Override Bảo trì:** {override_status}\n"
        f"**Nhận dự đoán cho:**\n{receiving_games_text}"
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
            'is_ctv': False,
            'banned': False,
            'ban_reason': None,
            'override_maintenance': False,
            'receiving_games': {game: True for game in GAME_CONFIGS.keys()}
        }
        bot.send_message(message.chat.id, f"Đã tạo tài khoản mới cho user ID `{target_user_id_str}`.")
        # Add to all game receiving lists by default
        for game_name, config in GAME_CONFIGS.items():
            config['users_receiving'].add(int(target_user_id_str))

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
            'is_ctv': True,
            'banned': False,
            'ban_reason': None,
            'override_maintenance': False,
            'receiving_games': {game: True for game in GAME_CONFIGS.keys()}
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
            print(f"LỖI: Không thể gửi thông báo cho user {user_id_str}: {e}")
            sys.stdout.flush()
            fail_count += 1
            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                print(f"CẢNH BÁO: Người dùng {user_id_str} đã chặn bot hoặc bị vô hiệu hóa. Đang xóa người dùng khỏi danh sách nhận.")
                # Remove user from all receiving lists
                for game_name, config in GAME_CONFIGS.items():
                    config['users_receiving'].discard(int(user_id_str))
                if user_id_str in user_data:
                    del user_data[user_id_str] # Remove completely
                    save_user_data(user_data) # Save immediately
        except Exception as e:
            print(f"LỖI: Lỗi không xác định khi gửi thông báo cho user {user_id_str}: {e}")
            sys.stdout.flush()
            fail_count += 1
            
    bot.reply_to(message, f"Đã gửi thông báo đến {success_count} người dùng. Thất bại: {fail_count}.")

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
    save_bot_status() # Save bot status
    bot.reply_to(message, f"✅ Bot dự đoán đã được tắt bởi Admin `{message.from_user.username or message.from_user.first_name}`.\nLý do: `{reason}`", parse_mode='Markdown')
    sys.stdout.flush()
    
    # Optionally notify all users
    for user_id_str in list(user_data.keys()):
        try:
            bot.send_message(int(user_id_str), f"📢 **THÔNG BÁO QUAN TRỌNG:** Bot dự đoán toàn bộ tạm thời dừng hoạt động.\nLý do: {reason}\nVui lòng chờ thông báo mở lại.", parse_mode='Markdown')
        except Exception:
            pass

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
    save_bot_status() # Save bot status
    bot.reply_to(message, "✅ Bot dự đoán đã được mở lại bởi Admin.")
    sys.stdout.flush()
    
    # Optionally notify all users
    for user_id_str in list(user_data.keys()):
        try:
            bot.send_message(int(user_id_str), "🎉 **THÔNG BÁO:** Bot dự đoán toàn bộ đã hoạt động trở lại!.", parse_mode='Markdown')
        except Exception:
            pass

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

@bot.message_handler(commands=['kiemtra'])
def check_all_users(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    if not user_data:
        bot.reply_to(message, "Chưa có người dùng nào trong hệ thống.")
        return

    response_parts = ["📋 **THÔNG TIN TẤT CẢ NGƯỜI DÙNG** 📋\n"]
    for user_id_str, user_info in user_data.items():
        expiry_date_str = user_info.get('expiry_date', 'Không có')
        username = user_info.get('username', 'Không rõ')
        is_ctv_status = "CTV" if user_info.get('is_ctv') else "User"
        banned_status = "🚫 Banned" if user_info.get('banned') else ""
        
        response_parts.append(f"• ID: `{user_id_str}` | Tên: @{username} | Hạn: `{expiry_date_str}` | Quyền: {is_ctv_status} {banned_status}")
        
        # Telegram message limit is 4096 characters, split if too long
        if len("\n".join(response_parts)) > 3500: # Leave some buffer
            bot.send_message(message.chat.id, "\n".join(response_parts), parse_mode='Markdown')
            response_parts = [] # Reset for next part
    
    if response_parts: # Send any remaining parts
        bot.send_message(message.chat.id, "\n".join(response_parts), parse_mode='Markdown')

@bot.message_handler(commands=['xoahan'])
def clear_expiry(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/xoahan <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    if target_user_id_str in user_data:
        user_data[target_user_id_str]['expiry_date'] = None
        save_user_data(user_data)
        bot.reply_to(message, f"Đã xóa hạn sử dụng của user ID `{target_user_id_str}`.")
        try:
            bot.send_message(int(target_user_id_str), "❌ Hạn sử dụng tài khoản của bạn đã bị xóa bởi Admin.")
        except Exception:
            pass
    else:
        bot.reply_to(message, f"Không tìm thấy người dùng có ID `{target_user_id_str}`.")

@bot.message_handler(commands=['ban'])
def ban_user(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/ban <id_nguoi_dung> [lý do]`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    reason = " ".join(args[1:]) if len(args) > 1 else "Không có lý do cụ thể."

    if target_user_id_str not in user_data:
        user_data[target_user_id_str] = {
            'username': "UnknownUser",
            'expiry_date': None,
            'is_ctv': False,
            'banned': True,
            'ban_reason': reason,
            'override_maintenance': False,
            'receiving_games': {game: False for game in GAME_CONFIGS.keys()} # Bị ban thì không nhận dự đoán
        }
    else:
        user_data[target_user_id_str]['banned'] = True
        user_data[target_user_id_str]['ban_reason'] = reason
        # Also stop them from receiving any predictions
        for game_name, config in GAME_CONFIGS.items():
            user_data[target_user_id_str]['receiving_games'][game_name] = False
            config['users_receiving'].discard(int(target_user_id_str))
    
    save_user_data(user_data)
    bot.reply_to(message, f"Đã cấm user ID `{target_user_id_str}`. Lý do: `{reason}`", parse_mode='Markdown')
    try:
        bot.send_message(int(target_user_id_str), f"🚫 Tài khoản của bạn đã bị cấm sử dụng bot bởi Admin. Lý do: `{reason}`")
    except Exception:
        pass

@bot.message_handler(commands=['unban'])
def unban_user(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/unban <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    if target_user_id_str in user_data:
        user_data[target_user_id_str]['banned'] = False
        user_data[target_user_id_str]['ban_reason'] = None
        # User is unbanned, let them receive predictions for games they previously opted for
        for game_name, config in GAME_CONFIGS.items():
             if user_data[target_user_id_str]['receiving_games'].get(game_name, False): # Only re-add if they were set to receive before
                config['users_receiving'].add(int(target_user_id_str))

        save_user_data(user_data)
        bot.reply_to(message, f"Đã bỏ cấm user ID `{target_user_id_str}`.")
        try:
            bot.send_message(int(target_user_id_str), "✅ Tài khoản của bạn đã được bỏ cấm.")
        except Exception:
            pass
    else:
        bot.reply_to(message, f"Không tìm thấy người dùng có ID `{target_user_id_str}`.")

@bot.message_handler(commands=['baotri'])
def set_game_maintenance(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split(maxsplit=1) # Split only once to get reason
    if len(args) < 1:
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/baotri <tên_game> [lý do]`", parse_mode='Markdown')
        return
    
    game_arg = args[0].lower()
    reason = args[1] if len(args) > 1 else "Bot đang bảo trì để nâng cấp."

    matched_game = None
    for game_key, config in GAME_CONFIGS.items():
        if game_key == game_arg or config['game_name_vi'].lower().replace(" ", "") == game_arg.replace(" ", ""):
            matched_game = game_key
            break
    
    if not matched_game:
        bot.reply_to(message, "Tên game không hợp lệ. Các game được hỗ trợ: Luckywin, Hit Club, Sunwin.", parse_mode='Markdown')
        return

    GAME_CONFIGS[matched_game]['maintenance_mode'] = True
    GAME_CONFIGS[matched_game]['maintenance_reason'] = reason
    save_bot_status() # Save game status

    bot.reply_to(message, f"✅ Đã đặt game **{GAME_CONFIGS[matched_game]['game_name_vi']}** vào trạng thái bảo trì.\nLý do: `{reason}`", parse_mode='Markdown')
    
    # Notify all users receiving predictions for this game (who don't have override)
    for user_id_int in list(GAME_CONFIGS[matched_game]['users_receiving']):
        if not can_override_maintenance(user_id_int):
            try:
                bot.send_message(user_id_int, 
                                 f"📢 **THÔNG BÁO BẢO TRÌ:** Game **{GAME_CONFIGS[matched_game]['game_name_vi']}** tạm thời dừng dự đoán.\nLý do: `{reason}`\nVui lòng chờ thông báo mở lại.", 
                                 parse_mode='Markdown')
            except Exception:
                pass

@bot.message_handler(commands=['mobaochi'])
def unset_game_maintenance(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if len(args) < 1:
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/mobaochi <tên_game>`", parse_mode='Markdown')
        return
    
    game_arg = args[0].lower()
    matched_game = None
    for game_key, config in GAME_CONFIGS.items():
        if game_key == game_arg or config['game_name_vi'].lower().replace(" ", "") == game_arg.replace(" ", ""):
            matched_game = game_key
            break
    
    if not matched_game:
        bot.reply_to(message, "Tên game không hợp lệ. Các game được hỗ trợ: Luckywin, Hit Club, Sunwin.", parse_mode='Markdown')
        return

    if not GAME_CONFIGS[matched_game]['maintenance_mode']:
        bot.reply_to(message, f"Game **{GAME_CONFIGS[matched_game]['game_name_vi']}** không ở trạng thái bảo trì.", parse_mode='Markdown')
        return

    GAME_CONFIGS[matched_game]['maintenance_mode'] = False
    GAME_CONFIGS[matched_game]['maintenance_reason'] = ""
    save_bot_status() # Save game status

    bot.reply_to(message, f"✅ Đã bỏ trạng thái bảo trì cho game **{GAME_CONFIGS[matched_game]['game_name_vi']}**.", parse_mode='Markdown')

    # Notify all users previously receiving predictions for this game
    for user_id_int in list(GAME_CONFIGS[matched_game]['users_receiving']):
        try:
            bot.send_message(user_id_int, 
                             f"🎉 **THÔNG BÁO:** Game **{GAME_CONFIGS[matched_game]['game_name_vi']}** đã hoạt động trở lại! Bạn có thể tiếp tục nhận dự đoán.", 
                             parse_mode='Markdown')
        except Exception:
            pass

@bot.message_handler(commands=['override'])
def add_override_permission(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/override <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    if target_user_id_str not in user_data:
        user_data[target_user_id_str] = {
            'username': "UnknownUser",
            'expiry_date': None,
            'is_ctv': False,
            'banned': False,
            'ban_reason': None,
            'override_maintenance': True, # Grant permission
            'receiving_games': {game: True for game in GAME_CONFIGS.keys()}
        }
    else:
        user_data[target_user_id_str]['override_maintenance'] = True
    
    save_user_data(user_data)
    bot.reply_to(message, f"Đã cấp quyền override bảo trì cho user ID `{target_user_id_str}`.")
    try:
        bot.send_message(int(target_user_id_str), "🎉 Bạn đã được cấp quyền bỏ qua trạng thái bảo trì của game!")
    except Exception:
        pass

@bot.message_handler(commands=['unoverride'])
def remove_override_permission(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/unoverride <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    if target_user_id_str in user_data:
        user_data[target_user_id_str]['override_maintenance'] = False
        save_user_data(user_data)
        bot.reply_to(message, f"Đã xóa quyền override bảo trì của user ID `{target_user_id_str}`.")
        try:
            bot.send_message(int(target_user_id_str), "❌ Quyền bỏ qua trạng thái bảo trì của bạn đã bị gỡ bỏ.")
        except Exception:
            pass
    else:
        bot.reply_to(message, f"Không tìm thấy người dùng có ID `{target_user_id_str}`.")

@bot.message_handler(commands=['stats'])
def show_prediction_stats(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).lower().split()
    
    stats_message = "📊 **THỐNG KÊ DỰ ĐOÁN CỦA BOT** 📊\n\n"
    
    if not args: # Show all games stats
        for game_key, config in GAME_CONFIGS.items():
            stats = config['prediction_stats']
            total = stats['total']
            correct = stats['correct']
            wrong = stats['wrong']
            accuracy = (correct / total * 100) if total > 0 else 0
            
            stats_message += (
                f"**{config['game_name_vi']}**:\n"
                f"  Tổng phiên dự đoán: `{total}`\n"
                f"  Dự đoán đúng: `{correct}`\n"
                f"  Dự đoán sai: `{wrong}`\n"
                f"  Tỷ lệ chính xác: `{accuracy:.2f}%`\n\n"
            )
    else: # Show specific game stats
        game_arg = args[0]
        matched_game = None
        for game_key, config in GAME_CONFIGS.items():
            if game_key == game_arg or config['game_name_vi'].lower().replace(" ", "") == game_arg.replace(" ", ""):
                matched_game = game_key
                break
        
        if not matched_game:
            bot.reply_to(message, "Tên game không hợp lệ. Các game được hỗ trợ: Luckywin, Hit Club, Sunwin.", parse_mode='Markdown')
            return
        
        config = GAME_CONFIGS[matched_game]
        stats = config['prediction_stats']
        total = stats['total']
        correct = stats['correct']
        wrong = stats['wrong']
        accuracy = (correct / total * 100) if total > 0 else 0
        
        stats_message += (
            f"**{config['game_name_vi']}**:\n"
            f"  Tổng phiên dự đoán: `{total}`\n"
            f"  Dự đoán đúng: `{correct}`\n"
            f"  Dự đoán sai: `{wrong}`\n"
            f"  Tỷ lệ chính xác: `{accuracy:.2f}%`\n\n"
        )

    bot.reply_to(message, stats_message, parse_mode='Markdown')

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
            load_bot_status() # Load bot status and game configs

            # Start prediction loop for each game in a separate thread
            for game_name in GAME_CONFIGS.keys():
                prediction_stop_events[game_name] = Event()
                prediction_thread = Thread(target=prediction_loop, args=(game_name, prediction_stop_events[game_name],))
                prediction_thread.daemon = True 
                prediction_thread.start()
                print(f"LOG: Luồng dự đoán cho {game_name} đã khởi động.")
                sys.stdout.flush()

            # Start bot polling in a separate thread
            polling_thread = Thread(target=bot.infinity_polling, kwargs={'none_stop': True})
            polling_thread.daemon = True 
            polling_thread.start()
            print("LOG: Luồng Telegram bot polling đã khởi động.")
            sys.stdout.flush()
            
            bot_initialized = True
            print("LOG: Bot đã được khởi tạo hoàn tất.")

# --- Điểm khởi chạy chính cho Gunicorn/Render ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"LOG: Khởi động Flask app trên cổng {port}")
    sys.stdout.flush()
    app.run(host='0.0.0.0', port=port, debug=False)
