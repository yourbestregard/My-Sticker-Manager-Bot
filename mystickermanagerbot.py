import logging
import os
import json
import subprocess
import uuid
from urllib.parse import urlparse

from PIL import Image
from telegram import Update, InputSticker
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.constants import StickerFormat
from telegram.error import BadRequest

# Konfigurasi Awal

BOT_TOKEN = "8481920942:AAE2_K7cd40j_6y9impZD0ncuxNy56dxPgc"
# Nama file database JSON
DB_FILE = "sticker_data.json"
# Direktori untuk file sementara
TEMP_DIR = "temp"

# State untuk ConversationHandler untuk membuat pack baru
GET_TITLE, GET_STICKER = range(2)

# Mengatur logging untuk melihat error dan status bot di konsol
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# Fungsi-fungsi Database JSON

def load_data():
    """Memuat data dari file JSON. Jika file tidak ada, kembalikan dictionary kosong."""
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_data(data):
    """Menyimpan data ke file JSON."""
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def set_user_sticker_pack(user_id: int, pack_name: str):
    """Menyimpan nama sticker pack untuk seorang pengguna."""
    data = load_data()
    # Pastikan user_id disimpan sebagai string karena kunci JSON harus string
    data[str(user_id)] = {'sticker_pack_name': pack_name}
    save_data(data)

def get_user_sticker_pack(user_id: int) -> str | None:
    """Mengambil nama sticker pack seorang pengguna."""
    data = load_data()
    return data.get(str(user_id), {}).get('sticker_pack_name')


# Fungsi Pemrosesan Media

def process_media(file_path: str, media_type: str) -> tuple[str, StickerFormat] | None:
    """Memproses gambar atau video menjadi format stiker yang valid."""
    unique_id = str(uuid.uuid4())
    
    if media_type in ['photo', 'sticker_static']:
        output_path = os.path.join(TEMP_DIR, f"{unique_id}.png")
        sticker_format = StickerFormat.STATIC
        with Image.open(file_path) as img:
            # Resize dengan menjaga aspek rasio
            if img.width > img.height:
                new_width, new_height = 512, int(512 * img.height / img.width)
            else:
                new_height, new_width = 512, int(512 * img.width / img.height)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            img.save(output_path, "PNG")
        return output_path, sticker_format

    elif media_type in ['animation', 'video', 'sticker_video']:
        output_path = os.path.join(TEMP_DIR, f"{unique_id}.webm")
        sticker_format = StickerFormat.VIDEO
        try:
            # Perintah FFmpeg untuk konversi ke stiker video (WEBM VP9)
            ffmpeg_command = [
                'ffmpeg', '-i', file_path, '-t', '3',
                '-vf', "scale='min(512,iw)':'min(512,ih)':force_original_aspect_ratio=decrease,fps=30,pad=512:512:-1:-1:color=black@0.0",
                '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p', '-an', '-y', output_path
            ]
            subprocess.run(ffmpeg_command, check=True, capture_output=True, text=True)
            return output_path, sticker_format
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error: {e.stderr}")
            return None
    return None

# Handler Perintah Bot

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /start dan /help."""
    user = update.effective_user
    await update.message.reply_html(
        f"Halo, {user.mention_html()}!\n"
        "Bot ini membantumu membuat dan mengelola sticker pack milikmu.\n\n"
        "/newstickerpack - Membuat pack baru dari nol dengan perintah.\n"
        "/setstickerpack <code>[link_sticker_pack]</code> - Menggunanakan pack yang sudah Anda miliki dengan perintah.\n"
        "/addsticker - Balas (reply) ke media untuk menambahkannya ke pack aktifmu.\n"
        "/cancel - Membatalkan proses pembuatan pack baru.\n"
        "Hubungi @yourbestregard, gabung @azmunaashome."
    )

async def set_sticker_pack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk mengatur sticker pack yang sudah ada."""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "Gagal! Mohon sertakan link sticker pack Anda.\n"
            "Contoh: /setstickerpack https://t.me/addstickers/nama_pack_anda"
        )
        return

    pack_url = context.args[0]
    try:
        pack_name = urlparse(pack_url).path.split('/')[-1]
        
        if not pack_name.strip():
            raise ValueError("Nama pack tidak valid.")
        
        set_user_sticker_pack(user_id, pack_name)
        await update.message.reply_text(
            f"Sticker pack Anda telah diatur ke:\n`{pack_name}`\n"
            "Pastikan Anda adalah **pemilik** dari sticker pack ini.\n"
            "Sekarang Anda bisa menambahkan stiker dengan me-reply media dan mengirim /addsticker.",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error saat mengatur sticker pack: {e}")
        await update.message.reply_text("Link yang Anda berikan sepertinya tidak valid.")

# --- Alur Pembuatan Sticker Pack Baru (ConversationHandler) ---

async def new_pack_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Memulai alur pembuatan pack baru, meminta judul."""
    await update.message.reply_text(
        "Anda akan membuat sticker pack baru.\n"
        "Silakan kirimkan <b>judul</b> untuk pack Anda (misal: 'Kucing Lucu Saya').\n\n"
        "Kirim /cancel untuk membatalkan.",
        parse_mode='HTML'
    )
    return GET_TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Menyimpan judul pack dan meminta stiker pertama."""
    title = update.message.text
    if len(title) > 64:
        await update.message.reply_text("Judul terlalu panjang (maksimal 64 karakter). Coba lagi.")
        return GET_TITLE
        
    context.user_data['pack_title'] = title
    await update.message.reply_text(f"Judul '{title}' diterima. Sekarang, kirimkan media pertama untuk pack ini.")
    return GET_STICKER

async def get_first_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Memproses stiker pertama dan membuat pack baru."""
    message = update.message
    user = update.effective_user
    bot_username = (await context.bot.get_me()).username
    processing_msg = await message.reply_text("Memproses stiker pertama...")

    media_type, file_id = None, None
    if message.photo: media_type, file_id = 'photo', message.photo[-1].file_id
    elif message.animation: media_type, file_id = 'animation', message.animation.file_id
    elif message.video: media_type, file_id = 'video', message.video.file_id
    else:
        await processing_msg.edit_text("Format tidak didukung. Kirimkan gambar, GIF, atau video.")
        return GET_STICKER

    original_file_path = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    output_file_path = None
    
    try:
        file_obj = await context.bot.get_file(file_id)
        await file_obj.download_to_drive(original_file_path)

        processed_result = process_media(original_file_path, media_type)
        if not processed_result: raise ValueError("Gagal memproses media.")
        output_file_path, sticker_format = processed_result
        
        pack_name = f"u{user.id}_by_{bot_username}_{uuid.uuid4().hex[:4]}"
        pack_title = context.user_data['pack_title']

        with open(output_file_path, 'rb') as sticker_file:
            sticker_to_add = InputSticker(sticker_file, ["🙂"], format=sticker_format)
            await context.bot.create_new_sticker_set(
                user.id, pack_name, pack_title, stickers=[sticker_to_add], sticker_format=sticker_format
            )
        
        set_user_sticker_pack(user.id, pack_name)
        pack_link = f"https://t.me/addstickers/{pack_name}"
        await processing_msg.edit_text(
            f"Selamat! Sticker pack '{pack_title}' berhasil dibuat: {pack_link}\n\n"
            "Pack ini sudah diatur sebagai pack aktifmu. Gunakan /addsticker untuk menambahkan stiker lain."
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Gagal membuat pack baru: {e}")
        await processing_msg.edit_text(f"Terjadi kesalahan: {e}\n\nSilakan coba lagi atau kirim /cancel.")
        return GET_STICKER
    finally:
        if os.path.exists(original_file_path): os.remove(original_file_path)
        if output_file_path and os.path.exists(output_file_path): os.remove(output_file_path)
        if 'pack_title' in context.user_data: del context.user_data['pack_title']

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Membatalkan alur pembuatan pack baru."""
    if 'pack_title' in context.user_data: del context.user_data['pack_title']
    await update.message.reply_text("Proses pembuatan sticker pack dibatalkan.")
    return ConversationHandler.END

# --- Alur Penambahan Stiker ke Pack yang Sudah Ada ---

async def add_sticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /addsticker."""
    if not update.message.reply_to_message:
        await update.message.reply_text("Mohon jalankan perintah ini dengan me-reply sebuah media.")
        return

    user_id = update.effective_user.id
    pack_name = get_user_sticker_pack(user_id)

    if not pack_name:
        await update.message.reply_text("Anda belum mengatur sticker pack. Silakan buat dengan /newstickerpack atau atur pack yang sudah ada dengan /setstickerpack.")
        return

    replied_message = update.message.reply_to_message
    media_type, file_id = None, None
    if replied_message.photo: media_type, file_id = 'photo', replied_message.photo[-1].file_id
    elif replied_message.animation: media_type, file_id = 'animation', replied_message.animation.file_id
    elif replied_message.video: media_type, file_id = 'video', replied_message.video.file_id
    elif replied_message.sticker:
        sticker = replied_message.sticker
        if sticker.is_video: media_type = 'sticker_video'
        elif sticker.is_animated:
             await update.message.reply_text("Maaf, stiker animasi berformat .TGS tidak bisa ditambahkan ulang oleh bot.")
             return
        else: media_type = 'sticker_static'
        file_id = sticker.file_id
    else:
        await update.message.reply_text("Format media tidak didukung. Harap reply ke gambar, GIF, video, atau stiker lain.")
        return

    processing_msg = await update.message.reply_text("Memproses media...")
    original_file_path = os.path.join(TEMP_DIR, str(uuid.uuid4()))
    output_file_path = None

    try:
        file_obj = await context.bot.get_file(file_id)
        await file_obj.download_to_drive(original_file_path)

        processed_result = process_media(original_file_path, media_type)
        if not processed_result: raise ValueError("Gagal memproses media.")
        output_file_path, sticker_format = processed_result
        
        with open(output_file_path, 'rb') as sticker_file:
            new_sticker = InputSticker(sticker_file, ["🙂"], format=sticker_format)
            await context.bot.add_sticker_to_set(user_id, pack_name, new_sticker)
        
        pack_link = f"https://t.me/addstickers/{pack_name}"
        await processing_msg.edit_text(f"Berhasil! Stiker ditambahkan ke pack Anda: {pack_link}")

    except BadRequest as e:
        logger.error(f"Gagal menambah stiker (BadRequest): {e}")
        error_message = "Oops, terjadi kesalahan."
        if "STICKERSET_INVALID" in e.message:
            error_message = "Sticker pack tidak ditemukan. Coba atur ulang dengan /setstickerpack."
        elif "STICKERS_TOO_MUCH" in e.message:
            error_message = "Gagal, sticker pack sudah penuh (120 untuk stiker biasa, 50 untuk video)."
        elif "USER_IS_BOT" in e.message:
             error_message = "Terjadi kesalahan internal (USER_IS_BOT)."
        await processing_msg.edit_text(error_message)
    except Exception as e:
        logger.error(f"Gagal menambah stiker (Exception): {e}")
        await processing_msg.edit_text(f"Oops, terjadi kesalahan umum. Pastikan Anda adalah pemilik pack ini.")
    finally:
        if os.path.exists(original_file_path): os.remove(original_file_path)
        if output_file_path and os.path.exists(output_file_path): os.remove(output_file_path)

# --- Fungsi Utama untuk Menjalankan Bot ---

def main():
    """Fungsi utama untuk menjalankan bot."""
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler untuk /newstickerpack
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newstickerpack", new_pack_start)],
        states={
            GET_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)],
            GET_STICKER: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.ANIMATION, get_first_sticker)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Menambahkan semua handler perintah
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start_command))
    # *** PENAMBAHAN BARIS DI BAWAH INI ***
    application.add_handler(CommandHandler("help", start_command))
    application.add_handler(CommandHandler("setstickerpack", set_sticker_pack_command))
    application.add_handler(MessageHandler(filters.COMMAND & filters.Regex(r'^/addsticker$'), add_sticker_command))
    
    logger.info("Bot mulai berjalan...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()