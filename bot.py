import os
import io
import logging
import tempfile
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from moviepy.editor import AudioFileClip
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
def recognize_audio(audio_path: str) -> str:
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
        with open(audio_path, "rb") as audio_file:
            response = requests.post(url, headers=headers, data=audio_file.read(), verify=False)
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

# Обработка входящих аудиосообщений (голосовые)
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Получаем файл голосового сообщения
        voice_file = await update.message.voice.get_file()
        ogg_data = io.BytesIO()
        await voice_file.download_to_memory(out=ogg_data)
        ogg_data.seek(0)

        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_ogg:
            temp_ogg.write(ogg_data.read())
            temp_ogg_path = temp_ogg.name

        # Конвертируем в MP3
        mp3_path = convert_ogg_to_mp3(temp_ogg_path)
        if not mp3_path:
            await update.message.reply_text("Ошибка при конвертации аудио.")
            return

        # Обрабатываем аудио
        await process_audio(mp3_path, update, context)

    except Exception as e:
        logger.error(f"Ошибка при обработке голосового сообщения: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")

# Обработка входящих аудиосообщений (MP3)
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Получаем файл аудио
        audio_file = await update.message.audio.get_file()
        mp3_data = io.BytesIO()
        await audio_file.download_to_memory(out=mp3_data)
        mp3_data.seek(0)

        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_mp3:
            temp_mp3.write(mp3_data.read())
            temp_mp3_path = temp_mp3.name

        # Обрабатываем аудио
        await process_audio(temp_mp3_path, update, context)

    except Exception as e:
        logger.error(f"Ошибка при обработке аудиофайла: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")

# Конвертация OGG в MP3
def convert_ogg_to_mp3(ogg_path: str) -> str:
    try:
        mp3_path = ogg_path.replace(".ogg", ".mp3")
        audio_clip = AudioFileClip(ogg_path)
        audio_clip.write_audiofile(mp3_path, codec="libmp3lame")
        audio_clip.close()
        return mp3_path
    except Exception as e:
        logger.error(f"Ошибка при конвертации аудио: {e}")
        return None

# Общая функция для обработки аудио
async def process_audio(audio_path: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Распознаем текст
        text = recognize_audio(audio_path)
        if not text:
            await update.message.reply_text("Не удалось распознать текст.")
            return

        # Логируем распознанный текст
        logger.info(f"Распознанный текст: {text}")

        # Ищем ключевые слова
        if "звезда часа" in text.lower():
            parts = text.lower().split("звезда часа")
            if len(parts) > 1:
                next_words = parts[1].strip().split()[:4]  # Берем до 4 слов
                keyword = " ".join(next_words).title()

                # Проверяем, является ли найденное слово известным исполнителем
                closest_artist = find_closest_artist(keyword)
                if closest_artist:
                    # Отправляем результат в группу и закрепляем сообщение
                    message = await context.bot.send_message(chat_id=TARGET_GROUP_ID, text=f"🌟 Звезда часа: {closest_artist}")
                    await message.pin()
                else:
                    # Если не удалось найти исполнителя, отправляем обрезанное аудио
                    await send_trimmed_audio(audio_path, update, context, text)
        else:
            await update.message.reply_text("Ключевая фраза 'звезда часа' не найдена.")

    except Exception as e:
        logger.error(f"Ошибка при обработке аудио: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")

# Отправка обрезанного аудио
async def send_trimmed_audio(audio_path: str, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        # Обрезаем аудио (начиная с момента фразы "звезда часа" + 3 секунды)
        trigger_time = find_trigger_in_audio(text)
        trimmed_audio_path = trim_audio(audio_path, trigger_time, trigger_time + 3)

        # Отправляем аудио в группу
        with open(trimmed_audio_path, "rb") as audio_file:
            await context.bot.send_audio(
                chat_id=TARGET_GROUP_ID,
                audio=audio_file,
                caption="Я не понял, кто звезда часа. Прослушайте и напишите в чат 'звезда часа' и нужное имя."
            )

        # Удаляем временный файл
        os.remove(trimmed_audio_path)

    except Exception as e:
        logger.error(f"Ошибка при отправке обрезанного аудио: {e}")
        await update.message.reply_text("Не удалось отправить обрезанное аудио.")

# Поиск времени начала фразы "звезда часа"
def find_trigger_in_audio(text: str) -> int:
    words_before = text.lower().split("звезда часа")[0].split()
    estimated_start_time = len(words_before) * 500  # Примерно 500 мс на слово
    return estimated_start_time

# Обрезка аудио
def trim_audio(audio_path: str, start_time: int, end_time: int) -> str:
    try:
        trimmed_audio_path = audio_path.replace(".mp3", "_trimmed.mp3")
        audio_clip = AudioFileClip(audio_path).subclip(start_time / 1000, end_time / 1000)
        audio_clip.write_audiofile(trimmed_audio_path, codec="libmp3lame")
        audio_clip.close()
        return trimmed_audio_path
    except Exception as e:
        logger.error(f"Ошибка при обрезке аудио: {e}")
        return None

# Основная функция
def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Обработчики
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))  # Голосовые сообщения
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))  # Аудиофайлы

    # Запуск бота
    application.run_polling()

if __name__ == "__main__":
    main()
