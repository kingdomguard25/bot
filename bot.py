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

# Обработка входящих аудиосообщений (голосовые)
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Получаем аудиофайл
        voice_file = await update.message.voice.get_file()
        ogg_data = io.BytesIO()
        await voice_file.download_to_memory(out=ogg_data)
        ogg_data.seek(0)

        # Конвертируем в MP3
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
        await process_audio(mp3_data, update, context)

    except Exception as e:
        logger.error(f"Ошибка при обработке аудиофайла: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")

# Поиск фразы "звезда часа" в аудио
def find_trigger_in_audio(audio_data: io.BytesIO) -> int:
    """Ищет фразу 'звезда часа' в аудио и возвращает временную метку начала фразы."""
    try:
        text = recognize_audio(audio_data)
        if text and "звезда часа" in text.lower():
            # Ищем позицию фразы в тексте
            start_index = text.lower().find("звезда часа")
            if start_index == -1:
                return None
            # Оцениваем время начала фразы (приблизительно)
            words_before = text[:start_index].split()
            estimated_start_time = len(words_before) * 500  # Примерно 500 мс на слово
            return estimated_start_time
        return None
    except Exception as e:
        logger.error(f"Ошибка при поиске триггера в аудио: {e}")
        return None

# Общая функция для обработки аудио
async def process_audio(audio_data: io.BytesIO, update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Ищем фразу "звезда часа"
        trigger_time = find_trigger_in_audio(audio_data)
        if not trigger_time:
            await update.message.reply_text("Фраза 'звезда часа' не найдена.")
            return

        # Обрезаем аудио (начиная с момента фразы + 3 секунды)
        trimmed_audio = trim_audio(audio_data, trigger_time, trigger_time + 3000)  # 3000 мс = 3 секунды
        if not trimmed_audio:
            await update.message.reply_text("Ошибка при обрезке аудио.")
            return

        # Распознаем текст из обрезанного аудио
        text = recognize_audio(trimmed_audio)
        if not text:
            await update.message.reply_text("Не удалось распознать текст.")
            return

        # Ищем совпадение в гугл-таблице
        result = search_google_table(text)

        # Отправляем аудио и результаты в чат для обработки
        await send_audio_and_results(context, AUDIO_CHAT_ID, trimmed_audio, text, result)

        # Отправляем аудио и результаты в целевую группу
        await send_audio_and_results(context, TARGET_GROUP_ID, trimmed_audio, text, result)

    except Exception as e:
        logger.error(f"Ошибка при обработке аудио: {e}")
        await update.message.reply_text("Произошла ошибка при обработке аудио.")

# Обрезка аудио
def trim_audio(audio_data: io.BytesIO, start_time: int, end_time: int) -> io.BytesIO:
    """Обрезает аудио до указанного временного интервала."""
    try:
        audio = AudioSegment.from_file(audio_data)
        logger.info(f"Длительность исходного аудио: {len(audio)} мс")
        trimmed_audio = audio[start_time:end_time]
        logger.info(f"Длительность обрезанного аудио: {len(trimmed_audio)} мс")

        output = io.BytesIO()
        trimmed_audio.export(output, format="mp3")
        output.seek(0)
        return output
    except Exception as e:
        logger.error(f"Ошибка при обрезке аудио: {e}")
        return None

# Конвертация OGG в MP3
def convert_ogg_to_mp3(ogg_data: io.BytesIO) -> io.BytesIO:
    try:
        audio = AudioSegment.from_ogg(ogg_data)
        mp3_data = io.BytesIO()
        audio.export(mp3_data, format="mp3")
        mp3_data.seek(0)
        return mp3_data
    except Exception as e:
        logger.error(f"Ошибка при конвертации аудио: {e}")
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
