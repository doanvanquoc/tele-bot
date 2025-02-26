import requests
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, JobQueue
from datetime import datetime
import warnings
import threading
from flask import Flask, jsonify
import pytz

warnings.filterwarnings("ignore", category=UserWarning)

# Binance Futures API
BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1/ticker/price"
BINANCE_24H_API = "https://fapi.binance.com/fapi/v1/ticker/24hr"

# Telegram config
TELEGRAM_TOKEN = "7528038148:AAFaLLQkc5EXgFLvXDHSSGFVcn1UYYOw8Tw"

# Flask app
app = Flask(__name__)

# Dictionary để lưu job theo chat_id và coin
active_jobs = {}  # Format: {(chat_id, coin): job_object}

# Lấy giá futures hiện tại
def get_futures_price(coin_symbol):
    try:
        symbol = f"{coin_symbol.upper()}USDT"
        response = requests.get(BINANCE_FUTURES_API, params={"symbol": symbol})
        data = response.json()
        return float(data["price"])
    except Exception:
        return None

# Lấy biến động 1h (ước lượng từ 24h API)
def get_price_change_1h(coin_symbol):
    try:
        symbol = f"{coin_symbol.upper()}USDT"
        response = requests.get(BINANCE_24H_API, params={"symbol": symbol})
        data = response.json()

        current_price = float(data["lastPrice"])
        high_price_24h = float(data["highPrice"])
        low_price_24h = float(data["lowPrice"])

        avg_price_24h = (high_price_24h + low_price_24h) / 2
        change_1h = ((current_price - avg_price_24h) / avg_price_24h) * 100 * 24
        return change_1h
    except Exception:
        return None

# Chọn 1 icon cho mỗi mức thay đổi
def get_change_icon(percentage):
    if percentage >= 50:
        return "🌩️"  # Tăng cực mạnh
    elif percentage >= 10:
        return "🌞"  # Tăng mạnh
    elif percentage > 0:
        return "🌱"  # Tăng nhẹ
    elif percentage <= -50:
        return "💣"  # Giảm cực mạnh
    elif percentage <= -10:
        return "☔"  # Giảm mạnh
    elif percentage < 0:
        return "🍂"  # Giảm nhẹ
    else:
        return "🌗"  # Không đổi

# Hàm gửi giá tự động mỗi 1h, thêm ngày giờ
def auto_price(context):
    job = context.job
    chat_id = job.context["chat_id"]
    coin = job.context["coin"]

    current_price = get_futures_price(coin)
    current_time = datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")  # Lấy ngày giờ hiện tại

    if current_price is not None:
        change_1h = get_price_change_1h(coin)
        reply = f"📅 **{current_time}**\nGiá {coin}/USDT: **${current_price}**\n"
    else:
        reply = f"📅 **{current_time}**\nKhông lấy được giá {coin}, kiểm tra lại bro!"

    context.bot.send_message(chat_id=chat_id, text=reply, parse_mode="Markdown")

# Command /start
def start(update, context):
    update.message.reply_text(
        "Yo bro! Gửi tao tên coin (ETH, SOL, DOGE) để xem giá, hoặc dùng /auto <coin> để nhận giá mỗi 1h! Muốn hủy thì /cancel <coin>."
    )

# Command /auto
def auto(update, context):
    if len(context.args) != 1:
        update.message.reply_text("Dùng: /auto <coin>, ví dụ /auto ETH")
        return

    coin = context.args[0].upper()
    chat_id = update.message.chat_id

    if get_futures_price(coin) is None:
        update.message.reply_text(
            f"Coin {coin} không hợp lệ hoặc lỗi API, thử lại bro!"
        )
        return

    # Kiểm tra nếu đã có job cho coin này
    job_key = (chat_id, coin)
    if job_key in active_jobs:
        update.message.reply_text(f"Đã có auto cho {coin} rồi bro, chill thôi!")
        return

    # Tạo job mới
    job = context.job_queue.run_repeating(
        auto_price, interval=300, first=0, context={"chat_id": chat_id, "coin": coin}
    )
    active_jobs[job_key] = job  # Lưu job vào dictionary
    update.message.reply_text(f"Đã set auto giá {coin} mỗi 10p, chill đi bro!")

# Command /cancel
def cancel(update, context):
    if len(context.args) != 1:
        update.message.reply_text("Dùng: /cancel <coin>, ví dụ /cancel ETH")
        return

    coin = context.args[0].upper()
    chat_id = update.message.chat_id
    job_key = (chat_id, coin)

    if job_key in active_jobs:
        job = active_jobs[job_key]
        job.schedule_removal()  # Xóa job khỏi queue
        del active_jobs[job_key]  # Xóa khỏi dictionary
        update.message.reply_text(f"Đã hủy auto giá {coin}, nghỉ ngơi chút đi bro!")
    else:
        update.message.reply_text(f"Chưa set auto cho {coin} mà bro, thử /auto trước đi!")

# Xử lý tin nhắn thường
def handle_message(update, context):
    coin = update.message.text.strip().upper()
    current_price = get_futures_price(coin)

    if current_price is not None:
        change_1h = get_price_change_1h(coin)
        reply = f"Giá {coin}/USDT: **${current_price}**\n"
    else:
        update.message.reply_text(
            f"Không tìm thấy coin {coin} hoặc lỗi API, thử lại bro!"
        )
    update.message.reply_text(reply, parse_mode="Markdown")

# Hàm chạy Telegram bot
def run_bot():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("auto", auto))
    dp.add_handler(CommandHandler("cancel", cancel))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    updater.start_polling()
    updater.idle()

# Flask endpoint để giả lập web service
@app.route('/')
def home():
    return jsonify({"message": "Bot is running!", "timestamp": datetime.now().isoformat()})

@app.route('/price/<coin>')
def get_price(coin):
    current_price = get_futures_price(coin)
    if current_price is not None:
        change_1h = get_price_change_1h(coin)
        return jsonify({
            "coin": coin.upper(),
            "price": current_price,
            "change_1h": change_1h if change_1h is not None else "Error"
        })
    return jsonify({"error": f"Could not fetch price for {coin}"}), 400

# Main
if __name__ == "__main__":
    # Chạy bot trong một thread riêng
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True  # Thread sẽ dừng khi chương trình chính dừng
    bot_thread.start()

    # Chạy Flask app cho web service
    app.run(host="0.0.0.0", port=5000)  # Đảm bảo phù hợp với cấu hình Render
