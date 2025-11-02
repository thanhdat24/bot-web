import os
import re
import json
import requests
import concurrent.futures
from datetime import datetime
from flask import Flask, request, jsonify, make_response
from apscheduler.schedulers.background import BackgroundScheduler
import telebot
from telebot import types as ttypes

# ========= Cấu hình qua biến môi trường =========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()  # ví dụ https://your-domain.com/telegram
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "supersecret")  # tuỳ ý đặt
PORT = int(os.environ.get("PORT", "5000"))
RUN_SCHEDULER = os.environ.get("RUN_SCHEDULER", "1") == "1"  # chỉ 1 instance bật
DEFAULT_CHAT_ID = os.environ.get("DEFAULT_CHAT_ID", "").strip()
DEFAULT_TOKEN = os.environ.get("DEFAULT_TOKEN", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("Thiếu BOT_TOKEN (biến môi trường).")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
if DEFAULT_CHAT_ID and DEFAULT_TOKEN:
    user_tokens[DEFAULT_CHAT_ID] = DEFAULT_TOKEN
    print(f"✅ Nạp sẵn token môi trường cho chat_id {DEFAULT_CHAT_ID}")


# ========= API nguồn dữ liệu =========
LIST_API_URL_Dat = 'https://apidvc.cantho.gov.vn/pa/dossier/search?code=&spec=slice&page=0&size=20&sort=appointmentDate,asc&identity-number=&applicant-name=&identity-number-kha=&applicant-name-kha=&applicant-owner-name=&nation-id=&province-id=&district-id=&ward-id=&accepted-from=&accepted-to=&dossier-status=2,3,4,5,16,17,8,11,10,9&remove-status=0&filter-type=1&assignee-id=685fc98e49c5131dadc9758e&sender-id=&candidate-group-id=6836c073cfd0c57611ffb6b4&candidate-position-id=681acf200ba0691de878b438&candidate-group-parent-id=682d3c33c9e3cf7e4111847f&current-task-agency-type-id=68576ff99ca45c48a8e97d8d,0000591c4e1bd312a6f00004&bpm-name-id=&noidungyeucaugiaiquyet=&noidung=&taxCode=&resPerson=&extendTime=&applicant-organization=&filter-by-candidate-group=false&is-query-processing-dossier=false&approve-agencys-id=6836c073cfd0c57611ffb6b4,682d3c33c9e3cf7e4111847f&remind-id=&procedure-id=&vnpost-status-return-code=&paystatus=&process-id=&appointment-from=&appointment-to=&enable-approvaled-agency-tree-view=true'
LIST_API_URL_Sau = 'https://apidvc.cantho.gov.vn/pa/dossier/search?code=&spec=slice&page=0&size=20&sort=appointmentDate,asc&identity-number=&applicant-name=&identity-number-kha=&applicant-name-kha=&applicant-owner-name=&nation-id=&province-id=&district-id=&ward-id=&accepted-from=&accepted-to=&dossier-status=2,3,4,5,16,17&remove-status=0&filter-type=1&assignee-id=6867a8c8ee7546773abb419e&sender-id=&candidate-group-id=684ed450408f250a1932dd27&candidate-position-id=677dd2ff022b4b20dc5c787d&candidate-group-parent-id=682d3c33c9e3cf7e4111847f&current-task-agency-type-id=0000591c4e1bd312a6f00004,684bd0d7abb19b59e8bd2390&bpm-name-id=&noidungyeucaugiaiquyet=&noidung=&taxCode=&resPerson=&extendTime=&applicant-organization=&filter-by-candidate-group=false&is-query-processing-dossier=false&approve-agencys-id=684ed450408f250a1932dd27,682d3c33c9e3cf7e4111847f&remind-id=&procedure-id=&vnpost-status-return-code=&paystatus=&process-id=&appointment-from=&appointment-to=&enable-approvaled-agency-tree-view=true'

# ========= Bộ nhớ token theo chat =========
# KHÔNG log token nhạy cảm ở production
user_tokens = {}  # {str(chat_id): "Bearer ...token..."}

# ========= Flask app =========
app = Flask(__name__)

# --------- Helper: Chuẩn hoá chuỗi cookie -> "Bearer <token>" ----------
def normalize_to_bearer_token(raw):
    """
    Chuẩn hóa dữ liệu cookie/token thành dạng 'Bearer <token>'.

    Hỗ trợ các trường hợp:
    - Dữ liệu là bytes (từ file upload)
    - Cookie dạng JSON (Chrome export)
    - Cookie dạng text (nhiều dòng, có key=value)
    - Token đã sẵn 'Bearer ' hoặc chỉ chuỗi thuần
    """

    if not raw:
        return None

    # Nếu là bytes → decode UTF-8
    if isinstance(raw, bytes):
        try:
            raw = raw.decode('utf-8', errors='ignore')
        except Exception:
            raw = str(raw)

    text = str(raw).strip()
    if not text:
        return None

    # Nếu người dùng dán nguyên token Bearer rồi
    if text.lower().startswith("bearer "):
        return text.strip()

    # Nếu nội dung có dấu "{" → có thể là JSON
    if text.strip().startswith("{") and text.strip().endswith("}"):
        try:
            data = json.loads(text)
            # Nếu có access_token
            if "access_token" in data:
                return f"Bearer {data['access_token'].strip()}"
            # Nếu có token hay tương tự
            for k in ["token", "authorization", "auth"]:
                if k in data:
                    return f"Bearer {str(data[k]).strip()}"
        except Exception as e:
            print("normalize_to_bearer_token: JSON parse error", e)

    # Nếu cookie Chrome (nhiều dòng có name=value)
    if "session" in text.lower() or "=" in text:
        lines = text.splitlines()
        for line in lines:
            if "Bearer " in line:
                return line.strip()
            if "access_token" in line:
                token_part = line.split("=", 1)[-1].strip()
                return f"Bearer {token_part}"
        # fallback: lấy phần dài nhất (thường là token)
        parts = [p.strip() for p in text.replace("\r", "").replace("\n", " ").split(" ") if len(p.strip()) > 10]
        longest = max(parts, key=len) if parts else ""
        if longest:
            return f"Bearer {longest}"

    # Mặc định: thêm tiền tố Bearer
    return f"Bearer {text}"


# --------- CORS preflight ----------
@app.before_request
def handle_preflight():
    if request.method == 'OPTIONS':
        response = make_response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

# --------- Trang chủ: form nhập/đăng file cookie ----------
@app.route("/", methods=["GET"])
def index():
    # Trang HTML đơn giản: nhập chat_id + dán cookie hoặc upload file
    return """
<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<title>Nạp cookie/token vào bot</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: system-ui, sans-serif; max-width: 720px; margin: 24px auto; padding: 0 12px; }
  label { display:block; margin: 12px 0 6px; font-weight: 600; }
  textarea, input[type=text] { width: 100%; padding: 8px; }
  .hint { color:#666; font-size: 14px; }
  .box { border:1px solid #ddd; padding:16px; border-radius:8px; }
  button { padding: 10px 16px; }
</style>
</head>
<body>
  <h2>Nạp cookie/token vào bot</h2>
  <p class="hint">Bạn có thể dán trực tiếp cookie/token hoặc tải file (.txt/.json) chứa cookie/token.
  Server sẽ trích xuất token và lưu cho chat_id của bạn.</p>

<form class="box" action="/upload-cookie" method="POST" enctype="multipart/form-data">
    <!-- Chat ID được dùng mặc định từ server, không cần nhập -->


    <label>Dán cookie/token (tùy chọn)</label>
    <textarea name="cookie_text" rows="6" placeholder="Authorization: Bearer xxx ... hoặc access_token=xxx ... hoặc JSON {access_token: ...}"></textarea>

    <label>Hoặc tải file cookie (.txt/.json) (tùy chọn)</label>
    <input type="file" name="cookie_file" accept=".txt,.json">

    <p class="hint">Chỉ cần <b>một</b> trong hai: dán vào ô trên hoặc chọn file.</p>

    <button type="submit">Gửi</button>
  </form>
</body>
</html>
    """, 200

# --------- Upload cookie qua form (HTML) ----------
@app.route("/upload-cookie", methods=["POST"])
def upload_cookie():
    try:
        # Lấy chat_id từ form hoặc fallback sang DEFAULT_CHAT_ID
        chat_id = str((request.form.get("chat_id") or DEFAULT_CHAT_ID).strip())
        if not chat_id:
            return "Thiếu chat_id (và DEFAULT_CHAT_ID chưa được cấu hình).", 400

        # Lấy cookie từ text hoặc file
        cookie_text = request.form.get("cookie_text")
        file_storage = request.files.get("cookie_file")

        raw = None
        if file_storage and file_storage.filename:
            # Đọc nội dung file tải lên
            raw = file_storage.read()
        elif cookie_text:
            raw = cookie_text

        if not raw:
            return "Vui lòng dán cookie/token hoặc chọn file.", 400

        # Chuẩn hóa cookie/token sang định dạng Bearer
        bearer = normalize_to_bearer_token(raw)
        if not bearer:
            return "Không trích xuất được token hợp lệ từ nội dung cung cấp.", 400

        # Lưu token vào bộ nhớ RAM
        user_tokens[chat_id] = bearer

        # (Tuỳ chọn) Lưu file token local để debug
        filename = f"userToken_{chat_id}.txt"
        try:
            if os.path.exists(filename):
                os.remove(filename)
            with open(filename, "w", encoding="utf-8") as f:
                f.write(bearer)
        except Exception as file_err:
            print(f"[{chat_id}] Lỗi lưu file token: {file_err}")

        # Phản hồi HTML xác nhận thành công
        return f"""
<!doctype html>
<html lang="vi">
<meta charset="utf-8">
<body style="font-family:sans-serif;max-width:720px;margin:24px auto;padding:0 12px">
  <h3>✅ Đã lưu token cho chat_id {chat_id}</h3>
  <p>Token (ẩn bớt): {bearer[:25]}…</p>
  <p>Bạn có thể quay lại Telegram và gõ /content để xem bảng Dat & Sau.</p>
  <p><a href="/">⬅️ Quay lại trang chính</a></p>
</body>
</html>
        """, 200

    except Exception as e:
        print("❌ upload_cookie error:", e)
        return f"Lỗi xử lý: {e}", 500



# ========= Handlers Telegram =========
@bot.message_handler(commands=['start'])
def start_message(message):
    chat_id = str(message.chat.id)
    bot.reply_to(message, (
        "Chào! Hãy mở trang web của bot để gửi token: https://bot-web-osb4.onrender.com/"
        "Dùng /content để hiển thị bảng Dat & Sau. "
        "Bot sẽ gửi báo cáo mỗi 30 phút nếu được bật."
    ))

def send_long_message(bot_obj, chat_id, text, reply_to_message_id=None):
    if len(text) <= 4096:
        bot_obj.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
        return
    lines = text.split('\n')
    current_part = ""
    parts = []
    for line in lines:
        test_part = current_part + line + '\n'
        if len(test_part) > 4000:
            if current_part:
                parts.append(current_part.strip())
            current_part = line + '\n'
        else:
            current_part = test_part
    if current_part.strip():
        parts.append(current_part.strip())
    for i, part in enumerate(parts):
        reply_id = reply_to_message_id if i == 0 else None
        bot_obj.send_message(chat_id, part, reply_to_message_id=reply_id)

def fetch_dossier_data(url, headers, chat_id):
    try:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        api_data = r.json()
        return api_data.get('content', [])
    except Exception as e:
        print(f"[{chat_id}] Lỗi gọi API: {e}")
        return None

def build_table(content_array, now, chat_id, prefix):
    if not content_array:
        return f"📋 Kết quả cho {prefix}: Không có kết quả tìm kiếm.\n", ""
    table = []
    table.append(f"📋 Kết quả cho {prefix}:")
    table.append("STT | Mã hồ sơ | Thủ tục hành chính | Yêu cầu giải quyết | Thực hiện | Thời hạn | Link")
    table.append("-" * 120)
    for i, item in enumerate(content_array, 1):
        code = item.get('code', 'N/A')
        noidungyeucau = item.get('applicant', {}).get('data', {}).get('noidungyeucaugiaiquyet', 'N/A')
        thuc_hien = item.get('accepter', {}).get('fullname', 'N/A')
        appointment_date_str = item.get('appointmentDate', 'N/A')
        label = ""
        formatted_time = appointment_date_str
        if appointment_date_str and appointment_date_str != 'N/A':
            try:
                s = appointment_date_str.replace('.000+0700', '+07:00')
                dt = datetime.fromisoformat(s)
                dt_naive = dt.replace(tzinfo=None)
                formatted_time = dt_naive.strftime('%d/%m/%Y %H:%M:%S')
                delta = dt_naive - now
                if delta.total_seconds() <= 24 * 3600:
                    label = "🔥 Hỏa tốc"
                elif delta.days <= 3:
                    label = "⚠️ Khẩn"
            except Exception:
                pass
        time_with_label = f"{formatted_time} {label}".strip()
        dossier_id = item.get('id', '')
        procedure_id = item.get('procedure', {}).get('id', '')
        current_task = item.get('currentTask', [{}])
        if isinstance(current_task, list):
            task_id = current_task[0].get('id', '') if current_task else ''
        elif isinstance(current_task, dict):
            task_id = current_task.get('id', '')
        else:
            task_id = ''
        url = 'N/A'
        if dossier_id and procedure_id and task_id:
            url = (
                "https://motcua.cantho.gov.vn/vi/dossier/processing/"
                f"{dossier_id}?procedure={procedure_id}&task={task_id}&xpandStatus=false"
            )
        link_text = f"[View]({url})" if url != 'N/A' else 'N/A'
        line = f"{i} | {code} | {noidungyeucau[:20]}... | {noidungyeucau[:20]}... | {thuc_hien} | {time_with_label} | {link_text}"
        table.append(line)
    return "\n".join(table) + "\n", ""

def send_periodic_report(chat_id):
    token = user_tokens.get(chat_id)
    if not token:
        print(f"[{chat_id}] Không có token cho báo cáo định kỳ")
        return
    now = datetime.now()
    headers = {'Authorization': token, 'Content-Type': 'application/json'}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(fetch_dossier_data, LIST_API_URL_Dat, headers, chat_id)
        f2 = ex.submit(fetch_dossier_data, LIST_API_URL_Sau, headers, chat_id)
        content_array_dat = f1.result()
        content_array_sau = f2.result()
    error_dat, table_dat = "", ""
    if content_array_dat is None:
        error_dat = "❌ Lỗi gọi LIST API Dat\n"
    else:
        table_dat, _ = build_table(content_array_dat, now, chat_id, "Dat")
    error_sau, table_sau = "", ""
    if content_array_sau is None:
        error_sau = "❌ Lỗi gọi LIST API Sau\n"
    else:
        table_sau, _ = build_table(content_array_sau, now, chat_id, "Sau")
    bot.send_message(chat_id, f"🔔 Báo cáo định kỳ lúc {now.strftime('%H:%M:%S')} - Bảng Dat & Sau:")
    send_long_message(bot, chat_id, f"{error_dat}{table_dat}")
    send_long_message(bot, chat_id, f"{error_sau}{table_sau}")

def send_periodic_reports():
    for chat_id in list(user_tokens.keys()):
        try:
            send_periodic_report(chat_id)
        except Exception as e:
            print(f"Lỗi gửi báo cáo cho {chat_id}: {e}")

@bot.message_handler(commands=['content'])
def content_table(message):
    chat_id = str(message.chat.id)
    token = user_tokens.get(chat_id)
    if not token:
        bot.reply_to(message, "❌ Chưa có token. Gửi tại trang / hoặc /settoken.")
        return
    now = datetime.now()
    headers = {'Authorization': token, 'Content-Type': 'application/json'}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(fetch_dossier_data, LIST_API_URL_Dat, headers, chat_id)
        f2 = ex.submit(fetch_dossier_data, LIST_API_URL_Sau, headers, chat_id)
        content_array_dat = f1.result()
        content_array_sau = f2.result()
    error_dat, table_dat = "", ""
    if content_array_dat is None:
        error_dat = "❌ Lỗi gọi LIST API Dat\n"
    else:
        table_dat, _ = build_table(content_array_dat, now, chat_id, "Dat")
    error_sau, table_sau = "", ""
    if content_array_sau is None:
        error_sau = "❌ Lỗi gọi LIST API Sau\n"
    else:
        table_sau, _ = build_table(content_array_sau, now, chat_id, "Sau")
    bot.reply_to(message, "✅ Đang gửi bảng Dat & Sau (có thể chia nhiều tin nhắn)...")
    send_long_message(bot, chat_id, f"{error_dat}{table_dat}", message.message_id)
    send_long_message(bot, chat_id, f"{error_sau}{table_sau}")

# ========= Webhook endpoint =========
@app.route('/telegram', methods=['POST', 'GET'])
def telegram_webhook():
    # Telegram sẽ gửi POST; GET có thể dùng để health-check
    if request.method == 'GET':
        return "OK", 200
    # (Tuỳ chọn) xác thực secret header
    secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if secret != WEBHOOK_SECRET:
        return "Forbidden", 403
    try:
        update_json = request.get_data().decode('utf-8')
        update = ttypes.Update.de_json(json.loads(update_json))
        bot.process_new_updates([update])
    except Exception as e:
        print("Webhook error:", e)
        return "Bad Request", 400
    return "OK", 200

def start_scheduler_if_needed():
    if RUN_SCHEDULER:
        scheduler = BackgroundScheduler()
        scheduler.add_job(send_periodic_reports, 'interval', minutes=30, id='periodic_reports', replace_existing=True)
        scheduler.start()
        print("Scheduler started: 30-minute reports.")

def setup_webhook_if_needed():
    if WEBHOOK_URL:
        full_url = WEBHOOK_URL.rstrip('/')
        try:
            bot.remove_webhook()
        except Exception:
            pass
        bot.set_webhook(url=full_url, secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
        print(f"Webhook set to: {full_url} (secret header enabled)")
        return True
    return False

if __name__ == '__main__':
    use_webhook = setup_webhook_if_needed()
    start_scheduler_if_needed()
    if use_webhook:
        # Chạy như web server (PaaS sẽ gọi cổng PORT)
        app.run(host='0.0.0.0', port=PORT)
    else:
       # Dev local: không có WEBHOOK_URL thì dùng polling (vẫn mở Flask cho / và /upload-cookie)

        from threading import Thread
        Thread(target=lambda: app.run(host='0.0.0.0', port=PORT), daemon=True).start()
        print("Running long polling (no WEBHOOK_URL).")
        bot.infinity_polling(skip_pending=True, timeout=30)
