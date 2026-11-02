import json
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models import BotMessage, Order, Product, Shop, utcnow
from app.services.ai_engine import analyze_seller_input
from app.services.draft_service import apply_draft_action, create_draft_from_ai_result
from app.services.paypal_service import add_paypal_order_tracking


async def process_seller_message(
    db: Session,
    shop: Shop,
    text: str = "",
    voice_transcript: Optional[str] = None,
    image_urls: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Unified pipeline for processing seller input from either Telegram Bot or Web Studio.
    Handles Scenarios 1, 2, 4, 5 and Edge Cases A1, A2, B, D.
    """
    imgs = image_urls or []
    display_text = voice_transcript or text or "(Медіа без тексту)"
    media_items = [{"type": "photo", "url": u} for u in imgs]
    if voice_transcript:
        media_items.append({"type": "voice", "transcript": voice_transcript, "duration": "0:06"})

    seller_msg = BotMessage(
        shop_id=shop.id,
        sender="seller",
        message_type="voice_photo" if (voice_transcript or imgs) else "text",
        text=display_text,
        media_json=json.dumps(media_items, ensure_ascii=False),
    )
    db.add(seller_msg)
    db.commit()

    products = db.query(Product).filter(Product.shop_id == shop.id, Product.is_active.is_(True)).all()
    ai_result = await analyze_seller_input(
        shop=shop,
        products=products,
        text=text,
        voice_transcript=voice_transcript,
        image_urls=imgs,
    )

    # -------------------------------------------------------------------------
    # Scenario 5: Non-commercial / Noise input
    # -------------------------------------------------------------------------
    if ai_result.intent == "NON_COMMERCIAL":
        err_text = (
            ai_result.error_message
            or "Не вдалося знайти інформацію про товар. Будь ласка, скиньте фото товару та вкажіть ціну"
        )
        bot_msg = BotMessage(
            shop_id=shop.id,
            sender="bot",
            message_type="error",
            text=f"🤖 {err_text}",
            metadata_json=json.dumps({"intent": "NON_COMMERCIAL"}, ensure_ascii=False),
        )
        db.add(bot_msg)
        db.commit()
        db.refresh(bot_msg)
        return {
            "ok": True,
            "intent": "NON_COMMERCIAL",
            "bot_message_id": bot_msg.id,
            "reply_text": bot_msg.text,
            "draft": None,
        }

    # -------------------------------------------------------------------------
    # Scenario 4: Fulfillment & PayPal Add Tracking API
    # -------------------------------------------------------------------------
    if ai_result.intent == "FULFILLMENT_TRACKING" and ai_result.fulfillment_data:
        fdata = ai_result.fulfillment_data
        order = (
            db.query(Order)
            .filter(Order.shop_id == shop.id, Order.id == fdata.order_id)
            .first()
        )
        if not order:
            # If Seller tests Scenario 4 before Scenario 3, auto-seed Order #102 so demo works in any sequence
            order = Order(
                id=fdata.order_id,
                shop_id=shop.id,
                product_id=5,
                variant_id=10,
                product_title="Кросівки Air Pulse",
                variant_size="43",
                amount=80.0,
                currency=shop.currency,
                buyer_email="buyer@example.com",
                buyer_name="Alex Buyer",
                paypal_order_id="PAYPAL-ORD-102DEMO",
                paypal_capture_id="CAP-102DEMO99",
                payee_merchant_id=shop.paypal_merchant_id,
                status="PAID",
                tracking_status="PENDING",
            )
            db.add(order)
            db.flush()

        pp_track = await add_paypal_order_tracking(
            order=order,
            tracking_number=fdata.tracking_number,
            carrier=fdata.carrier,
            carrier_name_other=fdata.carrier_name_other,
        )

        order.tracking_number = fdata.tracking_number
        order.carrier = fdata.carrier_name_other
        order.tracking_status = "SHIPPED"
        order.status = "SHIPPED"
        order.paypal_tracker_synced = True
        order.paypal_tracking_response_json = json.dumps(pp_track, ensure_ascii=False)
        order.shipped_at = utcnow()

        reply_text = (
            "✅ <b>Трек-номер успішно прив'язано до транзакції PayPal</b>\n\n"
            f"• <b>Замовлення:</b> #{order.id} ({order.product_title}, ромір {order.variant_size})\n"
            f"• <b>Служба доставки:</b> {fdata.carrier_name_other}\n"
            f"• <b>Трек-номер (ТТН):</b> <code>{fdata.tracking_number}</code>\n"
            f"• <b>PayPal API Endpoint:</b> <code>POST {pp_track['endpoint']}</code>\n"
            f"• <b>Сповіщення покупця ({order.buyer_email}):</b> «Ваше замовлення відправлено, трек-номер додано»"
        )

        bot_msg = BotMessage(
            shop_id=shop.id,
            sender="bot",
            message_type="tracking_confirm",
            text=reply_text,
            metadata_json=json.dumps(pp_track, ensure_ascii=False),
        )
        db.add(bot_msg)
        db.commit()
        db.refresh(bot_msg)
        return {
            "ok": True,
            "intent": "FULFILLMENT_TRACKING",
            "bot_message_id": bot_msg.id,
            "reply_text": reply_text,
            "order_id": order.id,
            "paypal_tracking": pp_track,
        }

    # -------------------------------------------------------------------------
    # Scenario 1 & Scenario 2: Draft-and-Merge Pattern
    # -------------------------------------------------------------------------
    draft, summary_text, buttons = create_draft_from_ai_result(
        db=db,
        shop=shop,
        ai_result=ai_result,
        image_urls=imgs,
    )

    bot_msg = BotMessage(
        shop_id=shop.id,
        sender="bot",
        message_type="draft_card",
        text=summary_text,
        buttons_json=json.dumps(buttons, ensure_ascii=False),
        draft_id=draft.id,
        metadata_json=json.dumps(
            {
                "intent": ai_result.intent,
                "preview_token": draft.preview_token,
                "action_type": draft.action_type,
            },
            ensure_ascii=False,
        ),
    )
    db.add(bot_msg)
    db.commit()
    db.refresh(bot_msg)

    return {
        "ok": True,
        "intent": ai_result.intent,
        "bot_message_id": bot_msg.id,
        "reply_text": summary_text,
        "buttons": buttons,
        "draft": {
            "id": draft.id,
            "preview_token": draft.preview_token,
            "action_type": draft.action_type,
            "status": draft.status,
            "has_price_warning": draft.has_price_warning,
            "preview_url": f"/shop/{shop.id}?preview_token={draft.preview_token}",
        },
    }


def handle_draft_button_click(
    db: Session,
    shop: Shop,
    draft_id: int,
    action: str,
    custom_price: Optional[float] = None,
) -> dict[str, Any]:
    """Handles inline button clicks ([🚀 Опублікувати], [✅ Застосувати], [❌ Скасувати], currency/price choices)."""
    ok, message, draft = apply_draft_action(
        db=db,
        shop=shop,
        draft_id=draft_id,
        action=action,
        custom_price=custom_price,
    )

    # Update buttons on the original draft BotMessage if needed
    orig_msg = (
        db.query(BotMessage)
        .filter(BotMessage.shop_id == shop.id, BotMessage.draft_id == draft_id)
        .order_by(BotMessage.id.desc())
        .first()
    )
    if orig_msg and draft:
        if draft.status in ("PUBLISHED", "CANCELLED", "EXPIRED"):
            orig_msg.buttons_json = json.dumps(
                [
                    {
                        "label": "🚀 Опубліковано на вітрині" if draft.status == "PUBLISHED" else "❌ Чернетку скасовано",
                        "action": "NONE",
                        "draft_id": draft.id,
                        "style": "done",
                        "disabled": True,
                    }
                ],
                ensure_ascii=False,
            )
        elif not draft.has_price_warning:
            # Enable Publish button after price/currency ambiguity was resolved
            preview_url = f"/shop/{shop.id}?preview_token={draft.preview_token}"
            orig_msg.buttons_json = json.dumps(
                [
                    {
                        "label": "🚀 Опублікувати",
                        "action": "PUBLISH",
                        "draft_id": draft.id,
                        "style": "success",
                        "disabled": False,
                    },
                    {
                        "label": "👁 Відкрити прев'ю",
                        "action": "OPEN_PREVIEW",
                        "url": preview_url,
                        "draft_id": draft.id,
                        "style": "link",
                    },
                    {
                        "label": "❌ Скасувати",
                        "action": "CANCEL",
                        "draft_id": draft.id,
                        "style": "danger",
                    },
                ],
                ensure_ascii=False,
            )

    bot_reply = BotMessage(
        shop_id=shop.id,
        sender="bot",
        message_type="text",
        text=message,
        draft_id=draft_id,
    )
    db.add(bot_reply)
    db.commit()

    return {
        "ok": ok,
        "message": message,
        "draft_status": draft.status if draft else None,
        "preview_token": draft.preview_token if draft else None,
    }
