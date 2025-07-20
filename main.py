import os
import json
import time
import math
import random
import threading
import logging
from collections import defaultdict, deque
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

# --- Basic Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Helper Functions ---
def _get_history_strings(history_list):
    """Hàm trợ giúp để lấy danh sách chuỗi 'Tài'/'Xỉu' từ danh sách dict."""
    return [item['ket_qua'] for item in history_list]

def _get_history_with_scores(history_list):
    """Hàm trợ giúp để lấy danh sách dict bao gồm 'ket_qua', 'phien', và 'totalScore'."""
    # Giả định totalScore có thể được tính toán hoặc lấy từ dữ liệu nếu có
    # Trong trường hợp API mới không cung cấp totalScore, chúng ta sẽ cần điều chỉnh
    # hoặc tính toán nó dựa trên Dice nếu cần. Hiện tại, mock nó là 0.
    return [{'result': item['ket_qua'], 'session': item['phien'], 'totalScore': sum(item.get('Dice', [0,0,0]))} for item in history_list]


# 1. Định nghĩa các Patterns (không thay đổi)
def define_patterns():
    """
    Định nghĩa một bộ sưu tập lớn các patterns từ đơn giản đến siêu phức tạp.
    Mỗi pattern là một hàm lambda nhận lịch sử (dạng chuỗi) và trả về True nếu khớp.
    """
    patterns = {
        # --- Cầu Bệt (Streaks) ---
        "Bệt": lambda h: len(h) >= 3 and h[-1] == h[-2] == h[-3],
        "Bệt siêu dài": lambda h: len(h) >= 5 and all(x == h[-1] for x in h[-5:]),
        "Bệt gãy nhẹ": lambda h: len(h) >= 4 and h[-1] != h[-2] and h[-2] == h[-3] == h[-4],
        "Bệt gãy sâu": lambda h: len(h) >= 5 and h[-1] != h[-2] and all(x == h[-2] for x in h[-5:-1]),
        "Bệt xen kẽ ngắn": lambda h: len(h) >= 4 and h[-4:-2] == [h[-4]]*2 and h[-2:] == [h[-2]]*2 and h[-4] != h[-2],
        "Bệt ngược": lambda h: len(h) >= 4 and h[-1] == h[-2] and h[-3] == h[-4] and h[-1] != h[-3],
        "Xỉu kép": lambda h: len(h) >= 2 and h[-1] == 'Xỉu' and h[-2] == 'Xỉu',
        "Tài kép": lambda h: len(h) >= 2 and h[-1] == 'Tài' and h[-2] == 'Tài',
        "Ngẫu nhiên bệt": lambda h: len(h) > 8 and 0.4 < (h[-8:].count('Tài') / 8) < 0.6 and h[-1] == h[-2],

        # --- Cầu Đảo (Alternating) ---
        "Đảo 1-1": lambda h: len(h) >= 4 and h[-1] != h[-2] and h[-2] != h[-3] and h[-3] != h[-4],
        "Xen kẽ dài": lambda h: len(h) >= 5 and all(h[i] != h[i+1] for i in range(-5, -1)),
        "Xen kẽ": lambda h: len(h) >= 3 and h[-1] != h[-2] and h[-2] != h[-3],
        "Xỉu lắc": lambda h: len(h) >= 4 and h[-4:] == ['Xỉu', 'Tài', 'Xỉu', 'Tài'],
        "Tài lắc": lambda h: len(h) >= 4 and h[-4:] == ['Tài', 'Xỉu', 'Tài', 'Xỉu'],
        
        # --- Cầu theo nhịp (Rhythmic) ---
        "Kép 2-2": lambda h: len(h) >= 4 and h[-4:] == [h[-4], h[-4], h[-2], h[-2]] and h[-4] != h[-2],
        "Nhịp 3-3": lambda h: len(h) >= 6 and all(x == h[-6] for x in h[-6:-3]) and all(x == h[-3] for x in h[-3:]),
        "Nhịp 4-4": lambda h: len(h) >= 8 and h[-8:-4] == [h[-8]]*4 and h[-4:] == [h[-4]]*4 and h[-8] != h[-4],
        "Lặp 2-1": lambda h: len(h) >= 3 and h[-3:-1] == [h[-3], h[-3]] and h[-1] != h[-3],
        "Lặp 3-2": lambda h: len(h) >= 5 and h[-5:-2] == [h[-5]]*3 and h[-2:] == [h[-2]]*2 and h[-5] != h[-2],
        "Cầu 3-1": lambda h: len(h) >= 4 and all(x == h[-4] for x in h[-4:-1]) and h[-1] != h[-4],
        "Cầu 4-1": lambda h: len(h) >= 5 and h[-5:-1] == [h[-5]]*4 and h[-1] != h[-5],
        "Cầu 1-2-1": lambda h: len(h) >= 4 and h[-4] != h[-3] and h[-3]==h[-2] and h[-2] != h[-1] and h[-4]==h[-1],
        "Cầu 2-1-2": lambda h: len(h) >= 5 and h[-5:-3] == [h[-5]]*2 and h[-2] != h[-5] and h[-1] == h[-5],
        "Cầu 3-1-2": lambda h: len(h) >= 6 and h[-6:-3]==[h[-6]]*3 and h[-3]!=h[-2] and h[-2:]==[h[-2]]*2 and len(set(h[-6:])) == 2,
        "Cầu 1-2-3": lambda h: len(h) >= 6 and h[-6:-5]==[h[-6]] and h[-5:-3]==[h[-5]]*2 and h[-3:]==[h[-3]]*3 and len(set(h[-6:])) == 2,
        "Dài ngắn đảo": lambda h: len(h) >= 5 and h[-5:-2] == [h[-5]] * 3 and h[-2] != h[-1] and h[-2] != h[-5],

        # --- Cầu Chu Kỳ & Đối Xứng (Cyclic & Symmetric) ---
        "Chu kỳ 2": lambda h: len(h) >= 4 and h[-1] == h[-3] and h[-2] == h[-4],
        "Chu kỳ 3": lambda h: len(h) >= 6 and h[-1] == h[-4] and h[-2] == h[-5] and h[-3] == h[-6],
        "Chu kỳ 4": lambda h: len(h) >= 8 and h[-8:-4] == h[-4:],
        "Đối xứng (Gương)": lambda h: len(h) >= 5 and h[-1] == h[-5] and h[-2] == h[-4],
        "Bán đối xứng": lambda h: len(h) >= 5 and h[-1] == h[-4] and h[-2] == h[-5],
        "Ngược chu kỳ": lambda h: len(h) >= 4 and h[-1] == h[-4] and h[-2] == h[-3] and h[-1] != h[-2],
        "Chu kỳ biến đổi": lambda h: len(h) >= 5 and h[-5:] == [h[-5], h[-4], h[-5], h[-4], h[-5]],
        "Cầu linh hoạt": lambda h: len(h) >= 6 and h[-1]==h[-3]==h[-5] and h[-2]==h[-4]==h[-6],
        "Chu kỳ tăng": lambda h: len(h) >= 6 and h[-6:] == [h[-6], h[-5], h[-6], h[-5], h[-6], h[-5]] and h[-6] != h[-5],
        "Chu kỳ giảm": lambda h: len(h) >= 6 and h[-6:] == [h[-6], h[-6], h[-5], h[-5], h[-4], h[-4]] and len(set(h[-6:])) == 3,
        "Cầu lặp": lambda h: len(h) >= 6 and h[-6:-3] == h[-3:],
        "Gãy ngang": lambda h: len(h) >= 4 and h[-1] == h[-3] and h[-2] == h[-4] and h[-1] != h[-2],

        # --- Cầu Phức Tạp & Tổng Hợp ---
        "Gập ghềnh": lambda h: len(h) >= 5 and h[-5:] == [h[-5], h[-5], h[-3], h[-3], h[-5]],
        "Bậc thang": lambda h: len(h) >= 3 and h[-3:] == [h[-3], h[-3], h[-1]] and h[-3] != h[-1],
        "Cầu đôi": lambda h: len(h) >= 4 and h[-1] == h[-2] and h[-3] != h[-4] and h[-3] != h[-1],
        "Đối ngược": lambda h: len(h) >= 4 and h[-1] == ('Xỉu' if h[-2]=='Tài' else 'Tài') and h[-3] == ('Xỉu' if h[-4]=='Tài' else 'Tài'),
        "Cầu gập": lambda h: len(h) >= 5 and h[-5:] == [h[-5], h[-4], h[-4], h[-2], h[-2]],
        "Phối hợp 1": lambda h: len(h) >= 5 and h[-1] == h[-2] and h[-3] != h[-4],
        "Phối hợp 2": lambda h: len(h) >= 4 and h[-4:] == ['Tài', 'Tài', 'Xỉu', 'Tài'],
        "Phối hợp 3": lambda h: len(h) >= 4 and h[-4:] == ['Xỉu', 'Xỉu', 'Tài', 'Xỉu'],
        "Chẵn lẻ lặp": lambda h: len(h) >= 4 and len(set(h[-4:-2])) == 1 and len(set(h[-2:])) == 1 and h[-1] != h[-3],
        "Cầu dài ngẫu": lambda h: len(h) >= 7 and all(x == h[-7] for x in h[-7:-3]) and len(set(h[-3:])) > 1,
        
        # --- Cầu Dựa Trên Phân Bố (Statistical) ---
        "Ngẫu nhiên": lambda h: len(h) > 10 and 0.4 < (h[-10:].count('Tài') / 10) < 0.6,
        "Đa dạng": lambda h: len(h) >= 5 and len(set(h[-5:])) == 2,
        "Phân cụm": lambda h: len(h) >= 6 and (all(x == 'Tài' for x in h[-6:-3]) or all(x == 'Xỉu' for x in h[-6:-3])),
        "Lệch ngẫu nhiên": lambda h: len(h) > 10 and (h[-10:].count('Tài') / 10 > 0.7 or h[-10:].count('Xỉu') / 10 > 0.7),

        # --- Siêu Cầu (Super Patterns) ---
        "Cầu Tiến 1-1-2-2": lambda h: len(h) >= 6 and h[-6:] == [h[-6], h[-5], h[-4], h[-4], h[-2], h[-2]] and len(set(h[-6:])) == 2,
        "Cầu Lùi 3-2-1": lambda h: len(h) >= 6 and h[-6:-3]==[h[-6]]*3 and h[-3:-1]==[h[-3]]*2 and h[-1]!=h[-3] and len(set(h[-6:])) == 2,
        "Cầu Sandwich": lambda h: len(h) >= 5 and h[-1] == h[-5] and h[-2] == h[-3] == h[-4] and h[-1] != h[-2],
        "Cầu Thang máy": lambda h: len(h) >= 7 and h[-7:] == [h[-7],h[-7],h[-5],h[-5],h[-3],h[-3],h[-1]] and len(set(h[-7:]))==4, # T-T-X-X-T-T-X
        "Cầu Sóng vỗ": lambda h: len(h) >= 8 and h[-8:] == [h[-8],h[-8],h[-6],h[-8],h[-8],h[-6],h[-8],h[-8]],
    }
    return patterns

# 2. Các hàm cập nhật và huấn luyện mô hình (không thay đổi)
def update_transition_matrix(app, prev_result, current_result):
    if not prev_result: return
    prev_idx = 0 if prev_result == 'Tài' else 1
    curr_idx = 0 if current_result == 'Tài' else 1
    app.transition_counts[prev_idx][curr_idx] += 1
    total_transitions = sum(app.transition_counts[prev_idx])
    alpha = 1 # Laplace smoothing để tránh xác suất bằng 0
    num_outcomes = 2
    app.transition_matrix[prev_idx][0] = (app.transition_counts[prev_idx][0] + alpha) / (total_transitions + alpha * num_outcomes)
    app.transition_matrix[prev_idx][1] = (app.transition_counts[prev_idx][1] + alpha) / (total_transitions + alpha * num_outcomes)

def update_pattern_accuracy(app, predicted_pattern_name, prediction, actual_result):
    if not predicted_pattern_name: return
    stats = app.pattern_accuracy[predicted_pattern_name]
    stats['total'] += 1
    if prediction == actual_result:
        stats['success'] += 1

def train_logistic_regression(app, features, actual_result):
    y = 1.0 if actual_result == 'Tài' else 0.0
    z = app.logistic_bias + sum(w * f for w, f in zip(app.logistic_weights, features))
    try:
        p = 1.0 / (1.0 + math.exp(-z))
    except OverflowError: # Xử lý trường hợp z quá lớn hoặc quá nhỏ gây tràn số
        p = 0.0 if z < 0 else 1.0
        
    error = y - p
    app.logistic_bias += app.learning_rate * error
    for i in range(len(app.logistic_weights)):
        gradient = error * features[i]
        regularization_term = app.regularization * app.logistic_weights[i]
        app.logistic_weights[i] += app.learning_rate * (gradient - regularization_term)

def update_model_weights(app):
    """Cập nhật trọng số của các mô hình trong ensemble dựa trên hiệu suất."""
    total_accuracy_score = 0
    accuracies_raw = {}
    
    # Tính toán độ chính xác và trọng số thô
    for name, perf in app.model_performance.items():
        # Chỉ cập nhật nếu có đủ dữ liệu, nếu không giữ trọng số mặc định ban đầu
        if perf['total'] > 5: 
            accuracy = perf['success'] / perf['total']
            accuracies_raw[name] = accuracy
            total_accuracy_score += accuracy
        else:
            # Nếu chưa đủ dữ liệu, gán trọng số mặc định ban đầu để chúng có cơ hội được "học"
            accuracies_raw[name] = app.default_model_weights[name] * 2 # Nhân đôi để ưu tiên khởi tạo
            total_accuracy_score += accuracies_raw[name]

    if total_accuracy_score > 0:
        for name in app.model_weights:
            app.model_weights[name] = accuracies_raw.get(name, 0) / total_accuracy_score
    else: # Trường hợp không có dữ liệu học
        app.model_weights = app.default_model_weights.copy()
        
    # Chuẩn hóa lại để tổng bằng 1 (đảm bảo)
    sum_weights = sum(app.model_weights.values())
    if sum_weights > 0:
        for name in app.model_weights:
            app.model_weights[name] /= sum_weights
    logging.info(f"Updated model weights: {app.model_weights}")


# 3. Các hàm dự đoán cốt lõi (không thay đổi)
def detect_pattern(app, history_str):
    detected_patterns = []
    if len(history_str) < 2: return None
    
    # Tính tổng số lần xuất hiện của tất cả các pattern để chuẩn hóa recency_score
    total_occurrences = max(1, sum(s['total'] for s in app.pattern_accuracy.values()))

    for name, func in app.patterns.items():
        try:
            if func(history_str):
                stats = app.pattern_accuracy[name]
                # Độ chính xác: nếu chưa đủ dữ liệu (total < 10), gán độ chính xác mặc định (ví dụ 0.55)
                accuracy = (stats['success'] / stats['total']) if stats['total'] > 10 else 0.55 
                # Điểm gần đây: tần suất xuất hiện của pattern
                recency_score = stats['total'] / total_occurrences
                
                # Trọng số kết hợp độ chính xác lịch sử (70%) và tần suất xuất hiện (30%)
                weight = 0.7 * accuracy + 0.3 * recency_score
                detected_patterns.append({'name': name, 'weight': weight})
        except IndexError:
            continue
    if not detected_patterns:
        return None
    # Trả về pattern có trọng số cao nhất
    return max(detected_patterns, key=lambda x: x['weight'])

def predict_with_pattern(app, history_str, detected_pattern_info):
    if not detected_pattern_info or len(history_str) < 2:
        return 'Tài', 0.5 # Dự đoán mặc định và độ tin cậy thấp nếu không có pattern

    name = detected_pattern_info['name']
    last = history_str[-1]
    prev = history_str[-2]
    anti_last = 'Xỉu' if last == 'Tài' else 'Tài' # Ngược lại của kết quả cuối cùng

    # Logic dự đoán chi tiết hơn dựa trên loại pattern
    if any(p in name for p in ["Bệt", "kép", "2-2", "3-3", "4-4", "Nhịp", "Sóng vỗ", "Cầu 3-1", "Cầu 4-1", "Lặp"]):
        prediction = last # Theo cầu
    elif any(p in name for p in ["Đảo 1-1", "Xen kẽ", "lắc", "Đối ngược", "gãy", "Bậc thang", "Dài ngắn đảo"]):
        prediction = anti_last # Bẻ cầu
    elif any(p in name for p in ["Chu kỳ 2", "Gãy ngang", "Chu kỳ tăng", "Chu kỳ giảm"]):
        prediction = prev # Quay về kết quả trước đó
    elif 'Chu kỳ 3' in name:
        prediction = history_str[-3]
    elif 'Chu kỳ 4' in name:
        prediction = history_str[-4]
    elif name == "Cầu 2-1-2":
        prediction = history_str[-5] # Kết quả của phiên T-T-X-X-T-T-X
    elif name == "Cầu 1-2-1":
        prediction = anti_last # Nếu T-XX-T, dự đoán Xỉu
    elif name == "Đối xứng (Gương)":
        prediction = history_str[-3] # Dự đoán phần tử tiếp theo trong chuỗi đối xứng
    elif name == "Cầu lặp":
        prediction = history_str[-3]
    elif name == "Cầu Sandwich":
        prediction = anti_last # Nếu T-XXX-T, dự đoán Xỉu
    elif name == "Cầu Thang máy":
        prediction = history_str[-3] # Nếu T-T-X-X-T-T-X, dự đoán Tài
    else: # Mặc định cho các cầu phức tạp khác là bẻ cầu
        prediction = anti_last
        
    return prediction, detected_pattern_info['weight']

def get_logistic_features(history_str):
    if not history_str: return [0.0] * 6 # Đảm bảo trả về list đủ kích thước

    # Feature 1: Current streak length (độ dài cầu hiện tại)
    current_streak = 0
    if len(history_str) > 0:
        last = history_str[-1]
        current_streak = 1
        for i in range(len(history_str) - 2, -1, -1):
            if history_str[i] == last: current_streak += 1
            else: break
    
    # Feature 2: Previous streak length (độ dài cầu trước đó)
    previous_streak_len = 0
    if len(history_str) > current_streak:
        prev_streak_start_idx = len(history_str) - current_streak - 1
        if prev_streak_start_idx >= 0:
            prev_streak_val = history_str[prev_streak_start_idx]
            previous_streak_len = 1
            for i in range(prev_streak_start_idx - 1, -1, -1):
                if history_str[i] == prev_streak_val: previous_streak_len += 1
                else: break

    # Feature 3 & 4: Balance (Tài-Xỉu) short-term and long-term (tỷ lệ Tài/Xỉu trong quá khứ gần và xa)
    recent_history = history_str[-20:] # Lịch sử 20 phiên gần nhất
    balance_short = (recent_history.count('Tài') - recent_history.count('Xỉu')) / max(1, len(recent_history))
    
    long_history = history_str[-100:] # Lịch sử 100 phiên gần nhất
    balance_long = (long_history.count('Tài') - long_history.count('Xỉu')) / max(1, len(long_history))
    
    # Feature 5: Volatility (tần suất thay đổi giữa Tài và Xỉu)
    changes = sum(1 for i in range(len(recent_history)-1) if recent_history[i] != recent_history[i+1])
    volatility = changes / max(1, len(recent_history) - 1) if len(recent_history) > 1 else 0.0

    # Feature 6: Alternation count in last 10 results (số lần luân phiên trong 10 phiên gần nhất)
    last_10 = history_str[-10:]
    alternations = sum(1 for i in range(len(last_10) - 1) if last_10[i] != last_10[i+1])
    
    return [float(current_streak), float(previous_streak_len), balance_short, balance_long, volatility, float(alternations)]

def apply_meta_logic(prediction, confidence, history_str):
    """
    Áp dụng logic cấp cao để điều chỉnh dự đoán cuối cùng.
    Ví dụ: Logic "bẻ cầu" khi cầu quá dài.
    """
    final_prediction, final_confidence, reason = prediction, confidence, ""

    # Logic 1: Bẻ cầu khi cầu bệt quá dài (Anti-Streak)
    streak_len = 0
    if len(history_str) > 0: # Check if history_str is not empty
        last = history_str[-1]
        for x in reversed(history_str):
            if x == last: streak_len += 1
            else: break
    
    if streak_len >= 9 and prediction == history_str[-1]:
        final_prediction = 'Xỉu' if history_str[-1] == 'Tài' else 'Tài'
        final_confidence = 78.0 # Gán một độ tin cậy khá cao cho việc bẻ cầu
        reason = f"Bẻ cầu bệt siêu dài ({streak_len})"
        logging.warning(f"META-LOGIC: Activated Anti-Streak. Streak of {streak_len} detected. Forcing prediction to {final_prediction}.")
    elif streak_len >= 7 and prediction == history_str[-1]:
        final_confidence = max(50.0, confidence - 15) # Giảm độ tin cậy
        reason = f"Cầu bệt dài ({streak_len}), giảm độ tin cậy"
        logging.info(f"META-LOGIC: Long streak of {streak_len} detected. Reducing confidence.")
        
    return final_prediction, final_confidence, reason


def predict_advanced(app, history_str):
    """Hàm điều phối dự đoán nâng cao, kết hợp nhiều mô hình với trọng số động."""
    if len(history_str) < 5: # Yêu cầu tối thiểu 5 phiên lịch sử để bắt đầu dự đoán
        return "Chờ dữ liệu", "Phân tích", 50.0, {}

    last_result = history_str[-1]

    # --- Model 1: Pattern Matching ---
    detected_pattern_info = detect_pattern(app, history_str)
    patt_pred, patt_conf = predict_with_pattern(app, history_str, detected_pattern_info)
    # Scale confidence to be from 0 to 1
    patt_conf_scaled = patt_conf # pattern weight is already a confidence score

    # --- Model 2: Markov Chain ---
    last_result_idx = 0 if last_result == 'Tài' else 1
    prob_tai_markov = app.transition_matrix[last_result_idx][0]
    markov_pred = 'Tài' if prob_tai_markov >= 0.5 else 'Xỉu'
    markov_conf_scaled = max(prob_tai_markov, 1 - prob_tai_markov)

    # --- Model 3: Logistic Regression ---
    features = get_logistic_features(history_str)
    z = app.logistic_bias + sum(w * f for w, f in zip(app.logistic_weights, features))
    try:
        prob_tai_logistic = 1.0 / (1.0 + math.exp(-z))
    except OverflowError:
        prob_tai_logistic = 0.0 if z < 0 else 1.0
        
    logistic_pred = 'Tài' if prob_tai_logistic >= 0.5 else 'Xỉu'
    logistic_conf_scaled = max(prob_tai_logistic, 1 - prob_tai_logistic)
    
    # Lưu lại dự đoán của từng mô hình để học
    individual_predictions = {
        'pattern': patt_pred,
        'markov': markov_pred,
        'logistic': logistic_pred
    }

    # --- Ensemble Prediction (Kết hợp các mô hình với trọng số động) ---
    # Sử dụng confidence đã được scale (0-1)
    predictions_with_weights = {
        'pattern': {'pred': patt_pred, 'conf': patt_conf_scaled, 'weight': app.model_weights['pattern']},
        'markov': {'pred': markov_pred, 'conf': markov_conf_scaled, 'weight': app.model_weights['markov']},
        'logistic': {'pred': logistic_pred, 'conf': logistic_conf_scaled, 'weight': app.model_weights['logistic']},
    }
    
    tai_score, xiu_score = 0.0, 0.0
    for model_info in predictions_with_weights.values():
        score = model_info['conf'] * model_info['weight']
        if model_info['pred'] == 'Tài': tai_score += score
        else: xiu_score += score

    final_prediction = 'Tài' if tai_score > xiu_score else 'Xỉu'
    total_score = tai_score + xiu_score
    # Chuyển đổi về phần trăm (0-100)
    final_confidence = (max(tai_score, xiu_score) / total_score * 100) if total_score > 0 else 50.0
    
    # Tăng độ tin cậy nếu pattern mạnh nhất trùng với dự đoán cuối cùng
    if detected_pattern_info and detected_pattern_info['weight'] > 0.6 and patt_pred == final_prediction:
        final_confidence = min(98.0, final_confidence + (patt_conf_scaled * 10)) # Thêm một phần nhỏ từ độ tin cậy của pattern

    # Áp dụng logic meta cuối cùng
    final_prediction, final_confidence, meta_reason = apply_meta_logic(final_prediction, final_confidence, history_str)

    used_pattern_name = detected_pattern_info['name'] if detected_pattern_info else "Ensemble"
    if meta_reason:
        used_pattern_name = meta_reason

    return final_prediction, used_pattern_name, final_confidence, individual_predictions


# Các hàm JS chuyển sang Python
def detect_streak_and_break(history):
    if not history:
        return {'streak': 0, 'currentResult': None, 'breakProb': 0.0}
    
    streak = 1
    current_result = history[-1]['result']
    for i in range(len(history) - 2, -1, -1):
        if history[i]['result'] == current_result:
            streak += 1
        else:
            break
    
    last_15 = [h['result'] for h in history[-15:]]
    if not last_15:
        return {'streak': streak, 'currentResult': current_result, 'breakProb': 0.0}
    
    switches = sum(1 for i in range(len(last_15) - 1) if last_15[i] != last_15[i+1])
    tai_count = last_15.count('Tài')
    xiu_count = last_15.count('Xỉu')
    imbalance = abs(tai_count - xiu_count) / len(last_15)
    break_prob = 0.0

    if streak >= 8:
        break_prob = min(0.7 + (switches / 15) + imbalance * 0.2, 0.95)
    elif streak >= 5:
        break_prob = min(0.4 + (switches / 10) + imbalance * 0.3, 1.0)
    elif streak >= 3 and switches >= 6:
        break_prob = 0.35

    return {'streak': streak, 'currentResult': current_result, 'breakProb': break_prob}

def evaluate_model_performance(model_predictions, model_name, history, lookback=10):
    if model_name not in model_predictions or not history or len(history) < 2:
        return 1.0
    lookback = min(lookback, len(history) - 1)
    correct_count = 0
    for i in range(lookback):
        # Lấy phiên của kết quả thực tế (session + 1 so với session tại thời điểm dự đoán)
        session_at_prediction_time = history[len(history) - (i + 2)]['session']
        actual_result_session = history[len(history) - (i + 1)]['session'] # Phiên đã có kết quả
        
        # Dự đoán của mô hình cho phiên trước đó (phien_truoc)
        pred = model_predictions[model_name].get(session_at_prediction_time + 1) # Lấy dự đoán cho phiên tiếp theo
        actual = history[len(history) - (i + 1)]['result']

        if pred == actual:
            correct_count += 1
    
    performance_score = 1.0 + (correct_count - lookback / 2) / (lookback / 2) if lookback > 0 else 1.0
    return max(0.0, min(2.0, performance_score))


def smart_bridge_break(history):
    if not history or len(history) < 5:
        return {'prediction': 'Tài', 'breakProb': 0.0, 'reason': 'Không đủ dữ liệu để bẻ cầu'}

    streak_info = detect_streak_and_break(history)
    streak = streak_info['streak']
    current_result = streak_info['currentResult']
    break_prob = streak_info['breakProb']
    
    last_20 = [h['result'] for h in history[-20:]]
    last_scores = [h.get('totalScore', 0) for h in history[-20:]] # Lấy totalScore

    break_probability = break_prob
    reason = ''

    avg_score = sum(last_scores) / (len(last_scores) or 1)
    score_deviation = sum(abs(score - avg_score) for score in last_scores) / (len(last_scores) or 1)

    last_5 = last_20[-5:]
    pattern_counts = defaultdict(int)
    for i in range(len(last_20) - 2): # Lặp qua 3 phần tử
        pattern = ','.join(last_20[i:i+3])
        pattern_counts[pattern] += 1
    
    most_common_pattern = None
    if pattern_counts:
        most_common_pattern = max(pattern_counts.items(), key=lambda item: item[1])

    is_stable_pattern = most_common_pattern and most_common_pattern[1] >= 3

    if streak >= 6:
        break_probability = min(break_probability + 0.2, 0.95)
        reason = f"[Bẻ Cầu] Chuỗi {streak} {current_result} quá dài, khả năng bẻ cầu cao"
    elif streak >= 4 and score_deviation > 3:
        break_probability = min(break_probability + 0.15, 0.9)
        reason = f"[Bẻ Cầu] Biến động điểm số lớn ({score_deviation:.1f}), khả năng bẻ cầu tăng"
    elif is_stable_pattern and all(r == current_result for r in last_5):
        break_probability = min(break_probability + 0.1, 0.85)
        reason = f"[Bẻ Cầu] Phát hiện mẫu lặp {most_common_pattern[0]}, có khả năng bẻ cầu"
    else:
        break_probability = max(break_probability - 0.1, 0.2)
        reason = f"[Bẻ Cầu] Không phát hiện mẫu bẻ cầu mạnh, tiếp tục theo cầu"

    prediction = (current_result == 'Tài' and 'Xỉu') or 'Tài' if break_probability > 0.6 else current_result
    return {'prediction': prediction, 'breakProb': break_probability, 'reason': reason}

def trend_and_prob(history):
    streak_info = detect_streak_and_break(history)
    streak = streak_info['streak']
    current_result = streak_info['currentResult']
    break_prob = streak_info['breakProb']

    if streak >= 5:
        if break_prob > 0.7:
            return 'Xỉu' if current_result == 'Tài' else 'Tài'
        return current_result
    
    last_15 = [h['result'] for h in history[-15:]]
    if not last_15:
        return 'Tài'
    
    weights = [1.3**i for i in range(len(last_15))]
    tai_weighted = sum(w for i, w in enumerate(weights) if last_15[i] == 'Tài')
    xiu_weighted = sum(w for i, w in enumerate(weights) if last_15[i] == 'Xỉu')
    total_weight = tai_weighted + xiu_weighted

    last_10 = last_15[-10:]
    patterns = []
    if len(last_10) >= 4:
        for i in range(len(last_10) - 3):
            patterns.append(','.join(last_10[i:i+4]))
    
    pattern_counts = defaultdict(int)
    for p in patterns:
        pattern_counts[p] += 1
    
    most_common = None
    if pattern_counts:
        most_common = max(pattern_counts.items(), key=lambda item: item[1])

    if most_common and most_common[1] >= 3:
        pattern_elements = most_common[0].split(',')
        return 'Tài' if pattern_elements[-1] != last_10[-1] else 'Xỉu'
    elif total_weight > 0 and abs(tai_weighted - xiu_weighted) / total_weight >= 0.2:
        return 'Tài' if tai_weighted > xiu_weighted else 'Xỉu'
    
    return 'Tài' if last_15[-1] == 'Xỉu' else 'Xỉu'


def short_pattern(history):
    streak_info = detect_streak_and_break(history)
    streak = streak_info['streak']
    current_result = streak_info['currentResult']
    break_prob = streak_info['breakProb']

    if streak >= 4:
        if break_prob > 0.7:
            return 'Xỉu' if current_result == 'Tài' else 'Tài'
        return current_result
    
    last_8 = [h['result'] for h in history[-8:]]
    if not last_8:
        return 'Tài'
    
    patterns = []
    if len(last_8) >= 3:
        for i in range(len(last_8) - 2):
            patterns.append(','.join(last_8[i:i+3]))
    
    pattern_counts = defaultdict(int)
    for p in patterns:
        pattern_counts[p] += 1
    
    most_common = None
    if pattern_counts:
        most_common = max(pattern_counts.items(), key=lambda item: item[1])

    if most_common and most_common[1] >= 2:
        pattern_elements = most_common[0].split(',')
        return 'Tài' if pattern_elements[-1] != last_8[-1] else 'Xỉu'
    
    return 'Tài' if last_8[-1] == 'Xỉu' else 'Xỉu'


def mean_deviation(history):
    streak_info = detect_streak_and_break(history)
    streak = streak_info['streak']
    current_result = streak_info['currentResult']
    break_prob = streak_info['breakProb']

    if streak >= 4:
        if break_prob > 0.7:
            return 'Xỉu' if current_result == 'Tài' else 'Tài'
        return current_result
    
    last_12 = [h['result'] for h in history[-12:]]
    if not last_12:
        return 'Tài'
    
    tai_count = last_12.count('Tài')
    xiu_count = len(last_12) - tai_count
    deviation = abs(tai_count - xiu_count) / len(last_12)

    if deviation < 0.3:
        return 'Tài' if last_12[-1] == 'Xỉu' else 'Xỉu'
    
    return 'Tài' if xiu_count > tai_count else 'Xỉu'


def recent_switch(history):
    streak_info = detect_streak_and_break(history)
    streak = streak_info['streak']
    current_result = streak_info['currentResult']
    break_prob = streak_info['breakProb']

    if streak >= 4:
        if break_prob > 0.7:
            return 'Xỉu' if current_result == 'Tài' else 'Tài'
        return current_result
    
    last_10 = [h['result'] for h in history[-10:]]
    if not last_10:
        return 'Tài'
    
    switches = sum(1 for i in range(len(last_10) - 1) if last_10[i] != last_10[i+1])
    
    return 'Tài' if switches >= 5 and last_10[-1] == 'Xỉu' else ('Xỉu' if switches >=5 and last_10[-1] == 'Tài' else ('Tài' if last_10[-1] == 'Xỉu' else 'Xỉu'))


def is_bad_pattern(history):
    last_15 = [h['result'] for h in history[-15:]]
    if not last_15:
        return False
    
    switches = sum(1 for i in range(len(last_15) - 1) if last_15[i] != last_15[i+1])
    streak_info = detect_streak_and_break(history)
    streak = streak_info['streak']
    
    return switches >= 8 or streak >= 9


def ai_htdd_logic(history):
    recent_history = [h['result'] for h in history[-6:]]
    recent_scores = [h.get('totalScore', 0) for h in history[-6:]]
    tai_count = recent_history.count('Tài')
    xiu_count = recent_history.count('Xỉu')

    if len(history) >= 6:
        last_6 = ','.join([h['result'] for h in history[-6:]])
        if last_6 == 'Tài,Xỉu,Xỉu,Tài,Tài,Tài':
            return {'prediction': 'Xỉu', 'reason': '[AI] Phát hiện mẫu 1T2X3T (Tài, Xỉu, Xỉu, Tài, Tài, Tài) → dự đoán Xỉu', 'source': 'AI HTDD 123'}
        elif last_6 == 'Xỉu,Tài,Tài,Xỉu,Xỉu,Xỉu':
            return {'prediction': 'Tài', 'reason': '[AI] Phát hiện mẫu 1X2T3X (Xỉu, Tài, Tài, Xỉu, Xỉu, Xỉu) → dự đoán Tài', 'source': 'AI HTDD 123'}
    
    if len(history) >= 3:
        last_3 = [h['result'] for h in history[-3:]]
        if ','.join(last_3) == 'Tài,Xỉu,Tài':
            return {'prediction': 'Xỉu', 'reason': '[AI] Phát hiện mẫu 1T1X → tiếp theo nên đánh Xỉu', 'source': 'AI HTDD'}
        elif ','.join(last_3) == 'Xỉu,Tài,Xỉu':
            return {'prediction': 'Tài', 'reason': '[AI] Phát hiện mẫu 1X1T → tiếp theo nên đánh Tài', 'source': 'AI HTDD'}
    
    if len(history) >= 4:
        last_4 = [h['result'] for h in history[-4:]]
        if ','.join(last_4) == 'Tài,Tài,Xỉu,Xỉu':
            return {'prediction': 'Tài', 'reason': '[AI] Phát hiện mẫu 2T2X → tiếp theo nên đánh Tài', 'source': 'AI HTDD'}
        elif ','.join(last_4) == 'Xỉu,Xỉu,Tài,Tài':
            return {'prediction': 'Xỉu', 'reason': '[AI] Phát hiện mẫu 2X2T → tiếp theo nên đánh Xỉu', 'source': 'AI HTDD'}

    if len(history) >= 9 and all(h['result'] == 'Xỉu' for h in history[-9:]):
        return {'prediction': 'Tài', 'reason': '[AI] Chuỗi Xỉu quá dài (9 lần) → dự đoán Tài', 'source': 'AI HTDD'}

    avg_score = sum(recent_scores) / (len(recent_scores) or 1)
    if avg_score > 10:
        return {'prediction': 'Tài', 'reason': f'[AI] Điểm trung bình cao ({avg_score:.1f}) → dự đoán Tài', 'source': 'AI HTDD'}
    elif avg_score < 8:
        return {'prediction': 'Xỉu', 'reason': f'[AI] Điểm trung bình thấp ({avg_score:.1f}) → dự đoán Xỉu', 'source': 'AI HTDD'}

    if tai_count > xiu_count + 1:
        return {'prediction': 'Tài', 'reason': f'[AI] Tài chiếm đa số ({tai_count}/{len(recent_history)}) → dự đoán Tài', 'source': 'AI HTDD'}
    elif xiu_count > tai_count + 1:
        return {'prediction': 'Xỉu', 'reason': f'[AI] Xỉu chiếm đa số ({xiu_count}/{len(recent_history)}) → dự đoán Xỉu', 'source': 'AI HTDD'}
    else:
        overall_tai = sum(1 for h in history if h['result'] == 'Tài')
        overall_xiu = sum(1 for h in history if h['result'] == 'Xỉu')
        if overall_tai > overall_xiu:
            return {'prediction': 'Xỉu', 'reason': '[AI] Tổng thể Tài nhiều hơn → dự đoán Xỉu', 'source': 'AI HTDD'}
        else:
            return {'prediction': 'Tài', 'reason': '[AI] Tổng thể Xỉu nhiều hơn hoặc bằng → dự đoán Tài', 'source': 'AI HTDD'}


def generate_prediction_js_logic(history_data, model_predictions_state):
    if not history_data or len(history_data) < 5:
        logging.info('Insufficient history for JS logic, defaulting to Tài')
        return {'prediction': 'Tài', 'reason': 'Không đủ dữ liệu', 'scores': {'taiScore': 0.5, 'xiuScore': 0.5}}

    # Convert history_data to the format expected by JS functions
    history_for_js_logic = [{'result': h['ket_qua'], 'session': h['phien'], 'totalScore': sum(h.get('Dice', [0,0,0]))} for h in history_data]

    # Initialize modelPredictions objects if not exists
    # These will store predictions made by the JS-ported models for training
    model_predictions_state['trend'] = model_predictions_state.get('trend', {})
    model_predictions_state['short'] = model_predictions_state.get('short', {})
    model_predictions_state['mean'] = model_predictions_state.get('mean', {})
    model_predictions_state['switch'] = model_predictions_state.get('switch', {})
    model_predictions_state['bridge'] = model_predictions_state.get('bridge', {})


    # Run models
    trend_pred = trend_and_prob(history_for_js_logic)
    short_pred = short_pattern(history_for_js_logic)
    mean_pred = mean_deviation(history_for_js_logic)
    switch_pred = recent_switch(history_for_js_logic)
    bridge_pred = smart_bridge_break(history_for_js_logic)
    ai_pred = ai_htdd_logic(history_for_js_logic)
    
    current_session_for_pred = history_for_js_logic[-1]['session'] + 1 # Phiên mà chúng ta đang dự đoán cho

    # Store predictions for performance evaluation later
    model_predictions_state['trend'][current_session_for_pred] = trend_pred
    model_predictions_state['short'][current_session_for_pred] = short_pred
    model_predictions_state['mean'][current_session_for_pred] = mean_pred
    model_predictions_state['switch'][current_session_for_pred] = switch_pred
    model_predictions_state['bridge'][current_session_for_pred] = bridge_pred['prediction']


    # Evaluate model performance
    model_scores = {
        'trend': evaluate_model_performance(model_predictions_state, 'trend', history_for_js_logic),
        'short': evaluate_model_performance(model_predictions_state, 'short', history_for_js_logic),
        'mean': evaluate_model_performance(model_predictions_state, 'mean', history_for_js_logic),
        'switch': evaluate_model_performance(model_predictions_state, 'switch', history_for_js_logic),
        'bridge': evaluate_model_performance(model_predictions_state, 'bridge', history_for_js_logic)
    }

    # Weighted voting
    weights = {
        'trend': 0.25 * model_scores['trend'],
        'short': 0.2 * model_scores['short'],
        'mean': 0.2 * model_scores['mean'],
        'switch': 0.15 * model_scores['switch'],
        'bridge': 0.2 * model_scores['bridge'],
        'aihtdd': 0.3
    }

    tai_score = 0.0
    xiu_score = 0.0

    tai_score += (weights['trend'] if trend_pred == 'Tài' else 0)
    xiu_score += (weights['trend'] if trend_pred == 'Xỉu' else 0)
    tai_score += (weights['short'] if short_pred == 'Tài' else 0)
    xiu_score += (weights['short'] if short_pred == 'Xỉu' else 0)
    tai_score += (weights['mean'] if mean_pred == 'Tài' else 0)
    xiu_score += (weights['mean'] if mean_pred == 'Xỉu' else 0)
    tai_score += (weights['switch'] if switch_pred == 'Tài' else 0)
    xiu_score += (weights['switch'] if switch_pred == 'Xỉu' else 0)
    tai_score += (weights['bridge'] if bridge_pred['prediction'] == 'Tài' else 0)
    xiu_score += (weights['bridge'] if bridge_pred['prediction'] == 'Xỉu' else 0)
    tai_score += (weights['aihtdd'] if ai_pred['prediction'] == 'Tài' else 0)
    xiu_score += (weights['aihtdd'] if ai_pred['prediction'] == 'Xỉu' else 0)

    # Adjust for bad pattern
    if is_bad_pattern(history_for_js_logic):
        logging.info('Bad pattern detected, reducing confidence')
        tai_score *= 0.7
        xiu_score *= 0.7

    # Adjust for bridge break probability
    if bridge_pred['breakProb'] > 0.6:
        logging.info(f"High bridge break probability: {bridge_pred['breakProb']:.2f}, {bridge_pred['reason']}")
        if bridge_pred['prediction'] == 'Tài':
            tai_score += 0.3
        else:
            xiu_score += 0.3

    final_prediction = 'Tài' if tai_score > xiu_score else 'Xỉu'
    
    total_score = tai_score + xiu_score
    confidence = (max(tai_score, xiu_score) / total_score * 100) if total_score > 0 else 50.0

    reason = f"{ai_pred['reason']} | {bridge_pred['reason']}"
    logging.info(f"JS-based Prediction: {{'prediction': '{final_prediction}', 'reason': '{reason}', 'scores': {{'taiScore': {tai_score:.2f}, 'xiuScore': {xiu_score:.2f}}}, 'confidence': {confidence:.1f}}}")
    return {'prediction': final_prediction, 'confidence': confidence, 'reason': reason, 'individual_predictions': {
        'trend': trend_pred, 'short': short_pred, 'mean': mean_pred, 'switch': switch_pred, 'bridge': bridge_pred['prediction'], 'aihtdd': ai_pred['prediction']
    }}


# --- Flask App Factory ---
def create_app():
    app = Flask(__name__)
    CORS(app)

    # --- Khởi tạo State ---
    app.lock = threading.Lock() # Lock để bảo vệ dữ liệu dùng chung giữa các luồng
    app.MAX_HISTORY_LEN = 200 # Số phiên lịch sử tối đa lưu trữ
    
    app.history = deque(maxlen=app.MAX_HISTORY_LEN) # Lưu kết quả và phiên
    app.session_ids = deque(maxlen=app.MAX_HISTORY_LEN) # Lưu id phiên để kiểm tra trùng lặp
    app.last_fetched_session = None # Phiên cuối cùng đã được fetch từ API

    # State cho các thuật toán (các thuật toán đã có trong Python trước đó)
    app.patterns = define_patterns() 
    app.transition_matrix = [[0.5, 0.5], [0.5, 0.5]] 
    app.transition_counts = [[0, 0], [0, 0]] 
    app.logistic_weights = [0.0] * 6 
    app.logistic_bias = 0.0 
    app.learning_rate = 0.01 
    app.regularization = 0.01 
    
    # State cho ensemble model động
    app.default_model_weights = {'pattern': 0.5, 'markov': 0.2, 'logistic': 0.3, 'js_ensemble': 0.5} # Thêm JS ensemble weight
    app.model_weights = app.default_model_weights.copy() 
    app.model_performance = {name: {"success": 0, "total": 0} for name in app.default_model_weights} # Cập nhật này
    
    app.overall_performance = {"success": 0, "total": 0} 

    app.last_prediction = None 
    app.pattern_accuracy = defaultdict(lambda: {"success": 0, "total": 0}) 

    # --- State mới cho các mô hình JS ---
    app.js_model_predictions = defaultdict(dict) # Để lưu dự đoán của từng mô hình JS cho việc học
    app.js_model_performance = {
        'trend': {'success': 0, 'total': 0},
        'short': {'success': 0, 'total': 0},
        'mean': {'success': 0, 'total': 0},
        'switch': {'success': 0, 'total': 0},
        'bridge': {'success': 0, 'total': 0},
        'aihtdd': {'success': 0, 'total': 0},
    }

    # --- Cấu hình API endpoint mới ---
    app.TAIXIUMD5_API_URL = "http://localhost:10000/taixiu" # Cập nhật API URL
    logging.info(f"External TaiXiu API URL: {app.TAIXIUMD5_API_URL}")

    def fetch_data_from_api():
        """Luồng chạy ngầm để lấy dữ liệu lịch sử từ API định kỳ."""
        while True:
            try:
                response = requests.get(app.TAIXIUMD5_API_URL, timeout=10) # Thêm timeout để tránh treo
                response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
                data = response.json()
                
                # --- Xử lý định dạng dữ liệu mới từ API ---
                if isinstance(data, dict) and "phien_truoc" in data and "ket_qua" in data:
                    phien = data.get("phien_truoc")
                    ket_qua = data.get("ket_qua")
                    dice = data.get("Dice") # Lấy thông tin xúc xắc
                    
                    if phien is None or ket_qua not in ["Tài", "Xỉu"]:
                        logging.warning(f"Invalid 'phien_truoc' or 'ket_qua' in data from external API: {data}. Skipping.")
                        time.sleep(2)
                        continue

                    with app.lock: # Đảm bảo an toàn luồng khi cập nhật app.history
                        # Chỉ thêm dữ liệu mới nếu phiên chưa tồn tại hoặc là phiên mới nhất
                        # Lưu ý: API này trả về "phien_truoc", đó là phiên LỊCH SỬ.
                        # Chúng ta cần đảm bảo không thêm trùng lặp và giữ thứ tự.
                        # Nếu API luôn trả về phiên mới nhất (phien_truoc) thì logic này ổn.
                        if not app.session_ids or phien > app.session_ids[-1]:
                            app.session_ids.append(phien)
                            app.history.append({'ket_qua': ket_qua, 'phien': phien, 'Dice': dice})
                            app.last_fetched_session = phien # Cập nhật phiên cuối cùng đã fetch
                            logging.info(f"Fetched new result for session {phien}: {ket_qua} (Dice: {dice}). History length: {len(app.history)}")
                        elif phien == app.session_ids[-1]:
                            logging.debug(f"Session {phien} already in history, no new data to add.")
                        else:
                            logging.warning(f"Fetched older session {phien} (current latest: {app.session_ids[-1]}). Skipping addition.")
                else:
                    logging.warning(f"External API response is not a dictionary with 'phien_truoc' and 'ket_qua' as expected: {data}. Skipping.")
                # --- KẾT THÚC SỬA LỖI ---
                
            except requests.exceptions.Timeout:
                logging.error("External API request timed out while fetching historical data.")
            except requests.exceptions.RequestException as e:
                logging.error(f"Error fetching data from external API: {e}")
            except (json.JSONDecodeError, TypeError) as e:
                logging.error(f"Error decoding external API response or invalid format: {e}. Raw response: {response.text if 'response' in locals() else 'N/A'}", exc_info=True)
            except Exception as e:
                logging.error(f"Unexpected error in fetch_data_from_api: {e}", exc_info=True) # exc_info=True để in traceback
            
            time.sleep(2) # Poll API every 2 seconds

    # --- API Endpoints ---
    @app.route("/api/taixiumd5", methods=["GET"])
    def get_taixiu_prediction():
        with app.lock:
            # Kiểm tra xem có đủ dữ liệu lịch sử để dự đoán không
            if len(app.history) < 2:
                if not app.last_fetched_session:
                    return jsonify({"error": "Đang chờ lấy dữ liệu lịch sử từ API. Vui lòng thử lại sau vài giây."}), 503
                else:
                    return jsonify({"error": "Chưa có đủ dữ liệu lịch sử để dự đoán.", "current_history_length": len(app.history)}), 503
            
            # Tạo bản sao lịch sử để thao tác mà không cần giữ khóa
            history_copy = list(app.history)
            last_prediction_copy = app.last_prediction
        
        # --- Học Online (Online Learning) ---
        if last_prediction_copy and history_copy and \
           last_prediction_copy['session'] == history_copy[-1]['phien'] + 1 and \
           not last_prediction_copy.get('learned', False):
            
            actual_result_of_learned_session = history_copy[-1]['ket_qua']
            history_at_prediction_time_str = _get_history_strings(history_copy[:-1]) 
            history_at_prediction_time_with_scores = _get_history_with_scores(history_copy[:-1])

            with app.lock: # Khóa để cập nhật state của các mô hình
                # Học cho các mô hình Python
                train_logistic_regression(app, last_prediction_copy['features'], actual_result_of_learned_session)
                
                if len(history_at_prediction_time_str) > 0:
                     update_transition_matrix(app, history_at_prediction_time_str[-1], actual_result_of_learned_session)
                
                update_pattern_accuracy(app, last_prediction_copy['pattern'], last_prediction_copy['prediction'], actual_result_of_learned_session)
                
                for model_name, model_pred in last_prediction_copy['individual_predictions'].items():
                    # Chỉ cập nhật performance cho các mô hình Python gốc
                    if model_name in app.model_performance: 
                        app.model_performance[model_name]['total'] += 1
                        if model_pred == actual_result_of_learned_session:
                            app.model_performance[model_name]['success'] += 1

                # Học cho các mô hình JS
                for js_model_name in app.js_model_performance.keys():
                    js_pred_for_session = last_prediction_copy['js_individual_predictions'].get(js_model_name)
                    if js_pred_for_session:
                        app.js_model_performance[js_model_name]['total'] += 1
                        if js_pred_for_session == actual_result_of_learned_session:
                            app.js_model_performance[js_model_name]['success'] += 1
                
                # Cập nhật hiệu suất của JS ensemble
                if 'js_ensemble' in app.model_performance:
                    app.model_performance['js_ensemble']['total'] += 1
                    if last_prediction_copy['js_prediction'] == actual_result_of_learned_session:
                        app.model_performance['js_ensemble']['success'] += 1

                app.overall_performance['total'] += 1
                if last_prediction_copy['final_prediction'] == actual_result_of_learned_session:
                    app.overall_performance['success'] += 1

                update_model_weights(app) # Cập nhật trọng số của ensemble
                app.last_prediction['learned'] = True 

            logging.info(f"Learned from session {history_copy[-1]['phien']}. Final Predicted: {last_prediction_copy['final_prediction']}, Actual: {actual_result_of_learned_session}. Pattern: {last_prediction_copy['pattern']}")
        
        # --- Dự đoán cho phiên tiếp theo (Prediction) ---
        history_str_for_prediction = _get_history_strings(history_copy)
        
        # Chạy dự đoán từ các mô hình Python gốc
        py_prediction_str, py_pattern_str, py_confidence, py_individual_preds = predict_advanced(app, history_str_for_prediction)

        # Chạy dự đoán từ logic JS đã chuyển đổi
        # Truyền app.js_model_predictions để các hàm JS-based có thể cập nhật
        js_prediction_result = generate_prediction_js_logic(history_copy, app.js_model_predictions)
        js_prediction_str = js_prediction_result['prediction']
        js_confidence = js_prediction_result['confidence']
        js_reason = js_prediction_result['reason']
        js_individual_preds = js_prediction_result['individual_predictions']

        # --- Kết hợp dự đoán từ hai hệ thống (Python gốc và JS đã chuyển đổi) ---
        ensemble_tai_score = 0.0
        ensemble_xiu_score = 0.0

        # Trọng số từ update_model_weights đã bao gồm js_ensemble
        py_weight = app.model_weights.get('pattern', 0) + app.model_weights.get('markov', 0) + app.model_weights.get('logistic', 0)
        js_weight = app.model_weights.get('js_ensemble', 0)

        # Chuẩn hóa lại nếu tổng py_weight không bằng 1
        total_py_conf = (py_confidence / 100.0) # Convert to 0-1
        total_js_conf = (js_confidence / 100.0) # Convert to 0-1

        if py_prediction_str == 'Tài':
            ensemble_tai_score += total_py_conf * py_weight
        else:
            ensemble_xiu_score += total_py_conf * py_weight

        if js_prediction_str == 'Tài':
            ensemble_tai_score += total_js_conf * js_weight
        else:
            ensemble_xiu_score += total_js_conf * js_weight
        
        final_prediction_combined = 'Tài' if ensemble_tai_score > ensemble_xiu_score else 'Xỉu'
        total_ensemble_score = ensemble_tai_score + ensemble_xiu_score
        final_confidence_combined = (max(ensemble_tai_score, ensemble_xiu_score) / total_ensemble_score * 100) if total_ensemble_score > 0 else 50.0

        # Áp dụng meta logic cuối cùng (sẽ áp dụng cho dự đoán cuối cùng)
        final_prediction_combined, final_confidence_combined, meta_reason_final = apply_meta_logic(final_prediction_combined, final_confidence_combined, history_str_for_prediction)

        final_suggested_pattern = f"{py_pattern_str} | JS: {js_reason}"
        if meta_reason_final:
            final_suggested_pattern = meta_reason_final
            
        # Lưu lại thông tin dự đoán hiện tại để học ở lần tiếp theo (khi có kết quả thực tế)
        with app.lock:
            current_session = history_copy[-1]['phien']
            app.last_prediction = {
                'session': current_session + 1, # Phiên tiếp theo mà chúng ta đang dự đoán
                'prediction': py_prediction_str, # Dự đoán từ Python gốc (để học pattern/markov/logistic)
                'pattern': py_pattern_str,
                'features': get_logistic_features(history_str_for_prediction), 
                'individual_predictions': py_individual_preds, # Dự đoán từ các mô hình Python riêng lẻ
                'js_prediction': js_prediction_str, # Dự đoán từ JS ensemble
                'js_individual_predictions': js_individual_preds, # Dự đoán từ các mô hình JS riêng lẻ
                'final_prediction': final_prediction_combined, # Dự đoán cuối cùng sau khi kết hợp
                'learned': False 
            }
            current_result = history_copy[-1]['ket_qua']
            current_dice = history_copy[-1]['Dice']
        
        # Tinh chỉnh hiển thị độ tin cậy và dự đoán
        prediction_display = final_prediction_combined
        final_confidence_display = round(final_confidence_combined, 1)

        # Nếu độ tin cậy thấp và không phải là do logic bẻ cầu, hiển thị "Đang phân tích"
        if final_confidence_display < 65.0 and "Bẻ cầu" not in final_suggested_pattern: # Ngưỡng 65% để hiển thị dự đoán rõ ràng
            prediction_display = "Đang phân tích"
            
        return jsonify({
            "current_session": current_session,
            "current_result": current_result,
            "current_dice": current_dice,
            "next_session": current_session + 1,
            "prediction": prediction_display,
            "confidence_percent": final_confidence_display,
            "suggested_pattern": final_suggested_pattern,
        })

    @app.route("/api/history", methods=["GET"])
    def get_history_api():
        with app.lock:
            hist_copy = list(app.history)
        return jsonify({"history": hist_copy, "length": len(hist_copy)})

    @app.route("/api/performance", methods=["GET"])
    def get_performance():
        with app.lock:
            # Sắp xếp pattern theo tổng số lần xuất hiện và độ chính xác
            seen_patterns = {k: v for k, v in app.pattern_accuracy.items() if v['total'] > 0}
            sorted_patterns = sorted(
                seen_patterns.items(), 
                key=lambda item: (item[1]['total'], (item[1]['success'] / item[1]['total'] if item[1]['total'] > 0 else 0)),
                reverse=True
            )
            pattern_result = {}
            for p_type, data in sorted_patterns[:30]: # Lấy 30 pattern hàng đầu có dữ liệu
                accuracy = round(data["success"] / data["total"] * 100, 2) if data["total"] > 0 else 0
                pattern_result[p_type] = { "total": data["total"], "success": data["success"], "accuracy_percent": accuracy }
            
            # Lấy hiệu suất của các mô hình con (Python gốc)
            model_perf_result = {}
            for name, perf in app.model_performance.items():
                 accuracy = round(perf["success"] / perf["total"] * 100, 2) if perf["total"] > 0 else 0
                 model_perf_result[name] = {**perf, "accuracy_percent": accuracy}

            # Lấy hiệu suất của các mô hình JS
            js_model_perf_result = {}
            for name, perf in app.js_model_performance.items():
                 accuracy = round(perf["success"] / perf["total"] * 100, 2) if perf["total"] > 0 else 0
                 js_model_perf_result[name] = {**perf, "accuracy_percent": accuracy}


            # Lấy hiệu suất tổng thể của API dự đoán
            overall_total = app.overall_performance['total']
            overall_success = app.overall_performance['success']
            overall_accuracy_percent = round(overall_success / overall_total * 100, 2) if overall_total > 0 else 0


        return jsonify({
            "pattern_performance": pattern_result,
            "python_model_performance": model_perf_result,
            "javascript_model_performance": js_model_perf_result, # Thêm phần này
            "ensemble_weights": app.model_weights,
            "overall_prediction_performance": { 
                "total_predictions": overall_total,
                "correct_predictions": overall_success,
                "accuracy_percent": overall_accuracy_percent
            }
        })

    # Khởi tạo và chạy luồng lấy dữ liệu API định kỳ
    api_fetch_thread = threading.Thread(target=fetch_data_from_api, daemon=True)
    api_fetch_thread.start()
    logging.info("Background API fetching thread started.")
    

    @app.route("/", methods=["GET"])
    def homepage():
        return """
        <h2>✅ Tool AI Dự Đoán Tài/Xỉu đang chạy!</h2>
        <ul>
            <li><a href='/api/taixiumd5'>Xem dự đoán tiếp theo</a></li>
            <li><a href='/api/history'>Xem lịch sử</a></li>
            <li><a href='/api/performance'>Xem hiệu suất mô hình</a></li>
        </ul>
        """

    return app

# --- Thực thi chính ---
app = create_app()

if __name__ == "__main__":
    # Render sẽ tự đặt biến môi trường PORT. 
    # Nếu chạy local, nó sẽ sử dụng 8080 làm mặc định.
    port = int(os.getenv("PORT", 8089)) 
    logging.info(f"Flask app is starting. Serving on http://0.0.0.0:{port}")
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, threads=8)
