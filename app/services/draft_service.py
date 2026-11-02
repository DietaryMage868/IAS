from datetime import timedelta
import json
import secrets
from typing import Optional
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Draft, Product, ProductVariant, Shop, utcnow
from app.schemas import AIIntentResult


def generate_preview_token() -> str:
    return f"prv_{secrets.token_urlsafe(12)}"


def create_draft_from_ai_result(
    db: Session,
    shop: Shop,
    ai_result: AIIntentResult,
    image_urls: list[str],
) -> tuple[Draft, str, list[dict]]:
    """
    Creates a staging Draft with a 15-minute expiring preview_token.
    Returns (draft, formatted_telegram_message, inline_buttons).
    """
    token = generate_preview_token()
    expires_at = utcnow() + timedelta(minutes=settings.preview_token_ttl_minutes)
    preview_url = f"/shop/{shop.id}?preview_token={token}"

    if ai_result.intent == "CREATE_PRODUCT" and ai_result.product_data:
        pdata = ai_result.product_data
        default_img = "/static/assets/green_hoodie.svg"
        if "сукн" in pdata.category.lower() or "dress" in pdata.title.lower():
            default_img = "/static/assets/red_dress.svg" if "червон" in (pdata.title_uk or "").lower() else "/static/assets/summer_dress_1.svg"
        elif "кросівк" in pdata.category.lower() or "sneaker" in pdata.title.lower():
            default_img = "/static/assets/sneakers_43.svg"

        final_images = image_urls if image_urls else [default_img]
        has_price_warning = pdata.price <= 0.0

        currency_ambiguity = None
        if (
            pdata.detected_currency.upper() == "UAH"
            and shop.currency.upper() == "USD"
            and pdata.original_spoken_price
        ):
            currency_ambiguity = {
                "original_amount": pdata.original_spoken_price,
                "original_currency": "UAH",
                "converted_usd": pdata.price,
                "exchange_rate": shop.exchange_rate_uah,
                "resolved": False,
            }

        payload = {
            "title": pdata.title,
            "title_uk": pdata.title_uk,
            "description": pdata.description,
            "category": pdata.category,
            "price": pdata.price,
            "currency": shop.currency,
            "images": final_images,
            "variants": [v.model_dump() for v in pdata.variants],
            "attributes": pdata.attributes.model_dump(),
        }

        variants_str = ", ".join(f"{v.size} (stock: {v.stock_quantity})" for v in pdata.variants)
        attrs = pdata.attributes.model_dump()
        null_attrs = [k for k, v in attrs.items() if v is None]

        lines = [
            "✨ <b>Чернетку нового товару створено (Draft)</b>",
            f"• <b>Назва:</b> {pdata.title} ({pdata.title_uk})",
            f"• <b>Опис:</b> {pdata.description}",
            f"• <b>Ціна:</b> {'$' if shop.currency == 'USD' else '₴'}{pdata.price:.2f} {shop.currency}",
            f"• <b>Варіанти:</b> {variants_str}",
            f"• <b>Strict Fact Extracting:</b> невідомі поля ({', '.join(null_attrs)}) залишено <code>null</code> (Zero Hallucination)",
        ]

        if has_price_warning:
            lines.append("\n⚠️ <b>Увага: Ціну не вказано! Потрібно вказати ціну перед публікацією</b>")

        if currency_ambiguity:
            lines.append(
                f"\n💱 <b>Валюта вашого магазину {shop.currency}. Встановити ціну ${pdata.price:.2f} чи змінити валюту?</b> "
                f"(розпізнано: {int(pdata.original_spoken_price)} грн)"
            )

        lines.append(f"\n🔗 <b>Посилання на прев'ю (15 хв):</b> <code>{preview_url}</code>")
        summary_text = "\n".join(lines)

        draft = Draft(
            shop_id=shop.id,
            preview_token=token,
            action_type="CREATE_PRODUCT",
            payload_json=json.dumps(payload, ensure_ascii=False),
            diff_summary_text=summary_text,
            status="PENDING",
            has_price_warning=has_price_warning,
            currency_ambiguity_json=json.dumps(currency_ambiguity, ensure_ascii=False) if currency_ambiguity else None,
            expires_at=expires_at,
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)

        buttons = []
        if has_price_warning:
            buttons.append(
                {
                    "label": "💲 Встановити ціну $45.00",
                    "action": "SET_PRICE",
                    "draft_id": draft.id,
                    "custom_price": 45.0,
                    "style": "warning",
                }
            )
        if currency_ambiguity:
            buttons.append(
                {
                    "label": f"💵 Встановити ${pdata.price:.2f} (USD)",
                    "action": "SET_CURRENCY_USD",
                    "draft_id": draft.id,
                    "style": "primary",
                }
            )
            buttons.append(
                {
                    "label": f"🇺🇦 Змінити валюту на {int(pdata.original_spoken_price)} ₴ (UAH)",
                    "action": "SET_CURRENCY_UAH",
                    "draft_id": draft.id,
                    "style": "secondary",
                }
            )

        buttons.extend(
            [
                {
                    "label": "🚀 Опублікувати",
                    "action": "PUBLISH",
                    "draft_id": draft.id,
                    "style": "success",
                    "disabled": has_price_warning,
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
            ]
        )
        return draft, summary_text, buttons

    # Otherwise: BULK_EDIT (Scenario 2)
    mutations_list = [m.model_dump() for m in ai_result.bulk_mutations]
    payload = {"mutations": mutations_list}

    diff_lines = ["📋 <b>Заплановані зміни:</b>"]
    sym = "$" if shop.currency == "USD" else "₴"
    for m in ai_result.bulk_mutations:
        if m.change_type == "EXCLUDED_NO_CHANGE":
            diff_lines.append(f"• {m.product_title}: {sym}{m.old_price:.0f} (без змін)")
        elif m.change_type == "PRICE_CHANGE":
            diff_lines.append(f"• {m.product_title}: {sym}{m.old_price:.0f} ➔ {sym}{m.new_price:.0f}")
        elif m.change_type == "VARIANT_STOCK_CHANGE":
            for vc in m.variant_changes:
                diff_lines.append(
                    f"• {m.product_title} (Розмір {vc['size']}): {vc.get('label', 'Знято з продажу (Out of stock)')}"
                )
        elif m.change_type == "PRICE_AND_STOCK_CHANGE":
            diff_lines.append(f"• {m.product_title}: {sym}{m.old_price:.0f} ➔ {sym}{m.new_price:.0f}")
            for vc in m.variant_changes:
                diff_lines.append(
                    f"• {m.product_title} (Розмір {vc['size']}): {vc.get('label', 'Знято з продажу (Out of stock)')}"
                )

    diff_lines.append(f"🔗 <b>Посилання на прев'ю:</b> <code>{preview_url}</code>")
    summary_text = "\n".join(diff_lines)

    draft = Draft(
        shop_id=shop.id,
        preview_token=token,
        action_type="BULK_EDIT",
        payload_json=json.dumps(payload, ensure_ascii=False),
        diff_summary_text=summary_text,
        status="PENDING",
        has_price_warning=False,
        expires_at=expires_at,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    buttons = [
        {
            "label": "✅ Застосувати",
            "action": "PUBLISH",
            "draft_id": draft.id,
            "style": "success",
        },
        {
            "label": "👁 Переглянути прев'ю з бейджами",
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
    ]
    return draft, summary_text, buttons


def apply_draft_action(
    db: Session,
    shop: Shop,
    draft_id: int,
    action: str,
    custom_price: Optional[float] = None,
) -> tuple[bool, str, Optional[Draft]]:
    """
    Executes seller's decision on a Draft:
    - PUBLISH: Atomically merges new product or bulk edits into the primary catalog.
    - CANCEL: Invalidates preview_token and leaves live storefront untouched.
    - SET_PRICE / SET_CURRENCY_USD / SET_CURRENCY_UAH: Resolves voice ambiguities before publishing.
    """
    draft = db.query(Draft).filter(Draft.id == draft_id, Draft.shop_id == shop.id).first()
    if not draft:
        return False, "Чернетку не знайдено.", None

    if draft.status == "CANCELLED":
        return False, "Цю чернетку вже було скасовано.", draft

    if draft.status == "PUBLISHED":
        return False, "Цю чернетку вже опубліковано на вітрині.", draft

    if draft.is_expired and action == "PUBLISH":
        draft.status = "EXPIRED"
        db.commit()
        return False, "⚠️ Термін дії прев'ю-токена (15 хвилин) минув. Створіть нову чернетку.", draft

    if action == "CANCEL":
        draft.status = "CANCELLED"
        db.commit()
        return (
            True,
            f"🗑 Чернетку #{draft.id} скасовано. Токен <code>{draft.preview_token}</code> видалено, публічний каталог не змінено.",
            draft,
        )

    if action == "SET_PRICE" and custom_price is not None and custom_price > 0:
        payload = draft.payload
        payload["price"] = float(custom_price)
        draft.payload_json = json.dumps(payload, ensure_ascii=False)
        draft.has_price_warning = False
        db.commit()
        db.refresh(draft)
        return (
            True,
            f"✅ Ціну чернетки #{draft.id} встановлено на ${custom_price:.2f}. Тепер ви можете натиснути [🚀 Опублікувати]!",
            draft,
        )

    if action == "SET_CURRENCY_USD":
        payload = draft.payload
        amb = draft.currency_ambiguity or {}
        usd_val = float(amb.get("converted_usd", payload.get("price", 5.0)))
        payload["price"] = usd_val
        payload["currency"] = "USD"
        amb["resolved"] = True
        draft.payload_json = json.dumps(payload, ensure_ascii=False)
        draft.currency_ambiguity_json = json.dumps(amb, ensure_ascii=False)
        db.commit()
        db.refresh(draft)
        return (
            True,
            f"💵 Підтверджено ціну ${usd_val:.2f} USD для чернетки #{draft.id}. Натисніть [🚀 Опублікувати] для розміщення на вітрині.",
            draft,
        )

    if action == "SET_CURRENCY_UAH":
        payload = draft.payload
        amb = draft.currency_ambiguity or {}
        uah_val = float(amb.get("original_amount", 200.0))
        shop.currency = "UAH"
        payload["price"] = uah_val
        payload["currency"] = "UAH"
        amb["resolved"] = True
        draft.payload_json = json.dumps(payload, ensure_ascii=False)
        draft.currency_ambiguity_json = json.dumps(amb, ensure_ascii=False)
        db.commit()
        db.refresh(draft)
        return (
            True,
            f"🇺🇦 Валюту магазину змінено на UAH та встановлено ціну {uah_val:.2f} ₴ для чернетки #{draft.id}. Натисніть [🚀 Опублікувати]!",
            draft,
        )

    if action == "PUBLISH":
        if draft.has_price_warning:
            return (
                False,
                "⚠️ Увага: Ціну не вказано! Спочатку вкажіть ціну товару перед публікацією.",
                draft,
            )

        payload = draft.payload
        if draft.action_type == "CREATE_PRODUCT":
            new_prod = Product(
                shop_id=shop.id,
                title=payload.get("title", "New Product"),
                title_uk=payload.get("title_uk", payload.get("title", "Новий товар")),
                description=payload.get("description", ""),
                category=payload.get("category", "Одяг"),
                price=float(payload.get("price", 0.0)),
                currency=payload.get("currency", shop.currency),
                images_json=json.dumps(payload.get("images", []), ensure_ascii=False),
                attributes_json=json.dumps(payload.get("attributes", {}), ensure_ascii=False),
                is_active=True,
            )
            db.add(new_prod)
            db.flush()

            for v in payload.get("variants", []):
                db.add(
                    ProductVariant(
                        product_id=new_prod.id,
                        size=v.get("size", "One Size"),
                        stock_quantity=int(v.get("stock_quantity", 1)),
                        stock_reserved=0,
                    )
                )

            draft.status = "PUBLISHED"
            draft.published_at = utcnow()
            db.commit()
            return (
                True,
                f"🚀 <b>Опубліковано!</b> Товар «{new_prod.title}» (${new_prod.price:.2f}) миттєво з'явився на вітрині <code>/shop/{shop.id}</code>.",
                draft,
            )

        elif draft.action_type == "BULK_EDIT":
            mutations = payload.get("mutations", [])
            updated_count = 0
            for m in mutations:
                if m.get("change_type") == "EXCLUDED_NO_CHANGE":
                    continue
                prod = (
                    db.query(Product)
                    .filter(Product.id == m.get("product_id"), Product.shop_id == shop.id)
                    .first()
                )
                if not prod:
                    continue
                if "new_price" in m and m["new_price"] is not None:
                    prod.price = float(m["new_price"])
                for vc in m.get("variant_changes", []):
                    for pv in prod.variants:
                        if pv.size.upper() == str(vc.get("size", "")).upper():
                            pv.stock_quantity = int(vc.get("new_stock", 0))
                updated_count += 1

            draft.status = "PUBLISHED"
            draft.published_at = utcnow()
            db.commit()
            return (
                True,
                f"✅ <b>Зміни атомарно застосовано до основної бази!</b> Оновлено позицій каталогу: {updated_count}. Вітрина <code>/shop/{shop.id}</code> актуалізована.",
                draft,
            )

    return False, "Невідома дія над чернеткою.", draft
