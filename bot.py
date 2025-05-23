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
import psycopg2
import os
from bs4 import BeautifulSoup
import requests
from datetime import datetime, timedelta  # Добавьте timedelta в импорт
from psycopg2.extras import DictCursor

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Функция для очистки текста
def clean_text(text: str) -> str:
    if not text:
        return ""
    # Удаляем только лишние пробелы, но сохраняем эмодзи и специальные символы
    return " ".join(text.split()).lower()

# Токен вашего бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
    

# Ссылка на экспорт таблицы в CSV
HTML_URL = os.getenv("HTML_URL")

# ID целевой группы (если нужно пересылать сообщения)
TARGET_GROUP_ID = -1002437528572

# Разрешенные ID групп
ALLOWED_CHAT_IDS = [-1002201488475, -1002437528572, -1002385047417, -1002382138419]  # Замените на ID ваших групп

# Время в секундах (45 минут = 2700 секунд)
PINNED_DURATION = 2700  # Изменено на 45 минут

# Разрешенный пользователь для сброса таймера
ALLOWED_USER = "@Muzikant1429"

# Список запрещенных слов (антимат)
BANNED_WORDS = ["бляд", "хуй", "пизд", "наху", "гандон", "пидр", "пидорас","пидар", "шалав", "шлюх", "мразь", "мразо", "ебат", "дебил", "ебал", "имебецил", "говнюк"]

# Ключевые слова для мессенджеров и ссылок
MESSENGER_KEYWORDS = [
    "t.me", "telegram", "whatsapp", "viber", "discord", "vk.com", "instagram",
    "facebook", "twitter", "youtube", "http", "www", ".com", ".ru", ".net", "tiktok"
]

# Лимиты для антиспама
SPAM_LIMIT = 4  # Максимальное количество сообщений
SPAM_INTERVAL = 30  # Интервал в секундах
MUTE_DURATION = 900  # Время мута в секундах (15 минут)


# Глобальные переменные
last_pinned_times = {}  # {chat_id: timestamp}
last_user_username = {}  # {chat_id: username}
last_zch_times = {}  # {chat_id: timestamp}
last_thanks_times = {}  # {chat_id: timestamp}
pinned_messages = {}  # {chat_id: message_id}  # Добавлено

# Бан-лист
banned_users = set()

# База данных
# База данных
def get_db_connection():
    db_url = os.getenv("DATABASE_URL", "dbname=bot_database user=postgres")
    return psycopg2.connect(db_url, cursor_factory=DictCursor)


def init_db():
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pinned_messages (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT,
                user_id BIGINT,
                username TEXT,
                message_text TEXT,
                timestamp BIGINT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS active_users (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                delete_count INTEGER,
                timestamp BIGINT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS birthdays (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE,
                username TEXT,
                birth_date TEXT,
                last_congratulated_year INTEGER
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ban_list (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                phone TEXT,
                ban_time BIGINT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ban_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                reason TEXT,
                timestamp BIGINT
            )
        ''')
    conn.commit()
    conn.close()


init_db()


# Проверка прав администратора
async def is_admin_or_musician(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id

    # Проверяем, что сообщение пришло из разрешенной группы
    if chat_id not in ALLOWED_CHAT_IDS and chat_id != TARGET_GROUP_ID:
        logger.warning(f"Попытка доступа к командам из неизвестной группы {chat_id}.")
        return False

    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status in ["administrator", "creator"]:
            return True
    except Exception as e:
        logger.error(f"Ошибка при проверке прав пользователя {user_id}: {e}")

    # Проверяем специальное разрешение через ALLOWED_USER
    if update.message.from_user.username == ALLOWED_USER[1:]:
        return True

    return False

# импорт с гугл
def load_star_messages_from_html():
    try:
        # Получаем HTML-код таблицы
        response = requests.get(HTML_URL)
        response.raise_for_status()  # Проверяем успешность запроса

        # Парсим HTML с помощью BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")

        # Находим все строки таблицы
        table_rows = soup.find_all("tr")
        if not table_rows:
            logger.error("HTML-страница не содержит таблицы.")
            return {}

        star_messages = {}
        for row in table_rows[1:]:  # Пропускаем заголовок
            cols = row.find_all("td")
            if len(cols) < 3:  # Если строка не содержит нужных данных
                continue

            # Извлекаем ключевое слово, сообщение и фото
            keyword = clean_text(cols[0].text.strip()).lower()  # Первый столбец - ключевое слово
            message = cols[1].text.strip()  # Второй столбец - сообщение
            photo_url = cols[2].text.strip() if cols[2].text.strip().startswith("http") else None  # Третий столбец - фото

            # Добавляем в словарь
            if keyword and message:
                star_messages[keyword] = {"message": message, "photo": photo_url}

        logger.info(f"Загружено {len(star_messages)} сообщений из HTML.")
        return star_messages

    except Exception as e:
        logger.error(f"Ошибка при загрузке данных из HTML: {e}")
        return {}

# Загружаем сообщения при старте бота
STAR_MESSAGES = load_star_messages_from_html()
if not STAR_MESSAGES:
    logger.warning("Не удалось загрузить сообщения из гугл-таблицы. Будут использоваться только оригинальные сообщения.")

async def process_new_pinned_message(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, text: str, current_time: int):
    try:
        # Логируем оригинальный текст сообщения
        logger.info(f"Оригинальный текст сообщения: {text}")

        # Очищаем текст сообщения
        text_cleaned = clean_text(text)
        logger.info(f"Очищенный текст сообщения: {text_cleaned}")

        # Извлекаем ключевые слова для поиска
        search_words = text_cleaned.split()
        logger.info(f"Ключевые слова для поиска: {search_words}")

        # Проверяем, есть ли совпадение с ключевыми словами
        target_message = None
        for word in search_words:
            if word in STAR_MESSAGES:
                target_message = STAR_MESSAGES[word]
                logger.info(f"Найдено совпадение: {word} -> {target_message['message']}")
                break  # Прекращаем проверку после первого найденного совпадения

        # Закрепляем оригинальное сообщение в исходной группе
        await update.message.pin()
        last_pinned_times[chat_id] = current_time
        last_user_username[chat_id] = user.username if user.username else None

        # Сохраняем информацию о закрепленном сообщении
        try:
            save_pinned_message(chat_id, user.id, user.username, text, current_time)
        except Exception as e:
            logger.error(f"Ошибка при сохранении информации о закрепленном сообщении в чате {chat_id}: {e}")

        # Автопоздравление именинников
        await auto_birthdays(context, chat_id)

        # Отправка сообщения в целевую группу
        if chat_id != TARGET_GROUP_ID:
            if target_message:
                try:
                    # Проверяем, что бот является участником целевой группы
                    target_chat = await context.bot.get_chat(TARGET_GROUP_ID)

                    # Отправляем фото, если оно указано
                    if target_message["photo"]:
                        await context.bot.send_photo(
                            chat_id=TARGET_GROUP_ID,
                            photo=target_message["photo"]
                        )

                    # Отправляем текстовое сообщение
                    forwarded_message = await context.bot.send_message(
                        chat_id=TARGET_GROUP_ID,
                        text=target_message["message"]
                    )
                    await forwarded_message.pin()

                    logger.info(f"Отправлено сообщение из гугл-таблицы в чате {TARGET_GROUP_ID}: {target_message['message']}")

                except Exception as e:
                    logger.error(f"Ошибка при пересылке сообщения из гугл-таблицы в чате {TARGET_GROUP_ID}: {e}")
            else:
                # Если совпадения нет, отправляем оригинальное сообщение
                try:
                    new_text = text.replace("🌟 ", "").strip()
                    forwarded_message = await context.bot.send_message(
                        chat_id=TARGET_GROUP_ID,
                        text=new_text
                    )
                    await forwarded_message.pin()
                    logger.info(f"Отправлено оригинальное сообщение в чате {TARGET_GROUP_ID}: {new_text}")
                except Exception as e:
                    logger.error(f"Ошибка при пересылке оригинального сообщения в чате {TARGET_GROUP_ID}: {e}")

        # Если сообщение пришло из целевой группы
        elif chat_id == TARGET_GROUP_ID and target_message:
            if target_message["photo"]:
                # Отправляем только фото без текста
                await context.bot.send_photo(
                    chat_id=TARGET_GROUP_ID,
                    photo=target_message["photo"]
                )

        # Отправка фото в исходную группу
        if target_message and target_message["photo"] and chat_id != TARGET_GROUP_ID:
            try:
                # Отправляем фото в исходную группу без подписи
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=target_message["photo"]
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке фото в исходную группу {chat_id}: {e}")

        # Устанавливаем задачу на автооткрепление через 45 минут
        try:
            context.job_queue.run_once(unpin_all_messages, PINNED_DURATION, chat_id=chat_id)
            logger.info(f"Установлена задача на открепление сообщений в чате {chat_id} через {PINNED_DURATION // 60} минут.")
        except Exception as e:
            logger.error(f"Ошибка при установке задачи на открепление сообщений в чате {chat_id}: {e}")

    except Exception as e:
        logger.error(f"Ошибка при обработке нового закрепленного сообщения в чате {chat_id}: {e}")

# удаление сист сообщ
async def delete_system_message(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try:
        await context.bot.delete_message(chat_id=job.chat_id, message_id=job.data)
    except Exception as e:
        logger.error(f"Ошибка при удалении системного сообщения: {e}")

# Команда /timer
async def reset_pin_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    user = update.message.from_user

    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
        await update.message.delete()  # Удаляем команду
        return

    last_pinned_times[chat_id] = 0

    try:
        await context.bot.unpin_all_chat_messages(chat_id=chat_id)
        logger.info(f"Откреплены все сообщения в группе {chat_id}.")
    except Exception as e:
        logger.error(f"Ошибка при откреплении сообщений в группе {chat_id}: {e}")

    success_message = await update.message.reply_text("Таймер закрепа успешно сброшен.")
    context.job_queue.run_once(delete_system_message, 10, data=success_message.message_id, chat_id=chat_id)
    await update.message.delete()  # Удаляем команду

# получения имя пользователя с АРI
async def get_user_display_name(context: ContextTypes.DEFAULT_TYPE, user_id: int, username: str = None) -> str:
    """
    Получает имя пользователя из базы данных или через Telegram API.
    Если имя отсутствует, использует логин или ID.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Пытаемся получить имя из базы данных
            cursor.execute('SELECT first_name FROM active_users WHERE user_id = %s', (user_id,))
            result = cursor.fetchone()
            if result and result['first_name']:
                return result['first_name']

            # Если имя отсутствует, получаем его через Telegram API
            try:
                user = await context.bot.get_chat(user_id)
                first_name = user.first_name
                if first_name:
                    # Сохраняем имя в базу данных
                    cursor.execute('UPDATE active_users SET first_name = %s WHERE user_id = %s', (first_name, user_id))
                    conn.commit()
                    return first_name
            except Exception as e:
                logger.error(f"Ошибка при получении информации о пользователе {user_id}: {e}")

            # Если имя не удалось получить, используем логин или ID
            return f"@{username}" if username else f"ID: {user_id}"
    finally:
        conn.close()

# Функция для добавления нарушителей в банлист_ХИСТОРИ:
async def add_to_ban_history(user_id: int, username: str, reason: str):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('''
            INSERT INTO ban_history (user_id, username, reason, timestamp)
            VALUES (%s, %s, %s, %s)
        ''', (user_id, username, reason, int(time.time())))
    conn.commit()
    conn.close()

# Команда /ban_history:
async def ban_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return

    days = int(context.args[0]) if context.args else 1
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('''
            SELECT user_id, username, reason, timestamp 
            FROM ban_history 
            WHERE timestamp >= %s
        ''', (int(time.time()) - days * 86400,))
        results = cursor.fetchall()
    conn.close()

    if not results:
        response = await update.message.reply_text(f"Нет нарушителей за последние {days} дней.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return

    text = f"Нарушители за {days} д.:\n"
    for idx, row in enumerate(results, start=1):
        text += (
            f"{idx}. ID: {row['user_id']} | "
            f"Имя: {row['username']} | "
            f"Причина: {row['reason']} | "
            f"Дата: {datetime.fromtimestamp(row['timestamp']).strftime('%d.%m.%Y %H:%M')}\n"
        )
    await update.message.reply_text(text)
    context.job_queue.run_once(delete_system_message, 60, data=response.message_id, chat_id=update.message.chat.id)
    await update.message.delete()

# Команда /del
async def delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    user = update.message.from_user

    # Проверка прав администратора
    if not await is_admin_or_musician(update, context):
        try:
            success_message = await update.message.reply_text("У вас нет прав для выполнения этой команды.")
            context.job_queue.run_once(delete_system_message, 10, data=success_message.message_id, chat_id=chat_id)
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения о недостатке прав в чате {chat_id}: {e}")
        await update.message.delete()  # Удаляем команду
        return
    
    # Проверка наличия ответа на сообщение
    if not update.message.reply_to_message:
        try:
            success_message = await update.message.reply_text("Ответьте на сообщение, которое нужно удалить.")
            context.job_queue.run_once(delete_system_message, 10, data=success_message.message_id, chat_id=chat_id)
        except Exception as e:
            logger.error(f"Ошибка при отправке инструкции об удалении в чате {chat_id}: {e}")
        await update.message.delete()  # Удаляем команду
        return

    # Удаляем указанное сообщение
    try:
        await update.message.reply_to_message.delete()
        logger.info(f"Сообщение удалено пользователем {user.username} в чате {chat_id}.")
        await update.message.delete()  # Удаляем команду
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")
    
    # Уведомление об успешном удалении
    try:
        success_message = await update.message.reply_text("Не удалось удалить сообщение. Проверьте права бота.")
        context.job_queue.run_once(delete_system_message, 10, data=success_message.message_id, chat_id=chat_id)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об удалении в чате {chat_id}: {e}")

    await update.message.delete()  # Удаляем команду

# Обработчик новых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Добавленное логирование в самом начале функции
    logger.info(f"Получен апдейт: {update.to_dict()}")  # Логируем весь апдейт
    
    message = update.message
    if message is None:
        logger.warning("Получен апдейт без сообщения")
        return  # Игнорируем апдейты без сообщения
    
    # Логируем текст сообщения (если есть)
    logger.info(f"Текст сообщения: {message.text if message.text else 'Нет текста'}")
    
    user = message.from_user
    chat_id = message.chat.id
    text = message.text
    current_time = int(time.time())

    # Проверяем, что сообщение пришло из разрешенной группы
    if chat_id not in ALLOWED_CHAT_IDS and chat_id != TARGET_GROUP_ID:
        logger.warning(f"Сообщение от пользователя {user.id} из неизвестной группы {chat_id}.")
        return
    
    # Проверка на бан в базе бота
    if user.id in banned_users:
        try:
            await message.delete()
        except Exception as e:
            logger.error(f"Ошибка удаления: {e}")
        return

    # Игнорируем сообщения не из групп/супергрупп
    if message.chat.type not in ['group', 'supergroup']:
        return

    # Преобразуем текст в нижний регистр для удобства проверки
    if text:
        text = text.lower()

    # Проверка на антимат и антирекламу (для всех сообщений)
    if not await is_admin_or_musician(update, context):  # Проверяем, является ли пользователь администратором
        # Антимат
        if any(word in text for word in BANNED_WORDS):
            try:
                await message.delete()
                logger.info(f"Удалено сообщение с матом от пользователя {user.id}: {text}")
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с антиматом в чате {chat_id}: {e}")

            try:
                warning_message = await context.bot.send_message(
                    chat_id=chat_id,
                    text="Использование нецензурных выражений недопустимо!"
                )
                context.job_queue.run_once(delete_system_message, 10, data=warning_message.message_id, chat_id=chat_id)
            except Exception as e:
                logger.error(f"Ошибка при отправке предупреждения об антимате в чате {chat_id}: {e}")
            return

        # Антиреклама
        if any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in MESSENGER_KEYWORDS):
            try:
                await message.delete()
                logger.info(f"Удалено сообщение с рекламой от пользователя {user.id}: {text}")
            except Exception as e:
                logger.error(f"Ошибка при удалении сообщения с антирекламой в чате {chat_id}: {e}")

            try:
                warning_message = await context.bot.send_message(
                    chat_id=chat_id,
                    text="Отправка ссылок и упоминаний мессенджеров недопустима!"
                )
                context.job_queue.run_once(delete_system_message, 10, data=warning_message.message_id, chat_id=chat_id)
            except Exception as e:
                logger.error(f"Ошибка при отправке предупреждения об антирекламе в чате {chat_id}: {e}")
            return

    # Проверка наличия закрепленного сообщения в группе
    if text and (text.startswith(("звезда", "зч")) or "🌟" in text):
        try:
            chat = await context.bot.get_chat(chat_id)
            pinned_message = chat.pinned_message
        except Exception as e:
            logger.error(f"Ошибка при получении информации о закрепленном сообщении: {e}")
            pinned_message = None

        # Если закрепленного сообщения нет, разрешаем закрепление
        if pinned_message is None:
            try:
                await process_new_pinned_message(update, context, chat_id, user, text, current_time)
            except Exception as e:
                logger.error(f"Ошибка при обработке нового закрепленного сообщения в чате {chat_id}: {e}")
            return

        # Если закрепленное сообщение уже есть
        last_pinned_time = last_pinned_times.get(chat_id, 0)

        # Проверяем, истекло ли время закрепления
        if current_time - last_pinned_time < PINNED_DURATION:
            if not await is_admin_or_musician(update, context):
                # Обычный пользователь пытается закрепить сообщение
                try:
                    await process_duplicate_message(update, context, chat_id, user, text, current_time)
                except Exception as e:
                    logger.error(f"Ошибка при обработке повторного сообщения в чате {chat_id}: {e}")
            else:
                # Администратор корректирует закрепленное сообщение
                try:
                    await process_new_pinned_message(update, context, chat_id, user, text, current_time)
                except Exception as e:
                    logger.error(f"Ошибка при корректировке закрепленного сообщения администратором в чате {chat_id}: {e}")
                # Отправляем корректирующее сообщение
                try:
                    await send_correction_message(update, context, chat_id)
                except Exception as e:
                    logger.error(f"Ошибка при отправке корректирующего сообщения в чате {chat_id}: {e}")
            return

        # Если время закрепления истекло, закрепляем новое сообщение
        try:
            await process_new_pinned_message(update, context, chat_id, user, text, current_time)
        except Exception as e:
            logger.error(f"Ошибка при обработке нового закрепленного сообщения после истечения таймера в чате {chat_id}: {e}")

def save_pinned_message(chat_id: int, user_id: int, username: str, message_text: str, timestamp: int):
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute('''
                INSERT INTO pinned_messages (chat_id, user_id, username, message_text, timestamp)
                VALUES (%s, %s, %s, %s, %s)
            ''', (chat_id, user_id, username, message_text, timestamp))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при сохранении закрепленного сообщения: {e}")
    finally:
        conn.close()

# ДОБАВЛЕНО: Функция для автооткрепления всех сообщений
async def unpin_all_messages(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    try:
        await context.bot.unpin_all_chat_messages(chat_id=chat_id)
        logger.info(f"Все сообщения успешно откреплены в группе {chat_id}.")
    except Exception as e:
        logger.error(f"Ошибка при откреплении сообщений в группе {chat_id}: {e}")


# обрабатывает повторные сообщения от обычных пользователей.
async def process_duplicate_message(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, text: str, current_time: int):
    try:
        # Удаляем повторное сообщение
        try:
            await update.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении повторного сообщения в чате {chat_id}: {e}")

        # Сохраняем информацию о удаленном сообщении
        try:
            save_active_user(user.id, user.username, current_time)
        except Exception as e:
            logger.error(f"Ошибка при сохранении информации о удаленном сообщении в чате {chat_id}: {e}")

        # Отправляем благодарность за повторное сообщение
        try:
            await send_thanks_message(context, chat_id, user)
        except Exception as e:
            logger.error(f"Ошибка при отправке благодарности в чате {chat_id}: {e}")

    except Exception as e:
        logger.error(f"Ошибка при обработке повторного сообщения в чате {chat_id}: {e}")


# благодарность
async def send_thanks_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user):
    current_time = int(time.time())
    last_thanks_time = last_thanks_times.get(chat_id, 0)

    # Проверяем, прошло ли уже 3 минуты с последней благодарности
    if current_time - last_thanks_time < 180:
        return

    try:
        # Получаем имя текущего пользователя (который отправил повторное сообщение)
        current_user_display_name = await get_user_display_name(context, user.id, user.username)

        # Получаем информацию о последнем закрепленном сообщении из базы данных
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT user_id, username, message_text 
                FROM pinned_messages 
                WHERE chat_id = %s 
                ORDER BY timestamp DESC 
                LIMIT 1
            ''', (chat_id,))
            last_pinned = cursor.fetchone()
        conn.close()

        if last_pinned:
            # Получаем имя пользователя, чье сообщение было закреплено
            pinned_user_display_name = await get_user_display_name(
                context, 
                last_pinned['user_id'], 
                last_pinned['username']
            )

            # Формируем текст благодарности с именем пользователя, чье сообщение было закреплено
            thanks_text = (
                f"{current_user_display_name}, спасибо за вашу бдительность! "
                f"Звезда часа уже замечена пользователем {pinned_user_display_name} "
                f"и закреплена в группе. Надеюсь, в следующий раз именно Вы станете нашей 🌟!!!"
            )
        else:
            # Если по какой-то причине не нашли закрепленное сообщение
            thanks_text = (
                f"{current_user_display_name}, спасибо за вашу бдительность! "
                f"Звезда часа уже закреплена в группе. "
                f"Надеюсь, в следующий раз именно Вы станете нашей 🌟!!!"
            )

        thanks_message = await context.bot.send_message(
            chat_id=chat_id,
            text=thanks_text
        )

        # Устанавливаем задачу на удаление благодарности через 3 минуты
        context.job_queue.run_once(delete_system_message, 180, data=thanks_message.message_id, chat_id=chat_id)

        # Обновляем время последней благодарности
        last_thanks_times[chat_id] = current_time

    except Exception as e:
        logger.error(f"Ошибка при отправке благодарности в чате {chat_id}: {e}")

     
def save_active_user(user_id: int, username: str, current_time: int):
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute('SELECT id FROM active_users WHERE user_id = %s', (user_id,))
            result = cursor.fetchone()
            if result:
                cursor.execute('UPDATE active_users SET delete_count = delete_count + 1, timestamp = %s WHERE user_id = %s',
                            (current_time, user_id))
            else:
                cursor.execute('INSERT INTO active_users (user_id, username, delete_count, timestamp) VALUES (%s, %s, %s, %s)',
                            (user_id, username, 1, current_time))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при сохранении информации о удаленном сообщении для пользователя {user_id}: {e}")
    finally:
        conn.close()

    
async def check_all_birthdays(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('SELECT user_id, username, first_name, birth_date FROM birthdays')
        results = cursor.fetchall()
    conn.close()

    if not results:
        response = await update.message.reply_text("В базе данных нет записей о днях рождения.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return

    text = "Все дни рождения:\n"
    for row in results:
        user_id = row['user_id']
        username = row['username']
        first_name = row['first_name']

        # Определяем, что отображать
        display_name = first_name if first_name else (username if username else "Неизвестный пользователь")
        text += f"• {display_name} — {row['birth_date']}\n"
    
    try:
        stats_message = await update.message.reply_text(text)
        context.job_queue.run_once(delete_system_message, 180, data=stats_message.message_id, chat_id=update.message.chat.id)
    except Exception as e:
        logger.error(f"Ошибка при отправке статистического сообщения: {e}")
    await update.message.delete()

async def send_correction_message(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        correction_message = await context.bot.send_message(
            chat_id=chat_id,
            text="Корректировка звезды часа от Админа."
        )

        # Устанавливаем задачу на удаление сообщения через 10 секунд
        context.job_queue.run_once(delete_system_message, 10, data=correction_message.message_id, chat_id=chat_id)
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения о корректировке: {e}")

# Команда /liderX
async def lider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = int(context.args[0]) if context.args else 1
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('''
            SELECT user_id, username, COUNT(*) as count
            FROM pinned_messages
            WHERE timestamp >= %s
            GROUP BY user_id, username
            ORDER BY count DESC
            LIMIT 3
        ''', (int(time.time()) - days * 86400,))
        results = cursor.fetchall()
    conn.close()

    if not results:
        response = await update.message.reply_text("Нет данных за указанный период.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()  # Удаляем команду
        return

    text = f"Топ участников за - {days} д.:\n"
    for i, row in enumerate(results, start=1):
        user_id = row['user_id']
        username = row['username']

        # Получаем имя пользователя
        user_display_name = await get_user_display_name(context, user_id, username)

        text += f"{i}. {user_display_name} — {row['count']} 🌟\n"
    
    try:
        stats_message = await update.message.reply_text(text)
        context.job_queue.run_once(delete_system_message, 180, data=stats_message.message_id, chat_id=update.message.chat.id)
    except Exception as e:
        logger.error(f"Ошибка при отправке статистического сообщения: {e}")
    await update.message.delete()  # Удаляем команду


# Команда /zhX
async def zh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = int(context.args[0]) if context.args else 10
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('''
            SELECT user_id, username, message_text
            FROM pinned_messages
            ORDER BY timestamp DESC
            LIMIT %s
        ''', (count,))
        results = cursor.fetchall()
    conn.close()

    if not results:
        await update.message.reply_text("Нет закрепленных сообщений.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return

    text = f"Последние {count} ⭐️🕐:\n"
    for i, row in enumerate(results, start=1):
        user_id = row['user_id']
        username = row['username']

        # Получаем имя пользователя
        user_display_name = await get_user_display_name(context, user_id, username)

        text += f"{i}. {user_display_name}: {row['message_text']}\n"
    
    try:
        stats_message = await update.message.reply_text(text)
        context.job_queue.run_once(delete_system_message, 180, data=stats_message.message_id, chat_id=update.message.chat.id)
    except Exception as e:
        logger.error(f"Ошибка при отправке статистического сообщения: {e}")
    await update.message.delete()  # Удаляем команду


# Команда /activeX
async def active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = int(context.args[0]) if context.args else 1
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('''
            SELECT user_id, username, SUM(delete_count) as total_deletes
            FROM active_users
            WHERE timestamp >= %s
            GROUP BY user_id, username
            ORDER BY total_deletes DESC
            LIMIT 3
        ''', (int(time.time()) - days * 86400,))
        results = cursor.fetchall()
    conn.close()

    if not results:
        response = await update.message.reply_text("Нет активных пользователей за указанный период.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return

    text = f"Самые активные пользователи за период - {days} д.:\n"
    for i, row in enumerate(results, start=1):
        user_id = row['user_id']
        username = row['username']

        # Получаем имя пользователя
        user_display_name = await get_user_display_name(context, user_id, username)

        text += f"{i}. {user_display_name} — {row['total_deletes']} раз(а) написал(а)⭐\n"
    
    try:
        stats_message = await update.message.reply_text(text)
        context.job_queue.run_once(delete_system_message, 180, data=stats_message.message_id, chat_id=update.message.chat.id)
    except Exception as e:
        logger.error(f"Ошибка при отправке статистического сообщения: {e}")
    await update.message.delete()  # Удаляем команду


# Команда /dr
async def dr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    if chat_id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text("иди нахуй рамзи")
        return
    user = update.message.from_user
    
    # Проверяем, существует ли уже дата рождения
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT birth_date FROM birthdays WHERE user_id = %s", (user.id,))
        existing_birth_date = cursor.fetchone()
    
    if existing_birth_date:
        # Если дата уже существует, запрещаем изменение
        response = await update.message.reply_text(
            "Ваша дата рождения уже зарегистрирована. "
            "Для изменения обратитесь к администратору."
        )
        context.job_queue.run_once(
            delete_system_message, 10, data=response.message_id, 
            chat_id=update.message.chat.id
        )
        await update.message.delete()
        return
    
    # Если даты нет, позволяем установить её
    if not context.args:
        response = await update.message.reply_text(
            "Напишите свою дату рождения в формате ДД.ММ.ГГГГ"
        )
        context.job_queue.run_once(
            delete_system_message, 10, data=response.message_id, 
            chat_id=update.message.chat.id
        )
        await update.message.delete()
        return

    birth_date = context.args[0]
    if not re.match(r"\d{2}\.\d{2}\.\d{4}", birth_date):
        response = await update.message.reply_text(
            "Неверный формат даты. Напишите одним сообщением /dr ДД.ММ.ГГГГ"
        )
        context.job_queue.run_once(
            delete_system_message, 10, data=response.message_id, 
            chat_id=update.message.chat.id
        )
        await update.message.delete()
        return
    
    # Сохраняем новую дату рождения
    with conn.cursor() as cursor:
        cursor.execute('''
            INSERT INTO birthdays (user_id, username, birth_date, last_congratulated_year)
            VALUES (%s, %s, %s, %s)
        ''', (user.id, user.username, birth_date, None))
        conn.commit()
    
    response = await update.message.reply_text(
        "Ваша дата рождения успешно сохранена!"
    )
    context.job_queue.run_once(
        delete_system_message, 10, data=response.message_id, 
        chat_id=update.message.chat.id
    )
    await update.message.delete()


async def birthday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Получаем сегодняшнюю дату в формате ДД.ММ
    today = datetime.now().strftime("%d.%m")
    
    # Подключаемся к базе данных
    conn = get_db_connection()
    
    # Логируем запрос и данные
    logger.info(f"Ищем именинников на дату: {today}")
    
    # Выполняем запрос к базе данных для поиска сегодняшних именинников
    with conn.cursor() as cursor:
        cursor.execute('SELECT user_id, username FROM birthdays WHERE substr(birth_date, 1, 5) = %s', (today,))
        results = cursor.fetchall()
    conn.close()

    # Если именинников нет
    if not results:
        response = await update.message.reply_text(f"Сегодня ({today}) нет именинников. Чтобы добавить свою дату рождения, напишите /dr и дату рождения одним сообщением в формате /dr ДД.ММ.ГГГГ")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return

    # Формируем сообщение с именинниками
    text = f"Сегодня ({today}) день рождения у:\n"
    for row in results:
        user_id = row['user_id']
        username = row['username']

        # Получаем имя пользователя
        user_display_name = await get_user_display_name(context, user_id, username)

        text += f"• {user_display_name}\n"

    # Отправляем сообщение
    try:
        stats_message = await update.message.reply_text(text)
        context.job_queue.run_once(delete_system_message, 180, data=stats_message.message_id, chat_id=update.message.chat.id)
    except Exception as e:
        logger.error(f"Ошибка при отправке статистического сообщения: {e}")
    await update.message.delete()  # Удаляем команду

# Автопоздравление именинников
async def auto_birthdays(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    today = time.strftime("%d.%m")  # Сегодняшняя дата в формате ДД.ММ
    current_year = datetime.now().year  # Текущий год

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT user_id, username 
                FROM birthdays 
                WHERE substr(birth_date, 1, 5) = %s AND (last_congratulated_year IS NULL OR last_congratulated_year < %s)
            ''', (today, current_year))
            results = cursor.fetchall()

        for row in results:
            user_id = row['user_id']
            username = row['username']

            # Получаем имя пользователя
            user_display_name = await get_user_display_name(context, user_id, username)

            # Поздравляем пользователя
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎉{user_display_name} 🎊 - Поздравляю тебя с днем рождения! 🍀Желаю умножить свой cash🎁back x10 раз 🎉."
                     f" Чтобы добавить свою дату рождения в базу, напишите /dr и дату рождения одним сообщением в формате /dr ДД.ММ.ГГГГ"
            )

            # Обновляем год последнего поздравления
            with conn.cursor() as cursor:
                cursor.execute('UPDATE birthdays SET last_congratulated_year = %s WHERE user_id = %s', (current_year, user_id))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при автопоздравлении: {e}")
    finally:
        conn.close()

async def druser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    user = update.message.from_user

    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
        await update.message.delete()
        return

    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        user_id = target_user.id
        username = target_user.username or f"ID: {target_user.id}"
        birth_date = " ".join(context.args) if context.args else None
    else:
        if not context.args or len(context.args) < 2:
            response = await update.message.reply_text(
                "Используйте команду в формате: /druser @username dd.mm.yyyy, /druser ID dd.mm.yyyy или ответьте на сообщение пользователя с командой /druser dd.mm.yyyy"
            )
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
            await update.message.delete()
            return

        user_identifier = context.args[0]
        birth_date = context.args[1]

        user_id = None
        username = None

        if user_identifier.startswith("@"):
            username = user_identifier[1:]
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute('SELECT user_id FROM birthdays WHERE username = %s', (username,))
                result = cursor.fetchone()
                if result:
                    user_id = result['user_id']
            conn.close()

            if not user_id:
                try:
                    chat_member = await context.bot.get_chat_member(chat_id, username)
                    user_id = chat_member.user.id
                    username = chat_member.user.username or username
                except Exception as e:
                    logger.error(f"Ошибка при получении информации о пользователе {username}: {e}")
                    response = await update.message.reply_text(f"Пользователь @{username} не найден.")
                    context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
                    await update.message.delete()
                    return
        else:
            try:
                user_id = int(user_identifier)
            except ValueError:
                response = await update.message.reply_text("Неверный формат ID. Используйте числовой ID.")
                context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
                await update.message.delete()
                return

    if not birth_date or not re.match(r"\d{2}\.\d{2}\.\d{4}", birth_date):
        response = await update.message.reply_text("Неверный формат даты. Используйте ДД.ММ.ГГГГ.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
        await update.message.delete()
        return

    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('''
            INSERT INTO birthdays (user_id, username, birth_date, last_congratulated_year)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET birth_date = EXCLUDED.birth_date, last_congratulated_year = EXCLUDED.last_congratulated_year
        ''', (user_id, username, birth_date, 0))
    conn.commit()
    conn.close()

    response = await update.message.reply_text(f"Дата рождения для пользователя {username or f'ID: {user_id}'} сохранена: {birth_date}")
    context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
    await update.message.delete()

# id пользователя или чата
async def get_user_or_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    user = update.message.from_user

    # Проверка прав администратора или музыканта
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
        await update.message.delete()  # Удаляем команду
        return

    # Если команда отправлена в ответ на сообщение пользователя
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        user_id = target_user.id
        username = target_user.username or "без username"
        first_name = target_user.first_name or "без имени"

        # Отправляем ID пользователя
        response = await update.message.reply_text(
            f"ID пользователя {first_name} (@{username}): {user_id}"
        )
    else:
        # Если команда отправлена просто в чат, возвращаем ID чата
        response = await update.message.reply_text(f"ID текущего чата: {chat_id}")

    # Устанавливаем задачу на удаление ответного сообщения через 10 секунд
    context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)

    # Удаляем команду /id
    await update.message.delete()

    # Логируем действие
    logger.info(f"Пользователь {user.id} запросил ID чата {chat_id}.")

# Команда /ban_list
async def ban_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('SELECT user_id, username FROM ban_list')
        results = cursor.fetchall()
    conn.close()

    if not results:
        response = await update.message.reply_text("Бан-лист пуст.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return

    text = "Бан-лист:\n"
    for idx, row in enumerate(results, start=1):
        text += f"{idx}. ID: {row['user_id']} | Username: @{row['username']}\n"
    response = await update.message.reply_text(text)
    context.job_queue.run_once(delete_system_message, 60, data=response.message_id, chat_id=update.message.chat.id)
    await update.message.delete()


# Команда /ban
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("❌ Только админы могут банить!")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()  # Удаляем команду
        return
    
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        
        try:
            await update.message.delete()  # Удаляем команду
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения пользователя {target_user.id}: {e}")

        if target_user.id in banned_users:
            response = await update.message.reply_text(f"@{target_user.username} уже забанен.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
            await update.message.delete()  # Удаляем команду
            return

        conn = get_db_connection()
       
        with conn.cursor() as cursor:
            cursor.execute('INSERT INTO ban_list (user_id, username, ban_time) VALUES (%s, %s, %s)', 
                     (target_user.id, target_user.username, int(time.time())))
        conn.commit()
        conn.close()

        banned_users.add(target_user.id)

        try:
            await context.bot.ban_chat_member(chat_id=update.message.chat.id, user_id=target_user.id)
        except Exception as e:
            logger.error(f"Ошибка при бане пользователя {target_user.id}: {e}")     
            response = await update.message.reply_text("Не удалось забанить пользователя. Проверьте права бота.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
            await update.message.delete()  # Удаляем команду
            return

        
        response = await update.message.reply_text(f"@{target_user.username} забанен.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()  # Удаляем команду
    elif context.args:
        user_id = context.args[0]
        try:
            user_id = int(user_id)
        except ValueError:
            response = await update.message.reply_text("Введите корректный ID пользователя.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
            await update.message.delete()  # Удаляем команду
            return

        conn = get_db_connection()

        with conn.cursor() as cursor:
            cursor.execute('INSERT INTO ban_list (user_id, username, ban_time) VALUES (%s, %s, %s)', 
                     (user_id, "Unknown", int(time.time())))
        conn.commit()

        banned_users.add(user_id) # Обновляем кэш

        try:
            await context.bot.ban_chat_member(chat_id=update.message.chat.id, user_id=user_id)
        except Exception as e:
            logger.error(f"Ошибка при бане пользователя {user_id}: {e}")
            response = await update.message.reply_text("Не удалось забанить пользователя. Проверьте права бота.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
            await update.message.delete()  # Удаляем команду
            return

        response = await update.message.reply_text(f"Пользователь с ID {user_id} забанен.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()  # Удаляем команду
    else:
        response = await update.message.reply_text("Ответьте на сообщение пользователя или укажите его ID.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()  # Удаляем команду


# Команда /deban
async def deban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()  # Удаляем команду
        return
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        if target_user.id not in banned_users:
            response = await update.message.reply_text(f"@{target_user.username} не находится в бане.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
            await update.message.delete()  # Удаляем команду
            return

        conn = get_db_connection()
 
        with conn.cursor() as cursor:
            cursor.execute('DELETE FROM ban_list WHERE user_id = %s', (target_user.id,))
        conn.commit()
        conn.close()

        banned_users.discard(target_user.id)

        try:
            await context.bot.unban_chat_member(chat_id=update.message.chat.id, user_id=target_user.id)
        except Exception as e:
            logger.error(f"Ошибка при разбане пользователя {target_user.id}: {e}")
            response = await update.message.reply_text("Не удалось разбанить пользователя. Проверьте права бота.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
            await update.message.delete()  # Удаляем команду
            return

        response = await update.message.reply_text(f"@{target_user.username} разбанен.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()  # Удаляем команду
    elif context.args:
        user_id = context.args[0]
        try:
            user_id = int(user_id)
        except ValueError:
            response = await update.message.reply_text("Введите корректный ID пользователя.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
            await update.message.delete()  # Удаляем команду
            return

        if user_id not in banned_users:
            response = await update.message.reply_text(f"Пользователь с ID {user_id} не находится в бане.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
            await update.message.delete()  # Удаляем команду
            return

        conn = get_db_connection()

        with conn.cursor() as cursor:
            cursor.execute('DELETE FROM ban_list WHERE user_id = %s', (user_id,))
        conn.commit()
        conn.close()

        banned_users.discard(user_id)

        try:
            await context.bot.unban_chat_member(chat_id=update.message.chat.id, user_id=user_id)
        except Exception as e:
            logger.error(f"Ошибка при разбане пользователя {user_id}: {e}")
            response = await update.message.reply_text("Не удалось разбанить пользователя. Проверьте права бота.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
            await update.message.delete()  # Удаляем команду
            return

        response = await update.message.reply_text(f"Пользователь с ID {user_id} разбанен.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()  # Удаляем команду
    else:
        response = await update.message.reply_text("Ответьте на сообщение пользователя или укажите его ID.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=update.message.chat.id)
        await update.message.delete()  # Удаляем команду

# обновляем гугл таблицу
async def update_google_table(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    user = update.message.from_user

    # Проверяем права администратора
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
        await update.message.delete()  # Удаляем команду
        return

    try:
        # Загружаем обновленные данные из гугл-таблицы
        global STAR_MESSAGES
        STAR_MESSAGES = load_star_messages_from_html()

        if not STAR_MESSAGES:
            response = await update.message.reply_text("Не удалось обновить таблицу. Проверьте URL или содержимое таблицы.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
        else:
            response = await update.message.reply_text(f"Таблица успешно обновлена. Загружено {len(STAR_MESSAGES)} записей.")
            context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)

        logger.info(f"Таблица обновлена пользователем {user.username or user.first_name} в чате {chat_id}.")
    except Exception as e:
        logger.error(f"Ошибка при обновлении гугл-таблицы: {e}")
        response = await update.message.reply_text("Произошла ошибка при обновлении таблицы.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)

    await update.message.delete()  # Удаляем команду

# Команда /clean
async def clean_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    user = update.message.from_user

    # Проверка прав администратора
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
        await update.message.delete()  # Удаляем команду
        return

    # Определяем количество дней для очистки
    days = int(context.args[0]) if context.args else None

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if days is not None:
                # Удаляем записи старше указанного количества дней
                cutoff_time = int(time.time()) - days * 86400
                cursor.execute('DELETE FROM pinned_messages WHERE timestamp < %s', (cutoff_time,))
                cursor.execute('DELETE FROM active_users WHERE timestamp < %s', (cutoff_time,))
                cursor.execute('DELETE FROM ban_history WHERE timestamp < %s', (cutoff_time,))
                logger.info(f"Очищена база данных за последние {days} дней.")
                response = await update.message.reply_text(f"База данных успешно очищена за последние {days} дней.")
            else:
                # Полная очистка базы данных
                cursor.execute('TRUNCATE TABLE pinned_messages RESTART IDENTITY CASCADE')
                cursor.execute('TRUNCATE TABLE active_users RESTART IDENTITY CASCADE')
                cursor.execute('TRUNCATE TABLE ban_history RESTART IDENTITY CASCADE')
                logger.info("Полностью очищена база данных.")
                response = await update.message.reply_text("База данных полностью очищена.")

        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при очистке базы данных: {e}")
        response = await update.message.reply_text("Произошла ошибка при очистке базы данных.")
    finally:
        conn.close()

    context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
    await update.message.delete()  # Удаляем команду

import os

# Команда /save
async def save_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    user = update.message.from_user

    # Проверка прав администратора
    if not await is_admin_or_musician(update, context):
        response = await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
        await update.message.delete()  # Удаляем команду
        return

    db_url = os.getenv("DATABASE_URL")
    backup_filename = "database_backup.sql"

    try:
        # Создаем бэкап базы данных
        conn = get_db_connection()
        with open(backup_filename, 'w') as f:
            with conn.cursor() as cursor:
                # Экспортируем структуру и данные всех таблиц
                cursor.copy_expert("COPY (SELECT * FROM pinned_messages) TO STDOUT WITH CSV HEADER", f)
                cursor.copy_expert("COPY (SELECT * FROM active_users) TO STDOUT WITH CSV HEADER", f)
                cursor.copy_expert("COPY (SELECT * FROM birthdays) TO STDOUT WITH CSV HEADER", f)
                cursor.copy_expert("COPY (SELECT * FROM ban_list) TO STDOUT WITH CSV HEADER", f)
                cursor.copy_expert("COPY (SELECT * FROM ban_history) TO STDOUT WITH CSV HEADER", f)
        conn.close()

        # Отправляем файл бэкапа в чат
        with open(backup_filename, 'rb') as f:
            await context.bot.send_document(chat_id=chat_id, document=f, filename=backup_filename)

        # Удаляем временный файл после отправки
        os.remove(backup_filename)

        logger.info("Создан бэкап базы данных.")
        response = await update.message.reply_text("Бэкап базы данных создан и отправлен.")
    except Exception as e:
        logger.error(f"Ошибка при создании бэкапа базы данных: {e}")
        response = await update.message.reply_text("Произошла ошибка при создании бэкапа базы данных.")
    finally:
        context.job_queue.run_once(delete_system_message, 10, data=response.message_id, chat_id=chat_id)
        await update.message.delete()  # Удаляем команду

def load_banned_users():
    global banned_users
    banned_users = set()
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('SELECT user_id FROM ban_list')
        results = cursor.fetchall()
        for row in results:
            banned_users.add(row['user_id'])
    conn.close()

# Основная функция
def main():
    load_banned_users()
    application = Application.builder().token(BOT_TOKEN).build()
    job_queue = application.job_queue  # Инициализация JobQueue

     # Добавляем новые команды
    application.add_handler(CommandHandler("clean", clean_database))
    application.add_handler(CommandHandler("save", save_backup))
    application.add_handler(CommandHandler("timer", reset_pin_timer))
    application.add_handler(CommandHandler("del", delete_message))
    application.add_handler(CommandHandler("lider", lider))
    application.add_handler(CommandHandler("zh", zh))
    application.add_handler(CommandHandler("active", active))
    application.add_handler(CommandHandler("dr", dr))
    application.add_handler(CommandHandler("druser", druser))  # Добавляем команду /druser
    application.add_handler(CommandHandler("id", get_user_or_chat_id))
    application.add_handler(CommandHandler("birthday", birthday))
    application.add_handler(CommandHandler("check_birthdays", check_all_birthdays))
    application.add_handler(CommandHandler("ban_list", ban_list))
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("deban", deban_user))
    application.add_handler(CommandHandler("ban_history", ban_history))
    application.add_handler(CommandHandler("google", update_google_table))  # Новая команда /google
 
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    try:
        application.run_polling()
        logger.info("Бот запущен. Ожидание сообщений...")
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
    finally:
        logger.info("Бот остановлен.")


if __name__ == '__main__':
    main()
