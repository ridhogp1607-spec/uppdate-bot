import os
import sys
import logging
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.constants import ChatAction
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Output UTF-8 di terminal Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6745837028"))

# Setup Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Watermark / Kredit Pembuat di bawah pesan bot
CREDIT_FOOTER = "\n\n—\n_Project Mahasiswa Teknik Informatika 24_"

def with_credit(text: str) -> str:
    """Menambahkan watermark kredit pembuat ke pesan bot"""
    return f"{text}{CREDIT_FOOTER}"

# ==========================================
# HEALTH-CHECK SERVER (UNTUK RENDER / KOYEB)
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"UPPdate Bot is Running 24/7!")

    def log_message(self, format, *args):
        return  # Heningkan log HTTP agar tidak mengotori terminal

def start_health_server():
    """Menjalankan server web mini di background untuk Render.com port binding"""
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"🌐 Health-check server aktif di port {port}")
    except Exception as e:
        print(f"⚠️ Warning: Gagal memulai health server: {e}")

# ==========================================
# 1. DATABASE SQLITE (PROFIL & MODERASI)
# ==========================================
DB_PATH = "uppdate.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Tabel Pengguna
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            gender TEXT,
            faculty TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Tabel Pengguna yang Diblokir (Banned)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_user_profile(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT gender, faculty FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] and row[1]:
        return {"gender": row[0], "faculty": row[1]}
    return None

def save_user_profile(user_id: int, username: str, gender: str, faculty: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, username, gender, faculty)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            gender = excluded.gender,
            faculty = excluded.faculty
    """, (user_id, username, gender, faculty))
    conn.commit()
    conn.close()

def is_user_banned(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    banned = cursor.fetchone() is not None
    conn.close()
    return banned

def ban_user_db(user_id: int, reason: str = "Melanggar etika"):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO banned_users (user_id, reason)
        VALUES (?, ?)
    """, (user_id, reason))
    conn.commit()
    conn.close()

def unban_user_db(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def count_stats():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM banned_users")
    total_banned = cursor.fetchone()[0]
    conn.close()
    return total_users, total_banned

init_db()

# ==========================================
# 2. DAFTAR FAKULTAS RESMI DI UPP
# ==========================================
FACULTIES = {
    "FILKOM": "💻 Fakultas Ilmu Komputer (FILKOM)",
    "FT": "⚙️ Fakultas Teknik (FT)",
    "FKIP": "🎓 FKIP",
    "FE": "📈 Fakultas Ekonomi (FE)",
    "FP": "🌾 Fakultas Pertanian (FP)",
    "FH": "⚖️ Fakultas Hukum (FH)",
    "FIK": "🏥 Fakultas Ilmu Kesehatan (FIK)"
}

GENDERS = {
    "L": "👨 Laki-laki",
    "P": "👩 Perempuan"
}

# ==========================================
# 3. STATE MANAGEMENT DI MEMORI (REALTIME)
# ==========================================
active_chats = {}       # user_id -> partner_id
reveal_requests = {}    # user_id -> set of confirmed reveal users
waiting_queue = []      # antrean pencarian
temp_setup = {}         # state sementara saat setup filter

def get_main_keyboard():
    """Menu tombol keyboard bawah yang rapi dan mudah diakses"""
    keyboard = [
        [KeyboardButton("🔍 Cari Partner"), KeyboardButton("🛑 Berhenti")],
        [KeyboardButton("🔓 Buka Identitas"), KeyboardButton("👤 Profil Saya")],
        [KeyboardButton("ℹ️ Bantuan")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==========================================
# 4. HANDLER START & BANTUAN
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pesan pembuka singkat, jelas, dengan panduan perintah"""
    user = update.effective_user

    if is_user_banned(user.id):
        await update.message.reply_text("⛔ Akun Anda telah ditangguhkan secara permanen karena melanggar etika komunitas UPP.")
        return

    welcome_text = (
        f"👋 Halo, **{user.first_name}**!\n\n"
        "Selamat datang di **UPPdate** 🎓\n"
        "*(Bot Anonymous Chat Mahasiswa Universitas Pasir Pengaraian)*\n\n"
        "Wadah buat kamu yang mau ngobrol anonim, curhat seputar kampus, atau cari kenalan baru sesama anak UPP tanpa ketahuan identitas aslimu!\n\n"
        "📌 **Panduan Fitur & Perintah:**\n"
        "• /cari atau tombol **🔍 Cari Partner** : Mulai mencari lawan bicara\n"
        "• /berhenti atau tombol **🛑 Berhenti** : Mengakhiri obrolan saat ini\n"
        "• /buka atau tombol **🔓 Buka Identitas** : Mengajak partner saling bertukar kontak Telegram\n"
        "• /profil atau tombol **👤 Profil Saya** : Melihat atau mengubah data profil\n"
        "• /lapor : Melaporkan partner jika melanggar etika/berkata kasar\n"
        "• /bantuan atau tombol **ℹ️ Bantuan** : Panduan lengkap & etika penggunaan\n\n"
        "Silakan tekan tombol **🔍 Cari Partner** di bawah untuk mulai!"
    )
    await update.message.reply_text(
        with_credit(welcome_text),
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def bantuan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /bantuan dan /help"""
    help_text = (
        "📖 **PANDUAN & BANTUAN UPPDATE** 🎓\n\n"
        "UPPdate adalah bot chat anonim resmi untuk mahasiswa Universitas Pasir Pengaraian.\n\n"
        "📌 **Daftar Perintah:**\n"
        "• /cari : Mulai mencari lawan bicara\n"
        "• /berhenti : Mengakhiri obrolan yang sedang berjalan\n"
        "• /next : Mengakhiri dan langsung mencari partner baru\n"
        "• /buka : Mengajak partner saling bertukar kontak Telegram\n"
        "• /profil : Melihat atau mengubah gender dan fakultas kamu\n"
        "• /lapor : Melaporkan partner jika melanggar etika obrolan\n"
        "• /bantuan : Menampilkan pesan bantuan ini\n\n"
        "💡 **Tips Aman Mengobrol:**\n"
        "1. Identitas kamu 100% dirahasiakan oleh bot.\n"
        "2. Identitas hanya akan terbuka jika KAMU dan PARTNER sama-sama menekan tombol 'Buka Identitas'.\n"
        "3. Harap menjaga kesopanan sesama mahasiswa UPP. Pelanggaran berat atau pelecehan akan langsung diblokir permanen."
    )
    await update.message.reply_text(
        with_credit(help_text),
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Melihat atau mengubah profil pengguna"""
    user = update.effective_user
    profile = get_user_profile(user.id)

    if profile:
        text = (
            "👤 **Profil Mahasiswa UPP**\n\n"
            f"• Jenis Kelamin: {GENDERS.get(profile['gender'])}\n"
            f"• Fakultas: {FACULTIES.get(profile['faculty'])}\n\n"
            "Mau ubah data profil kamu?"
        )
    else:
        text = "Kamu belum melengkapi profil. Silakan pilih jenis kelamin kamu dulu yuk:"

    keyboard = [
        [
            InlineKeyboardButton("✏️ Ubah Profil", callback_data="profile_reset")
        ]
    ]
    await update.message.reply_text(
        with_credit(text),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==========================================
# 5. PENCARIAN PARTNER (MATCHMAKING)
# ==========================================
async def start_search_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Langkah pencarian dengan pengecekan profil dan status banned"""
    user = update.effective_user
    user_id = user.id

    if is_user_banned(user_id):
        await update.message.reply_text("⛔ Akun Anda telah ditangguhkan secara permanen karena melanggar etika komunitas UPP.")
        return

    if user_id in active_chats:
        await update.message.reply_text(
            with_credit("⚠️ Kamu masih terhubung sama partner obrolan! Tekan **🛑 Berhenti** dulu jika ingin berganti partner."),
            parse_mode="Markdown"
        )
        return

    profile = get_user_profile(user_id)

    # JIKA BELUM ADA PROFIL: MUNCULKAN INPUT IDENTITAS DULU
    if not profile:
        text = (
            "📝 **Lengkapi Profil Kamu**\n\n"
            "Sebelum mulai mencari partner, pilih jenis kelamin kamu dulu yuk:\n"
        )
        keyboard = [
            [
                InlineKeyboardButton("👨 Laki-laki", callback_data="reg_gender:L"),
                InlineKeyboardButton("👩 Perempuan", callback_data="reg_gender:P")
            ]
        ]
        await update.message.reply_text(
            with_credit(text),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # JIKA SUDAH ADA PROFIL: LANGSUNG KE PILIHAN PREFERENSI
    remove_from_queue(user_id)
    await show_gender_preference(update, context, user_id)

async def show_gender_preference(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Menampilkan pilihan filter lawan jenis atau acak beserta info profil aktif"""
    profile = get_user_profile(user_id)
    profile_info = ""
    if profile:
        profile_info = (
            f"👤 Profil Kamu: **{GENDERS.get(profile['gender'])} • {FACULTIES.get(profile['faculty'])}**\n"
            "_(Tekan tombol '👤 Profil Saya' jika ingin mengubah)_\n\n"
        )

    text = (
        f"{profile_info}"
        "🔍 **Cari Partner Ngobrol**\n\n"
        "Mau cari yang lawan jenis atau acak nih?"
    )
    keyboard = [
        [
            InlineKeyboardButton("💖 Cari Lawan Jenis", callback_data="search_gender:opposite")
        ],
        [
            InlineKeyboardButton("🎲 Acak / Siapa Saja", callback_data="search_gender:any")
        ]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(
            with_credit(text),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            with_credit(text),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

def remove_from_queue(user_id: int):
    """Menghapus user dari antrean tunggu"""
    global waiting_queue
    waiting_queue = [q for q in waiting_queue if q["user_id"] != user_id]

def try_match(new_user: dict):
    """Logika pencocokan dua arah yang saling cocok kriterianya"""
    for candidate in waiting_queue:
        # Cek Gender
        gender_match_1 = False
        if new_user["pref_gender"] == "any":
            gender_match_1 = True
        elif new_user["pref_gender"] == "opposite" and new_user["my_gender"] != candidate["my_gender"]:
            gender_match_1 = True

        gender_match_2 = False
        if candidate["pref_gender"] == "any":
            gender_match_2 = True
        elif candidate["pref_gender"] == "opposite" and candidate["my_gender"] != new_user["my_gender"]:
            gender_match_2 = True

        if not (gender_match_1 and gender_match_2):
            continue

        # Cek Fakultas
        faculty_match_1 = False
        if new_user["pref_faculty"] == "any":
            faculty_match_1 = True
        elif new_user["pref_faculty"] == "same" and new_user["my_faculty"] == candidate["my_faculty"]:
            faculty_match_1 = True

        faculty_match_2 = False
        if candidate["pref_faculty"] == "any":
            faculty_match_2 = True
        elif candidate["pref_faculty"] == "same" and candidate["my_faculty"] == new_user["my_faculty"]:
            faculty_match_2 = True

        if not (faculty_match_1 and faculty_match_2):
            continue

        waiting_queue.remove(candidate)
        return candidate

    return None

# ==========================================
# 6. CALLBACK QUERY HANDLER (TOMBOL INLINE)
# ==========================================
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani seluruh respon tombol inline"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name

    # --- A. PILIH GENDER SAAT DAFTAR ---
    if data.startswith("reg_gender:"):
        gender = data.split(":")[1]
        temp_setup[user_id] = {"gender": gender}

        keyboard = []
        for code, name in FACULTIES.items():
            keyboard.append([InlineKeyboardButton(name, callback_data=f"reg_fac:{code}")])

        text = (
            f"Sip! Gender kamu: **{GENDERS.get(gender)}**\n\n"
            "👉 **Sekarang, pilih fakultas kamu di UPP:**"
        )
        await query.edit_message_text(
            with_credit(text),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # --- B. PILIH FAKULTAS SAAT DAFTAR -> LANGSUNG PREFERENSI SEARCH ---
    elif data.startswith("reg_fac:"):
        faculty = data.split(":")[1]
        gender = temp_setup.get(user_id, {}).get("gender", "L")

        save_user_profile(user_id, username, gender, faculty)
        temp_setup.pop(user_id, None)

        # Setelah profil tersimpan, langsung lanjut ke pencarian
        await show_gender_preference(update, context, user_id)

    # --- C. RESET PROFIL DARI MENU PROFIL ---
    elif data == "profile_reset":
        keyboard = [
            [
                InlineKeyboardButton("👨 Laki-laki", callback_data="reg_gender:L"),
                InlineKeyboardButton("👩 Perempuan", callback_data="reg_gender:P")
            ]
        ]
        text = "Pilih jenis kelamin kamu:"
        await query.edit_message_text(
            with_credit(text),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # --- D. FILTER GENDER SEARCH -> LANJUT KE JANGKAUAN FAKULTAS ---
    elif data.startswith("search_gender:"):
        pref_gender = data.split(":")[1]
        temp_setup[user_id] = {"pref_gender": pref_gender}

        keyboard = [
            [
                InlineKeyboardButton("🏛️ Satu Fakultas", callback_data="search_fac:same")
            ],
            [
                InlineKeyboardButton("🌐 Lintas Fakultas (Semua UPP)", callback_data="search_fac:any")
            ]
        ]
        text = "Mau cari yang satu fakultas atau bebas se-UPP?"
        await query.edit_message_text(
            with_credit(text),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # --- E. FILTER FAKULTAS SEARCH -> MULAI MATCHING ---
    elif data.startswith("search_fac:"):
        pref_fac = data.split(":")[1]
        pref_gender = temp_setup.get(user_id, {}).get("pref_gender", "any")
        temp_setup.pop(user_id, None)

        profile = get_user_profile(user_id)
        if not profile:
            await query.edit_message_text(
                with_credit("⚠️ Sesi terputus. Silakan tekan tombol '🔍 Cari Partner' lagi."),
                parse_mode="Markdown"
            )
            return

        new_entry = {
            "user_id": user_id,
            "my_gender": profile["gender"],
            "my_faculty": profile["faculty"],
            "pref_gender": pref_gender,
            "pref_faculty": pref_fac
        }

        partner = try_match(new_entry)

        if partner:
            partner_id = partner["user_id"]
            active_chats[user_id] = partner_id
            active_chats[partner_id] = user_id
            reveal_requests[user_id] = set()
            reveal_requests[partner_id] = set()

            partner_info_for_me = (
                "🎉 **Partner Ditemukan!**\n\n"
                f"• **Lawan Bicara:** {GENDERS.get(partner['my_gender'])}\n"
                f"• **Fakultas:** {FACULTIES.get(partner['my_faculty'])}\n\n"
                "Kalian sekarang sudah terhubung secara anonim. Silakan mulai mengobrol!\n"
                "*(Ketik /report jika partner berkata tidak senonoh)*"
            )
            my_info_for_partner = (
                "🎉 **Partner Ditemukan!**\n\n"
                f"• **Lawan Bicara:** {GENDERS.get(profile['gender'])}\n"
                f"• **Fakultas:** {FACULTIES.get(profile['faculty'])}\n\n"
                "Kalian sekarang sudah terhubung secara anonim. Silakan mulai mengobrol!\n"
                "*(Ketik /report jika partner berkata tidak senonoh)*"
            )

            await query.edit_message_text(with_credit(partner_info_for_me), parse_mode="Markdown")
            try:
                await context.bot.send_message(chat_id=partner_id, text=with_credit(my_info_for_partner), parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Gagal kirim notif match ke {partner_id}: {e}")
        else:
            waiting_queue.append(new_entry)
            pref_g_text = "Lawan Jenis" if pref_gender == "opposite" else "Acak / Siapa Saja"
            pref_f_text = "Satu Fakultas" if pref_fac == "same" else "Lintas Fakultas (Semua UPP)"

            text_waiting = (
                "🔍 **Sedang mencari partner...**\n\n"
                f"• Target: **{pref_g_text}**\n"
                f"• Jangkauan: **{pref_f_text}**\n\n"
                "Tunggu sebentar ya, bot akan langsung memberi tahu saat partner ditemukan!\n"
                "*(Tekan tombol '🛑 Berhenti' jika ingin membatalkan)*"
            )
            await query.edit_message_text(with_credit(text_waiting), parse_mode="Markdown")

    # --- F. REVEAL CALLBACK (BUKA IDENTITAS) ---
    elif data.startswith("reveal_reject:"):
        partner_id = active_chats.get(user_id)
        await query.edit_message_text(
            with_credit("🔒 Kamu memilih untuk tetap anonim. Obrolan tetap aman dan rahasia!"),
            parse_mode="Markdown"
        )
        if partner_id:
            try:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text=with_credit("🔒 Partner kamu masih ingin menjaga privasinya. Obrolan tetap aman & anonim ya!"),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    elif data.startswith("reveal_accept:"):
        partner_id = active_chats.get(user_id)
        if not partner_id:
            await query.edit_message_text(with_credit("⚠️ Obrolan sudah berakhir."), parse_mode="Markdown")
            return

        await query.edit_message_text(with_credit("✅ Kamu menyetujui untuk saling membuka identitas!"), parse_mode="Markdown")

        user_me = query.from_user
        try:
            user_partner = await context.bot.get_chat(partner_id)
        except Exception:
            user_partner = None

        def format_user_info(u):
            if u.username:
                return f"@{u.username} ({u.first_name})"
            return f"[{u.first_name}](tg://user?id={u.id})"

        info_me = format_user_info(user_me)
        info_partner = format_user_info(user_partner) if user_partner else "Partner"

        msg_me = (
            "🎉 **KEDUA PIHAK SEPAKAT BUKA IDENTITAS!**\n\n"
            f"👤 **Identitas Partner Kamu:** {info_partner}\n\n"
            "Sekarang kalian bisa langsung saling DM secara personal di Telegram! Selamat kenalan! 🥳"
        )
        msg_partner = (
            "🎉 **KEDUA PIHAK SEPAKAT BUKA IDENTITAS!**\n\n"
            f"👤 **Identitas Partner Kamu:** {info_me}\n\n"
            "Sekarang kalian bisa langsung saling DM secara personal di Telegram! Selamat kenalan! 🥳"
        )

        await context.bot.send_message(chat_id=user_id, text=with_credit(msg_me), parse_mode="Markdown")
        await context.bot.send_message(chat_id=partner_id, text=with_credit(msg_partner), parse_mode="Markdown")

    # --- G. REPORT CALLBACK (KONFIRMASI LAPORAN) ---
    elif data.startswith("report_confirm:"):
        reported_id = int(data.split(":")[1])

        # Cek apakah masih dalam chat aktif dengan orang tersebut
        if user_id in active_chats and active_chats[user_id] == reported_id:
            # Putus obrolan
            del active_chats[user_id]
            if reported_id in active_chats:
                del active_chats[reported_id]

            reveal_requests.pop(user_id, None)
            reveal_requests.pop(reported_id, None)

            # Notifikasi ke pelapor
            await query.edit_message_text(
                with_credit("✅ **Laporan Diterima!**\n\nPartner telah dilaporkan ke admin dan sesi obrolan telah langsung diputus. Terima kasih telah menjaga kenyamanan komunitas UPP!"),
                parse_mode="Markdown"
            )

            # Notifikasi ke terlapor
            try:
                await context.bot.send_message(
                    chat_id=reported_id,
                    text=with_credit("🛑 Obrolan telah diakhiri karena partner mengajukan laporan ketidaknyamanan."),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            # Teruskan Laporan ke Telegram Admin (Anda)
            reporter = query.from_user
            reported_profile = get_user_profile(reported_id)
            reported_faculty = FACULTIES.get(reported_profile["faculty"]) if reported_profile else "Tidak Diketahui"
            reported_gender = GENDERS.get(reported_profile["gender"]) if reported_profile else "Tidak Diketahui"

            admin_alert = (
                "🚨 **LAPORAN PELANGGARAN PENGGUNA!**\n\n"
                f"• **Pelapor:** {reporter.first_name} (`{reporter.id}`)\n"
                f"• **Terlapor ID:** `{reported_id}`\n"
                f"• **Profil Terlapor:** {reported_gender} • {reported_faculty}\n\n"
                "Gunakan tombol di bawah untuk memblokir akun terlapor:"
            )
            admin_keyboard = [
                [InlineKeyboardButton(f"⛔ Ban Pengguna {reported_id}", callback_data=f"admin_ban:{reported_id}")]
            ]
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=admin_alert,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(admin_keyboard)
                )
            except Exception as e:
                logger.error(f"Gagal mengirim laporan ke admin {ADMIN_ID}: {e}")

    elif data == "report_cancel":
        await query.edit_message_text(with_credit("Laporan dibatalkan. Obrolan tetap dilanjutkan."), parse_mode="Markdown")

    # --- H. ADMIN BAN CALLBACK ---
    elif data.startswith("admin_ban:"):
        if user_id != ADMIN_ID:
            await query.answer("Khusus Admin!", show_alert=True)
            return

        target_id = int(data.split(":")[1])
        ban_user_db(target_id, "Dilaporkan oleh mahasiswa lain")

        # Putus chat jika masih aktif
        if target_id in active_chats:
            p_id = active_chats[target_id]
            del active_chats[target_id]
            if p_id in active_chats:
                del active_chats[p_id]
            try:
                await context.bot.send_message(chat_id=p_id, text=with_credit("🛑 Partner kamu telah ditangguhkan oleh admin."), parse_mode="Markdown")
            except Exception:
                pass

        await query.edit_message_text(f"⛔ **BERHASIL:** User ID `{target_id}` telah diblokir permanen dari bot UPPdate.", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="⛔ Akun Anda telah ditangguhkan secara permanen karena melanggar etika dan norma komunitas mahasiswa UPP."
            )
        except Exception:
            pass

# ==========================================
# 7. PERINTAH STOP, REVEAL, REPORT, ADMIN
# ==========================================
async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /report untuk melaporkan partner chat saat ini"""
    user_id = update.effective_user.id

    if user_id not in active_chats:
        await update.message.reply_text(
            with_credit("⚠️ Kamu hanya bisa menggunakan fitur /report saat sedang terhubung dalam obrolan aktif!"),
            parse_mode="Markdown"
        )
        return

    partner_id = active_chats[user_id]

    keyboard = [
        [
            InlineKeyboardButton("🚨 Ya, Laporkan & Putus Chat", callback_data=f"report_confirm:{partner_id}")
        ],
        [
            InlineKeyboardButton("Batal", callback_data="report_cancel")
        ]
    ]
    await update.message.reply_text(
        with_credit(
            "⚠️ **Konfirmasi Laporan**\n\n"
            "Apakah partner obrolan kamu melakukan pelecehan, berkata kasar, atau melanggar etika?\n\n"
            "Jika kamu melapor, **sesi chat akan langsung diputus** dan identitas partner akan dikirim ke Admin untuk ditinjau."
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perintah Admin untuk memblokir user: /ban <user_id>"""
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text("Format: `/ban <user_id>`")
        return

    try:
        target_id = int(context.args[0])
        ban_user_db(target_id, "Banned by Admin")
        await update.message.reply_text(f"✅ User ID `{target_id}` berhasil diblokir permanen.")
    except Exception as e:
        await update.message.reply_text(f"Gagal ban: {e}")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perintah Admin untuk membuka blokir: /unban <user_id>"""
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text("Format: `/unban <user_id>`")
        return

    try:
        target_id = int(context.args[0])
        unban_user_db(target_id)
        await update.message.reply_text(f"✅ Blokir User ID `{target_id}` berhasil dibuka.")
    except Exception as e:
        await update.message.reply_text(f"Gagal unban: {e}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perintah Admin untuk melihat statistik bot: /stats"""
    if update.effective_user.id != ADMIN_ID:
        return

    total_users, total_banned = count_stats()
    active_pairs = len(active_chats) // 2
    waiting_count = len(waiting_queue)

    stats_msg = (
        "📊 **STATISTIK UPPDATE BOT**\n\n"
        f"• Total Pengguna Terdaftar: **{total_users}**\n"
        f"• Pasangan Chat Aktif: **{active_pairs}** pasang\n"
        f"• Dalam Antrean Tunggu: **{waiting_count}** orang\n"
        f"• Pengguna Diblokir: **{total_banned}** orang"
    )
    await update.message.reply_text(stats_msg, parse_mode="Markdown")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mengakhiri obrolan atau membatalkan antrean pencarian"""
    user_id = update.effective_user.id

    if any(q["user_id"] == user_id for q in waiting_queue):
        remove_from_queue(user_id)
        await update.message.reply_text(
            with_credit("✅ Pencarian partner telah dibatalkan."),
            parse_mode="Markdown"
        )
        return

    if user_id in active_chats:
        partner_id = active_chats[user_id]
        del active_chats[user_id]
        if partner_id in active_chats:
            del active_chats[partner_id]

        reveal_requests.pop(user_id, None)
        reveal_requests.pop(partner_id, None)

        msg_end = "🛑 Obrolan telah diakhiri. Tekan tombol **🔍 Cari Partner** untuk mencari teman baru!"
        await update.message.reply_text(with_credit(msg_end), parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=partner_id, text=with_credit(msg_end), parse_mode="Markdown")
        except Exception:
            pass
        return

    await update.message.reply_text(
        with_credit("Kamu sedang tidak berada dalam obrolan ataupun antrean."),
        parse_mode="Markdown"
    )

async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Langsung mengakhiri dan mencari partner baru"""
    await stop_command(update, context)
    await start_search_flow(update, context)

async def reveal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mengajak partner saling membuka identitas Telegram"""
    user_id = update.effective_user.id

    if user_id not in active_chats:
        await update.message.reply_text(
            with_credit("⚠️ Kamu harus sedang berada dalam obrolan aktif untuk mengajak buka identitas!"),
            parse_mode="Markdown"
        )
        return

    partner_id = active_chats[user_id]

    keyboard = [
        [
            InlineKeyboardButton("✅ Setuju Buka Identitas", callback_data=f"reveal_accept:{user_id}"),
            InlineKeyboardButton("❌ Tolak / Tetap Anonim", callback_data=f"reveal_reject:{user_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        with_credit(
            "📩 **Permintaan Buka Identitas Terkirim!**\n\n"
            "Bot sudah menanyakan ke partner kamu apakah dia juga setuju.\n"
            "Identitas kamu **TIDAK AKAN DIBUKA** kecuali partner juga menekan tombol 'Setuju'."
        ),
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_message(
            chat_id=partner_id,
            text=with_credit(
                "🔓 **Partner kamu mengajak saling membuka identitas Telegram!**\n\n"
                "Apakah kamu bersedia saling bertukar kontak Telegram?\n\n"
                "*(Jika kamu menolak, identitas kalian berdua tetap 100% aman dan rahasia)*"
            ),
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Gagal kirim permintaan reveal ke {partner_id}: {e}")

# ==========================================
# 8. RELAY PESAN + TYPING INDICATOR (SEDANG MENGETIK...)
# ==========================================
async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Meneruskan pesan teks dan media antar partner dengan efek sedang mengetik (typing indicator)"""
    user_id = update.effective_user.id
    msg = update.message

    if is_user_banned(user_id):
        await msg.reply_text("⛔ Akun Anda telah ditangguhkan secara permanen karena melanggar etika komunitas UPP.")
        return

    text = (msg.text or "").strip()
    clean_text = text.lower()

    if "cari partner" in clean_text or clean_text in ["cari", "search"]:
        await start_search_flow(update, context)
        return
    elif "berhenti" in clean_text or clean_text in ["stop", "batal", "udahan"]:
        await stop_command(update, context)
        return
    elif "buka identitas" in clean_text or clean_text in ["buka", "reveal", "kontak"]:
        await reveal_command(update, context)
        return
    elif "profil saya" in clean_text or clean_text in ["profil", "profile"]:
        await profile_command(update, context)
        return
    elif "bantuan" in clean_text or clean_text in ["help", "panduan", "info"]:
        await bantuan_command(update, context)
        return

    if user_id not in active_chats:
        await msg.reply_text(
            with_credit("Kamu belum terhubung dengan siapa pun! Tekan tombol **🔍 Cari Partner** di bawah untuk mulai."),
            parse_mode="Markdown"
        )
        return

    partner_id = active_chats[user_id]

    try:
        # Kirim indikator typing/uploading ke partner agar obrolan terasa hidup
        if msg.text:
            await context.bot.send_chat_action(chat_id=partner_id, action=ChatAction.TYPING)
            await context.bot.send_message(chat_id=partner_id, text=msg.text)
        elif msg.photo:
            await context.bot.send_chat_action(chat_id=partner_id, action=ChatAction.UPLOAD_PHOTO)
            await context.bot.send_photo(chat_id=partner_id, photo=msg.photo[-1].file_id, caption=msg.caption)
        elif msg.voice:
            await context.bot.send_chat_action(chat_id=partner_id, action=ChatAction.RECORD_VOICE)
            await context.bot.send_voice(chat_id=partner_id, voice=msg.voice.file_id, caption=msg.caption)
        elif msg.video:
            await context.bot.send_chat_action(chat_id=partner_id, action=ChatAction.UPLOAD_VIDEO)
            await context.bot.send_video(chat_id=partner_id, video=msg.video.file_id, caption=msg.caption)
        elif msg.sticker:
            await context.bot.send_sticker(chat_id=partner_id, sticker=msg.sticker.file_id)
        elif msg.document:
            await context.bot.send_chat_action(chat_id=partner_id, action=ChatAction.UPLOAD_DOCUMENT)
            await context.bot.send_document(chat_id=partner_id, document=msg.document.file_id, caption=msg.caption)
        elif msg.animation:
            await context.bot.send_animation(chat_id=partner_id, animation=msg.animation.file_id, caption=msg.caption)
        else:
            await msg.reply_text(with_credit("⚠️ Format pesan ini belum didukung ya."), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Gagal meneruskan pesan ke {partner_id}: {e}")
        await msg.reply_text(with_credit("❌ Pesan gagal terkirim. Partner mungkin telah mematikan bot."), parse_mode="Markdown")

# ==========================================
# 9. RUN APPLICATION
# ==========================================
def main():
    if not BOT_TOKEN:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN belum diisi di .env!")
        return

    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        http_version="1.1"
    )
    async def post_init(application):
        try:
            await application.bot.set_my_commands([
                ("cari", "🔍 Cari partner ngobrol baru"),
                ("berhenti", "🛑 Akhiri obrolan saat ini"),
                ("next", "⏭️ Ganti partner obrolan"),
                ("buka", "🔓 Minta / setujui buka identitas"),
                ("profil", "👤 Profil kamu & fakultas"),
                ("lapor", "🚨 Laporkan partner jika melanggar"),
                ("bantuan", "ℹ️ Panduan & bantuan UPPdate"),
            ])
            logger.info("Bot commands menu registered successfully.")
        except Exception as e:
            logger.error(f"Gagal set_my_commands: {e}")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler(["start", "mulai"], start_command))
    app.add_handler(CommandHandler(["cari", "search"], start_search_flow))
    app.add_handler(CommandHandler(["berhenti", "stop"], stop_command))
    app.add_handler(CommandHandler(["next", "ganti", "lanjut"], next_command))
    app.add_handler(CommandHandler(["buka", "reveal", "kontak"], reveal_command))
    app.add_handler(CommandHandler(["profil", "profile"], profile_command))
    app.add_handler(CommandHandler(["lapor", "report"], report_command))
    app.add_handler(CommandHandler(["bantuan", "help", "panduan"], bantuan_command))

    # Perintah Admin
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("stats", stats_command))

    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Handler pesan teks biasa, klik tombol keyboard bawah, dan media chat
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay_message))

    # Error Handler Global
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling update:", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text("⚠️ Terjadi kendala teknis sejenak. Silakan coba lagi ya.")
            except Exception:
                pass

    app.add_error_handler(error_handler)

    # Jalankan health-check server (khusus untuk Render / Koyeb)
    start_health_server()

    print("=" * 60)
    print("🚀 Bot UPPdate (Universitas Pasir Pengaraian) BERJALAN...")
    print("=" * 60)

    app.run_polling(drop_pending_updates=True, bootstrap_retries=10, poll_interval=0.0)

if __name__ == "__main__":
    main()
