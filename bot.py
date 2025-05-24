from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    JobQueue,
)
import logging
import time
import re
import os
from bs4 import BeautifulSoup
import requests
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Токен вашего бота
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Ссылка на экспорт таблицы в CSV
HTML_URL = os.getenv("HTML_URL")

# ID целевой группы (если нужно пересылать сообщения)
TARGET_GROUP_ID = -1002382138419

# Разрешенные ID групп
ALLOWED_CHAT_IDS = [-1002201488475, -1002437528572, -1002385047417, -1002382138419]

# Время в секундах (45 минут = 2700 секунд)
PINNED_DURATION = 2700

# Разрешенный пользователь для сброса таймера
ALLOWED_USER = "@Muzikant1429"

# Список запрещенных слов (антимат) - ищет частичное совпадение
BANNED_WORDS = ["бляд", "хуй", "пизд", "наху", "гандон", "пидр", "пидорас", "пидар", 
                "шалав", "шлюх", "мразь", "мразо", "ебат", "ебал", "дебил", "имебецил", "говнюк"]

# Ключевые слова для мессенджеров и ссылок
MESSENGER_KEYWORDS = [
    "t.me", "telegram", "whatsapp", "viber", "discord", "vk.com", "instagram",
    "facebook", "twitter", "youtube", "http", "www", ".com", ".ru", ".net", "tiktok"
]

# Лимиты для антиспама
SPAM_LIMIT = 4  # Максимальное количество сообщений
SPAM_INTERVAL = 30  # Интервал в секундах
MUTE_DURATION = 900  # Время мута в секундах (15 минут)

user_message_history = {}  # {user_id: [(chat_id, message_id), ...]}
user_message_counts = {}  # {user_id: [timestamp1, timestamp2, ...]}
user_mute_times = {}  # {user_id: mute_end_time}

async def delete_all_user_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    if user_id in user_message_history:
        for chat_id, message_id in user_message_history[user_id]:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                logger.info(f"Удалено сообщение {message_id} пользователя {user_id} в чате {chat_id}.")
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения {message_id} пользователя {user_id}: {e}")
        user_message_history[user_id].clear()  # Очищаем историю после удаления


# Глобальные переменные для временного хранения данных
last_pinned_times = {}  # {chat_id: timestamp}
last_user_username = {}  # {chat_id: username}
last_zch_times = {}  # {chat_id: timestamp}
last_thanks_times = {}  # {chat_id: timestamp}
pinned_messages = {}  # {chat_id: message_id}

# Хранение данных о пользователях
active_users = {}  # {user_id: {"username": str, "delete_count": int, "timestamp": int}}
pinned_stats = {}  # {user_id: {"username": str, "count": int}}
banned_users = set()  # {user_id}
spammers = {}  # {user_id: {"count": int, "timestamp": int}}


async def check_admin_rights(context, chat_id):
    try:
        chat_member = await context.bot.get_chat_member(chat_id=chat_id, user_id=context.bot.id)
        return chat_member.status in ["administrator", "creator"]
    except Exception as e:
        logger.error(f"Ошибка при проверке прав администратора в чате {chat_id}: {e}")
        return False


# Функция для очистки текста
def clean_text(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split()).lower()

# Загрузка сообщений из Google таблицы
def load_star_messages_from_html():
    try:
        response = requests.get(HTML_URL)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        table_rows = soup.find_all("tr")
        
        star_messages = {}
        for row in table_rows[1:]:
            cols = row.find_all("td")
            if len(cols) < 3:
                continue

            keyword = clean_text(cols[0].text.strip())
            message = cols[1].text.strip()
            photo_url = cols[2].text.strip() if cols[2].text.strip().startswith("http") else None

            if keyword and message:
                star_messages[keyword] = {"message": message, "photo": photo_url}

        logger.info(f"Загружено {len(star_messages)} сообщений из HTML.")
        return star_messages
    except Exception as e:
        logger.error(f"Ошибка при загрузке данных из HTML: {e}")
        return {}

STAR_MESSAGES = load_star_messages_from_html()

# Проверка прав администратора
async def is_admin_or_musician(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id

    if chat_id not in ALLOWED_CHAT_IDS and chat_id != TARGET_GROUP_ID:
        return False

    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status in ["administrator", "creator"]:
            return True
    except Exception as e:
        logger.error(f"Ошибка при проверке прав пользователя {user_id}: {e}")

    if update.message.from_user.username == ALLOWED_USER[1:]:
        return True

    return False


async def send_correction_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        correction_message = await context.bot.send_message(
            chat_id=chat_id,
            text="Корректировка звезды часа от Админа."
        )
        context.job_queue.run_once(delete_system_message, 10, data=correction_message.message_id, chat_id=chat_id)
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения о корректировке: {e}")


# Удаление системных сообщений
async def delete_system_message(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try:
        await context.bot.delete_message(chat_id=job.chat_id, message_id=job.data)
    except Exception as e:
        logger.error(f"Ошибка при удалении системного сообщения: {e}")

# Открепление всех сообщений
async def unpin_all_messages(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    try:
        await context.bot.unpin_all_chat_messages(chat_id=chat_id)
        logger.info(f"Все сообщения откреплены в чате {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка при откреплении сообщений: {e}")

# Обработка нового закрепленного сообщения
async def process_new_pinned_message(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, text: str, current_time: int):
    try:
        text_cleaned = clean_text(text)
        search_words = text_cleaned.split()
        
        # Поиск совпадения в Google таблице
        target_message = None
        for word in search_words:
            if word in STAR_MESSAGES:
                target_message = STAR_MESSAGES[word]
                break

        # Закрепление сообщения
        await update.message.pin()
        last_pinned_times[chat_id] = current_time
        last_user_username[chat_id] = user.username if user.username else None

        # Обновление статистики пользователя
        if user.id not in pinned_stats:
            pinned_stats[user.id] = {"username": user.username, "count": 0}
        pinned_stats[user.id]["count"] += 1

        # Пересылка в целевую группу
        if chat_id != TARGET_GROUP_ID:
            if target_message:
                try:
                    if target_message["photo"]:
                        await context.bot.send_photo(
                            chat_id=TARGET_GROUP_ID,
                            photo=target_message["photo"]
                        )
                    forwarded_message = await context.bot.send_message(
                        chat_id=TARGET_GROUP_ID,
                        text=target_message["message"]
                    )
                    await forwarded_message.pin()
                except Exception as e:
                    logger.error(f"Ошибка при пересылке: {e}")
            else:
                new_text = text.replace("🌟 ", "").strip()
                try:
                    forwarded_message = await context.bot.send_message(
                        chat_id=TARGET_GROUP_ID,
                        text=new_text
                    )
                    await forwarded_message.pin()
                except Exception as e:
                    logger.error(f"Ошибка при пересылке: {e}")

        # Отправка фото в исходную группу
        if target_message and target_message["photo"] and chat_id != TARGET_GROUP_ID:
            try:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=target_message["photo"]
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке фото: {e}")

        # Установка таймера на открепление
        context.job_queue.run_once(unpin_all_messages, PINNED_DURATION, chat_id=chat_id)

    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}")

# Обработка повторного сообщения
async def process_duplicate_message(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, text: str, current_time: int):
    try:
        await update.message.delete()
        
        # Обновление статистики активных пользователей
        if user.id not in active_users:
            active_users[user.id] = {"username": user.username, "delete_count": 0, "timestamp": current_time}
        active_users[user.id]["delete_count"] += 1
        active_users[user.id]["timestamp"] = current_time

        await send_thanks_message(context, chat_id, user)
    except Exception as e:
        logger.error(f"Ошибка при обработке дубликата: {e}")

# Отправка благодарности
async def send_thanks_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user):
    current_time = time.time()
    if current_time - last_thanks_times.get(chat_id, 0) < 180:
        return

    last_user = last_user_username.get(chat_id, "")
    thanks_text = (
        f"{user.username if user.username else 'Пользователь'}, спасибо за бдительность! "
        f"Звезда часа уже закреплена пользователем @{last_user if last_user else 'ранее'}. "
        f"Надеюсь, в следующий раз именно Вы станете нашей 🌟!!!"
    )
    
    thanks_message = await context.bot.send_message(chat_id=chat_id, text=thanks_text)
    context.job_queue.run_once(delete_system_message, 180, data=thanks_message.message_id, chat_id=chat_id)
    last_thanks_times[chat_id] = current_time

# Команда /timer - сброс таймера закрепления
async def reset_pin_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return

    chat_id = update.message.chat.id
    last_pinned_times[chat_id] = 0
    await context.bot.unpin_all_chat_messages(chat_id=chat_id)
    
    response = await update.message.reply_text("Таймер закрепа сброшен.")
    context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
    await update.message.delete()

# Команда /del - удаление сообщений
async def delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return

    if not update.message.reply_to_message:
        response = await update.message.reply_text("Ответьте на сообщение для удаления.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return

    try:
        await update.message.reply_to_message.delete()
    except Exception as e:
        logger.error(f"Ошибка при удалении: {e}")
    
    await update.message.delete()

# Команда /liderX - топ пользователей по звездам
async def lider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = int(context.args[0]) if context.args else 1
    cutoff_time = time.time() - days * 86400
    
    # Фильтрация статистики по времени
    filtered_stats = {
        uid: data for uid, data in pinned_stats.items() 
        if data.get("timestamp", 0) >= cutoff_time
    }
    
    # Сортировка по количеству звезд
    sorted_users = sorted(
        filtered_stats.items(), 
        key=lambda x: x[1]["count"], 
        reverse=True
    )[:3]
    
    if not sorted_users:
        response = await update.message.reply_text("Нет данных за указанный период.")
    else:
        text = f"Топ участников за {days} д.:\n"
        for i, (user_id, data) in enumerate(sorted_users, start=1):
            text += f"{i}. @{data['username']} — {data['count']} 🌟\n"
        response = await update.message.reply_text(text)
    
    context.job_queue.run_once(delete_system_message, 180, data=response.message_id, chat_id=update.message.chat.id)
    await update.message.delete()

# Команда /zhX - последние звезды часа
async def zh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = int(context.args[0]) if context.args else 10
    text = "Последние ⭐️🕐:\n"
    
    # Здесь должна быть логика получения последних сообщений
    # Временное решение - просто выводим статистику
    for i, (user_id, data) in enumerate(list(pinned_stats.items())[:count], start=1):
        text += f"{i}. @{data['username']}\n"
    
    response = await update.message.reply_text(text)
    context.job_queue.run_once(delete_system_message, 180, data=response.message_id, chat_id=update.message.chat.id)
    await update.message.delete()

# Команда /activeX - самые активные пользователи
async def active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = int(context.args[0]) if context.args else 1
    cutoff_time = time.time() - days * 86400
    
    # Фильтрация активных пользователей
    filtered_active = {
        uid: data for uid, data in active_users.items() 
        if data["timestamp"] >= cutoff_time
    }
    
    # Сортировка по количеству удаленных сообщений
    sorted_active = sorted(
        filtered_active.items(), 
        key=lambda x: x[1]["delete_count"], 
        reverse=True
    )[:3]
    
    if not sorted_active:
        response = await update.message.reply_text("Нет активных пользователей.")
    else:
        text = f"Самые активные за {days} д.:\n"
        for i, (user_id, data) in enumerate(sorted_active, start=1):
            text += f"{i}. @{data['username']} — {data['delete_count']} раз\n"
        response = await update.message.reply_text(text)
    
    context.job_queue.run_once(delete_system_message, 180, data=response.message_id, chat_id=update.message.chat.id)
    await update.message.delete()

# Команда /ban - бан пользователя
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("❌ Только админы могут банить!")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return
    
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        banned_users.add(target_user.id)
        await context.bot.ban_chat_member(chat_id=update.message.chat.id, user_id=target_user.id)
        response = await update.message.reply_text(f"@{target_user.username} забанен.")
    elif context.args:
        try:
            user_id = int(context.args[0])
            banned_users.add(user_id)
            await context.bot.ban_chat_member(chat_id=update.message.chat.id, user_id=user_id)
            response = await update.message.reply_text(f"Пользователь {user_id} забанен.")
        except ValueError:
            response = await update.message.reply_text("Неверный ID пользователя.")
    else:
        response = await update.message.reply_text("Ответьте на сообщение или укажите ID.")
    
    context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
    await update.message.delete()

# Команда /deban - разбан пользователя
async def deban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("❌ Только админы могут разбанить!")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return
    
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        banned_users.discard(target_user.id)
        await context.bot.unban_chat_member(chat_id=update.message.chat.id, user_id=target_user.id)
        response = await update.message.reply_text(f"@{target_user.username} разбанен.")
    elif context.args:
        try:
            user_id = int(context.args[0])
            banned_users.discard(user_id)
            await context.bot.unban_chat_member(chat_id=update.message.chat.id, user_id=user_id)
            response = await update.message.reply_text(f"Пользователь {user_id} разбанен.")
        except ValueError:
            response = await update.message.reply_text("Неверный ID пользователя.")
    else:
        response = await update.message.reply_text("Ответьте на сообщение или укажите ID.")
    
    context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
    await update.message.delete()

# Команда /ban_list - список забаненных
async def ban_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not banned_users:
        response = await update.message.reply_text("Бан-лист пуст.")
    else:
        text = "Бан-лист:\n"
        for user_id in banned_users:
            text += f"- ID: {user_id}\n"
        response = await update.message.reply_text(text)
    
    context.job_queue.run_once(delete_system_message, 60, data=response.message_id, chat_id=update.message.chat.id)
    await update.message.delete()

# Команда /google - обновление Google таблицы
async def update_google_table(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return
    
    global STAR_MESSAGES
    STAR_MESSAGES = load_star_messages_from_html()
    
    if STAR_MESSAGES:
        response = await update.message.reply_text(f"Таблица обновлена. Записей: {len(STAR_MESSAGES)}")
    else:
        response = await update.message.reply_text("Ошибка загрузки таблицы")
    
    context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
    await update.message.delete()

# Основной обработчик сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    user = message.from_user
    chat_id = message.chat.id
    text = message.text
    current_time = time.time()

    # Проверка на бан
    if user.id in banned_users:
        await message.delete()
        return

    # Проверка разрешенных чатов
    if chat_id not in ALLOWED_CHAT_IDS and chat_id != TARGET_GROUP_ID:
        logger.warning(f"Сообщение из неразрешенного чата {chat_id}")
        return

    # Проверка текста сообщения
    if text is None:
        return

    # Антимат (ищет частичные совпадения)
    if text and any(bad_word in text.lower() for bad_word in BANNED_WORDS):
        await message.delete()
        warning = await context.bot.send_message(chat_id, "Использование мата запрещено!")
        context.job_queue.run_once(delete_system_message, 10, data=warning.message_id, chat_id=chat_id)
        return

    # Антиреклама
    if text and any(keyword in text.lower() for keyword in MESSENGER_KEYWORDS):
        await message.delete()
        warning = await context.bot.send_message(chat_id, "Реклама запрещена!")
        context.job_queue.run_once(delete_system_message, 10, data=warning.message_id, chat_id=chat_id)
        return

    # Проверка на спам (игнорируем для админов и музыканта)
    if not await is_admin_or_musician(update, context):
        if user.id not in user_message_counts:
            user_message_counts[user.id] = []
        user_message_counts[user.id] = [t for t in user_message_counts[user.id] if current_time - t < SPAM_INTERVAL]
        user_message_counts[user.id].append(current_time)

        if len(user_message_counts[user.id]) > SPAM_LIMIT:
            # Удаляем все сообщения пользователя
            await delete_all_user_messages(context, user.id)

            # Проверяем права администратора для мута
            mute_status = False
            try:
                await context.bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user.id,
                    permissions={"can_send_messages": False},
                    until_date=current_time + MUTE_DURATION
                )
                mute_status = True
                logger.info(f"Пользователь {user.username or 'анонимный'} замучен на 15 минут в чате {chat_id}.")
            except Exception as e:
                logger.error(f"Ошибка при муте пользователя {user.id} в чате {chat_id}: {e}")

            # Если мут не удался, добавляем пользователя в список для удаления сообщений
            if not mute_status:
                user_mute_times[user.id] = current_time + MUTE_DURATION
                logger.info(f"Пользователь {user.username or 'анонимный'} добавлен в список для удаления сообщений на 15 минут.")

            # Отправляем предупреждение
            warning_text = (
                f"{user.username or 'Уважаемый спамер'}, в связи с тем что вы захламляете группу, "
                f"все ваши сообщения были удалены. Пожалуйста, соблюдайте правила общения."
            )
            warning_message = await context.bot.send_message(chat_id=chat_id, text=warning_text)
            logger.info(f"Отправлено предупреждение спамеру {user.username or 'анонимному'} в чате {chat_id}.")

            # Удаляем предупреждение через 10 секунд
            context.job_queue.run_once(delete_system_message, 10, data=warning_message.message_id, chat_id=chat_id)

            # Очищаем счетчик сообщений спамера
            user_message_counts[user.id].clear()
            return

    # Обработка звезды часа
    if text and ("звезда" in text.lower() or "зч" in text.lower() or "🌟" in text):
        try:
            chat = await context.bot.get_chat(chat_id)
            pinned_message = chat.pinned_message

            if pinned_message is None:
                await process_new_pinned_message(update, context, chat_id, user, text, current_time)
            else:
                last_pinned_time = last_pinned_times.get(chat_id, 0)
                if current_time - last_pinned_time < PINNED_DURATION:
                    if not await is_admin_or_musician(update, context):
                        await process_duplicate_message(update, context, chat_id, user, text, current_time)
                    else:
                        await process_new_pinned_message(update, context, chat_id, user, text, current_time)
                        await send_correction_message(context, chat_id)
                else:
                    await process_new_pinned_message(update, context, chat_id, user, text, current_time)
        except Exception as e:
            logger.error(f"Ошибка обработки звезды: {e}")

# Запуск бота
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("timer", reset_pin_timer))
    application.add_handler(CommandHandler("del", delete_message))
    application.add_handler(CommandHandler("lider", lider))
    application.add_handler(CommandHandler("zh", zh))
    application.add_handler(CommandHandler("active", active))
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("deban", deban_user))
    application.add_handler(CommandHandler("ban_list", ban_list))
    application.add_handler(CommandHandler("google", update_google_table))
    
    # Обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    application.run_polling()
    logger.info("Бот запущен")

if __name__ == '__main__':
    main()
