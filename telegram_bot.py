import sqlite3
import logging
import schedule
import time
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from datetime import datetime

# Konfigurasi logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Inisialisasi database
def init_db():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                  last_name TEXT, registered_at TEXT, is_admin INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                  message TEXT, remind_at TEXT)''')
    conn.commit()
    conn.close()

# Fungsi untuk menambahkan pengguna ke database
def add_user(update: Update):
    user = update.effective_user
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users 
                 (user_id, username, first_name, last_name, registered_at, is_admin)
                 VALUES (?, ?, ?, ?, ?, ?)''',
                 (user.id, user.username, user.first_name, user.last_name,
                  datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 0))
    conn.commit()
    conn.close()

# Fungsi untuk cek admin
def is_admin(user_id: int) -> bool:
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result and result[0] == 1

# Menu utama
def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("📝 Set Reminder", callback_data='set_reminder'),
            InlineKeyboardButton("📋 List Reminders", callback_data='list_reminders')
        ],
        [InlineKeyboardButton("ℹ️ About", callback_data='about')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Handler untuk command /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update)
    user = update.effective_user
    welcome_text = (
        f"Halo {user.first_name}!\n"
        "Selamat datang di Bot Multifungsi!\n"
        "Gunakan menu di bawah untuk berinteraksi:"
    )
    await update.message.reply_text(welcome_text, reply_markup=main_menu())
    logger.info(f"User {user.id} started the bot")

# Handler untuk set reminder
async def set_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['setting_reminder'] = True
    await query.message.reply_text(
        "Kirim format reminder: pesan|waktu (contoh: Meeting|2025-06-24 10:00)"
    )

# Handler untuk pesan teks
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    if context.user_data.get('setting_reminder'):
        try:
            message, remind_time = text.split('|')
            remind_time = remind_time.strip()
            datetime.strptime(remind_time, '%Y-%m-%d %H:%M')
            
            conn = sqlite3.connect('bot_database.db')
            c = conn.cursor()
            c.execute('''INSERT INTO reminders (user_id, message, remind_at)
                        VALUES (?, ?, ?)''',
                     (user.id, message.strip(), remind_time))
            conn.commit()
            conn.close()
            
            context.user_data['setting_reminder'] = False
            await update.message.reply_text(
                f"Reminder diset: {message} pada {remind_time}",
                reply_markup=main_menu()
            )
        except ValueError:
            await update.message.reply_text(
                "Format salah! Gunakan: pesan|YYYY-MM-DD HH:MM"
            )
    else:
        await update.message.reply_text(
            "Gunakan menu untuk berinteraksi!", reply_markup=main_menu()
        )

# Handler untuk list reminders
async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('SELECT id, message, remind_at FROM reminders WHERE user_id = ?',
              (user.id,))
    reminders = c.fetchall()
    conn.close()
    
    if reminders:
        response = "📋 Daftar Reminder:\n\n"
        for reminder in reminders:
            response += f"ID: {reminder[0]}\nPesan: {reminder[1]}\nWaktu: {reminder[2]}\n\n"
    else:
        response = "Belum ada reminder!"
    
    await query.message.reply_text(response, reply_markup=main_menu())

# Handler untuk about
async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "Bot Multifungsi v1.0\n"
        "Fitur:\n- Reminder\n- Database\n- Menu Interaktif\n"
        "Dibuat dengan ❤️ menggunakan Python",
        reply_markup=main_menu()
    )

# Handler untuk broadcast (admin only)
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("Perintah hanya untuk admin!")
        return
    
    if not context.args:
        await update.message.reply_text("Gunakan: /broadcast pesan")
        return
    
    message = ' '.join(context.args)
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    users = c.fetchall()
    conn.close()
    
    for user_id in users:
        try:
            await context.bot.send_message(
                chat_id=user_id[0],
                text=f"📢 Broadcast:\n{message}"
            )
        except Exception as e:
            logger.error(f"Failed to send broadcast to {user_id[0]}: {e}")
    
    await update.message.reply_text("Broadcast terkirim!")

# Fungsi untuk cek dan kirim reminder
def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    c.execute('SELECT id, user_id, message FROM reminders WHERE remind_at = ?',
              (current_time,))
    reminders = c.fetchall()
    
    for reminder in reminders:
        context.job_queue.run_once(
            send_reminder,
            0,
            data={'user_id': reminder[1], 'message': reminder[2], 'reminder_id': reminder[0]}
        )
    
    conn.close()

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.data['user_id']
    message = job.data['message']
    reminder_id = job.data['reminder_id']
    
    await context.bot.send_message(
        chat_id=user_id,
        text=f"⏰ Reminder: {message}"
    )
    
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('DELETE FROM reminders WHERE id = ?', (reminder_id,))
    conn.commit()
    conn.close()

# Handler untuk foto
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Foto diterima! Saat ini hanya bisa menyimpan, belum bisa diproses.",
        reply_markup=main_menu()
    )

# Handler error
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

def main():
    init_db()
    
    # Ganti dengan token bot Anda
    application = Application.builder().token('YOUR_BOT_TOKEN').build()
    
    # Tambahkan handler
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('broadcast', broadcast))
    application.add_handler(CallbackQueryHandler(set_reminder, pattern='^set_reminder$'))
    application.add_handler(CallbackQueryHandler(list_reminders, pattern='^list_reminders$'))
    application.add_handler(CallbackQueryHandler(about, pattern='^about$'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_error_handler(error_handler)
    
    # Jadwalkan pengecekan reminder setiap menit
    application.job_queue.run_repeating(
        check_reminders,
        interval=60,
        first=0
    )
    
    # Mulai bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()