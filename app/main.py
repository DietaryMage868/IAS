import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import secrets
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.bot.telegram_bot import start_telegram_bot_if_configured
from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import BotMessage, Draft, Order, Product, Reservation, Shop, utcnow
from app.schemas import (
    BotInputRequest,
    CheckoutCancelRequest,
    CheckoutCaptureRequest,
    CheckoutReserveRequest,
    DraftActionRequest,
    RuntimeConfigUpdateRequest,
)
from app.services.bot_orchestrator import handle_draft_button_click, process_seller_message
from app.services.inventory import (
    cancel_reservation,
    cleanup_expired_reservations,
    complete_checkout_and_notify_seller,
    reserve_stock_for_checkout,
)
from app.services.seed import seed_initial_data

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
UPLOADS_DIR = STATIC_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


async def _periodic_reservation_cleaner() -> None:
    """Background task that automatically releases expired 15-minute reservations."""
    while True:
        try:
            with SessionLocal() as db:
                cleanup_expired_reservations(db)
        except Exception:
            pass
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_initial_data(db, force_reset=False)
    cleaner_task = asyncio.create_task(_periodic_reservation_cleaner())
    bot_task = asyncio.create_task(start_telegram_bot_if_configured())
    yield
    cleaner_task.cancel()
    bot_task.cancel()


app = FastAPI(
    title="Instant AI Storefront (IAS)",
    description="Mobile-First & Zero-Form AI E-Commerce Storefront with Telegram Voice/Photo Ingestion & PayPal Native Checkout",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


SCENARIO_PRESETS: dict[str, dict] = {
    "scenario_1_create": {
        "text": "Зелена кофта, м'яка, є розміри S і M, ціна 40 баксів, залишків по 3 штуки кожного",
        "voice_transcript": "Зелена кофта, м'яка, є розміри S і M, ціна 40 баксів, залишків по 3 штуки кожного",
        "image_urls": ["/static/assets/green_hoodie.svg", "/static/assets/green_hoodie_back.svg"],
    },
    "scenario_2_bulk_edit": {
        "text": "Знизь ціну на всі літні сукні на 10%, але чорну сукню залиш по старій ціні. І розмір S для синьої сукні познач як випроданий",
        "voice_transcript": "Знизь ціну на всі літні сукні на 10%, але чорну сукню залиш по старій ціні. І розмір S для синьої сукні познач як випроданий",
        "image_urls": [],
    },
    "scenario_4_fulfillment": {
        "text": "Замовлення #102 відправив, трек Нової Пошти 20450012345678",
        "voice_transcript": None,
        "image_urls": ["/static/assets/shipping_receipt.svg"],
    },
    "scenario_5_non_commercial": {
        "text": "Привіт! Ось випадкове особисте фото котика, просто перевірка мікрофона",
        "voice_transcript": "Привіт! Ось випадкове особисте фото котика, просто перевірка мікрофона",
        "image_urls": ["/static/assets/personal_cat.svg"],
    },
    "edge_case_missing_price": {
        "text": "Червона сукня, розміри S і M",
        "voice_transcript": "Червона сукня, розміри S і M",
        "image_urls": ["/static/assets/red_dress.svg"],
    },
    "edge_case_surzhyk_uah": {
        "text": "Червона сукня, розміри S і M, ціна двісті грн",
        "voice_transcript": "Червона сукня, розміри S і M, ціна двісті грн",
        "image_urls": ["/static/assets/red_dress.svg"],
    },
}


def _serialize_shop_state(db: Session, shop: Shop, preview_token: Optional[str] = None) -> dict:
    cleanup_expired_reservations(db, shop.id)

    products = (
        db.query(Product)
        .filter(Product.shop_id == shop.id, Product.is_active.is_(True))
        .order_by(Product.id.asc())
        .all()
    )
    active_reservations = (
        db.query(Reservation)
        .filter(Reservation.shop_id == shop.id, Reservation.status == "ACTIVE")
        .all()
    )
    res_by_variant: dict[int, Reservation] = {r.variant_id: r for r in active_reservations}

    draft_info = None
    bulk_mutation_map: dict[int, dict] = {}
    draft_new_product: Optional[dict] = None

    if preview_token:
        draft = (
            db.query(Draft)
            .filter(Draft.shop_id == shop.id, Draft.preview_token == preview_token)
            .first()
        )
        if draft:
            rem_seconds = max(0, int((draft.expires_at - utcnow()).total_seconds()))
            is_valid = draft.status == "PENDING" and rem_seconds > 0
            draft_info = {
                "id": draft.id,
                "preview_token": draft.preview_token,
                "action_type": draft.action_type,
                "status": "EXPIRED" if (draft.status == "PENDING" and rem_seconds == 0) else draft.status,
                "is_valid": is_valid,
                "remaining_seconds": rem_seconds,
                "has_price_warning": draft.has_price_warning,
                "currency_ambiguity": draft.currency_ambiguity,
                "diff_summary_text": draft.diff_summary_text,
            }
            if is_valid:
                payload = draft.payload
                if draft.action_type == "CREATE_PRODUCT":
                    draft_new_product = {
                        "id": -draft.id,
                        "is_draft_preview": True,
                        "draft_badge": "⚡ ЧЕРНЕТКА: НОВИЙ ТОВАР",
                        "title": payload.get("title", "New Draft Item"),
                        "title_uk": payload.get("title_uk", payload.get("title", "Новий товар")),
                        "description": payload.get("description", ""),
                        "category": payload.get("category", "Одяг"),
                        "price": float(payload.get("price", 0.0)),
                        "old_price": None,
                        "currency": payload.get("currency", shop.currency),
                        "images": payload.get("images", ["/static/assets/green_hoodie.svg"]),
                        "attributes": payload.get("attributes", {}),
                        "variants": [
                            {
                                "id": -( idx + 1 ),
                                "size": v.get("size", "One Size"),
                                "stock_quantity": int(v.get("stock_quantity", 1)),
                                "stock_reserved": 0,
                                "available_stock": int(v.get("stock_quantity", 1)),
                                "draft_change": f"Новий варіант ({v.get('stock_quantity', 1)} шт)",
                            }
                            for idx, v in enumerate(payload.get("variants", []))
                        ],
                    }
                elif draft.action_type == "BULK_EDIT":
                    for m in payload.get("mutations", []):
                        bulk_mutation_map[int(m["product_id"])] = m

    serialized_products = []
    if draft_new_product:
        serialized_products.append(draft_new_product)

    for p in products:
        mut = bulk_mutation_map.get(p.id)
        display_price = p.price
        old_price = None
        draft_badge = None

        if mut:
            c_type = mut.get("change_type")
            if c_type == "EXCLUDED_NO_CHANGE":
                draft_badge = "🛡 Без змін (виключення)"
            else:
                if mut.get("new_price") is not None and float(mut["new_price"]) != p.price:
                    old_price = p.price
                    display_price = float(mut["new_price"])
                    draft_badge = f"⚡ ЧЕРНЕТКА: ${p.price:.0f} ➔ ${display_price:.0f}"
                elif mut.get("variant_changes"):
                    draft_badge = "⚡ ЧЕРНЕТКА: Зміна залишків"

        var_mut_map = {}
        if mut:
            for vc in mut.get("variant_changes", []):
                var_mut_map[str(vc.get("size", "")).upper()] = vc

        variants_list = []
        for v in p.variants:
            vc = var_mut_map.get(v.size.upper())
            phys_stock = int(vc["new_stock"]) if vc and "new_stock" in vc else v.stock_quantity
            avail_stock = max(0, phys_stock - v.stock_reserved)
            active_res = res_by_variant.get(v.id)
            res_rem_sec = (
                max(0, int((active_res.expires_at - utcnow()).total_seconds()))
                if active_res
                else 0
            )
            variants_list.append(
                {
                    "id": v.id,
                    "size": v.size,
                    "stock_quantity": phys_stock,
                    "stock_reserved": v.stock_reserved,
                    "available_stock": avail_stock,
                    "reserved_by": active_res.buyer_label if active_res else None,
                    "reservation_id": active_res.id if active_res else None,
                    "reservation_remaining_seconds": res_rem_sec,
                    "draft_change": vc.get("label") if vc else None,
                }
            )

        serialized_products.append(
            {
                "id": p.id,
                "is_draft_preview": bool(mut),
                "draft_badge": draft_badge,
                "title": p.title,
                "title_uk": p.title_uk or p.title,
                "description": p.description,
                "category": p.category,
                "price": display_price,
                "old_price": old_price,
                "currency": p.currency,
                "images": p.images,
                "attributes": p.attributes,
                "variants": variants_list,
            }
        )

    messages = (
        db.query(BotMessage)
        .filter(BotMessage.shop_id == shop.id)
        .order_by(BotMessage.id.asc())
        .all()
    )
    orders = (
        db.query(Order)
        .filter(Order.shop_id == shop.id)
        .order_by(Order.id.desc())
        .all()
    )

    return {
        "shop": {
            "id": shop.id,
            "name": shop.name,
            "slug": shop.slug,
            "description": shop.description,
            "telegram_id": shop.telegram_id,
            "telegram_username": shop.telegram_username,
            "currency": shop.currency,
            "exchange_rate_uah": shop.exchange_rate_uah,
            "paypal_merchant_id": shop.paypal_merchant_id,
            "paypal_email": shop.paypal_email,
            "paypal_onboarding_status": shop.paypal_onboarding_status,
        },
        "integrations": {
            "gemini_mode": "LIVE_API" if settings.has_real_gemini else "SMART_DETERMINISTIC_ENGINE",
            "paypal_mode": "SANDBOX_LIVE_API" if settings.has_real_paypal else "SANDBOX_INTERACTIVE_SIMULATOR",
            "paypal_client_id": settings.paypal_client_id,
            "telegram_mode": "LIVE_BOT_AND_WEB_STUDIO" if settings.has_real_telegram else "WEB_TELEGRAM_STUDIO",
        },
        "preview": draft_info,
        "products": serialized_products,
        "messages": [
            {
                "id": m.id,
                "sender": m.sender,
                "message_type": m.message_type,
                "text": m.text,
                "media": m.media,
                "buttons": m.buttons,
                "draft_id": m.draft_id,
                "created_at": m.created_at.strftime("%H:%M:%S"),
            }
            for m in messages
        ],
        "orders": [
            {
                "id": o.id,
                "product_title": o.product_title,
                "variant_size": o.variant_size,
                "amount": o.amount,
                "currency": o.currency,
                "buyer_email": o.buyer_email,
                "paypal_order_id": o.paypal_order_id,
                "paypal_capture_id": o.paypal_capture_id,
                "payee_merchant_id": o.payee_merchant_id,
                "status": o.status,
                "tracking_number": o.tracking_number,
                "carrier": o.carrier,
                "tracking_status": o.tracking_status,
                "paypal_tracker_synced": o.paypal_tracker_synced,
                "created_at": o.created_at.strftime("%H:%M:%S"),
            }
            for o in orders
        ],
        "active_reservations": [
            {
                "id": r.id,
                "product_id": r.product_id,
                "variant_id": r.variant_id,
                "buyer_label": r.buyer_label,
                "buyer_session_id": r.buyer_session_id,
                "paypal_order_id": r.paypal_order_id,
                "remaining_seconds": max(0, int((r.expires_at - utcnow()).total_seconds())),
            }
            for r in active_reservations
        ],
    }


@app.get("/", response_class=RedirectResponse)
async def root_redirect():
    return RedirectResponse(url=f"/shop/{settings.default_shop_id}")


@app.get("/shop/{shop_id}", response_class=HTMLResponse)
async def render_storefront(
    request: Request,
    shop_id: int,
    preview_token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    shop = db.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        if shop_id == settings.default_shop_id:
            shop = seed_initial_data(db)
        else:
            raise HTTPException(status_code=404, detail="Магазин не знайдено")

    state = _serialize_shop_state(db, shop, preview_token=preview_token)
    return templates.TemplateResponse(
        request=request,
        name="shop.html",
        context={
            "shop": state["shop"],
            "initial_state": state,
            "is_preview_mode": bool(preview_token),
            "preview_token": preview_token or "",
        },
    )


@app.get("/api/shop/{shop_id}/state")
async def api_get_shop_state(
    shop_id: int,
    preview_token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    shop = db.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        shop = seed_initial_data(db)
    return _serialize_shop_state(db, shop, preview_token=preview_token)


@app.post("/api/bot/message")
async def api_bot_message(payload: BotInputRequest, db: Session = Depends(get_db)):
    shop = db.query(Shop).filter(Shop.id == payload.shop_id).first()
    if not shop:
        shop = seed_initial_data(db)

    text = payload.text
    voice_transcript = payload.voice_transcript
    image_urls = list(payload.image_urls)

    if payload.scenario_preset and payload.scenario_preset in SCENARIO_PRESETS:
        preset = SCENARIO_PRESETS[payload.scenario_preset]
        text = preset["text"]
        voice_transcript = preset["voice_transcript"]
        image_urls = list(preset["image_urls"])

    result = await process_seller_message(
        db=db,
        shop=shop,
        text=text,
        voice_transcript=voice_transcript,
        image_urls=image_urls,
    )
    result["state"] = _serialize_shop_state(db, shop)
    return result


@app.post("/api/bot/upload")
async def api_bot_upload_media(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "upload.jpg")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".svg", ".gif", ".ogg", ".mp3", ".wav"):
        ext = ".jpg"
    fname = f"media_{secrets.token_hex(6)}{ext}"
    dest = UPLOADS_DIR / fname
    content = await file.read()
    dest.write_bytes(content)
    return {"ok": True, "url": f"/static/uploads/{fname}", "filename": file.filename}


@app.post("/api/bot/draft-action")
async def api_bot_draft_action(payload: DraftActionRequest, db: Session = Depends(get_db)):
    shop = db.query(Shop).filter(Shop.id == payload.shop_id).first()
    if not shop:
        shop = seed_initial_data(db)

    res = handle_draft_button_click(
        db=db,
        shop=shop,
        draft_id=payload.draft_id,
        action=payload.action,
        custom_price=payload.custom_price,
    )
    res["state"] = _serialize_shop_state(db, shop)
    return res


@app.post("/api/checkout/reserve")
async def api_checkout_reserve(payload: CheckoutReserveRequest, db: Session = Depends(get_db)):
    shop = db.query(Shop).filter(Shop.id == payload.shop_id).first()
    if not shop:
        shop = seed_initial_data(db)

    ok, msg, reservation, paypal_order = await reserve_stock_for_checkout(
        db=db,
        shop=shop,
        product_id=payload.product_id,
        variant_id=payload.variant_id,
        buyer_session_id=payload.buyer_session_id,
        buyer_label=payload.buyer_label,
    )
    if not ok or not reservation:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "message": msg,
                "state": _serialize_shop_state(db, shop),
            },
        )
    return {
        "ok": True,
        "message": msg,
        "reservation_id": reservation.id,
        "paypal_order": paypal_order,
        "expires_at": reservation.expires_at.isoformat(),
        "state": _serialize_shop_state(db, shop),
    }


@app.post("/api/checkout/capture")
async def api_checkout_capture(payload: CheckoutCaptureRequest, db: Session = Depends(get_db)):
    shop = db.query(Shop).filter(Shop.id == payload.shop_id).first()
    if not shop:
        shop = seed_initial_data(db)

    ok, msg, order = await complete_checkout_and_notify_seller(
        db=db,
        shop=shop,
        reservation_id=payload.reservation_id,
        buyer_email=payload.buyer_email,
        buyer_name=payload.buyer_name,
    )
    if not ok or not order:
        raise HTTPException(status_code=400, detail=msg)

    return {
        "ok": True,
        "message": msg,
        "order_id": order.id,
        "paypal_order_id": order.paypal_order_id,
        "paypal_capture_id": order.paypal_capture_id,
        "state": _serialize_shop_state(db, shop),
    }


@app.post("/api/checkout/cancel")
async def api_checkout_cancel(payload: CheckoutCancelRequest, db: Session = Depends(get_db)):
    ok, msg = cancel_reservation(db, payload.reservation_id, reason=payload.reason)
    shop = db.query(Shop).filter(Shop.id == settings.default_shop_id).first() or seed_initial_data(db)
    return {
        "ok": ok,
        "message": msg,
        "state": _serialize_shop_state(db, shop),
    }


@app.post("/api/paypal/connect/{shop_id}")
async def api_connect_paypal_merchant(
    shop_id: int,
    merchant_id: Optional[str] = None,
    paypal_email: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Simulates PayPal Partner Referrals OAuth callback storing seller's merchant_id."""
    shop = db.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    shop.paypal_merchant_id = merchant_id or f"MERCHANT_UA_{secrets.token_hex(4).upper()}"
    if paypal_email:
        shop.paypal_email = paypal_email
    shop.paypal_onboarding_status = "CONNECTED"
    db.commit()
    return {
        "ok": True,
        "merchant_id": shop.paypal_merchant_id,
        "paypal_email": shop.paypal_email,
        "state": _serialize_shop_state(db, shop),
    }


@app.post("/api/config/runtime")
async def api_update_runtime_config(payload: RuntimeConfigUpdateRequest, db: Session = Depends(get_db)):
    if payload.gemini_api_key is not None:
        settings.gemini_api_key = payload.gemini_api_key.strip()
    if payload.paypal_client_id is not None:
        settings.paypal_client_id = payload.paypal_client_id.strip()
    if payload.paypal_client_secret is not None:
        settings.paypal_client_secret = payload.paypal_client_secret.strip()
    if payload.telegram_bot_token is not None:
        settings.telegram_bot_token = payload.telegram_bot_token.strip()

    shop = db.query(Shop).filter(Shop.id == settings.default_shop_id).first() or seed_initial_data(db)
    if payload.shop_currency in ("USD", "UAH"):
        shop.currency = payload.shop_currency
    if payload.paypal_merchant_id:
        shop.paypal_merchant_id = payload.paypal_merchant_id.strip()
    db.commit()

    return {
        "ok": True,
        "state": _serialize_shop_state(db, shop),
    }


@app.post("/api/demo/reset")
async def api_demo_reset(db: Session = Depends(get_db)):
    shop = seed_initial_data(db, force_reset=True)
    return {
        "ok": True,
        "message": "Демо-дані магазину #123 скинуто до початкового стану.",
        "state": _serialize_shop_state(db, shop),
    }
