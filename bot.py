import uuid
import os
import io
import logging
import tempfile
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from pydub import AudioSegment
from difflib import get_close_matches

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "7816260297:AAFDjI4_Tvsm9k6t8uymdUGkwD5zSptiCJI"
SALUTE_SPEECH_CLIENT_ID = "0750183c-dc6b-4a49-bc5e-7cfbdc5a93e6"
CLIENT_SECRET = "76c46237-c157-42cd-a71a-e2523bb686fe"
AUTHORIZATION_KEY = "MDc1MDE4M2MtZGM2Yi00YTQ5LWJjNWUtN2NmYmRjNWE5M2U2Ojc2YzQ2MjM3LWMxNTctNDJjZC1hNzFhLWUyNTIzYmI2ODZmZQ=="
TOKEN_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
AUDIO_CHAT_ID = -1002382138419  # ID чата для аудио
TARGET_GROUP_ID = -1002385047417  # ID целевой группы

# Список известных исполнителей
KNOWN_ARTISTS = [
    "Сиа", "Thirty Seconds to Mars", "Lady Gaga", "Джейсон Деруло", "Pink",
    "Maroon five", "Дуа Липа", "OneRepublic", "Imagine Dragons", "Рита Ора",
    "Coldplay", "David Guetta", "Selena Gomez", "The Black Eyed Peas",
    "Ariana Grande", "Justin Timberlake", "Rihanna", "Måneskin", "Манескин",
    "Weeknd", "Shakira", "Ed Sheeran", "Taylor Swift"
]

# Глобальная переменная для токена
SALUTE_SPEECH_TOKEN = None

# Функция для получения токена
def get_access_token():
    global SALUTE_SPEECH_TOKEN
    rq_uid = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": rq_uid,
        "Authorization": f"Basic {AUTHORIZATION_KEY}",
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "SALUTE_SPEECH_PERS",
    }
    try:
        response = requests.post(TOKEN_URL, headers=headers, data=data, verify=False)
        if response.status_code == 200:
            SALUTE_SPEECH_TOKEN = response.json().get("access_token")
            logger.info("Токен Sber SmartSpeech успешно получен.")
        else:
            logger.error(f"Ошибка при получении токена: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Ошибка при отправке запроса на получение токена: {e}")

# Распознавание текста через Sber SmartSpeech
def recognize_audio(audio_data: io.BytesIO) -> str:
    global SALUTE_SPEECH_TOKEN
    if not SALUTE_SPEECH_TOKEN:
        get_access_token()

    url = "https://smartspeech.sber.ru/rest/v1/speech:recognize"
    headers = {
        "Authorization": f"Bearer {SALUTE_SPEECH_TOKEN}",
        "Content-Type": "audio/mpeg",
        "Accept": "application/json",
    }
    try:
        response = requests.post(url, headers=headers, data=audio_data.read(), verify=False)
        if response.status_code == 200:
            result = response.json()
            if "result" in result:
                return result["result"]
            else:
                logger.error(f"Результат не содержит текста: {result}")
        else:
            logger.error(f"Ошибка при распознавании аудио: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Ошибка при отправке запроса на распознавание аудио: {e}")
    return None

# Поиск наиболее подходящего имени исполнителя
def find_closest_artist(text: str) -> str:
    matches = get_close_matches(text, KNOWN_ARTISTS, n=1, cutoff=0.6)
    return matches[0] if matches else None

def convert_ogg_to_mp3(ogg_data: io.BytesIO) -> io.BytesIO:
    try:
        logger.info("Начинаем конвертацию OGG в MP3")
        audio = AudioSegment.from_ogg(ogg_data)
        mp3_data = io.BytesIO()
        audio.export(mp3_data, format="mp3")
        mp3_data.seek(0)
        logger.info("Конвертация OGG в MP3 завершена успешно")
        return mp3_data
    except Exception as e:
        logger.error(f"Ошибка при конвертации OGG в MP3: {e}")
        return None
    
    
# Обработка входящих аудиосообщений (голосовые)
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        voice_file = await update.message.voice.get_file()
        ogg_data = io.BytesIO()
        await voice_file.download_to_memory(out=ogg_data)
        ogg_data.seek(0)

        # Конвертируем OGG в MP3
        mp3_data = convert_ogg_to_mp3(ogg_data)
        if not mp3_data:
            await update.message.reply_text("Ошибка при конвертации аудио.")
            return

        # Обрабатываем аудио
        await process_audio(mp3_data, update, context)
    except Exception as e:
        logger.error(f"Ошибка при обработке голосового сообщения: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")


# Обработка входящих аудиосообщений (MP3)
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Получаем аудиофайл
        audio_file = await update.message.audio.get_file()
        mp3_data = io.BytesIO()
        await audio_file.download_to_memory(out=mp3_data)
        mp3_data.seek(0)

        # Обрабатываем аудио
        await process_music(mp3_data, update, context)

    except Exception as e:
        logger.error(f"Ошибка при обработке аудиофайла: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")

# Обработка текстовых сообщений
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Получаем текст сообщения
        text = update.message.text.lower()

        # Удаляем символы и приводим к нижнему регистру
        clean_text = ''.join(e for e in text if e.isalnum() or e.isspace())

        # Разделяем текст на слова
        words = clean_text.split()

        # Проверяем совпадения с библиотекой имён
        for word in words:
            closest_artist = find_closest_artist(word)
            if closest_artist:
                # Отправляем результат в группу и закрепляем сообщение
                message = await context.bot.send_message(chat_id=TARGET_GROUP_ID, text=f"🌟 Совпадение: {closest_artist}")
                await message.pin()
                break

    except Exception as e:
        logger.error(f"Ошибка при обработке текстового сообщения: {e}")
        await update.message.reply_text("Произошла ошибка при обработке текста.")

# Обрезка аудио
def trim_audio(audio_data: io.BytesIO, start_time: int, end_time: int) -> io.BytesIO:
    try:
        # Проверяем входные данные
        if audio_data.getbuffer().nbytes == 0:
            logger.error("Входные данные пусты.")
            return None

        logger.debug(f"Первые 100 байт данных: {audio_data.getvalue()[:100]}")

        # Читаем аудио из потока с указанием формата
        audio = AudioSegment.from_file(audio_data, format="ogg")  # Для OGG файлов
        logger.info(f"Длительность исходного аудио: {len(audio)} мс")
        
        # Обрезаем аудио
        trimmed_audio = audio[start_time:end_time]
        
        # Экспортируем обрезанное аудио обратно в поток
        output = io.BytesIO()
        trimmed_audio.export(output, format="mp3")
        output.seek(0)
        logger.info("Обрезка аудио завершена успешно")
        return output
    except Exception as e:
        logger.error(f"Ошибка при обрезке аудио: {e}")
        return None
    
async def process_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Получаем аудиофайл
        audio_file = await update.message.audio.get_file()
        audio_data = io.BytesIO()
        await audio_file.download_to_memory(audio_data)
        audio_data.seek(0)

        # Проверяем входные данные
        if audio_data.getbuffer().nbytes == 0:
            logger.error("Входные данные пусты.")
            await update.message.reply_text("Аудиофайл пуст.")
            return

        logger.debug(f"Первые 100 байт данных: {audio_data.getvalue()[:100]}")

        # Ищем фразу "звезда часа"
        trigger_time = find_trigger_in_audio(audio_data)
        if not trigger_time:
            await update.message.reply_text("Фраза 'звезда часа' не найдена.")
            return

        # Обрезаем аудио (2-3 секунды после фразы)
        trimmed_audio = trim_audio(audio_data, trigger_time, trigger_time + 3000)
        if not trimmed_audio:
            await update.message.reply_text("Ошибка при обрезке аудио.")
            return

        # Распознаем текст из обрезанного аудио
        text = recognize_audio(trimmed_audio)
        if not text:
            await update.message.reply_text("Не удалось распознать текст.")
            return

        # Отправляем результаты
        await send_trimmed_audio(update, context, trimmed_audio, text)
    except Exception as e:
        logger.error(f"Ошибка при обработке аудио: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")
    
# Общая функция для обработки аудио
async def process_audio(audio_data: io.BytesIO, update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Ищем фразу "звезда часа"
        trigger_time = find_trigger_in_audio(audio_data)
        if not trigger_time:
            await update.message.reply_text("Фраза 'звезда часа' не найдена.")
            return

        # Обрезаем аудио (2-3 секунды после фразы)
        trimmed_audio = trim_audio(audio_data, trigger_time, trigger_time + 3000)  # Используем audio_data
        if not trimmed_audio:
            await update.message.reply_text("Ошибка при обрезке аудио.")
            return

        # Распознаем текст из обрезанного аудио
        text = recognize_audio(trimmed_audio)
        if not text:
            await update.message.reply_text("Не удалось распознать текст.")
            return

        # Отправляем результаты
        await send_trimmed_audio(update, context, trimmed_audio, text)
    except Exception as e:
        logger.error(f"Ошибка при обработке аудио: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")

# Обработка музыкального аудио
async def process_music(audio_data: io.BytesIO, update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Распознаем текст
        text = recognize_audio(audio_data)
        if not text:
            await update.message.reply_text("Не удалось распознать текст.")
            return

        # Логируем распознанный текст
        logger.info(f"Распознанный текст: {text}")

        # Ищем ключевые слова
        if isinstance(text, list):
            text = " ".join(text)  # Преобразуем список в строку, если текст в виде списка

        if "звезда часа" in text.lower():
            # Конвертируем MP3 в OGG
            ogg_data = convert_mp3_to_ogg(audio_data)
            if not ogg_data:
                await update.message.reply_text("Ошибка при конвертации аудио.")
                return

            # Отправляем голосовое сообщение
            await context.bot.send_document(
                chat_id=AUDIO_CHAT_ID,
                document=mp3_data,
                caption="/search"
            )

    except Exception as e:
        logger.error(f"Ошибка при обработке музыкального аудио: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")

# Отправка обрезанного аудио
# Отправка обрезанного аудио
async def send_trimmed_audio(update: Update, context: ContextTypes.DEFAULT_TYPE, audio_data: io.BytesIO, text: str):
    try:
        # Сохраняем обрезанное аудио во временный файл
        temp_filename = f"trimmed_audio_{os.urandom(8).hex()}.ogg"
        with open(temp_filename, "wb") as temp_file:
            temp_file.write(audio_data.getvalue())

        # Отправляем голосовое сообщение с комментарием /search
        with open(temp_filename, "rb") as audio_file:
            await context.bot.send_voice(
                chat_id=AUDIO_CHAT_ID,
                voice=audio_file,
                caption="/search"
            )

        # Удаляем временный файл
        os.remove(temp_filename)

    except Exception as e:
        logger.error(f"Ошибка при отправке обрезанного аудио: {e}")
        await update.message.reply_text("Не удалось отправить обрезанное аудио.")

# Обработка текстовых сообщений
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Получаем текст сообщения
        text = update.message.text.lower()

        # Удаляем символы и приводим к нижнему регистру
        clean_text = ''.join(e for e in text if e.isalnum() or e.isspace())

        # Разделяем текст на слова
        words = clean_text.split()

        # Проверяем совпадения с библиотекой имён
        for word in words:
            closest_artist = find_closest_artist(word)
            if closest_artist:
                # Отправляем результат в группу и закрепляем сообщение
                message = await context.bot.send_message(chat_id=TARGET_GROUP_ID, text=f"🌟 Совпадение: {closest_artist}")
                await message.pin()
                break

    except Exception as e:
        logger.error(f"Ошибка при обработке текстового сообщения: {e}")
        await update.message.reply_text("Произошла ошибка при обработке текста.")

# Конвертация MP3 в OGG
def convert_mp3_to_ogg(mp3_data: io.BytesIO) -> io.BytesIO:
    try:
        # Проверяем входные данные
        if mp3_data.getbuffer().nbytes == 0:
            logger.error("Входные данные пусты.")
            return None

        logger.debug(f"Первые 100 байт данных: {mp3_data.getvalue()[:100]}")

        # Читаем аудио из потока с указанием формата
        audio = AudioSegment.from_file(mp3_data, format="mp3")  # Для MP3 файлов
        
        # Конвертируем в OGG
        ogg_data = io.BytesIO()
        audio.export(ogg_data, format="ogg")
        ogg_data.seek(0)
        logger.info("Конвертация MP3 в OGG завершена успешно")
        return ogg_data
    except Exception as e:
        logger.error(f"Ошибка при конвертации MP3 в OGG: {e}")
        return None
    
# Основная функция для обработки аудио
async def process_audio(audio_data: io.BytesIO, update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Ищем фразу "звезда часа"
        trigger_time = find_trigger_in_audio(audio_data)
        if not trigger_time:
            await update.message.reply_text("Фраза 'звезда часа' не найдена.")
            return

        # Обрезаем аудио (2-3 секунды после фразы)
        trimmed_audio = trim_audio(audio_data, trigger_time, trigger_time + 3000)
        if not trimmed_audio:
            await update.message.reply_text("Ошибка при обрезке аудио.")
            return

        # Распознаем текст из обрезанного аудио
        text = recognize_audio(trimmed_audio)
        if not text:
            await update.message.reply_text("Не удалось распознать текст.")
            return

        # Отправляем результаты
        await send_trimmed_audio(update, context, trimmed_audio, text)
    except Exception as e:
        logger.error(f"Ошибка при обработке аудио: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")
        
# Основная функция
def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Обработчики
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Запуск бота
    application.run_polling()

if __name__ == "__main__":
    main()
