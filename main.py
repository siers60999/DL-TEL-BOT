# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import re
import time
from yt_dlp import YoutubeDL
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge

# --- پیکربندی اولیه ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("توکن ربات تلگرام در متغیرهای محیطی یافت نشد.")

# مسیر فایل کوکی که به صورت موقت ساخته می‌شود
COOKIE_FILE_PATH = '/tmp/cookies.txt'

MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024 * 1024
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

if not os.path.isdir("downloads"):
    os.makedirs("downloads")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- توابع کمکی ---

def setup_cookies():
    """بررسی می‌کند که آیا کوکی در متغیرهای محیطی وجود دارد و آن را در فایل می‌نویسد."""
    cookie_content = os.environ.get('YTDL_COOKIES')
    if cookie_content:
        try:
            with open(COOKIE_FILE_PATH, 'w') as f:
                f.write(cookie_content)
            logger.info("فایل کوکی با موفقیت از متغیر محیطی ساخته شد.")
            return True
        except Exception as e:
            logger.error(f"خطا در ساخت فایل کوکی: {e}")
            return False
    logger.warning("متغیر محیطی کوکی (YTDL_COOKIES) یافت نشد. بدون کوکی ادامه می‌دهیم.")
    return False

def is_valid_url(url: str) -> bool:
    regex = re.compile(
        r'^(?:http|ftp)s?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(regex, url) is not None

async def upload_progress(current: int, total: int, message: Message, start_time: float, last_update_time: dict):
    now = time.time()
    if now - last_update_time['time'] < 2:
        return
    last_update_time['time'] = now
    
    try:
        percentage = int(current * 100 / total)
        await message.edit_text(f"در حال آپلود ویدیو... {percentage}٪")
    except TelegramBadRequest:
        pass # If message is not modified
    except Exception as e:
        logger.error(f"خطا در تابع upload_progress: {e}")

# --- هندلرهای ربات ---

@dp.message(CommandStart())
async def handle_start(message: Message):
    await message.answer(
        "سلام! 👋\n\n"
        "من یک ربات دانلودر ویدیو هستم.\n"
        "فقط کافیه لینک ویدیوی خودت رو از یوتیوب، اینستاگرام یا تیک‌تاک برام بفرستی تا دانلودش کنم."
    )

@dp.message(F.text)
async def handle_url(message: Message):
    if not message.text or not is_valid_url(message.text):
        await message.reply("لطفاً یک لینک معتبر ارسال کنید.")
        return

    url = message.text
    status_message = await message.reply("⏳ در حال پردازش لینک...")
    file_path = None
    cookie_file_created = False

    try:
        # --- استفاده از کوکی در صورت وجود ---
        ydl_opts_base = {
            'quiet': True,
            'noplaylist': True,
        }
        if setup_cookies():
            ydl_opts_base['cookiefile'] = COOKIE_FILE_PATH
            cookie_file_created = True

        await status_message.edit_text("🔍 در حال دریافت اطلاعات ویدیو...")
        
        ydl_opts_info = {**ydl_opts_base, 'skip_download': True}
        with YoutubeDL(ydl_opts_info) as ydl:
            loop = asyncio.get_event_loop()
            info_dict = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))

        if 'youtube' in info_dict.get('extractor_key', '').lower():
            format_selector = 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best[ext=mp4]'
        else:
            format_selector = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'

        ydl_opts_download = {
            **ydl_opts_base,
            'format': format_selector,
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'merge_output_format': 'mp4',
        }
        
        with YoutubeDL(ydl_opts_download) as ydl:
             info_dict_with_format = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))

        filesize = info_dict_with_format.get('filesize') or info_dict_with_format.get('filesize_approx')
        if filesize and filesize > MAX_FILE_SIZE_BYTES:
            await status_message.edit_text(f"❌ حجم ویدیو بیش از ۱ گیگابایت است.")
            return
            
        await status_message.edit_text("📥 در حال دانلود ویدیو...")
        
        with YoutubeDL(ydl_opts_download) as ydl:
            await loop.run_in_executor(None, lambda: ydl.download([url]))
            
        file_path = ydl.prepare_filename(info_dict)
        if not os.path.exists(file_path):
             base, _ = os.path.splitext(file_path)
             file_path = base + ".mp4"
             if not os.path.exists(file_path):
                 raise FileNotFoundError("فایل دانلود شده پیدا نشد!")

        start_time = time.time()
        last_update_time = {'time': 0}
        progress_callback = lambda current, total: upload_progress(current, total, status_message, start_time, last_update_time)
        
        await status_message.edit_text("📤 در حال آپلود ویدیو...")
        
        video_file = FSInputFile(file_path)
        await bot.send_video(
            chat_id=message.chat.id,
            video=video_file,
            caption=info_dict.get('title', 'ویدیوی شما'),
            reply_to_message_id=message.message_id,
            progress=progress_callback
        )
        await status_message.delete()

    except Exception as e:
        logger.error(f"خطای کلی برای URL {url}: {e}", exc_info=True)
        await status_message.edit_text("❌ متاسفانه خطایی در پردازش لینک شما رخ داد.")

    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"فایل با موفقیت حذف شد: {file_path}")
            except OSError as e:
                logger.error(f"خطا در حذف فایل {file_path}: {e}")
        
        # پاک کردن فایل کوکی موقت
        if cookie_file_created and os.path.exists(COOKIE_FILE_PATH):
            os.remove(COOKIE_FILE_PATH)


async def main():
    logger.info("ربات در حال اجرا است...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    if BOT_TOKEN is None:
        logger.critical("متغیر محیطی BOT_TOKEN تنظیم نشده است.")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("ربات توسط کاربر متوقف شد.")

