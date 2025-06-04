import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, JobQueue
from datetime import datetime
import warnings
import threading
from flask import Flask, jsonify
import pytz
from binance.client import Client
from binance.streams import ThreadedWebsocketManager  # 🆕 Thêm dòng này
import requests
import time

warnings.filterwarnings("ignore", category=UserWarning)

BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1/ticker/price"
BINANCE_24H_API = "https://fapi.binance.com/fapi/v1/ticker/24hr"

# Binance API Key và Secret
BINANCE_API_KEY = "PTJ7sV7LzIzyOnoq3eAZcWCH20XGGX0Vyr77eddIYaWdG0bxGotZQw51ZIQOutKW"
BINANCE_API_SECRET = "rGagN1zhh6zmXmbRWYYQYomg7WHQxfLDF0urQn0ink8biKO06xnISg1eiRIIfagy"

# Telegram config
TELEGRAM_TOKEN = "7712968058:AAH5eAsVseRQbFHViMfJvs3YRQQaSx5k76c"

# Flask app
app = Flask(__name__)

# Dictionary để lưu job theo chat_id và coin hoặc chức năng
active_jobs = {}

# Khởi tạo Binance client
binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# --- START: Websocket & Real-time PNL Handler --- 🆕
twm = ThreadedWebsocketManager(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
twm.start()

# Biến để lưu danh sách người đăng ký nhận PNL
subscribed_chat_ids = set()

def calculate_and_send_pnl(update_dict):
    if update_dict["e"] == "ACCOUNT_UPDATE":
        positions = update_dict["a"]["P"]
        messages = []

        for pos in positions:
            amt = float(pos["pa"])
            if amt == 0:
                continue
            symbol = pos["s"].replace("USDT", "")
            entry = float(pos["ep"])
            mark = float(pos["mp"])
            pnl = (mark - entry) * amt if amt > 0 else (entry - mark) * abs(amt)
            messages.append(f"{symbol}: **{pnl:.2f} USDT**")

        if messages:
            text = "\n".join(messages)
            for chat_id in subscribed_chat_ids:
                try:
                    bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
                except Exception as e:
                    print(f"Send error: {e}")

def start_user_data_stream():
    listen_key = binance_client.futures_stream_get_listen_key()
    twm.start_futures_user_socket(callback=calculate_and_send_pnl, listen_key=listen_key)

    # Keep-alive listen_key mỗi 30 phút
    def keepalive():
        while True:
            try:
                binance_client.futures_stream_keepalive(listen_key)
            except Exception as e:
                print("Keepalive failed:", e)
            time.sleep(1800)

    threading.Thread(target=keepalive, daemon=True).start()

# Gọi khi start bot
start_user_data_stream()
# --- END: Websocket & Real-time PNL Handler --- 🆕

# Lấy giá futures hiện tại
def get_futures_price(coin_symbol):
    try:
        symbol = f"{coin_symbol.upper()}USDT"
        response = requests.get(BINANCE_FUTURES_API, params={"symbol": symbol})
        data = response.json()
        return float(data["price"])
    except Exception:
        return None

# Lấy biến động 1h
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

# Gửi giá mỗi 3 phút
def auto_price(context):
    job = context.job
    chat_id = job.context["chat_id"]
    coin = job.context["coin"]
    current_price = get_futures_price(coin)
    
    if current_price is not None:
        reply = f"{coin}: **${current_price}**"
    else:
        reply = f"Không lấy được giá {coin}"
    context.bot.send_message(chat_id=chat_id, text=reply, parse_mode="Markdown")

# Gửi PNL mỗi 3 phút (legacy)
def get_pnl():
    try:
        positions = binance_client.futures_position_information()
        open_positions = [pos for pos in positions if float(pos["positionAmt"]) != 0]
        
        if not open_positions:
            return "Hiện tại không có vị thế nào đang mở bro!"
        
        reply = ""
        for pos in open_positions:
            symbol = pos["symbol"].replace("USDT", "")
            unrealized_pnl = float(pos["unRealizedProfit"])
            reply += f"{symbol}: **{unrealized_pnl:.2f} USDT**\n"
        return reply
    except Exception as e:
        return f"Lỗi khi lấy PNL: {str(e)}"

def auto_pnl(context):
    job = context.job
    chat_id = job.context["chat_id"]
    pnl_info = get_pnl()
    context.bot.send_message(chat_id=chat_id, text=pnl_info, parse_mode="Markdown")

def start(update, context):
    update.message.reply_text(
        "Yo bro! Gửi tao tên coin (ETH BTC LTC) để xem giá, hoặc dùng /auto <coin> để nhận giá mỗi 3 phút! "
        "Gõ 'pnl' để xem PNL 1 lần, /pnl để nhận realtime PNL, /cancel <coin hoặc pnl> để hủy, /clear để xóa tin nhắn."
    )

def clear(update, context):
    chat_id = update.message.chat_id
    try:
        context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        update.message.reply_text("Đã xóa hết tin nhắn bro!", parse_mode="Markdown")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi xóa tin nhắn: {str(e)}", parse_mode="Markdown")

def auto(update, context):
    if len(context.args) != 1:
        update.message.reply_text("Dùng: /auto <coin>, ví dụ /auto ETH")
        return

    target = context.args[0].lower()
    chat_id = update.message.chat_id
    coin = target.upper()

    if get_futures_price(coin) is None:
        update.message.reply_text(f"Coin {coin} không hợp lệ hoặc lỗi API!")
        return
    job_key = (chat_id, coin)
    if job_key in active_jobs:
        update.message.reply_text(f"Đã có auto cho {coin} rồi bro!")
        return
    job = context.job_queue.run_repeating(
        auto_price, interval=300, first=0, context={"chat_id": chat_id, "coin": coin}
    )
    active_jobs[job_key] = job
    update.message.reply_text(f"Đã set auto giá {coin} mỗi 5 phút!")

def auto_pnl_command(update, context):
    chat_id = update.message.chat_id
    job_key = (chat_id, "pnl")
    if job_key in active_jobs:
        update.message.reply_text("Đã có auto PNL rồi bro!")
        return
    subscribed_chat_ids.add(chat_id)
    update.message.reply_text("Đã set auto PNL realtime bro! Hủy bằng /cancel pnl")

def cancel(update, context):
    if len(context.args) != 1:
        update.message.reply_text("Dùng: /cancel <coin hoặc pnl>")
        return

    target = context.args[0].upper() if context.args[0].lower() != "pnl" else "pnl"
    chat_id = update.message.chat_id
    job_key = (chat_id, target)

    if target == "pnl":
        subscribed_chat_ids.discard(chat_id)
        update.message.reply_text("Đã hủy auto PNL realtime!")
        return

    if job_key in active_jobs:
        job = active_jobs[job_key]
        job.schedule_removal()
        del active_jobs[job_key]
        update.message.reply_text(f"Đã hủy auto {target}!")
    else:
        update.message.reply_text(f"Chưa set auto cho {target}!")

def get_multiple_prices(coins):
    reply = ""
    for coin in coins:
        current_price = get_futures_price(coin)
        if current_price is not None:
            reply += f"{coin}: **${current_price}**\n"
        else:
            reply += f"Không tìm thấy coin {coin}\n"
    return reply

def handle_message(update, context):
    text = update.message.text.strip().lower()
    chat_id = update.message.chat_id

    if text == "pnl":
        pnl_info = get_pnl()
        update.message.reply_text(pnl_info, parse_mode="Markdown")
    else:
        coins = text.split()
        reply = get_multiple_prices([coin.upper() for coin in coins])
        update.message.reply_text(reply, parse_mode="Markdown")

# Bot khởi động
def run_bot():
    global bot
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    bot = updater.bot
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("clear", clear))
    dp.add_handler(CommandHandler("auto", auto))
    dp.add_handler(CommandHandler("pnl", auto_pnl_command))
    dp.add_handler(CommandHandler("cancel", cancel))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    updater.start_polling()
    updater.idle()

# Flask endpoint
@app.route("/")
def home():
    return jsonify({"message": "Bot is running!", "timestamp": datetime.now().isoformat()})

@app.route("/price/<coin>")
def get_price(coin):
    current_price = get_futures_price(coin)
    if current_price is not None:
        change_1h = get_price_change_1h(coin)
        return jsonify({"coin": coin.upper(), "price": current_price, "change_1h": change_1h if change_1h is not None else "Error"})
    return jsonify({"error": f"Could not fetch price for {coin}"}), 400

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    app.run(host="0.0.0.0", port=5000)
    
