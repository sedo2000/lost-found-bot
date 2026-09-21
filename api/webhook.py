"""
🔍 Lost & Found Bot - Complete Bot in One File
Vercel-ready entrypoint: app
الإصدار 3.0 - تفاصيل دقيقة + معلومات اتصال إلزامية + صور متعددة
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

print(f"🚀 Bot starting...")
print(f"   BOT_TOKEN: {'✅ set' if BOT_TOKEN else '❌ MISSING'}")
print(f"   DATABASE_URL: {'✅ set' if DATABASE_URL else '❌ MISSING'}")
print(f"   SIGHTENGINE: {'✅ set' if SIGHTENGINE_USER else '⚠️ not set'}")

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

# حدود
MAX_WARNINGS = 2
MAX_PHOTOS = 5
MIN_DESCRIPTION_LENGTH = 20
MAX_DESCRIPTION_LENGTH = 500
MIN_DESCRIPTION_WORDS = 4

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
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1", user_id
        )
        if row:
            await conn.execute(
                "UPDATE users SET username=COALESCE($2,username), "
                "first_name=COALESCE($3,first_name) WHERE user_id=$1",
                user_id, username, first_name
            )
            return dict(row)
        row = await conn.fetchrow(
            "INSERT INTO users (user_id, username, first_name, last_name) "
            "VALUES ($1,$2,$3,$4) RETURNING *",
            user_id, username, first_name, last_name
        )
        return dict(row)


async def get_user(user_id: int) -> Optional[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1", user_id
        )
        return dict(row) if row else None


async def add_warning(user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                reason VARCHAR(200),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute(
            "INSERT INTO warnings (user_id, reason) VALUES ($1, $2)",
            user_id, "صورة غير لائقة"
        )
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM warnings WHERE user_id = $1", user_id
        )
        return count


async def is_user_banned(user_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id BIGINT PRIMARY KEY,
                reason VARCHAR(200),
                banned_at TIMESTAMP DEFAULT NOW()
            )
        """)
        row = await conn.fetchrow(
            "SELECT * FROM banned_users WHERE user_id = $1", user_id
        )
        return row is not None


async def ban_user(user_id: int, reason: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO banned_users (user_id, reason)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET reason = $2
        """, user_id, reason)


async def create_item(user_id: int, item_type: str, category: str,
                     subcategory: str, description: str, city: str,
                     time_range: str, contact_method: str = None,
                     contact_value: str = None,
                     photos: List[str] = None) -> Dict:
    """🆕 إنشاء بلاغ مع معلومات اتصال وصور متعددة"""
    pool = await get_pool()
    photos = photos or []
    
    async with pool.acquire() as conn:
        # نحاول الأعمدة الجديدة أولاً
        try:
            row = await conn.fetchrow("""
                INSERT INTO items (
                    user_id, type, category, subcategory, description,
                    location_city, time_range, contact_method, contact_value,
                    photos, status
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'active') RETURNING *
            """, user_id, item_type, category, subcategory, description,
                city, time_range, contact_method, contact_value, photos)
        except Exception:
            # fallback للإصدار القديم
            row = await conn.fetchrow("""
                INSERT INTO items (user_id, type, category, subcategory,
                                  description, location_city, time_range)
                VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *
            """, user_id, item_type, category, subcategory,
                description, city, time_range)
        
        await conn.execute(
            "UPDATE users SET total_reports = total_reports + 1 WHERE user_id = $1",
            user_id
        )
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
            "SELECT COUNT(*) FROM items WHERE user_id = $1 AND status = 'active'",
            user_id
        ) or 0


async def delete_all_user_items(user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE items SET status = 'deleted'
            WHERE user_id = $1 AND status = 'active'
        """, user_id)
        try:
            count = int(result.split()[-1])
        except:
            count = 0
        await conn.execute(
            "UPDATE users SET total_reports = 0 WHERE user_id = $1", user_id
        )
        return count


async def search_items(query: str = None, city: str = None,
                      limit: int = 10) -> List[Dict]:
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
        await conn.execute(
            "UPDATE items SET views_count = views_count + 1 WHERE id = $1",
            item_id
        )


async def get_global_stats() -> Dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {
            "total_lost": await conn.fetchval(
                "SELECT COUNT(*) FROM items WHERE type='lost' AND status='active'"
            ) or 0,
            "total_found": await conn.fetchval(
                "SELECT COUNT(*) FROM items WHERE type='found' AND status='active'"
            ) or 0,
            "total_resolved": await conn.fetchval(
                "SELECT COUNT(*) FROM items WHERE status='resolved'"
            ) or 0,
            "total_users": await conn.fetchval(
                "SELECT COUNT(*) FROM users"
            ) or 0,
        }


# ============ 🛡️ كشف الصور الإباحية ============
async def check_image_nsfw(file_id: str) -> Tuple[bool, str]:
    if not SIGHTENGINE_USER or not SIGHTENGINE_SECRET:
        return True, "no_check"
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
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
            
            sexual_activity = nudity.get("sexual_activity", 0)
            sexual_display = nudity.get("sexual_display", 0)
            erotica = nudity.get("erotica", 0)
            very_suggestive = nudity.get("very_suggestive", 0)
            
            weapon_prob = weapon.get("classes", {}).get("firearm", 0) if weapon else 0
            gore_prob = gore.get("prob", 0) if gore else 0
            
            reasons = []
            if sexual_activity > 0.5: reasons.append("محتوى جنسي صريح")
            if sexual_display > 0.5: reasons.append("عرض جنسي")
            if erotica > 0.7: reasons.append("محتوى إباحي")
            if very_suggestive > 0.8: reasons.append("محتوى مثير جداً")
            if weapon_prob > 0.7: reasons.append("سلاح")
            if gore_prob > 0.7: reasons.append("عنف/دماء")
            
            if reasons:
                return False, ", ".join(reasons)
            return True, "safe"
    
    except Exception as e:
        print(f"❌ Error in NSFW check: {e}")
        return True, f"error: {e}"


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
    diff = abs(time_to_hours(lost.get("time_range", "")) -
               time_to_hours(found.get("time_range", "")))
    if diff <= 6:
        score += 15
    elif diff <= 24:
        score += 10
    lw = extract_words(lost.get("description", ""))
    fw = extract_words(found.get("description", ""))
    if lw and fw:
        jaccard = len(lw & fw) / len(lw | fw)
        score += int(jaccard * 25)
    return score


async def find_matches(item: Dict) -> List[Tuple[Dict, int]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        opposite = "found" if item["type"] == "lost" else "lost"
        rows = await conn.fetch("""
            SELECT * FROM items WHERE type = $1 AND status = 'active'
            AND category = $2 LIMIT 100
        """, opposite, item["category"])

    matches = []
    for row in rows:
        candidate = dict(row)
        if item["type"] == "lost":
            score = calculate_score(item, candidate)
        else:
            score = calculate_score(candidate, item)
        if score >= 40:
            matches.append((candidate, score))

    matches.sort(key=lambda x: -x[1])
    return matches[:3]


# ============ Messages ============
MSG = {
    "ar": {
        "welcome": (
            "🔍 **بوت المفقودات**\n\n"
            "📕 مفقود: {total_lost}\n"
            "📗 موجود: {total_found}\n"
            "🎯 حالات نجاح: {total_resolved}\n"
            "👥 مستخدمون: {total_users}"
        ),
        "choose_type": "📝 **ما نوع البلاغ؟**",
        "choose_category": "🏷️ **اختر الفئة:**",
        "choose_subcategory": "📂 **اختر الفئة الفرعية:**",
        "enter_description": (
            "✍️ **صف الشيء بالتفصيل**\n\n"
            "⚠️ **الشروط:**\n"
            f"• من {MIN_DESCRIPTION_LENGTH} إلى {MAX_DESCRIPTION_LENGTH} حرف\n"
            f"• {MIN_DESCRIPTION_WORDS} كلمات على الأقل\n"
            "• كن دقيقاً (اللون، الحجم، العلامات)\n\n"
            "مثال:\n"
            "_محفظة جلدية بنية، فيها هوية وبطاقة ماستر كارد، عليها خدش في الزاوية_"
        ),
        "choose_city": "📍 **اختر المحافظة:**",
        "choose_time": "⏰ **متى؟**",
        "choose_contact": (
            "📞 **معلومات الاتصال**\n\n"
            "⚠️ **مطلوب:** يجب إضافة وسيلة اتصال\n\n"
            "كيف يريد الناس التواصل معك؟"
        ),
        "ask_username": (
            "💬 **أرسل يوزر تلجرام**\n\n"
            "مثال: `@username` أو `username`\n\n"
            "⚠️ تأكد أن اليوزر صحيح!"
        ),
        "ask_phone": (
            "📱 **أرسل رقم هاتفك**\n\n"
            "مثال: `+9647712345678`\n\n"
            "⚠️ تأكد أن الرقم صحيح!"
        ),
        "send_photo": (
            "📸 **أرسل صور (اختياري)**\n\n"
            f"يمكنك إرسال حتى **{MAX_PHOTOS}** صور.\n"
            "أرسل الصور واحدة تلو الأخرى."
        ),
        "item_created": "🎉 **تم نشر بلاغك!**\n\n📋 رقم البلاغ: `#{number}`",
        "search_prompt": "🔍 **ما الذي تبحث عنه؟**",
        "no_results": "❌ **لا توجد نتائج**",
        "no_items": "📭 **لا توجد بلاغات**",
        "my_items": "📋 **بلاغاتي ({count})**",
        "no_matches": "🔍 **لا توجد تطابقات**",
        "help": (
            "📖 **المساعدة**\n\n"
            "**الأوامر:**\n"
            "• /start - البداية\n"
            "• 📝 أضف بلاغ - إضافة مفقود/موجود\n"
            "• 🔍 ابحث - البحث\n"
            "• 📋 بلاغاتي - بلاغاتك\n"
            "• 🎯 التطابقات - التطابقات\n\n"
            "**نصائح:**\n"
            "• وصف دقيق = نتائج أفضل\n"
            "• صورة = 70% زيادة\n"
            "• معلومات اتصال صحيحة = تواصل أسرع"
        ),
    },
    "en": {
        "welcome": "🔍 **Lost & Found**\n\n📕 {total_lost} | 📗 {total_found} | 🎯 {total_resolved}",
        "choose_type": "📝 **Type?**",
        "choose_category": "🏷️ **Category:**",
        "choose_subcategory": "📂 **Sub:**",
        "enter_description": "✍️ **Describe (20-500 chars):**",
        "choose_city": "📍 **City:**",
        "choose_time": "⏰ **When?**",
        "choose_contact": "📞 **Contact Info (required):**",
        "ask_username": "💬 **Send Telegram username:**",
        "ask_phone": "📱 **Send phone number:**",
        "send_photo": "📸 **Send photos (optional, max 5):**",
        "item_created": "🎉 **Published!** #{number}",
        "search_prompt": "🔍 **Search:**",
        "no_results": "❌ **No results**",
        "no_items": "📭 **No reports**",
        "my_items": "📋 **Mine ({count})**",
        "no_matches": "🔍 **No matches**",
        "help": "📖 **Help**",
    }
}


def t(lang: str, key: str, **kw) -> str:
    text = MSG.get(lang, MSG["ar"]).get(key, key)
    try:
        return text.format(**kw) if kw else text
    except Exception:
        return text


# ============ Keyboards ============
def kb_main(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 أضف بلاغ" if lang == "ar" else "📝 Add", callback_data="add")],
        [InlineKeyboardButton("🔍 ابحث" if lang == "ar" else "🔍 Search", callback_data="search")],
        [InlineKeyboardButton("📋 بلاغاتي" if lang == "ar" else "📋 Mine", callback_data="my"),
         InlineKeyboardButton("🎯 التطابقات" if lang == "ar" else "🎯 Matches", callback_data="matches")],
        [InlineKeyboardButton("ℹ️ مساعدة" if lang == "ar" else "ℹ️ Help", callback_data="help")],
    ])


def kb_type(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📕 مفقود" if lang == "ar" else "📕 Lost", callback_data="type_lost"),
         InlineKeyboardButton("📗 موجود" if lang == "ar" else "📗 Found", callback_data="type_found")],
        [InlineKeyboardButton("🔙 رجوع" if lang == "ar" else "🔙 Back", callback_data="menu")],
    ])


def kb_categories(lang: str = "ar") -> InlineKeyboardMarkup:
    rows = []
    for key, cat in CATEGORIES.items():
        rows.append([InlineKeyboardButton(cat["ar"], callback_data=f"cat_{key}")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="add")])
    return InlineKeyboardMarkup(rows)


def kb_subcategories(cat_key: str) -> InlineKeyboardMarkup:
    rows = []
    cat = CATEGORIES.get(cat_key, {})
    for sub_key, sub_name in cat.get("subs", {}).items():
        rows.append([InlineKeyboardButton(sub_name, callback_data=f"sub_{cat_key}_{sub_key}")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="add")])
    return InlineKeyboardMarkup(rows)


def kb_cities(lang: str = "ar") -> InlineKeyboardMarkup:
    rows = []
    row = []
    for city in CITIES:
        row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def kb_times(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕐 آخر ساعة", callback_data="time_1h"),
         InlineKeyboardButton("🕐 اليوم", callback_data="time_today")],
        [InlineKeyboardButton("🕐 أمس", callback_data="time_yesterday"),
         InlineKeyboardButton("🕐 هذا الأسبوع", callback_data="time_week")],
    ])


def kb_contact_info(lang: str = "ar") -> InlineKeyboardMarkup:
    """🆕 أزرار معلومات الاتصال"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 يوزر تلجرام" if lang == "ar" else "💬 Telegram", callback_data="contact_username")],
        [InlineKeyboardButton("📱 رقم هاتف" if lang == "ar" else "📱 Phone", callback_data="contact_phone")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_time")],
    ])


def kb_photos_done(lang: str = "ar") -> InlineKeyboardMarkup:
    """🆕 إتمام الصور"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تم، تأكيد" if lang == "ar" else "✅ Done", callback_data="photos_done")],
        [InlineKeyboardButton("⏭️ تخطي الصور" if lang == "ar" else "⏭️ Skip", callback_data="photos_skip")],
    ])


def kb_match_actions(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم، هذا هو", callback_data=f"m_yes_{match_id}")],
        [InlineKeyboardButton("❌ لا، ليس هو", callback_data=f"m_no_{match_id}")],
    ])


def kb_my_items_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ تصفير بلاغاتي", callback_data="clear_my")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="menu")],
    ])


def kb_confirm_clear() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم، احذف الكل", callback_data="confirm_clear_yes")],
        [InlineKeyboardButton("❌ لا، إلغاء", callback_data="confirm_clear_no")],
    ])


def kb_review(lang: str = "ar") -> InlineKeyboardMarkup:
    """🆕 مراجعة نهائية"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد ونشر" if lang == "ar" else "✅ Confirm", callback_data="confirm_item")],
        [InlineKeyboardButton("❌ إلغاء" if lang == "ar" else "❌ Cancel", callback_data="cancel_item")],
    ])


# ============ Telegram Application ============
print("🔧 Building Telegram application...")
app_tg = Application.builder().token(BOT_TOKEN).build()
print(f"✅ Telegram app built")


# ============ Handlers ============

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await is_user_banned(user.id):
        await update.message.reply_text(
            "🚫 **أنت محظور من استخدام البوت**", parse_mode="Markdown"
        )
        return
    
    await get_or_create_user(user.id, user.username, user.first_name, user.last_name)
    db_user = await get_user(user.id)
    lang = db_user.get("lang", "ar") if db_user else "ar"
    context.user_data.clear()
    context.user_data["lang"] = lang
    stats = await get_global_stats()
    await update.message.reply_text(
        t(lang, "welcome", **stats),
        reply_markup=kb_main(lang),
        parse_mode="Markdown"
    )


async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    context.user_data.clear()
    context.user_data["lang"] = lang
    stats = await get_global_stats()
    await q.edit_message_text(
        t(lang, "welcome", **stats),
        reply_markup=kb_main(lang),
        parse_mode="Markdown"
    )


async def cb_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if await is_user_banned(q.from_user.id):
        await q.edit_message_text("🚫 أنت محظور")
        return
    lang = context.user_data.get("lang", "ar")
    context.user_data["state"] = "choosing_type"
    context.user_data["item"] = {}
    context.user_data["photos"] = []
    context.user_data["contact"] = {}
    await q.edit_message_text(
        t(lang, "choose_type"),
        reply_markup=kb_type(lang),
        parse_mode="Markdown"
    )


async def cb_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    item = context.user_data.setdefault("item", {})
    item["type"] = "lost" if q.data == "type_lost" else "found"
    context.user_data["state"] = "choosing_category"
    await q.edit_message_text(
        t(lang, "choose_category"),
        reply_markup=kb_categories(lang),
        parse_mode="Markdown"
    )


async def cb_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    cat_key = q.data.replace("cat_", "")
    context.user_data["item"]["category"] = cat_key
    context.user_data["state"] = "choosing_subcategory"
    await q.edit_message_text(
        t(lang, "choose_subcategory"),
        reply_markup=kb_subcategories(cat_key),
        parse_mode="Markdown"
    )


async def cb_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    parts = q.data.split("_", 2)
    sub_key = parts[2] if len(parts) > 2 else "other"
    context.user_data["item"]["subcategory"] = sub_key
    context.user_data["state"] = "waiting_description"
    await q.edit_message_text(
        t(lang, "enter_description"),
        parse_mode="Markdown"
    )


async def cb_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    city = q.data.replace("city_", "")
    context.user_data["item"]["location_city"] = city
    context.user_data["state"] = "choosing_time"
    await q.edit_message_text(
        t(lang, "choose_time"),
        reply_markup=kb_times(lang),
        parse_mode="Markdown"
    )


async def cb_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    time_range = q.data
    context.user_data["item"]["time_range"] = time_range
    # 🆕 الانتقال لمعلومات الاتصال
    context.user_data["state"] = "choosing_contact_method"
    await q.edit_message_text(
        t(lang, "choose_contact"),
        reply_markup=kb_contact_info(lang),
        parse_mode="Markdown"
    )


# 🆕 معلومات الاتصال
async def cb_contact_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    contact_type = q.data.replace("contact_", "")
    context.user_data["contact_type"] = contact_type
    context.user_data["state"] = "waiting_contact_value"
    
    if contact_type == "username":
        await q.edit_message_text(t(lang, "ask_username"), parse_mode="Markdown")
    else:
        await q.edit_message_text(t(lang, "ask_phone"), parse_mode="Markdown")


# 🆕 معالج الرسائل (وصف + اتصال + بحث)
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    lang = context.user_data.get("lang", "ar")
    text = update.message.text.strip()

    # ===== 1. الوصف =====
    if state == "waiting_description":
        errors = []
        if len(text) < MIN_DESCRIPTION_LENGTH:
            errors.append(f"• قصير جداً (الحد: {MIN_DESCRIPTION_LENGTH} حرف)")
        if len(text) > MAX_DESCRIPTION_LENGTH:
            errors.append(f"• طويل جداً (الحد: {MAX_DESCRIPTION_LENGTH} حرف)")
        words = text.split()
        if len(words) < MIN_DESCRIPTION_WORDS:
            errors.append(f"• يحتاج {MIN_DESCRIPTION_WORDS} كلمات على الأقل")
        if len(set(words)) < 3:
            errors.append("• الوصف يبدو عشوائياً")
        forbidden = ["test", "asdf", "qwerty"]
        for w in forbidden:
            if w in text.lower():
                errors.append(f"• كلمة ممنوعة: {w}")
                break
        
        if errors:
            await update.message.reply_text(
                "❌ **الوصف غير مقبول:**\n\n" + "\n".join(errors) + "\n\n✍️ أرسل وصفاً أدق:",
                parse_mode="Markdown"
            )
            return
        
        context.user_data["item"]["description"] = text
        context.user_data["state"] = "choosing_city"
        
        # نصائح
        tips = []
        if not any(c in text for c in ["أسود", "أبيض", "أحمر", "أزرق", "بني", "وردي"]):
            tips.append("🎨 أضف اللون")
        if not any(s in text for s in ["صغير", "كبير", "متوسط"]):
            tips.append("📏 أضف الحجم")
        
        tips_text = "\n\n" + "\n".join(tips) if tips else ""
        
        await update.message.reply_text(
            f"✅ **تم حفظ الوصف**{tips_text}\n\n📍 اختر المحافظة:",
            reply_markup=kb_cities(lang),
            parse_mode="Markdown"
        )
        return

    # ===== 2. معلومات الاتصال =====
    if state == "waiting_contact_value":
        contact_type = context.user_data.get("contact_type")
        
        if contact_type == "username":
            username = text.lstrip("@").strip()
            if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', username):
                await update.message.reply_text(
                    "❌ **يوزر غير صحيح!**\n\n"
                    "• يبدأ بحرف إنجليزي\n• 5-32 حرفاً\n• أحرف وأرقام و _ فقط\n\n"
                    "مثال: `@ahmed_2024`",
                    parse_mode="Markdown"
                )
                return
            context.user_data["contact"] = {"type": "username", "value": f"@{username}"}
        else:
            phone = re.sub(r'[^\d+]', '', text)
            if len(phone) < 10 or len(phone) > 15:
                await update.message.reply_text(
                    "❌ **رقم غير صحيح!**\n\n"
                    "• 10-15 رقماً\n• يمكن أن يبدأ بـ +\n\n"
                    "مثال: `+9647712345678`",
                    parse_mode="Markdown"
                )
                return
            context.user_data["contact"] = {"type": "phone", "value": phone}
        
        # الانتقال للصور
        context.user_data["state"] = "waiting_photos"
        await update.message.reply_text(
            f"✅ **تم حفظ معلومات الاتصال**\n\n"
            f"📞 `{context.user_data['contact']['value']}`\n\n"
            + t(lang, "send_photo"),
            reply_markup=kb_photos_done(lang),
            parse_mode="Markdown"
        )
        return

    # ===== 3. البحث =====
    if state == "searching":
        results = await search_items(query=text)
        context.user_data["state"] = None
        if not results:
            await update.message.reply_text(
                t(lang, "no_results"),
                reply_markup=kb_main(lang)
            )
            return
        await update.message.reply_text(f"🔍 نتائج البحث ({len(results)}):")
        for item in results[:5]:
            await send_item_card(update.message, item, lang)
        await update.message.reply_text("🔙", reply_markup=kb_main(lang))


# 🆕 معالج الصور (متعدد)
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    lang = context.user_data.get("lang", "ar")
    user_id = update.effective_user.id
    
    if await is_user_banned(user_id):
        await update.message.reply_text("🚫 أنت محظور")
        return
    
    # ===== حالة الصور =====
    if state != "waiting_photos":
        return
    
    photos = context.user_data.get("photos", [])
    
    if len(photos) >= MAX_PHOTOS:
        await update.message.reply_text(
            f"⚠️ وصلت للحد الأقصى ({MAX_PHOTOS} صور)",
            reply_markup=kb_photos_done(lang)
        )
        return
    
    # فحص NSFW
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    checking_msg = await update.message.reply_text("🔍 جاري فحص الصورة...")
    
    try:
        is_safe, reason = await check_image_nsfw(file_id)
        
        if not is_safe:
            warning_count = await add_warning(user_id)
            if warning_count >= MAX_WARNINGS:
                await ban_user(user_id, f"محتوى غير لائق ({reason})")
                await checking_msg.edit_text(
                    f"🚫 **تم حظرك**\n\n"
                    f"⚠️ السبب: {reason}\n"
                    f"📊 التحذير: {warning_count}/{MAX_WARNINGS}",
                    parse_mode="Markdown"
                )
            else:
                await checking_msg.edit_text(
                    f"❌ **صورة مرفوضة!**\n\n"
                    f"⚠️ السبب: {reason}\n"
                    f"📊 التحذير: {warning_count}/{MAX_WARNINGS}\n\n"
                    f"⚠️ عند {MAX_WARNINGS} تحذيرات → حظر!\n\n"
                    f"📸 أرسل صورة أخرى:",
                    parse_mode="Markdown"
                )
            return
        
        photos.append(file_id)
        context.user_data["photos"] = photos
        await checking_msg.delete()
        
        await update.message.reply_text(
            f"✅ **تم إضافة الصورة {len(photos)}/{MAX_PHOTOS}**\n\n"
            f"أرسل صورة أخرى، أو اضغط 'تم' للتأكيد.",
            reply_markup=kb_photos_done(lang),
            parse_mode="Markdown"
        )
    
    except Exception as e:
        print(f"❌ Error: {e}")
        photos.append(file_id)
        context.user_data["photos"] = photos
        await checking_msg.delete()
        await update.message.reply_text(
            f"✅ **تم إضافة الصورة {len(photos)}/{MAX_PHOTOS}**",
            reply_markup=kb_photos_done(lang)
        )


# 🆕 تأكيد الصور
async def cb_photos_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await show_review(q, context)


async def cb_photos_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["photos"] = []
    await show_review(q, context)


# 🆕 عرض المراجعة
async def show_review(query, context):
    lang = context.user_data.get("lang", "ar")
    item = context.user_data.get("item", {})
    contact = context.user_data.get("contact", {})
    photos = context.user_data.get("photos", [])
    
    type_label = "📕 مفقود" if item["type"] == "lost" else "📗 موجود"
    cat = CATEGORIES.get(item["category"], {}).get("ar", item["category"])
    sub = CATEGORIES.get(item["category"], {}).get("subs", {}).get(item.get("subcategory", ""), "")
    contact_type_label = "💬 يوزر" if contact.get("type") == "username" else "📱 هاتف"
    
    text = (
        f"✅ **مراجعة البلاغ**\n\n"
        f"📕 **النوع:** {type_label}\n"
        f"🏷️ **الفئة:** {cat} > {sub}\n"
        f"✍️ **الوصف:** {item['description'][:150]}\n"
        f"📍 **الموقع:** {item['location_city']}\n"
        f"⏰ **الوقت:** {item.get('time_range', '—')}\n"
        f"📞 **الاتصال:** {contact_type_label} — `{contact.get('value', '—')}`\n"
        f"📸 **الصور:** {len(photos)}\n\n"
        f"⚠️ **تأكد أن المعلومات صحيحة!**"
    )
    
    context.user_data["state"] = "confirming"
    
    await query.edit_message_text(text, reply_markup=kb_review(lang), parse_mode="Markdown")


# 🆕 تأكيد نهائي
async def cb_confirm_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("⏳ جاري النشر...")
    await finalize_item(q.message, context, context.user_data.get("lang", "ar"), is_callback=True)


async def cb_cancel_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("❌ تم الإلغاء")
    lang = context.user_data.get("lang", "ar")
    context.user_data.clear()
    context.user_data["lang"] = lang
    stats = await get_global_stats()
    await q.edit_message_text(
        t(lang, "welcome", **stats),
        reply_markup=kb_main(lang),
        parse_mode="Markdown"
    )


async def finalize_item(message, context, lang: str, is_callback: bool = False):
    item = context.user_data.get("item", {})
    contact = context.user_data.get("contact", {})
    photos = context.user_data.get("photos", [])
    user_id = message.chat.id

    if not item.get("description") or not contact.get("value"):
        error = "❌ خطأ: معلومات ناقصة"
        if is_callback:
            await message.edit_text(error, reply_markup=kb_main(lang))
        else:
            await message.reply_text(error, reply_markup=kb_main(lang))
        return

    try:
        saved = await create_item(
            user_id=user_id,
            item_type=item["type"],
            category=item["category"],
            subcategory=item.get("subcategory", "other"),
            description=item["description"],
            city=item["location_city"],
            time_range=item["time_range"],
            contact_method=contact.get("type"),
            contact_value=contact.get("value"),
            photos=photos,
        )

        matches = await find_matches(saved)

        context.user_data.clear()
        context.user_data["lang"] = lang

        text = t(lang, "item_created", number=saved["report_number"])
        if matches:
            text += f"\n\n🎯 **{len(matches)} تطابق محتمل!**"

        if is_callback:
            await message.edit_text(text, reply_markup=kb_main(lang), parse_mode="Markdown")
        else:
            await message.reply_text(text, reply_markup=kb_main(lang), parse_mode="Markdown")

    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
        error_text = f"❌ خطأ: {e}"
        if is_callback:
            await message.edit_text(error_text, reply_markup=kb_main(lang))
        else:
            await message.reply_text(error_text, reply_markup=kb_main(lang))


async def send_item_card(message, item: Dict, lang: str):
    type_emoji = "📕" if item["type"] == "lost" else "📗"
    cat = CATEGORIES.get(item["category"], {}).get("ar", "")
    sub = CATEGORIES.get(item["category"], {}).get("subs", {}).get(item.get("subcategory", ""), "")

    contact_line = ""
    if item.get("contact_value"):
        contact_type_label = "💬" if item.get("contact_method") == "username" else "📱"
        contact_line = f"\n{contact_type_label} {item['contact_value']}"

    text = (
        f"{type_emoji} **#{item['report_number']}**\n"
        f"🏷️ {cat} > {sub}\n"
        f"✍️ {item['description'][:100]}\n"
        f"📍 {item.get('location_city', '—')}"
        f"{contact_line}"
    )

    await increment_views(item["id"])

    photos = item.get("photos") or []
    photo_id = photos[0] if photos else item.get("photo_file_id")

    if photo_id:
        await message.reply_photo(photo_id, caption=text, parse_mode="Markdown")
    else:
        await message.reply_text(text, parse_mode="Markdown")


async def cb_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    context.user_data["state"] = "searching"
    await q.edit_message_text(
        t(lang, "search_prompt"),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 رجوع", callback_data="menu")
        ]]),
        parse_mode="Markdown"
    )


async def cb_my(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    items = await get_user_items(q.from_user.id, limit=10)

    if not items:
        await q.edit_message_text(
            t(lang, "no_items"),
            reply_markup=kb_main(lang),
            parse_mode="Markdown"
        )
        return

    await q.edit_message_text(
        t(lang, "my_items", count=len(items)),
        reply_markup=kb_my_items_actions(),
        parse_mode="Markdown"
    )

    for item in items:
        await send_item_card(q.message, item, lang)


async def cb_clear_my(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    count = await count_user_items(q.from_user.id)
    if count == 0:
        await q.answer("لا توجد بلاغات", show_alert=True)
        return
    await q.edit_message_text(
        f"⚠️ **تأكيد التصفير**\n\n"
        f"حذف **{count}** بلاغ؟\n\n"
        f"⚠️ لا يمكن التراجع!",
        reply_markup=kb_confirm_clear(),
        parse_mode="Markdown"
    )


async def cb_confirm_clear_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    count = await delete_all_user_items(q.from_user.id)
    await q.edit_message_text(
        f"✅ **تم التصفير!**\n\n🗑️ حُذف **{count}** بلاغ",
        reply_markup=kb_main(lang),
        parse_mode="Markdown"
    )


async def cb_confirm_clear_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    await q.edit_message_text(
        "✅ **تم الإلغاء**",
        reply_markup=kb_main(lang)
    )


async def cb_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT m.*, l.description as lost_desc, f.description as found_desc
            FROM matches m
            JOIN items l ON m.lost_item_id = l.id
            JOIN items f ON m.found_item_id = f.id
            WHERE (l.user_id = $1 OR f.user_id = $1) AND m.resolved = FALSE
            ORDER BY m.score DESC LIMIT 10
        """, q.from_user.id)

    if not rows:
        await q.edit_message_text(
            t(lang, "no_matches"),
            reply_markup=kb_main(lang),
            parse_mode="Markdown"
        )
        return

    await q.edit_message_text(f"🎯 التطابقات ({len(rows)}):")
    for row in rows:
        m = dict(row)
        text = (
            f"🎯 **تطابق #{m['id']}**\n"
            f"📊 النسبة: {m['score']}%\n"
            f"📕 {m['lost_desc'][:60]}\n"
            f"📗 {m['found_desc'][:60]}"
        )
        await q.message.reply_text(text, reply_markup=kb_match_actions(m["id"]), parse_mode="Markdown")


async def cb_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    await q.edit_message_text(
        t(lang, "help"),
        reply_markup=kb_main(lang),
        parse_mode="Markdown"
    )


# ============ Register Handlers ============
print("🔧 Registering handlers...")

app_tg.add_handler(CommandHandler("start", cmd_start))
app_tg.add_handler(CallbackQueryHandler(cb_menu, pattern="^menu$"))
app_tg.add_handler(CallbackQueryHandler(cb_add, pattern="^add$"))
app_tg.add_handler(CallbackQueryHandler(cb_type, pattern="^type_"))
app_tg.add_handler(CallbackQueryHandler(cb_category, pattern="^cat_"))
app_tg.add_handler(CallbackQueryHandler(cb_subcategory, pattern="^sub_"))
app_tg.add_handler(CallbackQueryHandler(cb_city, pattern="^city_"))
app_tg.add_handler(CallbackQueryHandler(cb_time, pattern="^time_"))
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
app_tg.add_handler(CallbackQueryHandler(cb_help, pattern="^help$"))

app_tg.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

print(f"✅ {len(app_tg.handlers[0])} handlers registered")


# ============================================================
# ⭐ FastAPI App (Vercel Entrypoint)
# ============================================================
app = FastAPI()


@app.get("/")
async def root():
    return {"status": "ok", "bot": "Lost & Found"}


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "bot_running": app_tg.running,
        "handlers_count": len(app_tg.handlers[0]) if app_tg.handlers else 0,
        "has_token": bool(BOT_TOKEN),
        "has_db": bool(DATABASE_URL),
        "has_nsfw_check": bool(SIGHTENGINE_USER and SIGHTENGINE_SECRET),
        "version": "3.0",
    }


@app.post("/")
async def webhook(request: Request):
    try:
        data = await request.json()
        update_id = data.get("update_id", "?")
        
        if WEBHOOK_SECRET:
            secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if secret != WEBHOOK_SECRET:
                raise HTTPException(status_code=403)
        
        if not app_tg.running:
            await app_tg.initialize()
        
        update = Update.de_json(data, app_tg.bot)
        await app_tg.process_update(update)
        
        return JSONResponse({"ok": True})
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)
