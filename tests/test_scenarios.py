from fastapi.testclient import TestClient
import pytest
from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.post("/api/demo/reset")
        yield c


def test_scenario_1_create_product_happy_path_and_zero_hallucination(client: TestClient):
    """Scenario 1: 2 photos + voice 'Зелена кофта, м'яка, є розміри S і M, ціна 40 баксів, залишків по 3 штуки кожного'."""
    res = client.post(
        "/api/bot/message",
        json={
            "shop_id": 123,
            "voice_transcript": "Зелена кофта, м'яка, є розміри S і M, ціна 40 баксів, залишків по 3 штуки кожного",
            "image_urls": ["/static/assets/green_hoodie.svg", "/static/assets/green_hoodie_back.svg"],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["intent"] == "CREATE_PRODUCT"
    draft = data["draft"]
    assert draft["status"] == "PENDING"
    preview_token = draft["preview_token"]

    # Before publishing: public catalog must NOT contain Green Soft Hoodie yet (Draft-and-Merge pattern)
    pub_state = client.get("/api/shop/123/state").json()
    pub_titles = [p["title"] for p in pub_state["products"]]
    assert "Green Soft Hoodie" not in pub_titles

    # Preview mode URL must contain the draft product + noindex meta tag + preview banner
    prv_html = client.get(f"/shop/123?preview_token={preview_token}").text
    assert '<meta name="robots" content="noindex, nofollow"' in prv_html
    assert "Режим перегляду продавця" in prv_html

    prv_state = client.get(f"/api/shop/123/state?preview_token={preview_token}").json()
    draft_prod = prv_state["products"][0]
    assert draft_prod["title"] == "Green Soft Hoodie"
    assert draft_prod["price"] == 40.0
    assert len(draft_prod["variants"]) == 2
    assert draft_prod["variants"][0]["size"] == "S"
    assert draft_prod["variants"][0]["stock_quantity"] == 3
    # Edge Case D: Zero Hallucination Policy (unmentioned attributes remain None)
    assert draft_prod["attributes"]["brand"] is None
    assert draft_prod["attributes"]["material"] is None
    assert draft_prod["attributes"]["country_of_origin"] is None

    # Publish the draft
    pub_res = client.post(
        "/api/bot/draft-action",
        json={"shop_id": 123, "draft_id": draft["id"], "action": "PUBLISH"},
    )
    assert pub_res.status_code == 200
    assert pub_res.json()["ok"] is True

    # Now Green Soft Hoodie is live on the public storefront
    after_state = client.get("/api/shop/123/state").json()
    assert any(p["title"] == "Green Soft Hoodie" for p in after_state["products"])


def test_scenario_2_bulk_catalog_edit_natural_language(client: TestClient):
    """Scenario 2: -10% on summer dresses, exclude black dress, mark size S of blue dress out of stock."""
    res = client.post(
        "/api/bot/message",
        json={
            "shop_id": 123,
            "text": "Знизь ціну на всі літні сукні на 10%, але чорну сукню залиш по старій ціні. І розмір S для синьої сукні познач як випроданий",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["intent"] == "BULK_EDIT"
    assert "Заплановані зміни" in data["reply_text"]
    assert "без змін" in data["reply_text"]
    assert "Out of stock" in data["reply_text"]

    draft_id = data["draft"]["id"]
    pub_res = client.post(
        "/api/bot/draft-action",
        json={"shop_id": 123, "draft_id": draft_id, "action": "PUBLISH"},
    )
    assert pub_res.json()["ok"] is True

    state = client.get("/api/shop/123/state").json()
    by_uk_title = {p["title_uk"]: p for p in state["products"]}

    # Dress 1: $50 -> $45
    assert by_uk_title["Сукня-1 (Літня Флора)"]["price"] == 45.0
    # Dress 2: $60 -> $54
    assert by_uk_title["Сукня-2 (Літня Лаванда)"]["price"] == 54.0
    # Black dress excluded: stays $70
    assert by_uk_title["Чорна сукня"]["price"] == 70.0
    # Blue dress: $55 -> $49.5 and Size S stock == 0
    blue = by_uk_title["Синя сукня"]
    assert blue["price"] == 49.5
    size_s = next(v for v in blue["variants"] for _ in [1] if v["size"] == "S")
    assert size_s["stock_quantity"] == 0


def test_scenario_3_and_4_concurrency_paypal_checkout_and_fulfillment(client: TestClient):
    """Scenario 3 (Concurrency lock & PayPal capture) + Scenario 4 (Nova Poshta tracking sync to PayPal)."""
    state = client.get("/api/shop/123/state").json()
    sneakers = next(p for p in state["products"] if "Кросівки" in p["title_uk"])
    var_43 = next(v for v in sneakers["variants"] if v["size"] == "43")
    assert var_43["stock_quantity"] == 1

    # 1. Buyer A clicks Pay with PayPal -> creates 15-minute reservation lock
    res_a = client.post(
        "/api/checkout/reserve",
        json={
            "shop_id": 123,
            "product_id": sneakers["id"],
            "variant_id": var_43["id"],
            "buyer_session_id": "buyer_a_sess",
            "buyer_label": "Покупець А",
        },
    )
    assert res_a.status_code == 200
    res_a_data = res_a.json()
    assert res_a_data["ok"] is True
    reservation_id = res_a_data["reservation_id"]

    # 2. Buyer B tries to buy the same Size 43 while locked by Buyer A -> 409 Conflict
    res_b = client.post(
        "/api/checkout/reserve",
        json={
            "shop_id": 123,
            "product_id": sneakers["id"],
            "variant_id": var_43["id"],
            "buyer_session_id": "buyer_b_sess",
            "buyer_label": "Покупець Б",
        },
    )
    assert res_b.status_code == 409
    assert "Товар зарезервовано іншим покупцем" in res_b.json()["message"]

    # 3. Buyer A completes PayPal Checkout -> Order #102 created, stock_quantity becomes 0
    cap = client.post(
        "/api/checkout/capture",
        json={
            "shop_id": 123,
            "reservation_id": reservation_id,
            "buyer_email": "buyer@example.com",
            "buyer_name": "Buyer A",
        },
    )
    assert cap.status_code == 200
    cap_data = cap.json()
    assert cap_data["order_id"] == 102

    # 4. Scenario 4: Seller sends Nova Poshta tracking number in Telegram bot
    track_res = client.post(
        "/api/bot/message",
        json={
            "shop_id": 123,
            "text": "Замовлення #102 відправив, трек Нової Пошти 20450012345678",
        },
    )
    assert track_res.status_code == 200
    t_data = track_res.json()
    assert t_data["intent"] == "FULFILLMENT_TRACKING"
    assert "Трек-номер успішно прив'язано до транзакції PayPal" in t_data["reply_text"]
    assert t_data["paypal_tracking"]["request"]["tracking_number"] == "20450012345678"


def test_scenario_5_and_edge_cases_missing_price_and_uah_currency(client: TestClient):
    """Scenario 5 (Non-commercial rejection & Cancel) + Edge Cases A1 (Missing price) & A2 (200 UAH in USD shop)."""
    # Scenario 5: Non-commercial noise
    noise = client.post(
        "/api/bot/message",
        json={"shop_id": 123, "text": "Привіт, ось фото котика"},
    ).json()
    assert noise["intent"] == "NON_COMMERCIAL"
    assert "Не вдалося знайти інформацію про товар" in noise["reply_text"]

    # Edge Case A1: Missing price ("Червона сукня, розміри S і M")
    no_price = client.post(
        "/api/bot/message",
        json={"shop_id": 123, "text": "Червона сукня, розміри S і M"},
    ).json()
    assert no_price["intent"] == "CREATE_PRODUCT"
    assert no_price["draft"]["has_price_warning"] is True
    assert "Увага: Ціну не вказано!" in no_price["reply_text"]

    # Attempting to publish without setting price should be blocked
    blocked_pub = client.post(
        "/api/bot/draft-action",
        json={"shop_id": 123, "draft_id": no_price["draft"]["id"], "action": "PUBLISH"},
    ).json()
    assert blocked_pub["ok"] is False

    # Cancel the draft (Scenario 5 rollback safety)
    cancelled = client.post(
        "/api/bot/draft-action",
        json={"shop_id": 123, "draft_id": no_price["draft"]["id"], "action": "CANCEL"},
    ).json()
    assert cancelled["ok"] is True
    assert cancelled["draft_status"] == "CANCELLED"

    # Edge Case A2: Surzhyk / UAH currency ("Червона сукня, розміри S і M, ціна двісті грн")
    uah_res = client.post(
        "/api/bot/message",
        json={"shop_id": 123, "text": "Червона сукня, розміри S і M, ціна двісті грн"},
    ).json()
    assert uah_res["intent"] == "CREATE_PRODUCT"
    assert "Валюта вашого магазину USD. Встановити ціну $5.00 чи змінити валюту?" in uah_res["reply_text"]
