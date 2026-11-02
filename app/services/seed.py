import json
from sqlalchemy.orm import Session
from app.models import (
    BotMessage,
    Draft,
    Order,
    Product,
    ProductVariant,
    Reservation,
    Shop,
)


def seed_initial_data(db: Session, force_reset: bool = False) -> Shop:
    """
    Ensures Shop #123 (`/shop/123`) is populated with the exact initial catalog
    required to test Scenarios 1–5 and Edge Cases A–D out-of-the-box.
    """
    existing_shop = db.query(Shop).filter(Shop.id == 123).first()
    if existing_shop and not force_reset:
        return existing_shop

    if existing_shop and force_reset:
        db.query(Reservation).filter(Reservation.shop_id == 123).delete()
        db.query(Draft).filter(Draft.shop_id == 123).delete()
        db.query(Order).filter(Order.shop_id == 123).delete()
        db.query(BotMessage).filter(BotMessage.shop_id == 123).delete()
        products = db.query(Product).filter(Product.shop_id == 123).all()
        for p in products:
            db.delete(p)
        db.delete(existing_shop)
        db.commit()

    shop = Shop(
        id=123,
        name="NOVA KYIV • Instant AI Storefront",
        slug="shop-123",
        description="Mobile-First & Zero-Form вітрина. Усі товари створені голосом та фото через Telegram-бота з миттєвою оплатою PayPal.",
        telegram_id="tg_seller_123",
        telegram_username="@nova_kyiv_seller",
        currency="USD",
        exchange_rate_uah=40.0,
        paypal_merchant_id="MERCHANT_SANDBOX_UA_123",
        paypal_email="merchant-ua-123@sandbox.paypal.com",
        paypal_onboarding_status="CONNECTED",
    )
    db.add(shop)
    db.flush()

    null_attrs = json.dumps(
        {
            "brand": None,
            "material": None,
            "country_of_origin": None,
            "care_instructions": None,
        },
        ensure_ascii=False,
    )

    initial_products = [
        {
            "title": "Summer Dress-1 «Flora»",
            "title_uk": "Сукня-1 (Літня Флора)",
            "description": "Легка літня сукня сонячного відтінку з колекції магазину",
            "category": "Літні сукні",
            "price": 50.0,
            "images": ["/static/assets/summer_dress_1.svg"],
            "variants": [("S", 3), ("M", 4), ("L", 2)],
        },
        {
            "title": "Summer Dress-2 «Lavender»",
            "title_uk": "Сукня-2 (Літня Лаванда)",
            "description": "Ніжна літня сукня лавандового кольору",
            "category": "Літні сукні",
            "price": 60.0,
            "images": ["/static/assets/summer_dress_2.svg"],
            "variants": [("S", 2), ("M", 3)],
        },
        {
            "title": "Black Midnight Dress",
            "title_uk": "Чорна сукня",
            "description": "Класична чорна літня сукня (базова колекція)",
            "category": "Літні сукні",
            "price": 70.0,
            "images": ["/static/assets/black_dress.svg"],
            "variants": [("S", 2), ("M", 2), ("L", 1)],
        },
        {
            "title": "Blue Breeze Summer Dress",
            "title_uk": "Синя сукня",
            "description": "Літня синя сукня силуетного крою",
            "category": "Літні сукні",
            "price": 55.0,
            "images": ["/static/assets/blue_dress.svg"],
            "variants": [("S", 2), ("M", 3), ("L", 2)],
        },
        {
            "title": "Sneakers Air Pulse 43",
            "title_uk": "Кросівки Air Pulse",
            "description": "Лімітовані кросівки. Залишилася остання пара 43 розміру (тест Concurrency Lock)!",
            "category": "Взуття",
            "price": 80.0,
            "images": ["/static/assets/sneakers_43.svg"],
            "variants": [("42", 0), ("43", 1)],
        },
    ]

    for item in initial_products:
        prod = Product(
            shop_id=shop.id,
            title=item["title"],
            title_uk=item["title_uk"],
            description=item["description"],
            category=item["category"],
            price=item["price"],
            currency="USD",
            images_json=json.dumps(item["images"], ensure_ascii=False),
            attributes_json=null_attrs,
            is_active=True,
        )
        db.add(prod)
        db.flush()
        for sz, qty in item["variants"]:
            db.add(
                ProductVariant(
                    product_id=prod.id,
                    size=sz,
                    stock_quantity=qty,
                    stock_reserved=0,
                )
            )

    # Seed welcome message in Telegram Bot chat history
    welcome_msg = BotMessage(
        shop_id=shop.id,
        sender="bot",
        message_type="text",
        text=(
            "👋 <b>Вітаю у Instant AI Storefront (IAS)!</b>\n\n"
            "Ваш магазин <code>/shop/123</code> активний та підключений до PayPal Sandbox "
            "(Merchant ID: <code>MERCHANT_SANDBOX_UA_123</code>).\n\n"
            "🎙 <b>Як керувати вітриною без жодної форми:</b>\n"
            "1️⃣ Надішліть <b>фото + голосове/текст</b> для створення товару.\n"
            "2️⃣ Напишіть команду природною мовою для <b>масової зміни цін чи залишків</b>.\n"
            "3️⃣ Скиньте <b>номер ТТН Нової Пошти</b> для синхронізації з <b>PayPal Add Tracking API</b>.\n\n"
            "<i>Оберіть готовий сценарій нижче або введіть власне повідомлення!</i>"
        ),
    )
    db.add(welcome_msg)
    db.commit()
    db.refresh(shop)
    return shop
