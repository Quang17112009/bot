# --- START OF FILE code.py ---

import os
import json
import time
import math
import random
import threading
import logging
from collections import defaultdict, deque
from flask import Flask, jsonify
from flask_cors import CORS
import websocket

# --- Basic Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Core Prediction Logic ---

def _get_history_strings(history_list):
    """Hàm trợ giúp để lấy danh sách chuỗi 'Tài'/'Xỉu' từ danh sách dict."""
    return [item['ket_qua'] for item in history_list]

# --- Định nghĩa các Patterns ---
def define_patterns():
    patterns = {
        "Bệt": lambda h: len(h) >= 3 and h[-1] == h[-2] == h[-3],
        "Bệt siêu dài": lambda h: len(h) >= 5 and all(x == h[-1] for x in h[-5:]),
        "Bệt xen kẽ ngắn": lambda h: len(h) >= 4 and h[-4:] == [h[-4], h[-4], h[-2], h[-2]],
        "Bệt gãy nhẹ": lambda h: len(h) >= 4 and h[-1] != h[-2] and h[-2] == h[-3] == h[-4],
        "Đảo 1-1": lambda h: len(h) >= 4 and h[-1] != h[-2] and h[-2] != h[-3] and h[-3] != h[-4],
        "Kép 2-2": lambda h: len(h) >= 4 and h[-4:] == [h[-4], h[-4], h[-2], h[-2]] and h[-4] != h[-2],
        "1-2-3": lambda h: len(h) >= 6 and h[-6:-3] == [h[-6]]*1 and h[-3:-1] == [h[-3]]*2 and h[-1] == h[-6],
        "3-2-1": lambda h: len(h) >= 6 and h[-6:-3] == [h[-6]]*3 and h[-3:-1] == [h[-3]]*2 and h[-1] != h[-3],
        "3-3": lambda h: len(h) >= 6 and all(x == h[-6] for x in h[-6:-3]) and all(x == h[-3] for x in h[-3:]),
        "Chu kỳ 2": lambda h: len(h) >= 4 and h[-1] == h[-3] and h[-2] == h[-4],
        "Chu kỳ 3": lambda h: len(h) >= 6 and h[-1] == h[-4] and h[-2] == h[-5] and h[-3] == h[-6],
        "Lặp 2-1": lambda h: len(h) >= 3 and h[-3:-1] == [h[-3], h[-3]] and h[-1] != h[-3],
        "Lặp 3-2": lambda h: len(h) >= 5 and h[-5:-2] == [h[-5]]*3 and h[-2:] == [h[-2]]*2,
        "Đối xứng": lambda h: len(h) >= 5 and h[-1] == h[-5] and h[-2] == h[-4],
        "Bán đối xứng": lambda h: len(h) >= 5 and h[-1] == h[-4] and h[-2] == h[-5],
        "Bệt ngược": lambda h: len(h) >= 4 and h[-1] == h[-2] and h[-3] == h[-4] and h[-1] != h[-3],
        "Xỉu kép": lambda h: len(h) >= 2 and h[-1] == 'Xỉu' and h[-2] == 'Xỉu',
        "Tài kép": lambda h: len(h) >= 2 and h[-1] == 'Tài' and h[-2] == 'Tài',
        "Xen kẽ": lambda h: len(h) >= 3 and h[-1] != h[-2] and h[-2] != h[-3],
        "Gập ghềnh": lambda h: len(h) >= 5 and h[-5:] == [h[-5], h[-5], h[-3], h[-3], h[-5]],
        "Bậc thang": lambda h: len(h) >= 3 and h[-3:] == [h[-3], h[-3], h[-1]] and h[-3] != h[-1],
        "Cầu lặp": lambda h: len(h) >= 6 and h[-6:-3] == h[-3:],
        "Đối ngược": lambda h: len(h) >= 4 and h[-1] == ('Xỉu' if h[-2]=='Tài' else 'Tài') and h[-3] == ('Xỉu' if h[-4]=='Tài' else 'Tài'),
        "Phân cụm": lambda h: len(h) >= 6 and (all(x == 'Tài' for x in h[-6:-3]) or all(x == 'Xỉu' for x in h[-6:-3])),
        "Lệch ngẫu nhiên": lambda h: len(h) > 10 and (h[-10:].count('Tài') / 10 > 0.7 or h[-10:].count('Xỉu') / 10 > 0.7),
        "Xen kẽ dài": lambda h: len(h) >= 5 and all(h[i] != h[i+1] for i in range(-5, -1)),
        "Xỉu lắc": lambda h: len(h) >= 4 and h[-4:] == ['Xỉu', 'Tài', 'Xỉu', 'Tài'],
        "Tài lắc": lambda h: len(h) >= 4 and h[-4:] == ['Tài', 'Xỉu', 'Tài', 'Xỉu'],
        "Cầu 3-1": lambda h: len(h) >= 4 and all(x == h[-4] for x in h[-4:-1]) and h[-1] != h[-4],
        "Cầu 2-1-2": lambda h: len(h) >= 5 and h[-5:-3] == [h[-5]]*2 and h[-2] != h[-5] and h[-1] == h[-5],
    }
    return patterns

# --- Các hàm huấn luyện và cập nhật mô hình ---

def train_pattern_model(app, detected_pattern, prediction, actual_result):
    if not detected_pattern: return
    stats = app.pattern_accuracy[detected_pattern]
    stats['total'] += 1
    if prediction == actual_result:
        stats['success'] += 1
    app.pattern_outcomes[detected_pattern][actual_result] += 1

def update_markov2_matrix(app, history_str):
    if len(history_str) < 3: return
    state = history_str[-3] + history_str[-2]
    outcome = history_str[-1]
    counts = app.markov2_counts[state]
    counts[outcome] += 1

def train_logistic_regression(app, features, actual_result):
    y = 1.0 if actual_result == 'Tài' else 0.0
    z = app.logistic_bias + sum(w * f for w, f in zip(app.logistic_weights, features))
    p = 1.0 / (1.0 + math.exp(-z))
    error = y - p
    app.logistic_bias += app.learning_rate * error
    for i in range(len(app.logistic_weights)):
        gradient = error * features[i]
        regularization_term = app.regularization * app.logistic_weights[i]
        app.logistic_weights[i] += app.learning_rate * (gradient - regularization_term)

def update_dynamic_model_weights(app):
    accuracies = {}
    total_effective_accuracy = 0
    for model_name, perf_deque in app.model_performance.items():
        if not perf_deque:
            accuracy = app.initial_model_weights[model_name]
        else:
            accuracy = sum(perf_deque) / len(perf_deque)
        effective_accuracy = accuracy + 0.05 
        accuracies[model_name] = effective_accuracy
        total_effective_accuracy += effective_accuracy
    if total_effective_accuracy == 0: return
    for model_name in app.model_weights:
        app.model_weights[model_name] = accuracies[model_name] / total_effective_accuracy
    logging.info(f"Updated model weights: { {k: round(v, 2) for k, v in app.model_weights.items()} }")

# --- Các hàm dự đoán cốt lõi ---

def detect_pattern(app, history_str):
    detected_patterns = []
    for name, func in app.patterns.items():
        try:
            if func(history_str):
                stats = app.pattern_accuracy[name]
                accuracy = (stats['success'] / stats['total']) if stats['total'] > 5 else 0.5
                weight = accuracy
                detected_patterns.append({'name': name, 'weight': weight})
        except IndexError:
            continue
    if not detected_patterns: return None
    return max(detected_patterns, key=lambda x: x['weight'])

def predict_with_pattern(app, history_str, detected_pattern_info):
    if not detected_pattern_info: return 'Tài', 0.5
    name = detected_pattern_info['name']
    outcomes = app.pattern_outcomes[name]
    tai_count = outcomes.get('Tài', 1)
    xiu_count = outcomes.get('Xỉu', 1)
    total_outcomes = tai_count + xiu_count
    if total_outcomes < 5:
        if 'Bệt' in name or 'Kép' in name or '3-3' in name:
            prediction = 'Xỉu' if history_str[-1] == 'Tài' else 'Tài'
        else:
            prediction = history_str[-1]
        return prediction, 0.55
    prediction = 'Tài' if tai_count > xiu_count else 'Xỉu'
    confidence = max(tai_count, xiu_count) / total_outcomes
    return prediction, confidence

def predict_with_markov2(app, history_str):
    if len(history_str) < 2: return "Tài", 0.5
    state = history_str[-2] + history_str[-1]
    counts = app.markov2_counts.get(state)
    if not counts or sum(counts.values()) < 3: return "Tài", 0.5
    tai_count = counts.get('Tài', 0)
    xiu_count = counts.get('Xỉu', 0)
    total = tai_count + xiu_count
    prob_tai = tai_count / total
    prediction = 'Tài' if prob_tai > 0.5 else 'Xỉu'
    confidence = max(prob_tai, 1 - prob_tai)
    return prediction, confidence

def get_logistic_features(history_str, dice_history_list):
    if not history_str: return [0.0] * 8
    streak = 0.0
    if len(history_str) > 1:
        last = history_str[-1]
        for i in range(len(history_str) - 2, -1, -1):
            if history_str[i] == last:
                streak += 1
            else:
                break
        streak +=1
    h_20 = history_str[-20:]
    balance = (h_20.count('Tài') - h_20.count('Xỉu')) / max(1, len(h_20))
    changes = sum(1 for i in range(len(h_20)-1) if h_20[i] != h_20[i+1])
    volatility = changes / max(1, len(h_20) - 1) if len(h_20) > 1 else 0.0
    h_100 = history_str[-100:]
    long_balance = (h_100.count('Tài') - h_100.count('Xỉu')) / max(1, len(h_100))
    lag_1 = 1.0 if len(history_str) >= 1 and history_str[-1] == 'Tài' else 0.0
    lag_2 = 1.0 if len(history_str) >= 2 and history_str[-2] == 'Tài' else 0.0
    last_dice = dice_history_list[-1] if dice_history_list else [0, 0, 0]
    sum_of_dice = sum(last_dice)
    is_sum_even = 1.0 if sum_of_dice % 2 == 0 else 0.0
    normalized_sum = (sum_of_dice - 3) / 15.0 if sum_of_dice > 0 else 0.5
    return [streak, balance, volatility, long_balance, lag_1, lag_2, is_sum_even, normalized_sum]

def predict_advanced(app, history_str, dice_history_list):
    """Hàm điều phối dự đoán nâng cao, kết hợp các mô hình."""
    # SỬA LỖI: Luôn trả về 5 giá trị để tránh ValueError
    if len(history_str) < 5:
        # Trả về 5 giá trị với các placeholder phù hợp
        return "Chờ dữ liệu", "Chưa đủ dữ liệu", 50.0, {}, []

    # --- Model 1: Pattern Matching (Học tự động) ---
    detected_pattern = detect_pattern(app, history_str)
    patt_pred, patt_conf = predict_with_pattern(app, history_str, detected_pattern)

    # --- Model 2: Markov Chain Bậc 2 ---
    markov_pred, markov_conf = predict_with_markov2(app, history_str)

    # --- Model 3: Logistic Regression ---
    features = get_logistic_features(history_str, dice_history_list)
    z = app.logistic_bias + sum(w * f for w, f in zip(app.logistic_weights, features))
    prob_tai_logistic = 1.0 / (1.0 + math.exp(-z))
    logistic_pred = 'Tài' if prob_tai_logistic > 0.5 else 'Xỉu'
    logistic_conf = max(prob_tai_logistic, 1 - prob_tai_logistic)

    # --- Ensemble Prediction (Sử dụng trọng số động) ---
    predictions = {
        'pattern': {'pred': patt_pred, 'conf': patt_conf, 'weight': app.model_weights['pattern']},
        'markov': {'pred': markov_pred, 'conf': markov_conf, 'weight': app.model_weights['markov']},
        'logistic': {'pred': logistic_pred, 'conf': logistic_conf, 'weight': app.model_weights['logistic']},
    }
    
    tai_score, xiu_score = 0.0, 0.0
    for model in predictions.values():
        score = model['conf'] * model['weight']
        if model['pred'] == 'Tài':
            tai_score += score
        else:
            xiu_score += score

    final_prediction = 'Tài' if tai_score > xiu_score else 'Xỉu'
    total_score = tai_score + xiu_score
    final_confidence = (max(tai_score, xiu_score) / total_score * 100) if total_score > 0 else 50.0
    
    used_pattern_name = detected_pattern['name'] if detected_pattern else "Ensemble"
    
    # Trả về cả dự đoán của từng mô hình để phục vụ việc học
    individual_preds = {name: {'pred': data['pred'], 'conf': data['conf']} for name, data in predictions.items()}
    
    return final_prediction, used_pattern_name, final_confidence, individual_preds, features

# --- Flask App Factory ---

def create_app():
    app = Flask(__name__)
    CORS(app)

    app.lock = threading.Lock()
    app.MAX_HISTORY_LEN = 200
    app.history = deque(maxlen=app.MAX_HISTORY_LEN)
    app.session_ids = deque(maxlen=app.MAX_HISTORY_LEN)
    app.dice_history = deque(maxlen=app.MAX_HISTORY_LEN)
    app.patterns = define_patterns()
    app.markov2_counts = defaultdict(lambda: defaultdict(int))
    app.logistic_weights = [0.0] * 8
    app.logistic_bias = 0.0
    app.learning_rate = 0.01
    app.regularization = 0.01
    app.initial_model_weights = {'pattern': 0.4, 'markov': 0.3, 'logistic': 0.3}
    app.model_weights = app.initial_model_weights.copy()
    app.model_performance = {'pattern': deque(maxlen=50), 'markov': deque(maxlen=50), 'logistic': deque(maxlen=50)}
    app.last_prediction = None
    app.pattern_accuracy = defaultdict(lambda: {"success": 0, "total": 0})
    app.pattern_outcomes = defaultdict(lambda: defaultdict(int))
    app.WS_URL = os.getenv("WS_URL", "ws://163.61.110.10:8000/game_sunwin/ws?id=duy914c&key=dduy1514nsadfl")

    def on_data(ws, data):
        try:
            message = json.loads(data)
            phien = message.get("Phien")
            if "Ket_qua" not in message or phien is None: return
            ket_qua = message.get("Ket_qua")
            dices = [message.get("Xuc_xac_1"), message.get("Xuc_xac_2"), message.get("Xuc_xac_3")]
            if ket_qua not in ["Tài", "Xỉu"] or None in dices: return
            with app.lock:
                if not app.session_ids or phien > app.session_ids[-1]:
                    app.session_ids.append(phien)
                    app.history.append({'ket_qua': ket_qua, 'phien': phien})
                    app.dice_history.append(dices)
                    history_str = _get_history_strings(app.history)
                    update_markov2_matrix(app, history_str)
                    logging.info(f"New result for session {phien}: {ket_qua} {dices}")
        except (json.JSONDecodeError, TypeError): pass
        except Exception as e:
            logging.error(f"Error in on_data: {e}")

    def on_error(ws, error): logging.error(f"WebSocket error: {error}")
    def on_close(ws, close_status_code, close_msg): logging.info(f"WebSocket closed. Reconnecting...")
    def on_open(ws): logging.info("WebSocket connection opened.")
    def start_ws():
        while True:
            logging.info(f"Connecting to WebSocket: {app.WS_URL}")
            try:
                ws = websocket.WebSocketApp(app.WS_URL, on_open=on_open, on_message=on_data, on_error=on_error, on_close=on_close)
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                 logging.error(f"WebSocket run_forever crashed: {e}")
            time.sleep(5)

    @app.route("/api/taixiu_ws", methods=["GET"])
    def get_taixiu_ws_prediction():
        with app.lock:
            # Điều kiện nên >= 5 để phù hợp với predict_advanced
            if len(app.history) < 5:
                return jsonify({"error": "Chưa có đủ dữ liệu (cần ít nhất 5 phiên)"}), 500
            history_copy = list(app.history)
            dice_history_copy = list(app.dice_history)
            session_ids_copy = list(app.session_ids)
            last_prediction_copy = app.last_prediction
        
        actual_result = history_copy[-1]['ket_qua']
        if last_prediction_copy and last_prediction_copy['session'] == session_ids_copy[-1]:
            with app.lock:
                # Chỉ học khi có đủ features
                if last_prediction_copy['features']:
                    train_logistic_regression(app, last_prediction_copy['features'], actual_result)
                train_pattern_model(app, last_prediction_copy['pattern'], last_prediction_copy['prediction'], actual_result)
                for name, pred_data in last_prediction_copy['individual_preds'].items():
                    is_correct = 1 if pred_data['pred'] == actual_result else 0
                    app.model_performance[name].append(is_correct)
            logging.info(f"Learned from session {session_ids_copy[-1]}. Prediction was {last_prediction_copy['prediction']}, actual was {actual_result}.")
        
        with app.lock:
            update_dynamic_model_weights(app)

        prediction_str, pattern_str, confidence, individual_preds, features = predict_advanced(app, _get_history_strings(history_copy), dice_history_copy)
        
        with app.lock:
            current_session = session_ids_copy[-1]
            app.last_prediction = {
                'session': current_session + 1,
                'prediction': prediction_str,
                'pattern': pattern_str,
                'features': features,
                'individual_preds': individual_preds
            }
            current_result = history_copy[-1]['ket_qua']

        return jsonify({
            "Dữ Liệu : WangLin": "WangLin Api Dự Đoán (v2.1 - Patched)",
            "current_session": current_session,
            "current_result": current_result,
            "next_session": current_session + 1,
            "prediction": prediction_str,
            "based_on_pattern": pattern_str,
            "confidence_percent": round(confidence, 2),
            "model_weights": {k: round(v, 3) for k, v in app.model_weights.items()}
        })

    @app.route("/api/history", methods=["GET"])
    def get_history_api():
        with app.lock:
            return jsonify({ "history": list(app.history), "length": len(app.history) })

    @app.route("/api/performance", methods=["GET"])
    def get_performance():
        with app.lock:
            seen_patterns = {k: v for k, v in app.pattern_accuracy.items() if v['total'] > 0}
            sorted_patterns = sorted(seen_patterns.items(), key=lambda item: item[1]['total'], reverse=True)
            result = {}
            for p_type, data in sorted_patterns:
                total, success = data["total"], data["success"]
                accuracy = round(success / total * 100, 2) if total > 0 else 0
                outcomes = app.pattern_outcomes.get(p_type, {})
                result[p_type] = { "total": total, "success": success, "accuracy_percent": accuracy, "outcomes": outcomes }
        return jsonify(result)

    ws_thread = threading.Thread(target=start_ws, daemon=True)
    ws_thread.start()
    return app

# --- Thực thi chính ---
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Flask app (v2.1 - Patched) ready. Serving on http://0.0.0.0:{port}")
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, threads=8)