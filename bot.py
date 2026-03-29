"""
🎬 KinoBoom Bot - Professional Telegram Movie Bot
Barcha interfeys O'zbek tilida (Lotin yozuvi)
"""

import logging
import sqlite3
import time
import random
import string
from functools import wraps

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ──────────────────────────────────────────────
#  SOZLAMALAR (CONFIG)
# ──────────────────────────────────────────────
BOT_TOKEN   = "YOUR_BOT_TOKEN_HERE"   # 👈 Shu yerga tokeningizni qo'ying
CHANNEL_ID  = "@Kinoboom_12"
ADMIN_ID    = 8512949204
DB_PATH     = "kinoboom.db"
COOLDOWN_SECONDS = 10

# Sticker ID-lari (Telegram'dan olingan rasmiy stikerlar)
STICKER_WELCOME = "CAACAgIAAxkBAAIBsWZ5AAGq6QrVqVt4R6kS3bCz2AABiAACAgADwDZIE4nXGkW5LQSHNAQ"
STICKER_SUCCESS = "CAACAgIAAxkBAAIBs2Z5AAGsVn-0R2pjXq1rYAABb7sAAg8AA8A2SBOsYj2s3DRFITUENAQ"
STICKER_ERROR   = "CAACAgIAAxkBAAIBtWZ5AAGuMDWsNjFnb5XuCFY9wgABqwACEAADwDZIE0llZGJZAAGPxjUE"

# Conversation holatlari
(
    WAITING_CODE,
    WAITING_PHOTO,
    WAITING_LINK,
) = range(3)

# Logging
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  DATABASE
# ──────────────────────────────────────────────
def init_db():
    """Ma'lumotlar bazasini yaratish va jadvallarni sozlash."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            code      TEXT PRIMARY KEY,
            photo     TEXT NOT NULL,
            link      TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id   INTEGER,
            code      TEXT,
            PRIMARY KEY (user_id, code)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS cooldown (
            user_id   INTEGER PRIMARY KEY,
            last_time INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()
    logger.info("✅ Ma'lumotlar bazasi tayyor.")


def db_get_movie(code: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT photo, link FROM movies WHERE code = ?", (code,))
    row = c.fetchone()
    conn.close()
    return row  # (photo_file_id, link) yoki None


def db_add_movie(code: str, photo: str, link: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO movies (code, photo, link) VALUES (?, ?, ?)",
              (code, photo, link))
    conn.commit()
    conn.close()


def db_delete_movie(code: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM movies WHERE code = ?", (code,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def db_log_user(user_id: int, code: str):
    """Foydalanuvchi + kod juftini yozib qo'yish (unique)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, code) VALUES (?, ?)",
              (user_id, code))
    conn.commit()
    conn.close()


def db_get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    c.execute("""
        SELECT code, COUNT(*) as cnt
        FROM users
        GROUP BY code
        ORDER BY cnt DESC
        LIMIT 5
    """)
    top5 = c.fetchall()
    conn.close()
    return total, top5


def db_check_cooldown(user_id: int) -> int:
    """Agar cooldown tugamagan bo'lsa, qolgan soniyani qaytaradi. 0 = ruxsat."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT last_time FROM cooldown WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row is None:
        return 0
    elapsed = int(time.time()) - row[0]
    remaining = COOLDOWN_SECONDS - elapsed
    return remaining if remaining > 0 else 0


def db_set_cooldown(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO cooldown (user_id, last_time) VALUES (?, ?)",
              (user_id, int(time.time())))
    conn.commit()
    conn.close()


def db_code_exists(code: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM movies WHERE code = ?", (code,))
    exists = c.fetchone() is not None
    conn.close()
    return exists


def generate_code() -> str:
    """Bazada mavjud bo'lmagan noyob 4 raqamli kod yaratish."""
    while True:
        code = "".join(random.choices(string.digits, k=4))
        if not db_code_exists(code):
            return code


# ──────────────────────────────────────────────
#  YORDAMCHI: KANAL OBUNASINI TEKSHIRISH
# ──────────────────────────────────────────────
async def is_subscribed(bot, user_id: int) -> bool:
    """Foydalanuvchi kanalga obuna bo'lganmi yoki yo'qligini tekshiradi."""
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"Obuna tekshirishda xato: {e}")
        return False


def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Kanalga o'tish", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
        [InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")],
    ])


async def require_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Obunani tekshiradi. Agar obuna bo'lmasa, xabar yuboradi va False qaytaradi.
    True qaytarsa, davom etish mumkin.
    """
    user_id = update.effective_user.id
    if not await is_subscribed(context.bot, user_id):
        text = (
            "❗ Botdan foydalanish uchun avval kanalga obuna bo'ling!\n\n"
            "📢 Obuna bo'lgach, <b>✅ Tekshirish</b> tugmasini bosing."
        )
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(
                text, reply_markup=subscription_keyboard(), parse_mode="HTML"
            )
        elif update.message:
            await update.message.reply_text(
                text, reply_markup=subscription_keyboard(), parse_mode="HTML"
            )
        return False
    return True


# ──────────────────────────────────────────────
#  ASOSIY MENYU
# ──────────────────────────────────────────────
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🎬 Kino olish"],
            ["📊 Statistika", "ℹ️ Yordam"],
        ],
        resize_keyboard=True,
        input_field_placeholder="Menyudan tanlang...",
    )


# ──────────────────────────────────────────────
#  /start KOMANDASI
# ──────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    first_name = user.first_name or "Do'stim"

    # Stiker yuborish
    try:
        await update.message.reply_sticker(sticker=STICKER_WELCOME)
    except Exception:
        pass  # Stiker topilmasa, davom etaveradi

    welcome_text = (
        f"🎬 <b>KinoBoom</b> botiga xush kelibsiz, {first_name}!\n\n"
        "🍿 Bu bot orqali siz Instagram'dagi maxsus kodlar yordamida "
        "kinolarni topib, to'g'ridan-to'g'ri tomosha qilishingiz mumkin!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 <b>Qanday ishlaydi?</b>\n"
        "1️⃣ Instagram'dagi postdan kino kodini oling\n"
        "2️⃣ «🎬 Kino olish» tugmasini bosing\n"
        "3️⃣ Kodni kiriting va kinoni oling!\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⬇️ Quyidagi menyudan boshlang:"
    )

    await update.message.reply_text(
        welcome_text,
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────
#  🎬 KINO OLISH - ConversationHandler
# ──────────────────────────────────────────────
async def kino_olish_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi "🎬 Kino olish" tugmasini bosdi."""
    if not await require_subscription(update, context):
        return ConversationHandler.END

    # Cooldown tekshirish
    user_id = update.effective_user.id
    remaining = db_check_cooldown(user_id)
    if remaining > 0:
        await update.message.reply_text(
            f"⏳ Iltimos, <b>{remaining}</b> soniya kuting va qayta urinib ko'ring.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🎬 <b>Kino kodini kiriting:</b>\n\n"
        "💡 <i>Instagram postidagi 4 raqamli kodni yozing</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            [["❌ Bekor qilish"]], resize_keyboard=True
        ),
    )
    return WAITING_CODE


async def kino_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi kodni kiritdi."""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if text == "❌ Bekor qilish":
        await update.message.reply_text(
            "🔙 Asosiy menyuga qaytdingiz.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    # Cooldown sozlash
    db_set_cooldown(user_id)

    movie = db_get_movie(text)
    if movie:
        photo_id, link = movie
        # Foydalanuvchini logga yozish
        db_log_user(user_id, text)

        caption = (
            "🎬 <b>Kino topildi!</b>\n\n"
            "📥 Ko'rish uchun pastdagi tugmani bosing 👇"
        )
        watch_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Ko'rish", url=link)]
        ])

        try:
            await update.message.reply_photo(
                photo=photo_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=watch_btn,
            )
        except Exception:
            # Rasm yuborishda xato bo'lsa, matn bilan yuborish
            await update.message.reply_text(
                caption + f"\n\n🔗 <a href='{link}'>Ko'rish uchun bosing</a>",
                parse_mode="HTML",
                reply_markup=watch_btn,
            )

        # Muvaffaqiyat stikeri
        try:
            await update.message.reply_sticker(sticker=STICKER_SUCCESS)
        except Exception:
            pass

    else:
        # Noto'g'ri kod
        try:
            await update.message.reply_sticker(sticker=STICKER_ERROR)
        except Exception:
            pass

        await update.message.reply_text(
            "❌ <b>Bunday kod topilmadi!</b>\n\n"
            "🔍 Kodni to'g'ri kiritganingizni tekshiring.\n"
            "💡 Instagram postidagi 4 raqamni qaytadan kiriting.",
            parse_mode="HTML",
        )

    await update.message.reply_text(
        "🏠 Asosiy menyuga qaytdingiz.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Amal bekor qilindi.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# ──────────────────────────────────────────────
#  📊 STATISTIKA
# ──────────────────────────────────────────────
async def statistika(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_subscription(update, context):
        return

    total, top5 = db_get_stats()

    top5_text = ""
    if top5:
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, (code, cnt) in enumerate(top5):
            top5_text += f"  {medals[i]} Kod <code>{code}</code> — {cnt} marta\n"
    else:
        top5_text = "  📭 Hali ma'lumot yo'q\n"

    text = (
        "📊 <b>Bot statistikasi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Umumiy kirishlar:</b> {total} ta\n\n"
        "🏆 <b>TOP 5 eng ko'p ishlatilgan kodlar:</b>\n"
        f"{top5_text}"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    await update.message.reply_text(text, parse_mode="HTML")


# ──────────────────────────────────────────────
#  ℹ️ YORDAM
# ──────────────────────────────────────────────
async def yordam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ <b>Yordam va ma'lumot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎬 <b>Kino kodini qayerdan olaman?</b>\n"
        "Instagram'dagi @Kinoboom_12 sahifasidagi postlar "
        "tagida 4 raqamli maxsus kod yozilgan bo'ladi.\n\n"
        "🔑 <b>Kodni kiritish tartibi:</b>\n"
        "1️⃣ «🎬 Kino olish» tugmasini bosing\n"
        "2️⃣ 4 raqamli kodni kiriting\n"
        "3️⃣ Kinoni oling va tomosha qiling!\n\n"
        "⏳ <b>Anti-spam:</b> Har 10 soniyada 1 ta so'rov\n\n"
        "📢 <b>Kanal:</b> @Kinoboom_12\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "❓ Muammo bo'lsa, kanalga yozing."
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ──────────────────────────────────────────────
#  ✅ OBUNA TEKSHIRISH (Callback)
# ──────────────────────────────────────────────
async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    await query.answer()

    if await is_subscribed(context.bot, user_id):
        await query.message.edit_text(
            "✅ <b>Obuna tasdiqlandi!</b>\n\n"
            "🎬 Endi botdan to'liq foydalanishingiz mumkin!\n"
            "Quyidagi menyudan boshlang 👇",
            parse_mode="HTML",
        )
        await query.message.reply_text(
            "🏠 Asosiy menyu:",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await query.message.reply_text(
            "❗ Siz hali kanalga obuna bo'lmadingiz!\n\n"
            "📢 Iltimos, avval kanalga obuna bo'ling, so'ng «✅ Tekshirish» tugmasini bosing.",
            reply_markup=subscription_keyboard(),
            parse_mode="HTML",
        )


# ──────────────────────────────────────────────
#  ADMIN: /add — KINO QO'SHISH
# ──────────────────────────────────────────────
async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Bu buyruq faqat admin uchun!")
        return ConversationHandler.END

    code = generate_code()
    context.user_data["new_code"] = code

    await update.message.reply_text(
        f"➕ <b>Yangi kino qo'shish</b>\n\n"
        f"🎬 Yangi kod: <code>{code}</code>\n\n"
        f"📸 Endi kino rasmini yuboring:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup([["❌ Bekor qilish"]], resize_keyboard=True),
    )
    return WAITING_PHOTO


async def admin_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        context.user_data.clear()
        await update.message.reply_text("❌ Bekor qilindi.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    if not update.message.photo:
        await update.message.reply_text("❗ Iltimos, rasm yuboring!")
        return WAITING_PHOTO

    # Eng yuqori sifatli rasmni saqlash
    photo_id = update.message.photo[-1].file_id
    context.user_data["new_photo"] = photo_id

    await update.message.reply_text(
        "🔗 Endi kino havolasini (link) yuboring:\n\n"
        "<i>Masalan: https://t.me/Kinoboom_12/123</i>",
        parse_mode="HTML",
    )
    return WAITING_LINK


async def admin_link_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        context.user_data.clear()
        await update.message.reply_text("❌ Bekor qilindi.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    link = update.message.text.strip()
    if not link.startswith("http"):
        await update.message.reply_text("❗ To'g'ri havola kiriting (http/https bilan boshlang)!")
        return WAITING_LINK

    code  = context.user_data.get("new_code")
    photo = context.user_data.get("new_photo")

    if not code or not photo:
        await update.message.reply_text("⚠️ Xato: ma'lumotlar topilmadi. Qayta /add bosing.")
        return ConversationHandler.END

    db_add_movie(code, photo, link)
    context.user_data.clear()

    await update.message.reply_text(
        f"✅ <b>Kino muvaffaqiyatli qo'shildi!</b>\n\n"
        f"🎬 Kod: <code>{code}</code>\n"
        f"🔗 Havola: {link}",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    logger.info(f"Admin kino qo'shdi: kod={code}, link={link}")
    return ConversationHandler.END


# ──────────────────────────────────────────────
#  ADMIN: /delete <code>
# ──────────────────────────────────────────────
async def admin_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Bu buyruq faqat admin uchun!")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "❗ Foydalanish: <code>/delete XXXX</code>",
            parse_mode="HTML",
        )
        return

    code = args[0].strip()
    if db_delete_movie(code):
        await update.message.reply_text(
            f"🗑 <b>Kod <code>{code}</code> o'chirildi.</b>",
            parse_mode="HTML",
        )
        logger.info(f"Admin kino o'chirdi: kod={code}")
    else:
        await update.message.reply_text(
            f"❌ <code>{code}</code> kodi topilmadi.",
            parse_mode="HTML",
        )


# ──────────────────────────────────────────────
#  ADMIN: /stats
# ──────────────────────────────────────────────
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Bu buyruq faqat admin uchun!")
        return

    total, top5 = db_get_stats()

    top5_text = ""
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, (code, cnt) in enumerate(top5):
        top5_text += f"  {medals[i]} Kod <code>{code}</code> — {cnt} ta unique foydalanuvchi\n"

    if not top5_text:
        top5_text = "  📭 Hali yo'q\n"

    await update.message.reply_text(
        "📊 <b>Admin statistikasi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Jami unique kirishlar:</b> {total}\n\n"
        f"🏆 <b>TOP 5 kodlar:</b>\n{top5_text}"
        "━━━━━━━━━━━━━━━━━━━━",
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────
#  NOMA'LUM XABARLARNI USHLASH
# ──────────────────────────────────────────────
async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤔 Tushunmadim. Iltimos, menyudan tanlang:",
        reply_markup=main_menu_keyboard(),
    )


# ──────────────────────────────────────────────
#  ASOSIY ISHGA TUSHIRISH
# ──────────────────────────────────────────────
def main():
    # DB ni ishga tushirish
    init_db()

    # Application yaratish
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Kino olish ConversationHandler ──
    kino_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🎬 Kino olish$"), kino_olish_start)],
        states={
            WAITING_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, kino_code_received)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Bekor qilish$"), cancel_handler),
            CommandHandler("cancel", cancel_handler),
        ],
    )

    # ── Admin /add ConversationHandler ──
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", admin_add_start)],
        states={
            WAITING_PHOTO: [
                MessageHandler(filters.PHOTO, admin_photo_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_photo_received),
            ],
            WAITING_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_link_received),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Bekor qilish$"), cancel_handler),
            CommandHandler("cancel", cancel_handler),
        ],
    )

    # ── Handlerlarni ro'yxatdan o'tkazish ──
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("delete", admin_delete))
    app.add_handler(CommandHandler("stats",  admin_stats))
    app.add_handler(kino_conv)
    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))
    app.add_handler(MessageHandler(filters.Regex("^📊 Statistika$"), statistika))
    app.add_handler(MessageHandler(filters.Regex("^ℹ️ Yordam$"),      yordam))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,  unknown_message))

    logger.info("🚀 KinoBoom bot ishga tushdi!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
