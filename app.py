import requests
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, JobQueue
from datetime import datetime
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

# Binance Futures API
BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1/ticker/price"
BINANCE_24H_API = "https://fapi.binance.com/fapi/v1/ticker/24hr"

# Telegram config
TELEGRAM_TOKEN = "7528038148:AAFaLLQkc5EXgFLvXDHSSGFVcn1UYYOw8Tw"


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


# Hàm gửi giá tự động mỗi 1h
def auto_price(context):
    job = context.job
    chat_id = job.context["chat_id"]
    coin = job.context["coin"]

    current_price = get_futures_price(coin)
    if current_price is not None:
        change_1h = get_price_change_1h(coin)
        reply = f"Giá {coin}/USDT: **${current_price:.2f}**\n"
        reply += (
            f"Biến động trong 1h {get_change_icon(change_1h)}: **{change_1h:.2f}%**"
            if change_1h is not None
            else "Biến động trong 1h ⚠️: Lỗi"
        )
    else:
        reply = f"Không lấy được giá {coin}, kiểm tra lại bro!"

    context.bot.send_message(chat_id=chat_id, text=reply, parse_mode="Markdown")


# Command /start
def start(update, context):
    update.message.reply_text(
        "Yo bro! Gửi tao tên coin (ETH, SOL, DOGE) để xem giá, hoặc dùng /auto <coin> để nhận giá mỗi 1h!"
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

    context.job_queue.run_repeating(
        auto_price, interval=3600, first=0, context={"chat_id": chat_id, "coin": coin}
    )
    update.message.reply_text(f"Đã set auto giá {coin} mỗi 1h, chill đi bro!")


# Xử lý tin nhắn thường
def handle_message(update, context):
    coin = update.message.text.strip().upper()
    current_price = get_futures_price(coin)

    if current_price is not None:
        change_1h = get_price_change_1h(coin)
        reply = f"Giá {coin}/USDT: **${current_price:.2f}**\n"
        reply += (
            f"Biến động trong 1h {get_change_icon(change_1h)}: **{change_1h:.2f}%**"
            if change_1h is not None
            else "Biến động trong 1h ⚠️: Lỗi"
        )
        update.message.reply_text(reply, parse_mode="Markdown")
    else:
        update.message.reply_text(
            f"Không tìm thấy coin {coin} hoặc lỗi API, thử lại bro!"
        )


# Main
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("auto", auto))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
