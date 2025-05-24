from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    JobQueue,
    CallbackContext
)
import logging
import time
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

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
HTML_URL = os.getenv("HTML_URL")
TARGET_GROUP_ID = -1002382138419
ALLOWED_CHAT_IDS = [-1002201488475, -1002437528572, -1002385047417, -1002382138419]
PINNED_DURATION = 2700  # 45 минут
MESSAGE_STORAGE_TIME = 180  # 3 минуты для хранения сообщений
ALLOWED_USER = "@Muzikant1429"

# Антимат
BANNED_WORDS = ["бляд", "хуй", "пизд", "наху", "гандон", "пидр", "пидорас", "пидар", "шалав", "шлюх", "мразь", "мразо", "ебат", "ебал", "дебил", "имебецил", "говнюк"]
MESSENGER_KEYWORDS = ["t.me", "telegram", "whatsapp", "viber", "discord", "vk.com", "instagram", "facebook", "twitter", "youtube", "http", "www", ".com", ".ru", ".net", "tiktok"]

# Глобальные переменные
last_pinned_times = {}
last_user_username = {}
last_thanks_times = {}
pinned_messages = {}  # {chat_id: {"message_id": int, "user_id": int, "text": str, "timestamp": float}}
message_storage = {}  # {message_id: {"chat_id": int, "user_id": int, "text": str, "timestamp": float}}
STAR_MESSAGES = {}
banned_users = set()

def clean_text(text: str) -> str:
    return " ".join(text.split()).lower() if text else ""

def load_star_messages():
    try:
        response = requests.get(HTML_URL)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        return {
            clean_text(row.find_all("td")[0].text.strip()): {
                "message": row.find_all("td")[1].text.strip(),
                "photo": row.find_all("td")[2].text.strip() if row.find_all("td")[2].text.strip().startswith("http") else None
            }
            for row in soup.find_all("tr")[1:] if len(row.find_all("td")) >= 3
        }
    except Exception as e:
        logger.error(f"Ошибка загрузки Google таблицы: {e}")
        return {}

STAR_MESSAGES = load_star_messages()

async def is_admin_or_musician(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.username == ALLOWED_USER[1:]:
        return True
    
    try:
        chat_member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
        return chat_member.status in ["administrator", "creator"]
    except Exception as e:
        logger.error(f"Ошибка проверки прав: {e}")
        return False

async def cleanup_storage(context: CallbackContext):
    current_time = time.time()
    expired_messages = [
        msg_id for msg_id, data in message_storage.items() 
        if current_time - data["timestamp"] > MESSAGE_STORAGE_TIME
    ]
    for msg_id in expired_messages:
        del message_storage[msg_id]

async def unpin_message(context: CallbackContext):
    job = context.job
    chat_id = job.chat_id
    
    if chat_id in pinned_messages:
        try:
            await context.bot.unpin_chat_message(chat_id, pinned_messages[chat_id]["message_id"])
            logger.info(f"Сообщение откреплено в чате {chat_id}")
        except Exception as e:
            logger.error(f"Ошибка открепления: {e}")
        finally:
            del pinned_messages[chat_id]
            if chat_id in last_pinned_times:
                del last_pinned_times[chat_id]

async def process_new_pinned_message(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, text: str):
    current_time = time.time()
    message = update.message or update.edited_message
    
    # Закрепляем сообщение
    try:
        await message.pin()
    except Exception as e:
        logger.error(f"Ошибка при закреплении: {e}")
        return
    
    # Сохраняем данные
    pinned_messages[chat_id] = {
        "message_id": message.message_id,
        "user_id": user.id,
        "text": text,
        "timestamp": current_time
    }
    message_storage[message.message_id] = {
        "chat_id": chat_id,
        "user_id": user.id,
        "text": text,
        "timestamp": current_time
    }
    
    last_pinned_times[chat_id] = current_time
    last_user_username[chat_id] = user.username or f"id{user.id}"
    
    # Устанавливаем таймер открепления
    context.job_queue.run_once(unpin_message, PINNED_DURATION, chat_id=chat_id)
    
    # Для целевой группы - только закрепление
    if chat_id == TARGET_GROUP_ID:
        logger.info(f"ЗЧ в целевой группе от @{user.username}")
        return
    
    # Проверяем Google таблицу для обычных групп
    text_cleaned = clean_text(text)
    target_message = None
    for word in text_cleaned.split():
        if word in STAR_MESSAGES:
            target_message = STAR_MESSAGES[word]
            break
    
    try:
        # Отправляем фото в текущую группу, если есть
        if target_message and target_message["photo"]:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=target_message["photo"]
            )
        
        # Пересылаем в целевую группу только если там нет активной ЗЧ
        target_has_active_pin = TARGET_GROUP_ID in pinned_messages and current_time - pinned_messages[TARGET_GROUP_ID]["timestamp"] < PINNED_DURATION
        
        if not target_has_active_pin:
            forwarded_text = target_message["message"] if target_message else f"🌟 {text.replace('🌟', '').strip()}"
            forwarded = await context.bot.send_message(
                chat_id=TARGET_GROUP_ID,
                text=forwarded_text
            )
            await forwarded.pin()
            
            # Сохраняем данные о закреплении в целевой группе
            pinned_messages[TARGET_GROUP_ID] = {
                "message_id": forwarded.message_id,
                "user_id": user.id,
                "text": forwarded_text,
                "timestamp": current_time
            }
            context.job_queue.run_once(unpin_message, PINNED_DURATION, chat_id=TARGET_GROUP_ID)
            
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")

async def process_duplicate_message(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user):
    current_time = time.time()
    try:
        await update.message.delete()
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")
    
    if current_time - last_thanks_times.get(chat_id, 0) > 180:
        last_user = last_user_username.get(chat_id, "администратора")
        thanks = await context.bot.send_message(
            chat_id=chat_id,
            text=f"@{user.username or user.id}, спасибо за бдительность! Звезда часа уже закреплена пользователем {last_user}. Надеюсь, в следующий раз именно Вы станете нашей 🌟!!!"
        )
        context.job_queue.run_once(
            lambda ctx: ctx.bot.delete_message(chat_id=chat_id, message_id=thanks.message_id),
            180
        )
        last_thanks_times[chat_id] = current_time

async def handle_message_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.edited_message:
        return
    
    edited_msg = update.edited_message
    if edited_msg.message_id in message_storage:
        # Обновляем текст в хранилище
        message_storage[edited_msg.message_id]["text"] = edited_msg.text or edited_msg.caption
        message_storage[edited_msg.message_id]["timestamp"] = time.time()
        
        # Если это закрепленное сообщение - обрабатываем как новое
        chat_id = edited_msg.chat.id
        if chat_id in pinned_messages and pinned_messages[chat_id]["message_id"] == edited_msg.message_id:
            await handle_message(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message or update.edited_message
        if not message:
            return
            
        user = message.from_user
        chat_id = message.chat.id
        text = message.text or message.caption
        current_time = time.time()
        
        # Проверки на бан, разрешенные чаты, мат и рекламу
        if (user.id in banned_users or 
            chat_id not in ALLOWED_CHAT_IDS or
            not await basic_checks(update, context, text)):
            return

        # Проверка на ЗЧ
        if text and any(marker in text.lower() for marker in ["звезда", "зч", "🌟"]):
            # Проверяем, есть ли уже активное закрепленное сообщение
            if chat_id in pinned_messages:
                last_pin_time = pinned_messages[chat_id]["timestamp"]
                
                # Если время не истекло
                if current_time - last_pin_time < PINNED_DURATION:
                    if await is_admin_or_musician(update, context):
                        # Админ может заменить закреп
                        await process_new_pinned_message(update, context, chat_id, user, text)
                        correction = await context.bot.send_message(
                            chat_id=chat_id,
                            text="Корректировка звезды часа от Админа."
                        )
                        context.job_queue.run_once(
                            lambda ctx: ctx.bot.delete_message(chat_id=chat_id, message_id=correction.message_id),
                            10
                        )
                    else:
                        # Обычный пользователь - удаляем сообщение
                        await process_duplicate_message(update, context, chat_id, user)
                else:
                    # Время истекло - можно закрепить новое
                    await process_new_pinned_message(update, context, chat_id, user, text)
            else:
                # Нет активного закрепленного сообщения - закрепляем новое
                await process_new_pinned_message(update, context, chat_id, user, text)
                
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")

async def basic_checks(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    if not text:
        return False
        
    chat_id = update.effective_chat.id
    text_lower = text.lower()
    
    if any(bad in text_lower for bad in BANNED_WORDS):
        await update.message.delete()
        warn = await context.bot.send_message(chat_id, "Использование мата запрещено!")
        context.job_queue.run_once(
            lambda ctx: ctx.bot.delete_message(chat_id=chat_id, message_id=warn.message_id),
            10
        )
        return False
        
    if any(adv in text_lower for adv in MESSENGER_KEYWORDS):
        await update.message.delete()
        warn = await context.bot.send_message(chat_id, "Реклама запрещена!")
        context.job_queue.run_once(
            lambda ctx: ctx.bot.delete_message(chat_id=chat_id, message_id=warn.message_id),
            10
        )
        return False
        
    return True

async def reset_pin_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        resp = await update.message.reply_text("У вас нет прав для этой команды.")
        context.job_queue.run_once(
            lambda ctx: ctx.bot.delete_message(chat_id=update.message.chat.id, message_id=resp.message_id),
            10
        )
        await update.message.delete()
        return
        
    chat_id = update.message.chat.id
    if chat_id in pinned_messages:
        await context.bot.unpin_chat_message(chat_id, pinned_messages[chat_id]["message_id"])
        del pinned_messages[chat_id]
    if chat_id in last_pinned_times:
        del last_pinned_times[chat_id]
        
    resp = await update.message.reply_text("Таймер сброшен, можно публиковать новую ЗЧ.")
    context.job_queue.run_once(
        lambda ctx: ctx.bot.delete_message(chat_id=chat_id, message_id=resp.message_id),
        10
    )
    await update.message.delete()

async def update_google_table(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        resp = await update.message.reply_text("У вас нет прав для этой команды.")
        context.job_queue.run_once(
            lambda ctx: ctx.bot.delete_message(chat_id=update.message.chat.id, message_id=resp.message_id),
            10
        )
        await update.message.delete()
        return
    
    global STAR_MESSAGES
    STAR_MESSAGES = load_star_messages()
    
    resp = await update.message.reply_text(f"Google таблица обновлена. Загружено {len(STAR_MESSAGES)} записей.")
    context.job_queue.run_once(
        lambda ctx: ctx.bot.delete_message(chat_id=update.message.chat.id, message_id=resp.message_id),
        10
    )
    await update.message.delete()

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регулярная очистка хранилища
    job_queue = app.job_queue
    job_queue.run_repeating(cleanup_storage, interval=60, first=10)
    
    app.add_handler(CommandHandler("timer", reset_pin_timer))
    app.add_handler(CommandHandler("google", update_google_table))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.ALL & filters.UpdateType.EDITED_MESSAGE, handle_message_edit))
    
    app.run_polling()
    logger.info("Бот запущен")

if __name__ == '__main__':
    main()
