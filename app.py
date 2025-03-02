import requests
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, JobQueue
from datetime import datetime
import warnings
import threading
from flask import Flask, jsonify
import pytz
from binance.client import Client  # Thêm python-binance

warnings.filterwarnings("ignore", category=UserWarning)

# Binance Futures API (public)
BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1/ticker/price"
BINANCE_24H_API = "https://fapi.binance.com/fapi/v1/ticker/24hr"

# Binance API Key và Secret (cần thay bằng của bạn)
BINANCE_API_KEY = "PTJ7sV7LzIzyOnoq3eAZcWCH20XGGX0Vyr77eddIYaWdG0bxGotZQw51ZIQOutKW"
BINANCE_API_SECRET = "rGagN1zhh6zmXmbRWYYQYomg7WHQxfLDF0urQn0ink8biKO06xnISg1eiRIIfagy"

# Telegram config
TELEGRAM_TOKEN = "7528038148:AAFaLLQkc5EXgFLvXDHSSGFVcn1UYYOw8Tw"

# Flask app
app = Flask(__name__)

# Dictionary để lưu job theo chat_id và coin hoặc chức năng
active_jobs = {}  # Format: {(chat_id, coin hoặc "pnl"): job_object}

# Khởi tạo Binance client
binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)


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


# Hàm gửi giá tự động mỗi 3 phút, thêm ngày giờ
def auto_price(context):
    job = context.job
    chat_id = job.context["chat_id"]
    coin = job.context["coin"]

    current_price = get_futures_price(coin)
    current_time = datetime.now(pytz.timezone("Asia/Ho_Chi_Minh")).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    if current_price is not None:
        change_1h = get_price_change_1h(coin)
        reply = f"📅 **{current_time}**\nGiá {coin}/USDT: **${current_price}**\n"
    else:
        reply = f"📅 **{current_time}**\nKhông lấy được giá {coin}, kiểm tra lại bro!"

    context.bot.send_message(chat_id=chat_id, text=reply, parse_mode="Markdown")


# Hàm lấy PNL của các vị thế đang mở
def get_pnl():
    try:
        positions = binance_client.futures_position_information()
        open_positions = [pos for pos in positions if float(pos["positionAmt"]) != 0]

        if not open_positions:
            return "Hiện tại không có vị thế nào đang mở bro!"

        reply = "📊 **PNL các vị thế đang mở**:\n"
        for pos in open_positions:
            symbol = pos["symbol"]
            unrealized_pnl = float(pos["unRealizedProfit"])
            entry_price = float(pos["entryPrice"])
            position_amt = float(pos["positionAmt"])
            leverage = int(pos["leverage"])
            reply += (
                f"- {symbol}: **{unrealized_pnl:.2f} USDT** "
                f"(Entry: {entry_price}, Số lượng: {position_amt}, Đòn bẩy: {leverage}x)\n"
            )
        return reply
    except Exception as e:
        return f"Lỗi khi lấy PNL: {str(e)}"


# Hàm gửi PNL tự động mỗi 3 phút
def auto_pnl(context):
    job = context.job
    chat_id = job.context["chat_id"]
    current_time = datetime.now(pytz.timezone("Asia/Ho_Chi_Minh")).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    pnl_info = get_pnl()
    context.bot.send_message(
        chat_id=chat_id,
        text=f"📅 **{current_time}**\n{pnl_info}",
        parse_mode="Markdown",
    )


# Command /start
def start(update, context):
    update.message.reply_text(
        "Yo bro! Gửi tao tên coin (ETH, SOL, DOGE) để xem giá, hoặc dùng /auto <coin> để nhận giá mỗi 3 phút! "
        "Dùng /pnl để xem PNL, /cancel <coin hoặc pnl> để hủy."
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

    job_key = (chat_id, coin)
    if job_key in active_jobs:
        update.message.reply_text(f"Đã có auto cho {coin} rồi bro, chill thôi!")
        return

    job = context.job_queue.run_repeating(
        auto_price, interval=180, first=0, context={"chat_id": chat_id, "coin": coin}
    )
    active_jobs[job_key] = job
    update.message.reply_text(f"Đã set auto giá {coin} mỗi 3 phút, chill đi bro!")


# Command /pnl
def pnl(update, context):
    chat_id = update.message.chat_id
    job_key = (chat_id, "pnl")

    if job_key in active_jobs:
        update.message.reply_text("Đã set auto PNL rồi bro, chill đi!")
        return

    pnl_info = get_pnl()
    update.message.reply_text(pnl_info, parse_mode="Markdown")

    job = context.job_queue.run_repeating(
        auto_pnl, interval=180, first=0, context={"chat_id": chat_id}
    )
    active_jobs[job_key] = job
    update.message.reply_text("Đã set auto PNL mỗi 3 phút, chill đi bro!")


# Command /cancel
def cancel(update, context):
    if len(context.args) != 1:
        update.message.reply_text(
            "Dùng: /cancel <coin hoặc pnl>, ví dụ /cancel ETH hoặc /cancel pnl"
        )
        return

    target = context.args[0].upper() if context.args[0].lower() != "pnl" else "pnl"
    chat_id = update.message.chat_id
    job_key = (chat_id, target)

    if job_key in active_jobs:
        job = active_jobs[job_key]
        job.schedule_removal()
        del active_jobs[job_key]
        update.message.reply_text(f"Đã hủy auto {target}, nghỉ ngơi chút đi bro!")
    else:
        update.message.reply_text(
            f"Chưa set auto cho {target} mà bro, thử /auto hoặc /pnl trước đi!"
        )


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
    dp.add_handler(CommandHandler("pnl", pnl))  # Thêm handler cho /pnl
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    updater.start_polling()
    updater.idle()


# Flask endpoint
@app.route("/")
def home():
    return jsonify(
        {"message": "Bot is running!", "timestamp": datetime.now().isoformat()}
    )


@app.route("/price/<coin>")
def get_price(coin):
    current_price = get_futures_price(coin)
    if current_price is not None:
        change_1h = get_price_change_1h(coin)
        return jsonify(
            {
                "coin": coin.upper(),
                "price": current_price,
                "change_1h": change_1h if change_1h is not None else "Error",
            }
        )
    return jsonify({"error": f"Could not fetch price for {coin}"}), 400


# Main
if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    app.run(host="0.0.0.0", port=5000)
