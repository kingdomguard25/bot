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

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_GROUP_ID = -1002437528572
ALLOWED_CHAT_IDS = [-1002201488475, -1002437528572, -1002385047417, -1002382138419]
PINNED_DURATION = 2700  # 45 минут
ALLOWED_USER = "@Muzikant1429"

# Антимат и антиспам настройки
BANNED_WORDS = ["бляд", "хуй", "пизд", "наху", "гандон", "пидр", "пидорас", "пидар", "шалав", "шлюх", "мразь", "мразо", "ебат", "ебал", "дебил", "имебецил", "говнюк"]
MESSENGER_KEYWORDS = ["t.me", "telegram", "whatsapp", "viber", "discord", "vk.com", "instagram", "facebook", "twitter", "youtube", "http", "www", ".com", ".ru", ".net", "tiktok"]

# Глобальные переменные для хранения данных
last_pinned_times = {}  # {chat_id: timestamp}
last_user_username = {}  # {chat_id: username}
last_thanks_times = {}  # {chat_id: timestamp}
pinned_messages = {}  # {chat_id: {"message_id": int, "user_id": int}}
message_history = {}  # {message_id: {"chat_id": int, "user_id": int, "text": str}}

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
        logger.error(f"Ошибка при проверке прав: {e}")

    if update.message.from_user.username == ALLOWED_USER[1:]:
        return True

    return False

async def delete_system_message(context: CallbackContext):
    job = context.job
    try:
        await context.bot.delete_message(chat_id=job.chat_id, message_id=job.data)
    except Exception as e:
        logger.error(f"Ошибка при удалении системного сообщения: {e}")

async def unpin_message(context: CallbackContext):
    job = context.job
    chat_id = job.chat_id
    
    if chat_id in pinned_messages:
        try:
            await context.bot.unpin_chat_message(chat_id=chat_id, message_id=pinned_messages[chat_id]["message_id"])
            logger.info(f"Сообщение откреплено в чате {chat_id}")
            del pinned_messages[chat_id]
            if chat_id in last_pinned_times:
                del last_pinned_times[chat_id]
        except Exception as e:
            logger.error(f"Ошибка при откреплении: {e}")

async def process_new_pinned_message(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, text: str):
    try:
        current_time = time.time()
        
        # Закрепляем сообщение
        await update.message.pin()
        
        # Сохраняем данные
        pinned_messages[chat_id] = {
            "message_id": update.message.message_id,
            "user_id": user.id
        }
        
        last_pinned_times[chat_id] = current_time
        last_user_username[chat_id] = user.username or f"id{user.id}"
        
        # Сохраняем в историю сообщений
        message_history[update.message.message_id] = {
            "chat_id": chat_id,
            "user_id": user.id,
            "text": text
        }
        
        # Устанавливаем таймер открепления
        context.job_queue.run_once(unpin_message, PINNED_DURATION, chat_id=chat_id)
        
        # Пересылаем только если это не целевая группа
        if chat_id != TARGET_GROUP_ID:
            try:
                forwarded = await context.bot.send_message(
                    chat_id=TARGET_GROUP_ID,
                    text=f"🌟 {text.replace('🌟', '').strip()}"
                )
                await forwarded.pin()
            except Exception as e:
                logger.error(f"Ошибка пересылки: {e}")
        
        logger.info(f"Новая ЗЧ от @{user.username} в чате {chat_id}")
        
    except Exception as e:
        logger.error(f"Ошибка при закреплении: {e}")

async def process_duplicate_message(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user):
    try:
        current_time = time.time()
        
        # Удаляем дубликат
        await update.message.delete()
        
        # Отправляем благодарность (не чаще чем раз в 3 минуты)
        if current_time - last_thanks_times.get(chat_id, 0) > 180:
            last_user = last_user_username.get(chat_id, "администратора")
            thanks = await context.bot.send_message(
                chat_id=chat_id,
                text=f"@{user.username or user.id}, спасибо за бдительность! Звезда часа уже закреплена пользователем {last_user}. Надеюсь, в следующий раз именно Вы станете нашей 🌟!!!"
            )
            context.job_queue.run_once(delete_system_message, 180, data=thanks.message_id, chat_id=chat_id)
            last_thanks_times[chat_id] = current_time
        
        logger.info(f"Удален дубликат ЗЧ от @{user.username} в чате {chat_id}")
        
    except Exception as e:
        logger.error(f"Ошибка при обработке дубликата: {e}")

async def handle_message_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.edited_message or not update.message:
        return
        
    deleted_message_id = update.message.message_id
    if deleted_message_id in message_history:
        data = message_history[deleted_message_id]
        if data["chat_id"] in pinned_messages and pinned_messages[data["chat_id"]]["message_id"] == deleted_message_id:
            # Сбрасываем таймер если удалено закрепленное сообщение
            if data["chat_id"] in last_pinned_times:
                del last_pinned_times[data["chat_id"]]
            if data["chat_id"] in pinned_messages:
                del pinned_messages[data["chat_id"]]
                
            logger.info(f"ЗЧ удалена пользователем, сброс таймера в чате {data['chat_id']}")

async def handle_message_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.edited_message:
        return
        
    edited_message = update.edited_message
    if edited_message.message_id in message_history:
        # Полная повторная проверка отредактированного сообщения
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

        # Проверка на бан
        if user.id in banned_users:
            await message.delete()
            return

        # Проверка разрешенных чатов
        if chat_id not in ALLOWED_CHAT_IDS and chat_id != TARGET_GROUP_ID:
            return

        # Проверка на мат и рекламу
        if text:
            text_lower = text.lower()
            if any(bad in text_lower for bad in BANNED_WORDS):
                await message.delete()
                warn = await context.bot.send_message(chat_id, "Использование мата запрещено!")
                context.job_queue.run_once(delete_system_message, 10, data=warn.message_id, chat_id=chat_id)
                return
                
            if any(adv in text_lower for adv in MESSENGER_KEYWORDS):
                await message.delete()
                warn = await context.bot.send_message(chat_id, "Реклама запрещена!")
                context.job_queue.run_once(delete_system_message, 10, data=warn.message_id, chat_id=chat_id)
                return

        # Проверка на ЗЧ
        if text and ("звезда" in text.lower() or "зч" in text.lower() or "🌟" in text):
            # Проверяем есть ли уже закрепленное сообщение
            if chat_id in pinned_messages:
                last_pin_time = last_pinned_times.get(chat_id, 0)
                
                # Если админ - обновляем ЗЧ
                if await is_admin_or_musician(update, context):
                    await process_new_pinned_message(update, context, chat_id, user, text)
                    correction = await context.bot.send_message(chat_id, "Корректировка звезды часа от Админа.")
                    context.job_queue.run_once(delete_system_message, 10, data=correction.message_id, chat_id=chat_id)
                # Если время не вышло - удаляем дубликат
                elif current_time - last_pin_time < PINNED_DURATION:
                    await process_duplicate_message(update, context, chat_id, user)
                # Если время вышло - новая ЗЧ
                else:
                    await process_new_pinned_message(update, context, chat_id, user, text)
            else:
                await process_new_pinned_message(update, context, chat_id, user, text)
                
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")

async def reset_pin_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        resp = await update.message.reply_text("У вас нет прав для этой команды.")
        context.job_queue.run_once(delete_system_message, 10, data=resp.message_id, chat_id=update.message.chat.id)
        await update.message.delete()
        return
        
    chat_id = update.message.chat.id
    if chat_id in pinned_messages:
        await context.bot.unpin_chat_message(chat_id=chat_id, message_id=pinned_messages[chat_id]["message_id"])
        del pinned_messages[chat_id]
    if chat_id in last_pinned_times:
        del last_pinned_times[chat_id]
        
    resp = await update.message.reply_text("Таймер сброшен, можно публиковать новую ЗЧ.")
    context.job_queue.run_once(delete_system_message, 10, data=resp.message_id, chat_id=chat_id)
    await update.message.delete()

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Обработчики
    app.add_handler(CommandHandler("timer", reset_pin_timer))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.ALL & filters.UpdateType.EDITED_MESSAGE, handle_message_edit))
    app.add_handler(MessageHandler(filters.ALL & filters.UpdateType.DELETED_MESSAGE, handle_message_deletion))
    
    app.run_polling()
    logger.info("Бот запущен")

if __name__ == '__main__':
    main()
