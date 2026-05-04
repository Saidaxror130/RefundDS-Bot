import os
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sheets import fetch_refund_rows
from cache import load_cache, save_cache, get_changes
from filters import filter_our_data, OUR_PVZ

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.environ["BOT_TOKEN"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
OWNER_ID       = 6061065577
TIMEZONE       = ZoneInfo("Asia/Tashkent")
CHECK_INTERVAL_HOURS = int(os.environ.get("CHECK_INTERVAL_HOURS", "5"))

def now_local():
    """Возвращает текущее время в часовом поясе Ташкента."""
    return datetime.now(TIMEZONE)

def format_datetime(dt_str: str) -> str:
    """Конвертирует ISO строку в читаемый формат."""
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        dt_local = dt.astimezone(TIMEZONE)
        return dt_local.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return dt_str

# ─── ACCESS CONTROL ────────────────────────────────────────────────────────────
def owner_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            logger.warning(f"Unauthorized access attempt from {update.effective_user.id}")
            return
        return await func(update, ctx)
    return wrapper

# ─── HELPERS ───────────────────────────────────────────────────────────────────
def format_refund(r: dict, show_full_reason: bool = False) -> str:
    """Форматирует одну запись о возврате."""
    order_id = r.get("order_id", "—")
    pvz = r.get("pvz", "—")
    date = r.get("date_refund", "—")
    payment_type = r.get("payment_type", "—")
    amount = r.get("amount", "—")
    client = r.get("client", "—")
    reason = r.get("reason", "—")
    status = r.get("status", "Новая ошибка")

    # Сокращаем длинную причину
    if not show_full_reason and len(reason) > 50:
        reason = reason[:47] + "..."

    status_emoji = {
        "": "🆕",
        "Связались с клиентом, придет заполнять заявление": "📞",
        "Клиент не выходит на связь": "❌",
        "Клиент отказывается дать данные добровольно ,из-за чего не можем загрузить заявление": "⏸",
        "Обработали ,заявление загрузили в  WMS": "✅"
    }
    emoji = status_emoji.get(status, "❓")

    # Компактный формат для мобильного
    lines = [
        f"{emoji} <b>{order_id}</b> | {pvz}",
        f"💰 {amount} ({payment_type}) | 📅 {date}",
        f"👤 {client}"
    ]

    # Добавляем причину только если она не пустая
    if reason and reason != "—":
        lines.append(f"📝 {reason}")

    return "\n".join(lines)

def group_by_status(rows: list) -> dict:
    """Группирует записи по статусам."""
    groups = {
        "new": [],
        "contacted": [],
        "no_contact": [],
        "refused": [],
        "completed": []
    }

    for r in rows:
        status = r.get("status", "")
        if not status:
            groups["new"].append(r)
        elif "Связались с клиентом" in status:
            groups["contacted"].append(r)
        elif "не выходит на связь" in status:
            groups["no_contact"].append(r)
        elif "отказывается" in status:
            groups["refused"].append(r)
        elif "Обработали" in status:
            groups["completed"].append(r)

    return groups

# ─── CORE CHECK ────────────────────────────────────────────────────────────────
async def check_and_notify(app: Application, manual: bool = False):
    logger.info("Проверяем таблицу возвратов...")

    try:
        all_rows = await asyncio.to_thread(fetch_refund_rows, SPREADSHEET_ID)
    except Exception as e:
        logger.error(f"Ошибка при чтении таблицы: {e}", exc_info=True)
        if manual:
            await app.bot.send_message(
                OWNER_ID,
                f"❌ <b>Ошибка при загрузке таблицы</b>\n\n<code>{str(e)}</code>",
                parse_mode="HTML"
            )
        return

    # Фильтруем только наши данные
    our_rows = filter_our_data(all_rows)

    # Получаем изменения
    changes = get_changes(our_rows)

    if not changes["added"] and not changes["removed"] and not changes["modified"]:
        if manual:
            await app.bot.send_message(
                OWNER_ID,
                f"✅ <b>Новых изменений нет</b>\n\n"
                f"Всего строк: {len(all_rows)}, наших: {len(our_rows)}",
                parse_mode="HTML"
            )
        return

    # Формируем сообщение об изменениях
    parts = []

    if changes["added"]:
        parts.append(f"🆕 <b>Новые ошибки ({len(changes['added'])} шт.):</b>")
        for r in changes["added"]:
            parts.append(format_refund(r))
            parts.append("")  # пустая строка

    if changes["removed"]:
        parts.append(f"\n✅ <b>Устранены ({len(changes['removed'])} шт.):</b>")
        for r in changes["removed"]:
            parts.append(f"• Заказ {r.get('order_id')} | {r.get('pvz')}")

    if changes["modified"]:
        parts.append(f"\n🔄 <b>Изменены ({len(changes['modified'])} шт.):</b>")
        for old, new in changes["modified"]:
            parts.append(f"• Заказ {new.get('order_id')} | {new.get('pvz')}")
            parts.append(f"  Было: {old.get('status', 'Новая ошибка')}")
            parts.append(f"  Стало: {new.get('status', 'Новая ошибка')}")
            parts.append("")  # пустая строка

    msg = "\n".join(parts)

    # Разбиваем на части если слишком длинное
    if len(msg) > 4000:
        chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
        for chunk in chunks:
            await app.bot.send_message(OWNER_ID, chunk, parse_mode="HTML")
    else:
        await app.bot.send_message(OWNER_ID, msg, parse_mode="HTML")

# ─── COMMANDS ──────────────────────────────────────────────────────────────────
@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>Бот возвратов ДС</b>\n\n"
        "Команды:\n"
        "/refresh — проверить таблицу\n"
        "/pvz — выбрать ПВЗ\n"
        "/all — все ошибки\n"
        "/new — новые ошибки\n"
        "/contacted — связались с клиентом\n"
        "/no_contact — клиент не выходит на связь\n"
        "/refused — клиент отказывается\n"
        "/completed — обработанные\n"
        "/status — статус бота",
        parse_mode="HTML"
    )

@owner_only
async def cmd_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Проверяю таблицу...")
    await check_and_notify(ctx.application, manual=True)

@owner_only
async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать все ошибки."""
    try:
        all_rows = await asyncio.to_thread(fetch_refund_rows, SPREADSHEET_ID)
        our_rows = filter_our_data(all_rows)

        if not our_rows:
            await update.message.reply_text("✅ Ошибок нет!")
            return

        groups = group_by_status(our_rows)
        parts = [f"📋 <b>Все ошибки ({len(our_rows)} шт.):</b>\n"]

        for key, label in [
            ("new", "🆕 Новые"),
            ("contacted", "📞 Связались с клиентом"),
            ("no_contact", "❌ Не выходит на связь"),
            ("refused", "⏸ Отказывается"),
            ("completed", "✅ Обработаны")
        ]:
            if groups[key]:
                parts.append(f"\n<b>{label} ({len(groups[key])} шт.):</b>")
                for r in groups[key]:
                    parts.append(format_refund(r))
                    parts.append("")  # пустая строка между записями

        msg = "\n".join(parts)

        if len(msg) > 4000:
            chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode="HTML")
        else:
            await update.message.reply_text(msg, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

@owner_only
async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать новые ошибки."""
    try:
        all_rows = await asyncio.to_thread(fetch_refund_rows, SPREADSHEET_ID)
        our_rows = filter_our_data(all_rows)
        groups = group_by_status(our_rows)

        if not groups["new"]:
            await update.message.reply_text("✅ Новых ошибок нет!")
            return

        parts = [f"🆕 <b>Новые ошибки ({len(groups['new'])} шт.):</b>\n"]
        for r in groups["new"]:
            parts.append(format_refund(r))
            parts.append("")  # пустая строка

        msg = "\n".join(parts)
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

@owner_only
async def cmd_contacted(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать ошибки со статусом 'Связались с клиентом'."""
    try:
        all_rows = await asyncio.to_thread(fetch_refund_rows, SPREADSHEET_ID)
        our_rows = filter_our_data(all_rows)
        groups = group_by_status(our_rows)

        if not groups["contacted"]:
            await update.message.reply_text("✅ Таких ошибок нет!")
            return

        parts = [f"📞 <b>Связались с клиентом ({len(groups['contacted'])} шт.):</b>\n"]
        for r in groups["contacted"]:
            parts.append(format_refund(r))
            parts.append("")  # пустая строка

        msg = "\n".join(parts)
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

@owner_only
async def cmd_no_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать ошибки со статусом 'Клиент не выходит на связь'."""
    try:
        all_rows = await asyncio.to_thread(fetch_refund_rows, SPREADSHEET_ID)
        our_rows = filter_our_data(all_rows)
        groups = group_by_status(our_rows)

        if not groups["no_contact"]:
            await update.message.reply_text("✅ Таких ошибок нет!")
            return

        parts = [f"❌ <b>Клиент не выходит на связь ({len(groups['no_contact'])} шт.):</b>\n"]
        for r in groups["no_contact"]:
            parts.append(format_refund(r))
            parts.append("")  # пустая строка

        msg = "\n".join(parts)
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

@owner_only
async def cmd_refused(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать ошибки со статусом 'Клиент отказывается'."""
    try:
        all_rows = await asyncio.to_thread(fetch_refund_rows, SPREADSHEET_ID)
        our_rows = filter_our_data(all_rows)
        groups = group_by_status(our_rows)

        if not groups["refused"]:
            await update.message.reply_text("✅ Таких ошибок нет!")
            return

        parts = [f"⏸ <b>Клиент отказывается ({len(groups['refused'])} шт.):</b>\n"]
        for r in groups["refused"]:
            parts.append(format_refund(r))
            parts.append("")  # пустая строка

        msg = "\n".join(parts)
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

@owner_only
async def cmd_completed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать обработанные ошибки."""
    try:
        all_rows = await asyncio.to_thread(fetch_refund_rows, SPREADSHEET_ID)
        our_rows = filter_our_data(all_rows)
        groups = group_by_status(our_rows)

        if not groups["completed"]:
            await update.message.reply_text("✅ Обработанных ошибок нет!")
            return

        parts = [f"✅ <b>Обработаны ({len(groups['completed'])} шт.):</b>\n"]
        for r in groups["completed"]:
            parts.append(format_refund(r))
            parts.append("")  # пустая строка

        msg = "\n".join(parts)
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

@owner_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать статус бота."""
    cache = load_cache()
    last = cache.get("last_check", "никогда")
    if last != "никогда":
        last = format_datetime(last)

    try:
        all_rows = await asyncio.to_thread(fetch_refund_rows, SPREADSHEET_ID)
        our_rows = filter_our_data(all_rows)
        groups = group_by_status(our_rows)

        await update.message.reply_text(
            f"📊 <b>Статус бота</b>\n\n"
            f"🕐 Последняя проверка: {last}\n"
            f"📋 Всего строк: {len(all_rows)}\n"
            f"🎯 Наших ошибок: {len(our_rows)}\n\n"
            f"🆕 Новые: {len(groups['new'])}\n"
            f"📞 Связались: {len(groups['contacted'])}\n"
            f"❌ Не выходит: {len(groups['no_contact'])}\n"
            f"⏸ Отказывается: {len(groups['refused'])}\n"
            f"✅ Обработаны: {len(groups['completed'])}",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ─── PVZ NAVIGATION ────────────────────────────────────────────────────────────
@owner_only
async def cmd_pvz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать список ПВЗ с кнопками."""
    keyboard = []
    sorted_pvz = sorted(OUR_PVZ)

    # Создаем кнопки по 3 в ряд
    row = []
    for pvz in sorted_pvz:
        row.append(InlineKeyboardButton(pvz, callback_data=f"pvz:{pvz}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []

    # Добавляем оставшиеся кнопки
    if row:
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🏪 <b>Выберите ПВЗ:</b>",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def pvz_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия на кнопку ПВЗ."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "pvz:back":
        # Возвращаемся к списку ПВЗ
        keyboard = []
        sorted_pvz = sorted(OUR_PVZ)

        row = []
        for pvz in sorted_pvz:
            row.append(InlineKeyboardButton(pvz, callback_data=f"pvz:{pvz}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "🏪 <b>Выберите ПВЗ:</b>",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return

    # Показываем данные по выбранному ПВЗ
    if data.startswith("pvz:"):
        selected_pvz = data[4:]  # убираем "pvz:"

        try:
            all_rows = await asyncio.to_thread(fetch_refund_rows, SPREADSHEET_ID)
            our_rows = filter_our_data(all_rows)

            # Фильтруем по выбранному ПВЗ
            pvz_rows = [r for r in our_rows if r.get("pvz") == selected_pvz]

            if not pvz_rows:
                keyboard = [[InlineKeyboardButton("« Назад", callback_data="pvz:back")]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await query.edit_message_text(
                    f"🏪 <b>{selected_pvz}</b>\n\n✅ Ошибок нет!",
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                return

            # Группируем по статусам
            groups = group_by_status(pvz_rows)

            parts = [f"🏪 <b>{selected_pvz}</b> ({len(pvz_rows)} шт.)\n"]

            for key, label in [
                ("new", "🆕 Новые"),
                ("contacted", "📞 Связались"),
                ("no_contact", "❌ Не выходит"),
                ("refused", "⏸ Отказывается"),
                ("completed", "✅ Обработаны")
            ]:
                if groups[key]:
                    parts.append(f"\n<b>{label} ({len(groups[key])} шт.):</b>")
                    for r in groups[key]:
                        parts.append(format_refund(r))
                        parts.append("")

            msg = "\n".join(parts)

            # Кнопка "Назад"
            keyboard = [[InlineKeyboardButton("« Назад", callback_data="pvz:back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Разбиваем на части если слишком длинное
            if len(msg) > 4000:
                chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
                await query.edit_message_text(chunks[0], parse_mode="HTML", reply_markup=reply_markup)
                for chunk in chunks[1:]:
                    await query.message.reply_text(chunk, parse_mode="HTML")
            else:
                await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="HTML")

        except Exception as e:
            keyboard = [[InlineKeyboardButton("« Назад", callback_data="pvz:back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"❌ Ошибка: {e}",
                reply_markup=reply_markup
            )

# ─── MAIN ──────────────────────────────────────────────────────────────────────
async def post_init(app: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_and_notify,
        "interval",
        hours=CHECK_INTERVAL_HOURS,
        args=[app],
        id="refund_check",
        next_run_time=now_local()
    )
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    logger.info(f"✅ Планировщик запущен (каждые {CHECK_INTERVAL_HOURS} часов)")

def main():
    try:
        app = (
            Application.builder()
            .token(BOT_TOKEN)
            .post_init(post_init)
            .build()
        )

        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("refresh", cmd_refresh))
        app.add_handler(CommandHandler("pvz", cmd_pvz))
        app.add_handler(CommandHandler("all", cmd_all))
        app.add_handler(CommandHandler("new", cmd_new))
        app.add_handler(CommandHandler("contacted", cmd_contacted))
        app.add_handler(CommandHandler("no_contact", cmd_no_contact))
        app.add_handler(CommandHandler("refused", cmd_refused))
        app.add_handler(CommandHandler("completed", cmd_completed))
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CallbackQueryHandler(pvz_callback))

        logger.info("🚀 Бот возвратов запущен")
        app.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.critical(f"❌ Критическая ошибка: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()
