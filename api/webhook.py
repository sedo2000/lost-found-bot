"""
🔍 Lost & Found Bot - Complete Bot in One File
"""
import os
import re
from datetime import datetime
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

TIME_LABELS = {
    "time_1h": "آخر ساعة", "time_today": "اليوم",
    "time_yesterday": "أمس", "time_week": "هذا الأسبوع",
    "time_month": "هذا الشهر", "time_older": "أقدم",
}

# ============ قاعدة البيانات ============
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=1, max_size=3, command_timeout=8
        )
    return _pool


# ============ Users ============
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


async def set_user_lang(user_id: int, lang: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET lang = $1 WHERE user_id = $2", lang, user_id
        )


# ============ Items ============
async def create_item(user_id: int, item_type: str, category: str,
                     subcategory: str, description: str, city: str,
                     time_range: str, photo_file_id: str = None) -> Dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO items (user_id, type, category, subcategory,
                              description, location_city, time_range, photo_file_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *
        """, user_id, item_type, category, subcategory,
            description, city, time_range, photo_file_id)
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


async def resolve_item(item_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE items SET status = 'resolved' WHERE id = $1", item_id
        )


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
    # الفئة
    if lost.get("category") == found.get("category"):
        score += 25
        if lost.get("subcategory") == found.get("subcategory"):
            score += 15
    else:
        return 0
    # الموقع
    if lost.get("location_city") == found.get("location_city"):
        score += 20
    # الوقت
    diff = abs(time_to_hours(lost.get("time_range", "")) -
               time_to_hours(found.get("time_range", "")))
    if diff <= 6:
        score += 15
    elif diff <= 24:
        score += 10
    # الوصف
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
            "نساعدك في:\n"
            "• 📝 إيجاد ما فقدته\n"
            "• 🎁 إرجاع ما وجدته\n\n"
            "📊 **إحصائيات:**\n"
            "📕 مفقود: {total_lost}\n"
            "📗 موجود: {total_found}\n"
            "🎯 حالات نجاح: {total_resolved}\n"
            "👥 مستخدمون: {total_users}"
        ),
        "choose_type": "📝 **ما نوع البلاغ؟**",
        "choose_category": "🏷️ **اختر الفئة:**",
        "choose_subcategory": "📂 **اختر الفئة الفرعية:**",
        "enter_description": "✍️ **صف الشيء بالتفصيل:**\n\nمثال: _محفظة جلدية بنية فيها هوية_",
        "choose_city": "📍 **اختر المحافظة:**",
        "choose_time": "⏰ **متى؟**",
        "send_photo": "📸 **أرسل صورة (اختياري):**",
        "item_created": "🎉 **تم نشر بلاغك!**\n\n📋 رقم البلاغ: `#{number}`\n\n🔍 نبحث عن تطابقات...",
        "search_prompt": "🔍 **ما الذي تبحث عنه؟**",
        "no_results": "❌ **لا توجد نتائج**",
        "no_items": "📭 **لا توجد بلاغات**",
        "my_items": "📋 **بلاغاتي ({count})**",
        "no_matches": "🔍 **لا توجد تطابقات**",
        "match_found": "🎯 **تطابق!**\n\n📊 النسبة: **{score}%**",
        "match_confirmed": "🎉 **تم التأكيد!**",
        "help": "📖 **المساعدة**\n\n• أضف بلاغ\n• ابحث\n• تابع بلاغاتك",
    },
    "en": {
        "welcome": "🔍 **Lost & Found Bot**\n\n📕 Lost: {total_lost}\n📗 Found: {total_found}\n🎯 Success: {total_resolved}",
        "choose_type": "📝 **Report type?**",
        "choose_category": "🏷️ **Category:**",
        "choose_subcategory": "📂 **Subcategory:**",
        "enter_description": "✍️ **Describe:**",
        "choose_city": "📍 **City:**",
        "choose_time": "⏰ **When?**",
        "send_photo": "📸 **Send photo (optional):**",
        "item_created": "🎉 **Published!** #{number}",
        "search_prompt": "🔍 **Search:**",
        "no_results": "❌ **No results**",
        "no_items": "📭 **No reports**",
        "my_items": "📋 **My reports ({count})**",
        "no_matches": "🔍 **No matches**",
        "match_found": "🎯 **Match!** {score}%",
        "match_confirmed": "🎉 **Confirmed!**",
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
        [InlineKeyboardButton("📝 أضف بلاغ" if lang == "ar" else "📝 Add",
                              callback_data="add")],
        [InlineKeyboardButton("🔍 ابحث" if lang == "ar" else "🔍 Search",
                              callback_data="search")],
        [InlineKeyboardButton("📋 بلاغاتي" if lang == "ar" else "📋 Mine",
                              callback_data="my"),
         InlineKeyboardButton("🎯 التطابقات" if lang == "ar" else "🎯 Matches",
                              callback_data="matches")],
        [InlineKeyboardButton("ℹ️ مساعدة" if lang == "ar" else "ℹ️ Help",
                              callback_data="help")],
    ])


def kb_type(lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📕 مفقود" if lang == "ar" else "📕 Lost",
                              callback_data="type_lost"),
         InlineKeyboardButton("📗 موجود" if lang == "ar" else "📗 Found",
                              callback_data="type_found")],
        [InlineKeyboardButton("🔙 رجوع" if lang == "ar" else "🔙 Back",
                              callback_data="menu")],
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
        rows.append([InlineKeyboardButton(
            sub_name, callback_data=f"sub_{cat_key}_{sub_key}"
        )])
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
        [InlineKeyboardButton("⏭️ تخطي الصورة", callback_data="skip_photo")],
    ])


def kb_item_actions(item_id: int, lang: str = "ar") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تم الحل", callback_data=f"resolve_{item_id}")],
        [InlineKeyboardButton("🗑️ حذف", callback_data=f"delete_{item_id}")],
    ])


def kb_match_actions(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم، هذا هو", callback_data=f"m_yes_{match_id}")],
        [InlineKeyboardButton("❌ لا، ليس هو", callback_data=f"m_no_{match_id}")],
    ])


# ============ Telegram Application ============
app_tg = Application.builder().token(BOT_TOKEN).build()


# ============ Handlers ============
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
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
    lang = context.user_data.get("lang", "ar")
    context.user_data["state"] = "choosing_type"
    context.user_data["item"] = {}
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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج النصوص - يعالج حالات متعددة"""
    state = context.user_data.get("state")
    lang = context.user_data.get("lang", "ar")
    text = update.message.text.strip()

    # وصف
    if state == "waiting_description":
        if len(text) < 5:
            await update.message.reply_text("❌ الوصف قصير")
            return
        context.user_data["item"]["description"] = text
        context.user_data["state"] = "choosing_city"
        await update.message.reply_text(
            t(lang, "choose_city"),
            reply_markup=kb_cities(lang),
            parse_mode="Markdown"
        )

    # بحث
    elif state == "searching":
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
        await update.message.reply_text(
            "🔙",
            reply_markup=kb_main(lang)
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_photo":
        return
    lang = context.user_data.get("lang", "ar")
    photo = update.message.photo[-1]
    context.user_data["item"]["photo_file_id"] = photo.file_id
    await finalize_item(update.message, context, lang)


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
    context.user_data["state"] = "waiting_photo"
    await q.edit_message_text(
        t(lang, "send_photo"),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭️ تخطي", callback_data="skip_photo")
        ]]),
        parse_mode="Markdown"
    )


async def cb_skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = context.user_data.get("lang", "ar")
    await finalize_item(q.message, context, lang, is_callback=True)


async def finalize_item(message, context, lang: str, is_callback: bool = False):
    """حفظ البلاغ النهائي"""
    item = context.user_data.get("item", {})
    user_id = message.chat.id if is_callback else message.from_user.id

    # في حالة callback، from_user
    if is_callback:
        user_id = message.chat.id

    try:
        saved = await create_item(
            user_id=user_id,
            item_type=item["type"],
            category=item["category"],
            subcategory=item.get("subcategory", "other"),
            description=item["description"],
            city=item["location_city"],
            time_range=item["time_range"],
            photo_file_id=item.get("photo_file_id"),
        )

        # البحث عن تطابقات
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
        error_text = f"❌ خطأ: {e}"
        if is_callback:
            await message.edit_text(error_text, reply_markup=kb_main(lang))
        else:
            await message.reply_text(error_text, reply_markup=kb_main(lang))


async def send_item_card(message, item: Dict, lang: str):
    """بطاقة بلاغ"""
    type_emoji = "📕" if item["type"] == "lost" else "📗"
    cat = CATEGORIES.get(item["category"], {}).get("ar", "")
    sub = CATEGORIES.get(item["category"], {}).get("subs", {}).get(item.get("subcategory", ""), "")

    text = (
        f"{type_emoji} **#{item['report_number']}**\n"
        f"🏷️ {cat} > {sub}\n"
        f"✍️ {item['description'][:100]}\n"
        f"📍 {item.get('location_city', '—')}"
    )

    await increment_views(item["id"])

    if item.get("photo_file_id"):
        await message.reply_photo(item["photo_file_id"], caption=text, parse_mode="Markdown")
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
        parse_mode="Markdown"
    )

    for item in items:
        await send_item_card(q.message, item, lang)

    await q.message.reply_text(
        "🔙",
        reply_markup=kb_main(lang)
    )


async def cb_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("✅")
    item_id = int(q.data.replace("resolve_", ""))
    await resolve_item(item_id)
    lang = context.user_data.get("lang", "ar")
    await q.edit_message_text("🎉 تم تحديث البلاغ", reply_markup=kb_main(lang))


async def cb_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("🗑️")
    item_id = int(q.data.replace("delete_", ""))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE items SET status='hidden' WHERE id=$1", item_id)
    lang = context.user_data.get("lang", "ar")
    await q.edit_message_text("🗑️ تم الحذف", reply_markup=kb_main(lang))


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
        await q.message.reply_text(
            text,
            reply_markup=kb_match_actions(m["id"]),
            parse_mode="Markdown"
        )


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
app_tg.add_handler(CommandHandler("start", cmd_start))
app_tg.add_handler(CallbackQueryHandler(cb_menu, pattern="^menu$"))
app_tg.add_handler(CallbackQueryHandler(cb_add, pattern="^add$"))
app_tg.add_handler(CallbackQueryHandler(cb_type, pattern="^type_"))
app_tg.add_handler(CallbackQueryHandler(cb_category, pattern="^cat_"))
app_tg.add_handler(CallbackQueryHandler(cb_subcategory, pattern="^sub_"))
app_tg.add_handler(CallbackQueryHandler(cb_city, pattern="^city_"))
app_tg.add_handler(CallbackQueryHandler(cb_time, pattern="^time_"))
app_tg.add_handler(CallbackQueryHandler(cb_skip_photo, pattern="^skip_photo$"))
app_tg.add_handler(CallbackQueryHandler(cb_search, pattern="^search$"))
app_tg.add_handler(CallbackQueryHandler(cb_my, pattern="^my$"))
app_tg.add_handler(CallbackQueryHandler(cb_resolve, pattern="^resolve_"))
app_tg.add_handler(CallbackQueryHandler(cb_delete, pattern="^delete_"))
app_tg.add_handler(CallbackQueryHandler(cb_matches, pattern="^matches$"))
app_tg.add_handler(CallbackQueryHandler(cb_help, pattern="^help$"))

# الصورة أولاً
app_tg.add_handler(MessageHandler(filters.PHOTO, handle_photo))
# النص آخراً (مهم!)
app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))


# ============ FastAPI ============
api = FastAPI()


@api.get("/")
async def root():
    return {"status": "ok", "bot": "Lost & Found"}


@api.post("/")
async def webhook(request: Request):
    # التحقق من Secret
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret != WEBHOOK_SECRET:
            raise HTTPException(403)

    try:
        data = await request.json()
        update = Update.de_json(data, app_tg.bot)

        if not app_tg.running:
            await app_tg.initialize()

        await app_tg.process_update(update)
        return JSONResponse({"ok": True})

    except Exception as e:
        print(f"❌ Error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


# Vercel handler
handler = api
