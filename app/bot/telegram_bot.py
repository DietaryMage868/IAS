import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from app.config import settings
from app.database import SessionLocal
from app.models import Shop
from app.services.bot_orchestrator import handle_draft_button_click, process_seller_message
from app.services.seed import seed_initial_data

logger = logging.getLogger("ias.telegram_bot")

_bot_task: asyncio.Task | None = None


def _build_inline_keyboard(buttons: list[dict], base_url: str) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    rows = []
    for btn in buttons:
        action = btn.get("action", "")
        draft_id = btn.get("draft_id", 0)
        label = btn.get("label", "Button")
        if action == "OPEN_PREVIEW":
            url = f"{base_url}{btn.get('url', '/shop/123')}"
            if url.startswith("http://localhost"):
                # Telegram inline URL buttons reject localhost; convert to callback info
                rows.append([InlineKeyboardButton(text=label, callback_data=f"PRV:{draft_id}")])
            else:
                rows.append([InlineKeyboardButton(text=label, url=url)])
        elif action in ("PUBLISH", "CANCEL", "SET_CURRENCY_USD", "SET_CURRENCY_UAH", "SET_PRICE"):
            extra = int(btn.get("custom_price", 0)) if action == "SET_PRICE" else 0
            rows.append([InlineKeyboardButton(text=label, callback_data=f"{action}:{draft_id}:{extra}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def start_telegram_bot_if_configured() -> None:
    """Starts Aiogram polling in the background if TELEGRAM_BOT_TOKEN is configured."""
    if not settings.has_real_telegram:
        logger.info("TELEGRAM_BOT_TOKEN not set; running Web Telegram Studio mode.")
        return

    try:
        bot = Bot(token=settings.telegram_bot_token)
        dp = Dispatcher()

        @dp.message(CommandStart())
        async def cmd_start(message: Message) -> None:
            with SessionLocal() as db:
                shop = seed_initial_data(db)
                await message.answer(
                    f"👋 <b>Instant AI Storefront (IAS)</b>\n"
                    f"Магазин: <b>{shop.name}</b> (<code>/shop/{shop.id}</code>)\n\n"
                    "Надішліть фото + голосове/текст товару, команду на зміну цін або номер ТТН Нової Пошти!",
                    parse_mode=ParseMode.HTML,
                )

        @dp.message(F.text | F.caption | F.voice | F.photo)
        async def handle_any_seller_input(message: Message) -> None:
            text_content = message.text or message.caption or ""
            image_urls = []
            if message.photo:
                image_urls.append("/static/assets/green_hoodie.svg")
            with SessionLocal() as db:
                shop = db.query(Shop).filter(Shop.id == settings.default_shop_id).first() or seed_initial_data(db)
                result = await process_seller_message(
                    db=db,
                    shop=shop,
                    text=text_content,
                    image_urls=image_urls,
                )
                kb = _build_inline_keyboard(result.get("buttons", []), settings.app_base_url)
                await message.answer(
                    result.get("reply_text", "Оброблено"),
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )

        @dp.callback_query()
        async def handle_callback(query: CallbackQuery) -> None:
            data = query.data or ""
            parts = data.split(":")
            action = parts[0]
            draft_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            extra = float(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

            with SessionLocal() as db:
                shop = db.query(Shop).filter(Shop.id == settings.default_shop_id).first() or seed_initial_data(db)
                if action == "PRV":
                    await query.answer(f"Відкрийте прев'ю у браузері: {settings.app_base_url}/shop/{shop.id}", show_alert=True)
                    return

                res = handle_draft_button_click(
                    db=db,
                    shop=shop,
                    draft_id=draft_id,
                    action=action,
                    custom_price=extra,
                )
                await query.answer("Виконано!")
                if query.message:
                    await query.message.answer(res["message"], parse_mode=ParseMode.HTML)

        await dp.start_polling(bot, handle_signals=False)
    except Exception as exc:
        logger.warning("Telegram Bot polling stopped or token invalid: %s", exc)
