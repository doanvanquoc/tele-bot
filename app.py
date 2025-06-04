import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from datetime import datetime
import warnings
import threading
from flask import Flask, jsonify
import pytz
from binance.client import Client
# Thay đổi import này cho phiên bản 1.0.28
from binance import BinanceSocketManager
import requests
import json
import time

warnings.filterwarnings("ignore", category=UserWarning)

# Binance Futures API (public) - Vẫn giữ để lấy giá coin 1 lần
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
active_jobs = {}  # Format: {(chat_id, coin hoặc "pnl"): job_object}

# Khởi tạo Binance client (dùng cho REST API không liên quan đến PNL, ví dụ: lấy giá coin)
binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# --- GLOBAL VARIABLES FOR WEBSOCKET DATA ---
# Dictionary để lưu trữ dữ liệu PNL nhận được từ WebSocket
# Key: symbol, Value: { 'positionAmt': float, 'unRealizedProfit': float, ... }
current_pnl_data = {}
# Khóa để bảo vệ current_pnl_data khi cập nhật
pnl_data_lock = threading.Lock()
# --- END GLOBAL VARIABLES ---

# Lấy giá futures hiện tại (dùng REST API, không thay đổi)
def get_futures_price(coin_symbol):
    try:
        symbol = f"{coin_symbol.upper()}USDT"
        response = requests.get(BINANCE_FUTURES_API, params={"symbol": symbol})
        data = response.json()
        return float(data["price"])
    except Exception:
        return None

# Lấy biến động 1h (ước lượng từ 24h API, không thay đổi)
def get_price_change_1h(coin_symbol):
    try:
        symbol = f"{coin_symbol.upper()}USDT"
        response = requests.get(BINANCE_24H_API, params={"symbol": symbol})
        data = response.json()
        current_price = float(data["lastPrice"])
        high_price_24h = float(data["highPrice"])
        low_price_24h = float(data["lowPrice"])
        avg_price_24h = (high_price_24h + low_price_24h) / 2
        change_1h = ((current_price - avg_price_24h) / avg_price_24h) * 100
        return change_1h
    except Exception:
        return None

# --- WEBSOCKET HANDLERS ---
# Hàm này sẽ được gọi mỗi khi có tin nhắn từ WebSocket
def handle_socket_message(msg):
    global current_pnl_data

    # msg['data'] chứa dữ liệu thực tế từ Binance
    # Kiểm tra loại sự kiện 'e' (event type)
    if 'e' in msg['data']:
        event_type = msg['data']['e']
        
        # Xử lý sự kiện cập nhật tài khoản (ACCOUNT_UPDATE)
        if event_type == 'ACCOUNT_UPDATE' and 'a' in msg['data'] and 'P' in msg['data']['a']:
            # Lấy thông tin vị thế từ 'P'
            positions = msg['data']['a']['P']
            
            with pnl_data_lock: # Bảo vệ dữ liệu khi cập nhật
                for pos in positions:
                    symbol = pos['s']
                    position_amount = float(pos['pa'])
                    unrealized_profit = float(pos['up'])
                    
                    # Chỉ lưu trữ các vị thế đang mở (positionAmt != 0)
                    if position_amount != 0:
                        current_pnl_data[symbol] = {
                            'positionAmt': position_amount,
                            'unRealizedProfit': unrealized_profit,
                            'entryPrice': float(pos['ep']),
                            'markPrice': float(pos['mp']),
                            'liquidationPrice': float(pos['liqP'])
                        }
                    else:
                        # Nếu vị thế đóng, xóa khỏi dictionary
                        if symbol in current_pnl_data:
                            del current_pnl_data[symbol]
            # print(f"WebSocket PNL Updated: {current_pnl_data}") # Để debug

# Hàm chạy WebSocket client
def run_websocket_client():
    # Khởi tạo BinanceSocketManager với client hiện có
    bsm = BinanceSocketManager(binance_client)
    
    # Bắt đầu user data stream (cho tài khoản Futures)
    # Phương thức này sẽ xử lý việc lấy listenKey và gia hạn tự động
    conn_key = bsm.start_futures_user_socket(handle_socket_message)
    
    bsm.start() # Bắt đầu vòng lặp sự kiện WebSocket
    print("WebSocket client started.")
    
    # Giữ luồng sống
    while True:
        time.sleep(1)

# Hàm lấy PNL của các vị thế đang mở (Sử dụng dữ liệu từ WebSocket)
def get_pnl():
    global current_pnl_data
    
    with pnl_data_lock: # Đảm bảo đọc dữ liệu an toàn
        if not current_pnl_data:
            return "Hiện tại không có vị thế nào đang mở bro!"
        
        reply = ""
        total_pnl = 0.0
        
        # Duyệt qua dữ liệu PNL đã được cập nhật từ WebSocket
        for symbol, data in current_pnl_data.items():
            unrealized_pnl = data['unRealizedProfit']
            total_pnl += unrealized_pnl
            
            reply += f"{symbol.replace('USDT', '')}: **{unrealized_pnl:.2f} USDT** (Entry: {data['entryPrice']:.2f}, Mark: {data['markPrice']:.2f})\n"
        
        reply += f"\n**Tổng PNL: {total_pnl:.2f} USDT**"
        return reply
    
# Hàm gửi giá tự động mỗi 3 phút, chỉ gửi giá
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

# Hàm gửi PNL tự động mỗi 3 phút (sử dụng dữ liệu từ WebSocket)
def auto_pnl(context):
    job = context.job
    chat_id = job.context["chat_id"]
    pnl_info = get_pnl()
    context.bot.send_message(chat_id=chat_id, text=pnl_info, parse_mode="Markdown")

# Command /start
def start(update, context):
    update.message.reply_text(
        "Yo bro! Gửi tao tên coin (ETH BTC LTC) để xem giá, hoặc dùng /auto <coin> để nhận giá mỗi 3 phút! "
        "Gõ 'pnl' để xem PNL 1 lần, /pnl để auto PNL, /cancel <coin hoặc pnl> để hủy, /clear để xóa tin nhắn."
    )

# Command /clear - Xóa tất cả tin nhắn trong chat
def clear(update, context):
    chat_id = update.message.chat_id
    try:
        context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        update.message.reply_text("Đã xóa hết tin nhắn bro!", parse_mode="Markdown")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi xóa tin nhắn: {str(e)}", parse_mode="Markdown")

# Command /auto
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

# Command /pnl (auto PNL)
def auto_pnl_command(update, context):
    chat_id = update.message.chat_id
    job_key = (chat_id, "pnl")
    if job_key in active_jobs:
        update.message.reply_text("Đã có auto PNL rồi bro!")
        return
    job = context.job_queue.run_repeating(
        auto_pnl, interval=600, first=0, context={"chat_id": chat_id}
    )
    active_jobs[job_key] = job
    update.message.reply_text("Đã set auto PNL mỗi 10 phút!")

# Command /cancel
def cancel(update, context):
    if len(context.args) != 1:
        update.message.reply_text("Dùng: /cancel <coin hoặc pnl>")
        return

    target = context.args[0].upper() if context.args[0].lower() != "pnl" else "pnl"
    chat_id = update.message.chat_id
    job_key = (chat_id, target)

    if job_key in active_jobs:
        job = active_jobs[job_key]
        job.schedule_removal()
        del active_jobs[job_key]
        update.message.reply_text(f"Đã hủy auto {target}!")
    else:
        update.message.reply_text(f"Chưa set auto cho {target}!")

# Hàm lấy giá của nhiều coin (dùng REST API)
def get_multiple_prices(coins):
    reply = ""
    for coin in coins:
        current_price = get_futures_price(coin)
        if current_price is not None:
            reply += f"{coin}: **${current_price}**\n"
        else:
            reply += f"Không tìm thấy coin {coin}\n"
    return reply

# Xử lý tin nhắn thường
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

# Hàm chạy Telegram bot
def run_bot():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
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

# Main
if __name__ == "__main__":
    # Khởi tạo và chạy WebSocket client trong một luồng riêng
    websocket_thread = threading.Thread(target=run_websocket_client)
    websocket_thread.daemon = True # Đảm bảo luồng này sẽ kết thúc khi chương trình chính kết thúc
    websocket_thread.start()
    
    # Khởi tạo và chạy Telegram bot trong một luồng riêng
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Chạy Flask app
    app.run(host="0.0.0.0", port=5000)
