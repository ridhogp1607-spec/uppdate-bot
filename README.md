# 🎓 Menfess Kampus Anonymous Chat Telegram Bot

Bot Telegram chat anonim yang dirancang khusus untuk kultur menfess kampus, dilengkapi fitur **Mutual Reveal** (saling buka identitas dengan persetujuan bersama) dan **Room Code** (menghubungkan dua orang lewat kode rahasia dari postingan menfess).

---

## 🚀 Fitur Utama

1. **Obrolan Anonim Acak (`/search`)**:
   - Menghubungkan dua mahasiswa yang sedang mencari partner obrolan secara acak.
   - Semua pesan teks, foto, voice note, video, stiker, dan GIF diteruskan tanpa mengungkap identitas pengirim asli.
2. **Mutual Identity Reveal (`/reveal`)**:
   - Jika salah satu pihak ingin saling bertukar identitas/kontak, bot mengirim permintaan konfirmasi ke partner.
   - **Identitas hanya akan dibuka jika KEDUA pihak menekan "Setuju"**. Jika salah satu menolak, privasi tetap 100% terjaga.
3. **Room Kode Menfess (`/room <kode>`)**:
   - Menghubungkan pengirim menfess dengan pembaca yang dituju melalui kode unik (misal: `/room PERPUS88`).
4. **Kontrol Cepat Keyboard**:
   - Tombol cepat di bawah keyboard Telegram: `🔍 Cari Partner`, `🛑 Berhenti`, `🔓 Buka Identitas`, dan `ℹ️ Bantuan`.

---

## 🛠️ Cara Menjalankan di Lokal (Laptop/PC)

### 1. Dapatkan Token Bot dari Telegram
1. Buka aplikasi Telegram, cari akun `@BotFather`.
2. Kirim `/newbot`, lalu masukkan nama bot dan username (misal: `kampus_menfess_bot`).
3. Salin **API Token** yang diberikan oleh `@BotFather`.

### 2. Konfigurasi Token
Buka file `.env` di folder project ini:
```env
TELEGRAM_BOT_TOKEN=masukkan_token_kamu_disini
```

### 3. Jalankan Bot
Buka terminal PowerShell di folder ini, lalu jalankan:
```powershell
.\.venv\Scripts\python bot.py
```

Jika berhasil, akan muncul tulisan:
```
🚀 Bot Menfess Kampus Anonymous Chat sedang BERJALAN di lokal...
```

Sekarang kamu bisa buka Telegram dan ketik `/start` ke bot buatanmu untuk mencobanya! (Bisa dicoba menggunakan 2 akun Telegram berbeda untuk mengetes saling kirim pesan).
