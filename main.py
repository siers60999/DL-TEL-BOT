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

# توکن ربات از متغیرهای محیطی خوانده می‌شود
# قبل از اجرا، این متغیر را در سیستم خود تنظیم کنید
# export BOT_TOKEN='YOUR_TELEGRAM_BOT_TOKEN'
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("توکن ربات تلگرام در متغیرهای محیطی یافت نشد. لطفاً آن را با نام BOT_TOKEN تنظیم کنید.")

# حداکثر حجم مجاز فایل برای دانلود (۱ گیگابایت)
MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024 * 1024

# تنظیمات لاگ‌گیری برای دیباگ بهتر
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ساخت پوشه 'downloads' برای نگهداری موقت فایل‌ها
if not os.path.isdir("downloads"):
    os.makedirs("downloads")

# --- راه‌اندازی ربات و دیسپچر ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- توابع کمکی ---

def is_valid_url(url: str) -> bool:
    """بررسی می‌کند که آیا رشته ورودی یک URL معتبر است یا خیر."""
    regex = re.compile(
        r'^(?:http|ftp)s?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(regex, url) is not None

async def upload_progress(current: int, total: int, message: Message, start_time: float, last_update_time: dict):
    """
    این تابع به عنوان callback برای نمایش وضعیت آپلود استفاده می‌شود
    و برای جلوگیری از اسپم، پیام را در فواصل زمانی مشخص آپدیت می‌کند.
    """
    now = time.time()
    if now - last_update_time['time'] < 2:  # هر ۲ ثانیه یک‌بار آپدیت کن
        return
    last_update_time['time'] = now
    
    try:
        percentage = int(current * 100 / total)
        elapsed_time = now - start_time
        speed = current / elapsed_time if elapsed_time > 0 else 0
        speed_mbps = speed / (1024 * 1024)
        
        await message.edit_text(
            f"در حال آپلود ویدیو...\n\n"
            f"✅ پیشرفت: {percentage}٪\n"
            f"🚀 سرعت: {speed_mbps:.2f} MB/s"
        )
    except TelegramBadRequest:
        # اگر متن پیام تغییری نکرده باشد، تلگرام این خطا را می‌دهد که طبیعی است
        pass
    except Exception as e:
        logger.error(f"خطا در تابع upload_progress: {e}")


# --- هندلرهای ربات ---

@dp.message(CommandStart())
async def handle_start(message: Message):
    """هندلر برای دستور /start."""
    await message.answer(
        "سلام! 👋\n\n"
        "من یک ربات دانلودر ویدیو هستم.\n"
        "فقط کافیه لینک ویدیوی خودت رو از یوتیوب، اینستاگرام یا تیک‌تاک برام بفرستی تا دانلودش کنم."
    )

@dp.message(F.text)
async def handle_url(message: Message):
    """هندلر اصلی برای پردازش لینک‌های ارسالی توسط کاربر."""
    if not message.text or not is_valid_url(message.text):
        await message.reply("لطفاً یک لینک معتبر ارسال کنید.")
        return

    url = message.text
    status_message = await message.reply("⏳ در حال پردازش لینک...")
    file_path = None  # برای استفاده در بلوک finally

    try:
        # --- مرحله ۱: دریافت اطلاعات ویدیو بدون دانلود ---
        await status_message.edit_text("🔍 در حال دریافت اطلاعات ویدیو...")
        
        ydl_opts_info = {'quiet': True, 'noplaylist': True, 'skip_download': True}
        with YoutubeDL(ydl_opts_info) as ydl:
            loop = asyncio.get_event_loop()
            info_dict = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))

        # --- مرحله ۲: انتخاب بهترین فرمت و بررسی حجم فایل ---
        # برای یوتیوب کیفیت 720p و برای بقیه (ریلز/تیک‌تاک) بهترین کیفیت انتخاب می‌شود
        if 'youtube' in info_dict.get('extractor_key', '').lower():
            format_selector = 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best[ext=mp4]'
        else:
            format_selector = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'

        ydl_opts_download = {
            'format': format_selector,
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'noplaylist': True,
            'quiet': True,
            'merge_output_format': 'mp4',
        }

        # دوباره اطلاعات را با فرمت انتخابی می‌گیریم تا حجم دقیق مشخص شود
        with YoutubeDL(ydl_opts_download) as ydl:
             info_dict_with_format = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))

        filesize = info_dict_with_format.get('filesize') or info_dict_with_format.get('filesize_approx')
        if filesize and filesize > MAX_FILE_SIZE_BYTES:
            await status_message.edit_text(
                f"❌ خطا: حجم این ویدیو ({filesize / (1024*1024):.2f} مگابایت) بیشتر از حد مجاز (۱ گیگابایت) است."
            )
            return
            
        # --- مرحله ۳: دانلود ویدیو ---
        await status_message.edit_text("📥 در حال دانلود ویدیو... لطفاً صبور باشید، این مرحله ممکن است طول بکشد.")
        
        with YoutubeDL(ydl_opts_download) as ydl:
            await loop.run_in_executor(None, lambda: ydl.download([url]))
            
        # پیدا کردن مسیر فایل دانلود شده
        file_path = ydl.prepare_filename(info_dict)
        if not os.path.exists(file_path):
             base, _ = os.path.splitext(file_path)
             file_path = base + ".mp4"
             if not os.path.exists(file_path):
                 raise FileNotFoundError("فایل دانلود شده روی سرور پیدا نشد!")

        # --- مرحله ۴: آپلود در تلگرام ---
        start_time = time.time()
        last_update_time = {'time': 0}  # برای کنترل نرخ آپدیت پیام
        
        # استفاده از lambda برای ارسال آرگومان‌های اضافی به تابع callback
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

    except TelegramEntityTooLarge:
        logger.error(f"خطای حجم فایل تلگرام برای URL: {url}")
        await status_message.edit_text("❌ خطا: حجم ویدیو برای آپلود در تلگرام بیش از حد بزرگ است.")
    except Exception as e:
        logger.error(f"خطای کلی برای URL {url}: {e}", exc_info=True)
        error_message = "❌ متاسفانه خطایی در پردازش لینک شما رخ داد.\n\n"
        if "Unsupported URL" in str(e):
            error_message += "این لینک پشتیبانی نمی‌شود."
        elif "Private video" in str(e) or "login is required" in str(e):
            error_message += "این ویدیو خصوصی است و قابل دانلود نیست."
        else:
            error_message += "لطفاً لینک را بررسی کرده و دوباره تلاش کنید."
        await status_message.edit_text(error_message)

    finally:
        # --- مرحله ۵: پاک‌سازی ---
        # فایل دانلود شده از روی دیسک حذف می‌شود تا فضا اشغال نشود
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"فایل با موفقیت حذف شد: {file_path}")
            except OSError as e:
                logger.error(f"خطا در حذف فایل {file_path}: {e}")


# --- تابع اصلی برای اجرای ربات ---
async def main():
    """تابع اصلی برای راه‌اندازی ربات."""
    logger.info("ربات در حال اجرا است...")
    # شروع long polling
    await dp.start_polling(bot)

if __name__ == '__main__':
    if BOT_TOKEN is None:
        logger.critical("متغیر محیطی BOT_TOKEN تنظیم نشده است. برنامه خاتمه یافت.")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("ربات توسط کاربر متوقف شد.")


