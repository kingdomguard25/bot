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
from telegram.ext import MessageReactionHandler  # Добавь в импорты в начале файла
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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
TARGET_GROUP_ID = -1002437528572
ALLOWED_CHAT_IDS = [-1002201488475, -1002437528572, -1002385047417, -1002382138419]
PINNED_DURATION = 2700  # 45 минут
MESSAGE_STORAGE_TIME = 180  # 3 минуты для хранения сообщений
ALLOWED_USER = "@Muzikant1429"
ADMIN_GROUP_ID = -1002385047417  # ID админской группы
# Добавляем в глобальные переменные
REACTION_STATS = {}  # {user_id: {"username": str, "reactions": int}}
TRACKED_CHAT_ID = TARGET_GROUP_ID  # Чат, где отслеживаем реакции

# Антимат
BANNED_WORDS = ["бляд", "хуй", "хер", "чмо", "пизд", "идиот", "хуев","наху", "гандон", "пидр", "пидор", "пидар", "шалав", "шлюх", "мраз", "мразо", "ебат", "ебал", "дебил", "имбецил", "говно"]
MESSENGER_KEYWORDS = ["t.me", "telegram", "whatsapp", "viber", "discord", "vk.com", "instagram", "facebook", "twitter", "youtube",  ".be", "http", "https", "www", ".com", ".ru", ".net", "tiktok"]

# Глобальные переменные
last_pinned_times = {}
last_user_username = {}
last_thanks_times = {}
pinned_messages = {}  # {chat_id: {"message_id": int, "user_id": int, "text": str, "timestamp": float, "photo_id": int}}
message_storage = {}  # {message_id: {"chat_id": int, "user_id": int, "text": str, "timestamp": float}}
STAR_MESSAGES = {}
banned_users = set()
sent_photos = {}  # {chat_id: message_id} для хранения ID отправленных фото

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
            # Удаляем закрепленное сообщение
            await context.bot.unpin_chat_message(chat_id, pinned_messages[chat_id]["message_id"])
            logger.info(f"Сообщение откреплено в чате {chat_id}")
        except Exception as e:
            logger.error(f"Ошибка открепления: {e}")
        finally:
            # Удаляем фото только если оно принадлежит этому сообщению
            if "photo_id" in pinned_messages[chat_id] and pinned_messages[chat_id]["photo_id"] == sent_photos.get(chat_id):
                try:
                    await context.bot.delete_message(chat_id, pinned_messages[chat_id]["photo_id"])
                    del sent_photos[chat_id]
                except Exception as e:
                    logger.error(f"Ошибка удаления фото: {e}")
            
            del pinned_messages[chat_id]
            if chat_id in last_pinned_times:
                del last_pinned_times[chat_id]

async def check_pinned_message_exists(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Проверяет, существует ли закрепленное сообщение в чате"""
    try:
        chat = await context.bot.get_chat(chat_id)
        if chat.pinned_message and chat.pinned_message.message_id == pinned_messages.get(chat_id, {}).get("message_id"):
            return True
    except Exception as e:
        logger.error(f"Ошибка при проверке закрепленного сообщения: {e}")
    return False

async def process_new_pinned_message(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, text: str, is_edit: bool = False):
    current_time = time.time()
    message = update.message or update.edited_message
    
    # Проверяем Google таблицу
    text_cleaned = clean_text(text)
    target_message = None
    for word in text_cleaned.split():
        if word in STAR_MESSAGES:
            target_message = STAR_MESSAGES[word]
            break
    
    try:
        # Удаляем старое фото только если это редактирование тем же пользователем
        if is_edit and chat_id in sent_photos and pinned_messages.get(chat_id, {}).get("user_id") == user.id:
            try:
                await context.bot.delete_message(chat_id, sent_photos[chat_id])
                del sent_photos[chat_id]
            except Exception as e:
                logger.error(f"Ошибка удаления старого фото: {e}")
        
        # Отправляем новое фото, если есть в таблице
        photo_message = None
        if target_message and target_message["photo"]:
            photo_message = await context.bot.send_photo(
                chat_id=chat_id,
                photo=target_message["photo"]
            )
            sent_photos[chat_id] = photo_message.message_id
        
        # Закрепляем текстовое сообщение
        await message.pin()
        
        # Сохраняем данные о закреплении
        pinned_messages[chat_id] = {
            "message_id": message.message_id,
            "user_id": user.id,
            "text": text,
            "timestamp": current_time,
            "photo_id": photo_message.message_id if photo_message else None
        }
        
        last_pinned_times[chat_id] = current_time
        last_user_username[chat_id] = user.username or f"id{user.id}"
        
        # Устанавливаем таймер открепления
        context.job_queue.run_once(unpin_message, PINNED_DURATION, chat_id=chat_id)
        
        # Обработка для целевой группы
        if chat_id == TARGET_GROUP_ID:
            logger.info(f"ЗЧ в целевой группе от @{user.username}")
            return
        
        # Если это обычная группа - обрабатываем пересылку в целевую
        await process_target_group_forward(update, context, chat_id, user, text, target_message, current_time, is_edit)
                
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")

async def process_target_group_forward(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                     source_chat_id: int, user, text: str, 
                                     target_message: dict, current_time: float,
                                     is_edit: bool = False):
    try:
        # Проверяем, есть ли активное закрепленное сообщение в целевой группе
        target_has_active_pin = (TARGET_GROUP_ID in pinned_messages and 
                                current_time - pinned_messages[TARGET_GROUP_ID]["timestamp"] < PINNED_DURATION)
        
        # Если в целевой группе есть активная ЗЧ и она была переслана из этой группы
        if target_has_active_pin and pinned_messages[TARGET_GROUP_ID].get("source_chat_id") == source_chat_id:
            # Удаляем старую ЗЧ в целевой группе
            await context.bot.unpin_chat_message(TARGET_GROUP_ID, pinned_messages[TARGET_GROUP_ID]["message_id"])
            if "photo_id" in pinned_messages[TARGET_GROUP_ID]:
                try:
                    await context.bot.delete_message(TARGET_GROUP_ID, pinned_messages[TARGET_GROUP_ID]["photo_id"])
                except Exception as e:
                    logger.error(f"Ошибка удаления фото в целевой группе: {e}")
            del pinned_messages[TARGET_GROUP_ID]
            target_has_active_pin = False
        
        # Если в целевой группе нет активной ЗЧ или она была удалена
        if not target_has_active_pin:
            # Подготавливаем текст для пересылки
            forwarded_text = target_message["message"] if target_message else f"🌟 {text.replace('🌟', '').strip()}"
            
            # Отправляем сообщение в целевую группу
            forwarded = await context.bot.send_message(
                chat_id=TARGET_GROUP_ID,
                text=forwarded_text
            )
            
            # Закрепляем в целевой группе
            await forwarded.pin()
            
            # Сохраняем информацию о закреплении в целевой группе
            pinned_messages[TARGET_GROUP_ID] = {
                "message_id": forwarded.message_id,
                "user_id": user.id,
                "text": forwarded_text,
                "timestamp": current_time,
                "source_chat_id": source_chat_id  # Сохраняем ID исходной группы
            }
            
            # Устанавливаем таймер открепления для целевой группы
            context.job_queue.run_once(unpin_message, PINNED_DURATION, chat_id=TARGET_GROUP_ID)
            
            # Отправляем фото в целевую группу, если есть
            if target_message and target_message["photo"]:
                try:
                    photo_message = await context.bot.send_photo(
                        chat_id=TARGET_GROUP_ID,
                        photo=target_message["photo"]
                    )
                    pinned_messages[TARGET_GROUP_ID]["photo_id"] = photo_message.message_id
                except Exception as e:
                    logger.error(f"Ошибка отправки фото в целевую группу: {e}")
            
            logger.info(f"Сообщение переслано и закреплено в целевой группе")
            
    except Exception as e:
        logger.error(f"Ошибка при обработке целевой группы: {e}")

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
    chat_id = edited_msg.chat.id
    user = edited_msg.from_user
    
    # Проверяем, что это закрепленное сообщение и пользователь является его автором
    if (chat_id in pinned_messages and 
        pinned_messages[chat_id]["message_id"] == edited_msg.message_id and
        (pinned_messages[chat_id]["user_id"] == user.id or await is_admin_or_musician(update, context))):
        
        text = edited_msg.text or edited_msg.caption
        
        # Проверки на бан, разрешенные чаты, мат и рекламу
        if (user.id in banned_users or 
            chat_id not in ALLOWED_CHAT_IDS or
            not await basic_checks(update, context, text)):
            return
        
        # Обрабатываем как новое закрепленное сообщение
        await process_new_pinned_message(update, context, chat_id, user, text, is_edit=True)

async def handle_message_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик удаления сообщений"""
    if update.message.left_chat_member or update.message.new_chat_members:
        return
        
    chat_id = update.message.chat.id
    message_id = update.message.message_id
    
    # Проверяем, было ли это сообщение закрепленной "ЗЧ"
    if chat_id in pinned_messages and pinned_messages[chat_id]["message_id"] == message_id:
        logger.info(f"Удалена закрепленная ЗЧ в чате {chat_id}")
        
        # Сбрасываем таймер для этого чата
        if chat_id in last_pinned_times:
            del last_pinned_times[chat_id]
        
        # Удаляем информацию о закреплении
        del pinned_messages[chat_id]
        
        # Удаляем связанное фото, если оно есть
        if chat_id in sent_photos:
            try:
                await context.bot.delete_message(chat_id, sent_photos[chat_id])
            except Exception as e:
                logger.error(f"Ошибка при удалении фото: {e}")
            del sent_photos[chat_id]

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message or update.edited_message
        if not message:
            return
            
        user = message.from_user

        # Логируем входящее сообщение
        log_text = f"Сообщение от @{user.username or user.id} (ID: {user.id}) в чате {message.chat.id}: "
        if message.text:
            log_text += f"текст: {message.text}"
        elif message.caption:
            log_text += f"подпись: {message.caption}"
        elif message.photo:
            log_text += "фото"
        elif message.sticker:
            log_text += f"стикер ({message.sticker.emoji})"
        else:
            log_text += f"тип контента: {message.content_type}"
        logger.info(log_text)
        
        chat_id = message.chat.id
        # Проверка на неразрешенный чат (добавлено в начало)
        if chat_id not in ALLOWED_CHAT_IDS:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ Я работаю только в разрешенных чатах. Покидаю этот чат."
                )
                await context.bot.leave_chat(chat_id)
                logger.warning(f"Бот вышел из неразрешенного чата {chat_id}")
            except Exception as e:
                logger.error(f"Ошибка при выходе из чата {chat_id}: {e}")
            return
            
        text = message.text or message.caption
        current_time = time.time()
        
        # Проверки на бан, разрешенные чаты, мат и рекламу
        if (user.id in banned_users or 
            chat_id not in ALLOWED_CHAT_IDS or
            not await basic_checks(update, context, text)):
            return

        # Проверка на ЗЧ
        if text and any(marker in text.lower() for marker in ["звезда", "зч", "🌟"]):
            # Получаем текущее закрепленное сообщение из чата
            try:
                chat = await context.bot.get_chat(chat_id)
                current_pinned = chat.pinned_message
                
                # Если есть закрепленное сообщение в данных бота
                if chat_id in pinned_messages:
                    # Если сообщение в чате не соответствует сохраненному - значит оно было удалено
                    if not current_pinned or current_pinned.message_id != pinned_messages[chat_id]["message_id"]:
                        # Сбрасываем таймер, так как сообщение было удалено
                        del pinned_messages[chat_id]
                        if chat_id in last_pinned_times:
                            del last_pinned_times[chat_id]
                        if chat_id in sent_photos:
                            try:
                                await context.bot.delete_message(chat_id, sent_photos[chat_id])
                                del sent_photos[chat_id]
                            except Exception as e:
                                logger.error(f"Ошибка удаления фото: {e}")
            except Exception as e:
                logger.error(f"Ошибка при проверке закрепленного сообщения: {e}")

            # Проверяем, можно ли закрепить новое сообщение
            can_pin = True
            if chat_id in pinned_messages:
                last_pin_time = pinned_messages[chat_id]["timestamp"]
                if current_time - last_pin_time < PINNED_DURATION:
                    can_pin = False
            
            if can_pin:
                await process_new_pinned_message(update, context, chat_id, user, text)
            else:
                if await is_admin_or_musician(update, context):
                    await process_new_pinned_message(update, context, chat_id, user, text, is_edit=True)
                    correction = await context.bot.send_message(
                        chat_id=chat_id,
                        text="Корректировка звезды часа от Админа."
                    )
                    context.job_queue.run_once(
                        lambda ctx: ctx.bot.delete_message(chat_id=chat_id, message_id=correction.message_id),
                        10
                    )
                else:
                    await process_duplicate_message(update, context, chat_id, user)
                
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

# Новая функция для сбора информации о пользователе
async def get_user_info(user) -> str:
    info = [
        f"ID: {user.id}",
        f"Username: @{user.username}" if user.username else "Username: Нет",
        f"Имя: {user.first_name}" if user.first_name else "",
        f"Фамилия: {user.last_name}" if user.last_name else "",
        f"Язык: {user.language_code}" if user.language_code else ""
    ]
    return "\n".join(filter(None, info))

# Новая команда /del
async def delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Ответьте на сообщение, которое нужно удалить")
        return

    target_msg = update.message.reply_to_message
    user = target_msg.from_user

    try:
        # Удаляем целевое сообщение
        await target_msg.delete()
        
        # Удаляем команду /del
        await update.message.delete()
        
        # Отправляем информацию в админскую группу
        report_text = (
            f"🚨 Сообщение удалено администратором @{update.effective_user.username}\n"
            f"📌 Информация об авторе:\n"
            f"{await get_user_info(user)}\n"
            f"📝 Текст сообщения:\n"
            f"{target_msg.text or target_msg.caption or 'Нет текста'}"
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=report_text
        )
        
    except Exception as e:
        logger.error(f"Ошибка в команде /del: {e}")
        await update.message.reply_text("❌ Не удалось удалить сообщение")

# Новая функция для обработки реакций
async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reaction = update.message_reaction
        if not reaction:
            logger.warning("Получен пустой reaction update")
            return
        
        chat_id = reaction.chat.id
        logger.info(f"Реакция в чате {reaction.chat.id} от пользователя {reaction.user.id}")
        
        if chat_id != TRACKED_CHAT_ID:
            return
            
        user = reaction.user
        if user.is_bot:
            return
            
        user_id = user.id
        username = user.username or f"id{user_id}"
        
        logger.info(f"Новая реакция от @{username} (ID: {user_id})")
        
        if user_id not in REACTION_STATS:
            REACTION_STATS[user_id] = {"username": username, "reactions": 0}
            logger.info(f"Новый пользователь в статистике: @{username}")
        
        REACTION_STATS[user_id]["reactions"] += 1
        logger.info(f"Обновлена статистика: @{username} - {REACTION_STATS[user_id]['reactions']} реакций")
        
    except Exception as e:
        logger.error(f"Ошибка в handle_reaction: {e}", exc_info=True)
        
# Команда /clean
async def clean_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов")
        return
        
    global REACTION_STATS
    REACTION_STATS = {}
    
    await update.message.reply_text("📊 Статистика реакций очищена. Начинаем новый подсчет!")

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_musician(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов")
        return
        
    if not REACTION_STATS:
        await update.message.reply_text("📊 Пока нет данных о реакциях")
        return

    # Создаем кнопки
    keyboard = [
        [InlineKeyboardButton("Топ 3", callback_data="top3")],
        [InlineKeyboardButton("Топ 10", callback_data="top10")],
        [InlineKeyboardButton("Все", callback_data="topall")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📊 Выберите вариант статистики:",
        reply_markup=reply_markup
    )

async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    top_type = query.data
    sorted_stats = sorted(REACTION_STATS.items(), key=lambda x: x[1]["reactions"], reverse=True)
    
    if top_type == "top3":
        stats = sorted_stats[:3]
    elif top_type == "top10":
        stats = sorted_stats[:10]
    else:
        stats = sorted_stats
    
    stats_text = "🌟 Топ активных пользователей:\n\n" + "\n".join(
        f"{i+1}. @{data['username']}: {data['reactions']} реакций"
        for i, (_, data) in enumerate(stats)
    )
    
    # Кнопки для выбора чата отправки
    keyboard = [
        [InlineKeyboardButton("Админам", callback_data=f"send_admin_{top_type}")],
        [InlineKeyboardButton("В общий чат", callback_data=f"send_group_{top_type}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"{stats_text}\n\nКуда отправить?",
        reply_markup=reply_markup
    )

async def send_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action, top_type = query.data.split("_")
    sorted_stats = sorted(REACTION_STATS.items(), key=lambda x: x[1]["reactions"], reverse=True)
    
    if top_type == "top3":
        stats = sorted_stats[:3]
    elif top_type == "top10":
        stats = sorted_stats[:10]
    else:
        stats = sorted_stats
    
    stats_text = "🌟 Топ активных пользователей:\n\n" + "\n".join(
        f"{i+1}. @{data['username']}: {data['reactions']} реакций"
        for i, (_, data) in enumerate(stats)
    )
    
    if action == "send_admin":
        await context.bot.send_message(ADMIN_GROUP_ID, stats_text)
    else:
        await context.bot.send_message(TRACKED_CHAT_ID, stats_text)
    
    await query.edit_message_text("✅ Статистика отправлена")
    
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регулярная очистка хранилища
    job_queue = app.job_queue
    job_queue.run_repeating(cleanup_storage, interval=60, first=10)
    
    app.add_handler(CommandHandler("timer", reset_pin_timer))
    app.add_handler(CommandHandler("google", update_google_table))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.ALL & filters.UpdateType.EDITED_MESSAGE, handle_message_edit))
    # Добавляем новый обработчик для команды /del
    app.add_handler(CommandHandler("del", delete_message))
    app.add_handler(MessageReactionHandler(handle_reaction))
    app.add_handler(CommandHandler("stat", show_stats))
    app.add_handler(CommandHandler("clean", clean_stats))
    app.add_handler(CallbackQueryHandler(stats_callback, pattern="^top"))
    app.add_handler(CallbackQueryHandler(send_stats_callback, pattern="^send_"))

    app.run_polling()
    logger.info("Бот запущен")
if __name__ == '__main__':
    main()
