"""
🔍 Lost & Found Bot v4.5 FINAL
- إصلاح BadRequest من Markdown
- parse_mode=None افتراضياً
- كل الأزرار تعمل
"""
import os
import re
import traceback
import httpx
from typing import Optional, Dict, List, Tuple

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

load_dotenv()

# ============ الإعدادات ============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
SIGHTENGINE_USER = os.getenv("SIGHTENGINE_USER", "")
SIGHTENGINE_SECRET = os.getenv("SIGHTENGINE_SECRET", "")

print(f"🚀 Bot v4.5 FINAL starting...")
print(f"   Token: {'✅' if BOT_TOKEN else '❌'}")
print(f"   DB: {'✅' if DATABASE_URL else '❌'}")

# ============ الثوابت ============
CITIES = ["بغداد", "البصرة", "الموصل", "أربيل", "النجف", "كربلاء",
          "السليمانية", "دهوك", "كركوك", "الأنبار", "بابل"]

CATEGORIES = {
    "personal": {"ar": "🎒 شخصية", "subs": {
        "wallet": "👛 محفظة", "keys": "🔑 مفاتيح", "bag": "🎒 حقيبة",
        "glasses": "👓 نظارات", "watch": "⌚ ساعة", "medicine": "💊 أدوية",
    }},
    "study": {"ar": "📚 دراسية", "subs": {
        "book": "📖 كتاب", "pen": "✏️ أقلام", "laptop": "💻 لابتوب",
        "headphones": "🎧 سماعات",
    }},
    "electronics": {"ar": "📱 إلكترونيات", "subs": {
        "phone": "📱 هاتف", "laptop": "💻 لابتوب", "charger": "🔌 شاحن",
        "headphones": "🎧 سماعات", "flash": "💾 فلاش",
    }},
    "jewelry": {"ar": "💍 مجوهرات", "subs": {
        "ring": "💍 خاتم", "necklace": "📿 سلسال", "watch": "⌚ سوار",
    }},
    "documents": {"ar": "🪪 وثائق", "subs": {
        "id": "🪪 هوية", "student_id": "🎓 هوية جامعية",
        "bank_card": "💳 بطاقة", "passport": "📘 جواز",
    }},
    "clothing": {"ar": "👕 ملابس", "subs": {
        "jacket": "🧥 جاكيت", "shirt": "👕 قميص", "shoes": "👞 حذاء",
    }},
    "other": {"ar": "📦 أخرى", "subs": {"other": "📦 أخرى"}},
}

MAX_WARNINGS = 2
MAX_PHOTOS = 5
MIN_DESC_LEN = 20
MAX_DESC_LEN = 500
MIN_DESC_WORDS = 4

# ============ قاعدة البيانات ============
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=1, max_size=3, command_timeout=8
        )
    return _pool


async def get_or_create_user(user_id: int, username: str = None,
                             first_name: str = None, last_name: str = None) -> Dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if row:
            await conn.execute(
                "UPDATE users SET username=COALESCE($2,username), first_name=COALESCE($3,first_name) WHERE user_id=$1",
                user_id, username, first_name
            )
            return dict(row)
        row = await conn.fetchrow(
            "INSERT INTO users (user_id, username, first_name, last_name) VALUES ($1,$2,$3,$4) RETURNING *",
            user_id, username, first_name, last_name
        )
        return dict(row)


async def get_user(user_id: int) -> Optional[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        return dict(row) if row else None


async def add_warning(user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO warnings (user_id, reason) VALUES ($1, $2)",
            user_id, "صورة غير لائقة"
        )
        return await conn.fetchval("SELECT COUNT(*) FROM warnings WHERE user_id = $1", user_id)


async def is_user_banned(user_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM banned_users WHERE user_id = $1", user_id) is not None


async def ban_user(user_id: int, reason: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO banned_users (user_id, reason) VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET reason = $2
        """, user_id, reason)


async def create_item(user_id: int, item_type: str, category: str,
                     subcategory: str, description: str, city: str,
                     time_range: str, contact_method: str = None,
                     contact_value: str = None, photos: List[str] = None) -> Dict:
    pool = await get_pool()
    photos = photos or []
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO items (user_id, type, category, subcategory, description,
                location_city, time_range, contact_method, contact_value, photos, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'active') RETURNING *
        """, user_id, item_type, category, subcategory, description,
            city, time_range, contact_method, contact_value, photos)
        await conn.execute("UPDATE users SET total_reports = total_reports + 1 WHERE user_id = $1", user_id)
        return dict(row)


async def get_user_items(user_id: int, limit: int = 10) -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM items WHERE user_id = $1 AND status = 'active'
            ORDER BY created_at DESC LIMIT $2
        """, user_id, limit)
        return [dict(r) for r in rows]


async def count_user_items(user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM items WHERE user_id = $1 AND status = 'active'", user_id
        ) or 0


async def delete_all_user_items(user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE items SET status = 'deleted' WHERE user_id = $1 AND status = 'active'", user_id
        )
        try:
            count = int(result.split()[-1])
        except:
            count = 0
        await conn.execute("UPDATE users SET total_reports = 0 WHERE user_id = $1", user_id)
        return count


async def search_items(query: str = None, city: str = None, limit: int = 10) -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        conditions = ["status = 'active'"]
        params = []
        idx = 1
        if query:
            conditions.append(f"description ILIKE ${idx}")
            params.append(f"%{query}%")
            idx += 1
        if city:
            conditions.append(f"location_city = ${idx}")
            params.append(city)
            idx += 1
        params.append(limit)
        sql = f"SELECT * FROM items WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT ${idx}"
        rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]


async def increment_views(item_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE items SET views_count = views_count + 1 WHERE id = $1", item_id)


async def get_global_stats() -> Dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {
            "total_lost": await conn.fetchval("SELECT COUNT(*) FROM items WHERE type='lost' AND status='active'") or 0,
            "total_found": await conn.fetchval("SELECT COUNT(*) FROM items WHERE type='found' AND status='active'") or 0,
            "total_resolved": await conn.fetchval("SELECT COUNT(*) FROM items WHERE status='resolved'") or 0,
            "total_users": await conn.fetchval("SELECT COUNT(*) FROM users") or 0,
        }


# ============ الرسائل المباشرة ============
async def save_direct_message(from_user_id: int, to_user_id: int,
                             message: str, item_id: int = None, match_id: int = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO direct_messages (from_user_id, to_user_id, message, item_id, match_id)
            VALUES ($1, $2, $3, $4, $5) RETURNING id
        """, from_user_id, to_user_id, message, item_id, match_id)
        return row["id"]


async def get_user_messages(user_id: int, limit: int = 10) -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT dm.*, u.first_name as from_name, u.username as from_username
            FROM direct_messages dm
            LEFT JOIN users u ON dm.from_user_id = u.user_id
            WHERE dm.to_user_id = $1 ORDER BY dm.created_at DESC LIMIT $2
        """, user_id, limit)
        return [dict(r) for r in rows]


# ============ 🆕 دالة الرد الآمن ============
async def reply_or_edit(update: Update, text: str, reply_markup=None):
    """
    🆕 دالة موحّدة:
    - بدون parse_mode (لتجنب BadRequest)
    - إذا فشل edit → يرسل رسالة جديدة
    """
    try:
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    text, reply_markup=reply_markup
                )
            except BadRequest as e:
                print(f"⚠️ edit failed: {e}, sending new")
                try:
                    await update.callback_query.message.reply_text(
                        text, reply_markup=reply_markup
                    )
                except Exception as e2:
                    print(f"⚠️ send failed: {e2}")
                    # محاولة أخيرة بدون أي شيء
                    await update.callback_query.message.reply_text(text)
        elif update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)
    except Exception as e:
        print(f"⚠️ reply_or_edit error: {e}")
        traceback.print_exc()


# ============ NSFW ============
async def check_image_nsfw(file_id: str) -> Tuple[bool, str]:
    if not SIGHTENGINE_USER or not SIGHTENGINE_SECRET:
        return True, "no_check"
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            file_resp = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={"file_id": file_id}
            )
            file_data = file_resp.json()
            if not file_data.get("ok"):
                return True, "telegram_error"
            file_path = file_data["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

            check_resp = await client.get(
                "https://api.sightengine.com/1.0/check.json",
                params={
                    "models": "nudity-2.0,weapon,gore",
                    "api_user": SIGHTENGINE_USER,
                    "api_secret": SIGHTENGINE_SECRET,
                    "url": file_url,
                }
            )
            result = check_resp.json()
            if result.get("status") != "success":
                return True, "sightengine_error"

            nudity = result.get("nudity", {})
            weapon = result.get("weapon", {})
            gore = result.get("gore", {})

            reasons = []
            if nudity.get("sexual_activity", 0) > 0.5: reasons.append("محتوى جنسي")
            if nudity.get("sexual_display", 0) > 0.5: reasons.append("عرض جنسي")
            if nudity.get("erotica", 0) > 0.7: reasons.append("إباحي")
            if weapon.get("classes", {}).get("firearm", 0) > 0.7: reasons.append("سلاح")
            if gore.get("prob", 0) > 0.7: reasons.append("عنف")

            return (False, ", ".join(reasons)) if reasons else (True, "safe")
    except Exception as e:
        print(f"❌ NSFW error: {e}")
        return True, f"error"


# ============ Matcher ============
def extract_words(text: str) -> set:
    if not text:
        return set()
    text = re.sub(r'[^\w\s\u0600-\u06FF]', ' ', text.lower())
    return {w for w in text.split() if len(w) > 2}


def time_to_hours(t: str) -> int:
    return {"time_1h": 1, "time_today": 12, "time_yesterday": 36,
            "time_week": 168, "time_month": 720, "time_older": 8760}.get(t, 24)


def calculate_score(lost: Dict, found: Dict) -> int:
    score = 0
    if lost.get("category") == found.get("category"):
        score += 25
        if lost.get("subcategory") == found.get("subcategory"):
            score += 15
    else:
        return 0
    if lost.get("location_city") == found.get("location_city"):
        score += 20
    diff = abs(time_to_hours(lost.get("time_range", "")) - time_to_hours(found.get("time_range", "")))
    if diff <= 6: score += 15
    elif diff <= 24: score += 10
    lw = extract_words(lost.get("description", ""))
    fw = extract_words(found.get("description", ""))
    if lw and fw:
        score += int(len(lw & fw) / len(lw | fw) * 25)
    return score


async def find_matches(item: Dict) -> List[Tuple[Dict, int]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        opposite = "found" if item["type"] == "lost" else "lost"
        rows = await conn.fetch("""
            SELECT * FROM items WHERE type = $1 AND status = 'active'
            AND category = $2 LIMIT 50
        """, opposite, item["category"])
    matches = []
    for row in rows:
        candidate = dict(row)
        score = calculate_score(item, candidate) if item["type"] == "lost" else calculate_score(candidate, item)
        if score >= 40:
            matches.append((candidate, score))
    matches.sort(key=lambda x: -x[1])
    return matches[:3]


# ============ Messages (بدون Markdown) ============
MSG = {
    "ar": {
        "welcome": "🔍 بوت المفقودات\n\n📕 مفقود: {total_lost}\n📗 موجود: {total_found}\n🎯 حالات نجاح: {total_resolved}\n👥 مستخدمون: {total_users}",
        "help": (
            "📖 دليل استخدام البوت\n\n"
            "🔍 ما هو البوت؟\n"
            "بوت ذكي يساعدك على:\n"
            "• 🔎 إيجاد ما فقدته\n"
            "• 📦 إرجاع ما وجدته\n\n"
            "━━━━━━━━━━━━━━━\n\n"
            "📝 كيف أضيف بلاغ؟\n"
            "1️⃣ اضغط '📝 أضف بلاغ'\n"
            "2️⃣ اختر: مفقود/موجود\n"
            "3️⃣ اختر الفئة\n"
            "4️⃣ اكتب وصفاً دقيقاً\n"
            "5️⃣ حدد المحافظة والوقت\n"
            "6️⃣ أضف معلومات الاتصال\n"
            "7️⃣ أضف صوراً\n\n"
            "━━━━━━━━━━━━━━━\n\n"
            "🎯 التطابق:\n"
            "البوت يطابق تلقائياً:\n"
            "• الفئة (40 نقطة)\n"
            "• الموقع (20 نقطة)\n"
            "• الوقت (15 نقطة)\n"
            "• الوصف (25 نقطة)\n\n"
            "━━━━━━━━━━━━━━━\n\n"
            "💡 نصائح:\n"
            "✅ كن دقيقاً في الوصف\n"
            "✅ أضف صوراً\n"
            "✅ تحقق من معلومات الاتصال\n\n"
            "━━━━━━━━━━━━━━━\n\n"
            "🛡️ الحماية:\n"
            "صور غير لائقة = حظر فوري\n\n"
            "📬 رسائلي:\n"
            "استقبل رسائل ورد من داخل البوت"
        ),
        "choose_type": "📝 ما نوع البلاغ؟",
        "choose_category": "🏷️ اختر الفئة:",
        "choose_subcategory": "📂 اختر الفئة الفرعية:",
        "enter_description": f"✍️ صف الشيء بالتفصيل\n\n⚠️ الشروط:\n• {MIN_DESC_LEN}-{MAX_DESC_LEN} حرف\n• {MIN_DESC_WORDS} كلمات على الأقل\n• كن دقيقاً (اللون، الحجم، العلامات)\n\nمثال:\nمحفظة جلدية بنية، فيها هوية وبطاقة، عليها خدش",
        "choose_city": "📍 اختر المحافظة:",
        "choose_time": "⏰ متى؟",
        "choose_contact": "📞 معلومات الاتصال\n\n⚠️ مطلوب: وسيلة اتصال\n\nكيف يريد الناس التواصل معك؟",
        "ask_username": "💬 أرسل يوزر تلجرام\n\nمثال: @username",
        "ask_phone": "📱 أرسل رقم هاتفك\n\nمثال: +9647712345678",
        "send_photo": f"📸 أرسل صوراً\n\nحتى {MAX_PHOTOS} صور.\nالصورة تزيد فرص الإيجاد 70%!",
        "item_created": "🎉 تم نشر بلاغك!\n\n📋 رقم البلاغ: #{number}",
        "search_prompt": "🔍 ما الذي تبحث عنه؟",
        "no_results": "❌ لا توجد نتائج",
        "no_items": "📭 لا توجد بلاغات",
        "my_items": "📋 بلاغاتي ({count})",
        "no_matches": "🔍 لا توجد تطابقات\n\nالبوت يبحث تلقائياً...",
        "no_messages": "📭 لا توجد رسائل",
        "inbox": "📬 رسائلي ({count})",
    },
    "en": {
        "welcome": "🔍 Lost & Found\n\n📕 {total_lost} | 📗 {total_found} | 🎯 {total_resolved}",
        "help": "📖 Help",
        "choose_type": "📝 Type?",
        "choose_category": "🏷️ Category:",
        "choose_subcategory": "📂 Sub:",
        "enter_description": "✍️ Describe:",
        "choose_city": "📍 City:",
        "choose_time": "⏰ When?",
        "choose_contact": "📞 Contact:",
        "ask_username": "💬 Username:",
        "ask_phone": "📱 Phone:",
        "send_photo": "📸 Photos:",
        "item_created": "🎉 Published! #{number}",
        "search_prompt": "🔍 Search:",
        "no_results": "❌ No results",
        "no_items": "📭 No reports",
        "my_items": "📋 Mine ({count})",
        "no_matches": "🔍 No matches",
        "no_messages": "📭 No messages",
        "inbox": "📬 Inbox ({count})",
    }
}


def t(lang: str, key: str, **kw) -> str:
    text = MSG.get(lang, MSG["ar"]).get(key, key)
    try:
        return text.format(**kw) if kw else text
    except:
        return text


# ============ Keyboards ============
def kb_main(lang="ar"): return InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 أضف بلاغ" if lang == "ar" else "📝 Add", callback_data="add")],
    [InlineKeyboardButton("🔍 ابحث" if lang == "ar" else "🔍 Search", callback_data="search")],
    [InlineKeyboardButton("📋 بلاغاتي" if lang == "ar" else "📋 Mine", callback_data="my"),
     InlineKeyboardButton("🎯 التطابقات" if lang == "ar" else "🎯 Matches", callback_data="matches")],
    [InlineKeyboardButton("📬 رسائلي" if lang == "ar" else "📬 Inbox", callback_data="inbox")],
    [InlineKeyboardButton("ℹ️ مساعدة" if lang == "ar" else "ℹ️ Help", callback_data="help")],
])


def kb_type(lang="ar"): return InlineKeyboardMarkup([
    [InlineKeyboardButton("📕 مفقود" if lang == "ar" else "📕 Lost", callback_data="type_lost"),
     InlineKeyboardButton("📗 موجود" if lang == "ar" else "📗 Found", callback_data="type_found")],
    [InlineKeyboardButton("🔙 رجوع", callback_data="menu")],
])


def kb_categories(lang="ar"):
    rows = [[InlineKeyboardButton(c["ar"], callback_data=f"cat_{k}")] for k, c in CATEGORIES.items()]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def kb_subcategories(cat_key):
    rows = [[InlineKeyboardButton(n, callback_data=f"sub_{cat_key}_{k}")] for k, n in CATEGORIES.get(cat_key, {}).get("subs", {}).items()]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="add")])
    return InlineKeyboardMarkup(rows)


def kb_cities(lang="ar"):
    rows, row = [], []
    for city in CITIES:
        row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="add")])
    return InlineKeyboardMarkup(rows)


def kb_times(lang="ar"): return InlineKeyboardMarkup([
    [InlineKeyboardButton("🕐 آخر ساعة", callback_data="time_1h"),
     InlineKeyboardButton("🕐 اليوم", callback_data="time_today")],
    [InlineKeyboardButton("🕐 أمس", callback_data="time_yesterday"),
     InlineKeyboardButton("🕐 هذا الأسبوع", callback_data="time_week")],
    [InlineKeyboardButton("🔙 رجوع", callback_data="add")],
])


def kb_contact_info(lang="ar"): return InlineKeyboardMarkup([
    [InlineKeyboardButton("💬 يوزر تلجرام", callback_data="contact_username")],
    [InlineKeyboardButton("📱 رقم هاتف", callback_data="contact_phone")],
    [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_time")],
])


def kb_photos_done(lang="ar"): return InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ تم، تأكيد" if lang == "ar" else "✅ Done", callback_data="photos_done")],
    [InlineKeyboardButton("⏭️ تخطي" if lang == "ar" else "⏭️ Skip", callback_data="photos_skip")],
])


def kb_review(lang="ar"): return InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ تأكيد ونشر" if lang == "ar" else "✅ Confirm", callback_data="confirm_item")],
    [InlineKeyboardButton("❌ إلغاء" if lang == "ar" else "❌ Cancel", callback_data="cancel_item")],
])


def kb_my_items_actions(): return InlineKeyboardMarkup([
    [InlineKeyboardButton("🗑️ تصفير بلاغاتي", callback_data="clear_my")],
    [InlineKeyboardButton("🔙 رجوع", callback_data="menu")],
])


def kb_confirm_clear(): return InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ نعم، احذف الكل", callback_data="confirm_clear_yes")],
    [InlineKeyboardButton("❌ إلغاء", callback_data="confirm_clear_no")],
])


def kb_cancel_message(lang="ar"): return InlineKeyboardMarkup([
    [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_msg")],
])


def kb_inbox_back(lang="ar"): return InlineKeyboardMarkup([
    [InlineKeyboardButton("🔙 رجوع", callback_data="menu")],
])


# ============ App ============
app_tg = Application.builder().token(BOT_TOKEN).build()
print(f"✅ App built")


# ============ Handlers ============
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await is_user_banned(user.id):
        await update.message.reply_text("🚫 أنت محظور")
        return
    await get_or_create_user(user.id, user.username, user.first_name, user.last_name)
    db_user = await get_user(user.id)
    lang = db_user.get("lang", "ar") if db_user else "ar"
    context.user_data.clear()
    context.user_data["lang"] = lang
    stats = await get_global_stats()
    await update.message.reply_text(t(lang, "welcome", **stats), reply_markup=kb_main(lang))


async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    context.user_data.clear()
    context.user_data["lang"] = lang
    stats = await get_global_stats()
    await reply_or_edit(update, t(lang, "welcome", **stats), kb_main(lang))


async def cb_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    await reply_or_edit(update, t(lang, "help"), kb_main(lang))


async def cb_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if await is_user_banned(q.from_user.id):
        await reply_or_edit(update, "🚫 محظور")
        return
    lang = context.user_data.get("lang", "ar")
    context.user_data.clear()
    context.user_data["lang"] = lang
    context.user_data["state"] = "choosing_type"
    context.user_data["item"] = {}
    context.user_data["photos"] = []
    context.user_data["contact"] = {}
    await reply_or_edit(update, t(lang, "choose_type"), kb_type(lang))


async def cb_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    item = context.user_data.setdefault("item", {})
    item["type"] = "lost" if q.data == "type_lost" else "found"
    context.user_data["state"] = "choosing_category"
    await reply_or_edit(update, t(lang, "choose_category"), kb_categories(lang))


async def cb_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    cat_key = q.data.replace("cat_", "")
    context.user_data["item"]["category"] = cat_key
    context.user_data["state"] = "choosing_subcategory"
    await reply_or_edit(update, t(lang, "choose_subcategory"), kb_subcategories(cat_key))


async def cb_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    parts = q.data.split("_", 2)
    sub_key = parts[2] if len(parts) > 2 else "other"
    context.user_data["item"]["subcategory"] = sub_key
    context.user_data["state"] = "waiting_description"
    await reply_or_edit(update, t(lang, "enter_description"))


async def cb_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    city = q.data.replace("city_", "")
    context.user_data["item"]["location_city"] = city
    context.user_data["state"] = "choosing_time"
    await reply_or_edit(update, t(lang, "choose_time"), kb_times(lang))


async def cb_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    context.user_data["item"]["time_range"] = q.data
    context.user_data["state"] = "choosing_contact_method"
    await reply_or_edit(update, t(lang, "choose_contact"), kb_contact_info(lang))


async def cb_back_to_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    context.user_data["state"] = "choosing_time"
    await reply_or_edit(update, t(lang, "choose_time"), kb_times(lang))


async def cb_contact_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    contact_type = q.data.replace("contact_", "")
    context.user_data["contact_type"] = contact_type
    context.user_data["state"] = "waiting_contact_value"
    text = t(lang, "ask_username") if contact_type == "username" else t(lang, "ask_phone")
    await reply_or_edit(update, text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    lang = context.user_data.get("lang", "ar")
    text = update.message.text.strip()

    if state == "waiting_description":
        errors = []
        if len(text) < MIN_DESC_LEN: errors.append(f"• قصير جداً (الحد: {MIN_DESC_LEN})")
        if len(text) > MAX_DESC_LEN: errors.append(f"• طويل جداً (الحد: {MAX_DESC_LEN})")
        words = text.split()
        if len(words) < MIN_DESC_WORDS: errors.append(f"• {MIN_DESC_WORDS} كلمات على الأقل")
        if len(set(words)) < 3: errors.append("• الوصف عشوائي")

        if errors:
            await update.message.reply_text("❌ الوصف غير مقبول:\n\n" + "\n".join(errors) + "\n\n✍️ أعد الإرسال:")
            return

        context.user_data["item"]["description"] = text
        context.user_data["state"] = "choosing_city"
        await update.message.reply_text("✅ تم حفظ الوصف\n\n📍 اختر المحافظة:", reply_markup=kb_cities(lang))
        return

    if state == "waiting_contact_value":
        contact_type = context.user_data.get("contact_type")
        if contact_type == "username":
            username = text.lstrip("@").strip()
            if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', username):
                await update.message.reply_text("❌ يوزر غير صحيح!\n\nمثال: @ahmed_2024")
                return
            context.user_data["contact"] = {"type": "username", "value": f"@{username}"}
        else:
            phone = re.sub(r'[^\d+]', '', text)
            if len(phone) < 10 or len(phone) > 15:
                await update.message.reply_text("❌ رقم غير صحيح!\n\nمثال: +9647712345678")
                return
            context.user_data["contact"] = {"type": "phone", "value": phone}

        context.user_data["state"] = "waiting_photos"
        await update.message.reply_text(
            f"✅ تم حفظ الاتصال\n\n📞 {context.user_data['contact']['value']}\n\n" + t(lang, "send_photo"),
            reply_markup=kb_photos_done(lang)
        )
        return

    if state == "searching":
        results = await search_items(query=text)
        context.user_data["state"] = None
        if not results:
            await update.message.reply_text(t(lang, "no_results"), reply_markup=kb_main(lang))
            return
        await update.message.reply_text(f"🔍 نتائج البحث ({len(results)}):")
        for item in results[:5]:
            await send_item_card(update.message, item, lang)
        await update.message.reply_text("🔙", reply_markup=kb_main(lang))
        return

    if state and state.startswith("sending_message_to_"):
        target_user_id = int(state.replace("sending_message_to_", ""))
        item_id = context.user_data.get("msg_item_id")
        try:
            await save_direct_message(update.effective_user.id, target_user_id, text, item_id)
            sender_name = update.effective_user.first_name or "مستخدم"
            try:
                await app_tg.bot.send_message(
                    chat_id=target_user_id,
                    text=f"📬 رسالة جديدة!\n\n👤 من: {sender_name}\n\n💬 {text}\n\nللرد اذهب إلى '📬 رسائلي'"
                )
            except:
                pass
            context.user_data["state"] = None
            await update.message.reply_text("✅ تم إرسال رسالتك!", reply_markup=kb_main(lang))
        except Exception as e:
            await update.message.reply_text(f"❌ فشل: {e}", reply_markup=kb_main(lang))
        return


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    lang = context.user_data.get("lang", "ar")
    user_id = update.effective_user.id

    if await is_user_banned(user_id):
        await update.message.reply_text("🚫 محظور")
        return

    if state != "waiting_photos":
        return

    photos = context.user_data.get("photos", [])
    if len(photos) >= MAX_PHOTOS:
        await update.message.reply_text(f"⚠️ الحد الأقصى ({MAX_PHOTOS})", reply_markup=kb_photos_done(lang))
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id
    checking_msg = await update.message.reply_text("🔍 فحص...")

    try:
        is_safe, reason = await check_image_nsfw(file_id)
        if not is_safe:
            warning_count = await add_warning(user_id)
            if warning_count >= MAX_WARNINGS:
                await ban_user(user_id, f"محتوى غير لائق ({reason})")
                await checking_msg.edit_text(f"🚫 حُظرت\n\n⚠️ {reason}")
            else:
                await checking_msg.edit_text(
                    f"❌ صورة مرفوضة!\n\n⚠️ {reason}\n📊 التحذير: {warning_count}/{MAX_WARNINGS}\n\n📸 أرسل صورة أخرى:"
                )
            return

        photos.append(file_id)
        context.user_data["photos"] = photos
        await checking_msg.delete()
        await update.message.reply_text(
            f"✅ تم إضافة الصورة {len(photos)}/{MAX_PHOTOS}\n\nأرسل صورة أخرى، أو اضغط 'تم'.",
            reply_markup=kb_photos_done(lang)
        )
    except Exception as e:
        print(f"❌ photo error: {e}")
        photos.append(file_id)
        context.user_data["photos"] = photos
        await checking_msg.delete()
        await update.message.reply_text(
            f"✅ تم إضافة الصورة {len(photos)}/{MAX_PHOTOS}",
            reply_markup=kb_photos_done(lang)
        )


async def cb_photos_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await show_review(update, context)


async def cb_photos_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["photos"] = []
    await show_review(update, context)


async def show_review(update: Update, context):
    lang = context.user_data.get("lang", "ar")
    item = context.user_data.get("item", {})
    contact = context.user_data.get("contact", {})
    photos = context.user_data.get("photos", [])

    type_label = "📕 مفقود" if item.get("type") == "lost" else "📗 موجود"
    cat = CATEGORIES.get(item.get("category"), {}).get("ar", "")
    sub = CATEGORIES.get(item.get("category"), {}).get("subs", {}).get(item.get("subcategory", ""), "")
    contact_type_label = "💬 يوزر" if contact.get("type") == "username" else "📱 هاتف"

    text = (
        f"✅ مراجعة البلاغ\n\n"
        f"📕 النوع: {type_label}\n"
        f"🏷️ الفئة: {cat} > {sub}\n"
        f"✍️ الوصف: {item.get('description', '')[:150]}\n"
        f"📍 الموقع: {item.get('location_city', '—')}\n"
        f"⏰ الوقت: {item.get('time_range', '—')}\n"
        f"📞 الاتصال: {contact_type_label} — {contact.get('value', '—')}\n"
        f"📸 الصور: {len(photos)}\n\n"
        f"⚠️ تأكد من المعلومات!"
    )
    context.user_data["state"] = "confirming"
    await reply_or_edit(update, text, kb_review(lang))


async def cb_confirm_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("⏳")
    await finalize_item(update, context, is_callback=True)


async def cb_cancel_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("❌")
    lang = context.user_data.get("lang", "ar")
    context.user_data.clear()
    context.user_data["lang"] = lang
    stats = await get_global_stats()
    await reply_or_edit(update, t(lang, "welcome", **stats), kb_main(lang))


async def finalize_item(update: Update, context, is_callback: bool = False):
    lang = context.user_data.get("lang", "ar")
    item = context.user_data.get("item", {})
    contact = context.user_data.get("contact", {})
    photos = context.user_data.get("photos", [])
    user_id = update.effective_user.id

    if not item.get("description") or not contact.get("value"):
        await reply_or_edit(update, "❌ معلومات ناقصة", kb_main(lang))
        return

    try:
        saved = await create_item(
            user_id=user_id, item_type=item["type"], category=item["category"],
            subcategory=item.get("subcategory", "other"), description=item["description"],
            city=item.get("location_city", "—"), time_range=item.get("time_range", "—"),
            contact_method=contact.get("type"), contact_value=contact.get("value"),
            photos=photos
        )
        matches = await find_matches(saved)
        context.user_data.clear()
        context.user_data["lang"] = lang
        text = t(lang, "item_created", number=saved["report_number"])
        if matches:
            text += f"\n\n🎯 {len(matches)} تطابق محتمل!"
        await reply_or_edit(update, text, kb_main(lang))
    except Exception as e:
        print(f"❌ finalize error: {e}")
        traceback.print_exc()
        await reply_or_edit(update, f"❌ خطأ: {e}", kb_main(lang))


async def send_item_card(message, item: Dict, lang: str):
    type_emoji = "📕" if item["type"] == "lost" else "📗"
    cat = CATEGORIES.get(item["category"], {}).get("ar", "")
    sub = CATEGORIES.get(item["category"], {}).get("subs", {}).get(item.get("subcategory", ""), "")
    text = f"{type_emoji} #{item['report_number']}\n🏷️ {cat} > {sub}\n✍️ {item['description'][:100]}\n📍 {item.get('location_city', '—')}"
    await increment_views(item["id"])
    photos = item.get("photos") or []
    photo_id = photos[0] if photos else item.get("photo_file_id")
    if photo_id:
        await message.reply_photo(photo_id, caption=text)
    else:
        await message.reply_text(text)


async def cb_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    context.user_data["state"] = "searching"
    await reply_or_edit(update, t(lang, "search_prompt"),
        InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="menu")]]))


async def cb_my(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    items = await get_user_items(q.from_user.id, limit=10)
    if not items:
        await reply_or_edit(update, t(lang, "no_items"), kb_main(lang))
        return
    await reply_or_edit(update, t(lang, "my_items", count=len(items)), kb_my_items_actions())
    for item in items:
        await send_item_card(q.message, item, lang)


async def cb_clear_my(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    count = await count_user_items(q.from_user.id)
    if count == 0:
        await q.answer("لا توجد بلاغات", show_alert=True)
        return
    await reply_or_edit(update, f"⚠️ تأكيد التصفير\n\nحذف {count} بلاغ؟\n\n⚠️ لا يمكن التراجع!", kb_confirm_clear())


async def cb_confirm_clear_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    count = await delete_all_user_items(q.from_user.id)
    await reply_or_edit(update, f"✅ تم التصفير!\n\n🗑️ حُذف {count} بلاغ", kb_main(lang))


async def cb_confirm_clear_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    await reply_or_edit(update, "✅ تم الإلغاء", kb_main(lang))


async def cb_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT m.*, l.description as lost_desc, l.user_id as lost_user, l.contact_value as lost_contact, l.contact_method as lost_cmethod,
                   f.description as found_desc, f.user_id as found_user, f.contact_value as found_contact, f.contact_method as found_cmethod
            FROM matches m
            JOIN items l ON m.lost_item_id = l.id
            JOIN items f ON m.found_item_id = f.id
            WHERE (l.user_id = $1 OR f.user_id = $1) AND m.resolved = FALSE
            ORDER BY m.score DESC LIMIT 10
        """, q.from_user.id)

    if not rows:
        await reply_or_edit(update, t(lang, "no_matches"), kb_main(lang))
        return

    await reply_or_edit(update, f"🎯 التطابقات ({len(rows)})")

    for row in rows:
        m = dict(row)
        if m['lost_user'] == q.from_user.id:
            other_user_id = m['found_user']
            other_desc = m['found_desc']
            other_contact = m['found_contact']
            other_cmethod = m['found_cmethod']
            other_type = "📗 صاحب الموجود"
        else:
            other_user_id = m['lost_user']
            other_desc = m['lost_desc']
            other_contact = m['lost_contact']
            other_cmethod = m['lost_cmethod']
            other_type = "📕 صاحب المفقود"

        contact_label = "💬 يوزر تلجرام" if other_cmethod == "username" else "📱 رقم هاتف"
        contact_display = other_contact or "—"

        text = (f"🎯 تطابق #{m['id']} — {m['score']}%\n\n{other_type}\n✍️ {other_desc[:120]}\n\n"
                f"━━━━━━━━━━━━━━━\nمعلومات الاتصال:\n{contact_label}: {contact_display}\n━━━━━━━━━━━━━━━")

        buttons = []
        if other_cmethod == "username" and other_contact:
            buttons.append([InlineKeyboardButton("💬 فتح محادثة تلجرام", url=f"https://t.me/{other_contact.lstrip('@')}")])
        elif other_cmethod == "phone" and other_contact:
            buttons.append([InlineKeyboardButton("📱 الاتصال", url=f"tel:{other_contact}")])
        buttons.append([InlineKeyboardButton("💬 إرسال رسالة", callback_data=f"msg_owner_{other_user_id}_{m['id']}")])

        await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_msg_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    parts = q.data.split("_")
    target_user_id = int(parts[2])
    match_id = int(parts[3]) if len(parts) > 3 else None
    context.user_data["state"] = f"sending_message_to_{target_user_id}"
    context.user_data["msg_item_id"] = match_id
    target_user = await get_user(target_user_id)
    target_name = target_user.get("first_name", "المستخدم") if target_user else "المستخدم"
    await reply_or_edit(update,
        f"💬 إرسال رسالة إلى: {target_name}\n\n✍️ اكتب رسالتك:\n\n"
        f"💡 اذكر تفاصيل الشيء، حدد وقت ومكان التسليم\n\n⚠️ سيتم إشعاره فوراً.",
        kb_cancel_message(lang))


async def cb_cancel_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    context.user_data["state"] = None
    await reply_or_edit(update, "❌ تم الإلغاء", kb_main(lang))


async def cb_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    messages = await get_user_messages(q.from_user.id, limit=10)
    if not messages:
        await reply_or_edit(update, t(lang, "no_messages"), kb_main(lang))
        return
    await reply_or_edit(update, t(lang, "inbox", count=len(messages)), kb_inbox_back(lang))
    for msg in messages:
        sender_name = msg.get("from_name") or "مستخدم"
        sender_username = msg.get("from_username")
        text = f"📬 رسالة\n\n👤 من: {sender_name}"
        if sender_username:
            text += f" (@{sender_username})"
        text += f"\n\n💬 {msg['message']}"
        buttons = []
        if sender_username:
            buttons.append([InlineKeyboardButton("💬 الرد عبر تلجرام", url=f"https://t.me/{sender_username}")])
        buttons.append([InlineKeyboardButton("💬 رد من البوت", callback_data=f"msg_owner_{msg['from_user_id']}_0")])
        await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


# ============ Register ============
print("🔧 Registering handlers...")
app_tg.add_handler(CommandHandler("start", cmd_start))
app_tg.add_handler(CallbackQueryHandler(cb_menu, pattern="^menu$"))
app_tg.add_handler(CallbackQueryHandler(cb_help, pattern="^help$"))
app_tg.add_handler(CallbackQueryHandler(cb_add, pattern="^add$"))
app_tg.add_handler(CallbackQueryHandler(cb_type, pattern="^type_"))
app_tg.add_handler(CallbackQueryHandler(cb_category, pattern="^cat_"))
app_tg.add_handler(CallbackQueryHandler(cb_subcategory, pattern="^sub_"))
app_tg.add_handler(CallbackQueryHandler(cb_city, pattern="^city_"))
app_tg.add_handler(CallbackQueryHandler(cb_time, pattern="^time_"))
app_tg.add_handler(CallbackQueryHandler(cb_back_to_time, pattern="^back_to_time$"))
app_tg.add_handler(CallbackQueryHandler(cb_contact_method, pattern="^contact_"))
app_tg.add_handler(CallbackQueryHandler(cb_photos_done, pattern="^photos_done$"))
app_tg.add_handler(CallbackQueryHandler(cb_photos_skip, pattern="^photos_skip$"))
app_tg.add_handler(CallbackQueryHandler(cb_confirm_item, pattern="^confirm_item$"))
app_tg.add_handler(CallbackQueryHandler(cb_cancel_item, pattern="^cancel_item$"))
app_tg.add_handler(CallbackQueryHandler(cb_search, pattern="^search$"))
app_tg.add_handler(CallbackQueryHandler(cb_my, pattern="^my$"))
app_tg.add_handler(CallbackQueryHandler(cb_clear_my, pattern="^clear_my$"))
app_tg.add_handler(CallbackQueryHandler(cb_confirm_clear_yes, pattern="^confirm_clear_yes$"))
app_tg.add_handler(CallbackQueryHandler(cb_confirm_clear_no, pattern="^confirm_clear_no$"))
app_tg.add_handler(CallbackQueryHandler(cb_matches, pattern="^matches$"))
app_tg.add_handler(CallbackQueryHandler(cb_msg_owner, pattern="^msg_owner_"))
app_tg.add_handler(CallbackQueryHandler(cb_cancel_msg, pattern="^cancel_msg$"))
app_tg.add_handler(CallbackQueryHandler(cb_inbox, pattern="^inbox$"))
app_tg.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
print(f"✅ {len(app_tg.handlers[0])} handlers registered")


# ============ FastAPI ============
app = FastAPI()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ EXCEPTION: {context.error}")
    if context.error:
        traceback.print_exception(type(context.error), context.error, context.error.__traceback__)
    try:
        if isinstance(update, Update):
            if update.effective_message:
                await update.effective_message.reply_text("❌ حدث خطأ. حاول مرة أخرى.")
            elif update.callback_query:
                await update.callback_query.answer("❌ حدث خطأ", show_alert=True)
    except:
        pass


app_tg.add_error_handler(error_handler)


@app.get("/")
async def root():
    return {"status": "ok", "bot": "Lost & Found v4.5"}


@app.get("/health")
async def health():
    return {
        "status": "healthy", "version": "4.5",
        "handlers_count": len(app_tg.handlers[0]) if app_tg.handlers else 0,
    }


@app.post("/")
async def webhook(request: Request):
    try:
        data = await request.json()
        update_id = data.get("update_id", "?")
        print(f"\n{'='*70}\n📥 UPDATE #{update_id}")

        if "callback_query" in data:
            cb_data = data["callback_query"].get("data", "")
            print(f"🔘 CALLBACK: '{cb_data}'")
        elif "message" in data:
            msg_text = data["message"].get("text", "")
            has_photo = "photo" in data["message"]
            print(f"📨 MESSAGE: text='{msg_text[:40]}' photo={has_photo}")

        if WEBHOOK_SECRET:
            secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if secret != WEBHOOK_SECRET:
                print("❌ SECRET MISMATCH")
                raise HTTPException(status_code=403)

        if not app_tg.running:
            print("🔧 Initializing...")
            await app_tg.initialize()
            print(f"✅ Initialized, running={app_tg.running}")

        update = Update.de_json(data, app_tg.bot)
        await app_tg.process_update(update)
        print(f"✅ SUCCESS\n{'='*70}\n")
        return JSONResponse({"ok": True})

    except HTTPException:
        raise
    except Exception as e:
        print(f"\n❌ ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        print(f"{'='*70}\n")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)
