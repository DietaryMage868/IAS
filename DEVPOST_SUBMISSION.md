# 🏆 Devpost Submission Guide — Instant AI Storefront (IAS)
**Hackathon:** PayPal AI Hackathon  
**Repository:** https://github.com/DietaryMage868/IAS

---

## 💡 Inspiration
Millions of independent sellers on Instagram, TikTok, and Telegram run their entire business from a smartphone. Traditional e-commerce platforms force them into complex desktop admin dashboards, SKU spreadsheets, and multi-page product forms. As a result, most social sellers resort to chaotic DM conversations and manual card transfers — losing buyers and risking oversells.

We asked: **What if a seller never had to fill out a single form again?** What if sending 2 photos and a 5-second voice note to a Telegram bot was enough to spin up an enterprise-grade PayPal storefront with atomic inventory locks and automated shipment tracking?

## 🚀 What it does
**Instant AI Storefront (IAS)** is a Mobile-First, Zero-Form e-commerce platform that transforms unstructured voice messages, text commands, and photos in Telegram into a live, multi-tenant web storefront powered by **PayPal Native Checkout**:

1. **Zero-Form Voice & Photo Product Creation:** A seller sends photos + a voice message (*"Green soft hoodie, sizes S and M, $40, 3 in stock each"*). Multimodal AI extracts strictly verified facts without hallucinating unmentioned fabric or brand details.
2. **Draft-and-Merge Safety Pattern:** AI never mutates the live catalog directly. Every creation or bulk edit produces an expiring 15-minute `preview_token` (`/shop/123?preview_token=xyz`) with `<meta name="robots" content="noindex, nofollow">` and yellow visual diff badges. Only when the seller clicks `[🚀 Publish]` in Telegram do changes merge atomically.
3. **Natural Language Bulk Catalog Editing:** Sellers run sales by speaking naturally (*"Lower the price on all summer dresses by 10%, keep the black dress at the old price, and mark size S of the blue dress as sold out"*).
4. **Anti-Oversell Concurrency & 15-Minute PayPal Reservation Lock:** When Buyer A clicks **Pay with PayPal** on the last pair of Size 43 sneakers, IAS places a 15-minute reservation lock (`stock_reserved = 1`) and routes payment directly to the seller via `payee.merchant_id` (PayPal Partner Referrals). Concurrent Buyer B sees a real-time reservation badge until the payment completes or the 15-minute lock expires.
5. **Automated Fulfillment via PayPal Add Tracking API:** When the seller drops a shipping receipt or tracking number in Telegram (*"Order #102 shipped, Nova Poshta 20450012345678"*), IAS automatically calls `POST /v2/checkout/orders/{id}/track`, triggering PayPal's official shipment notification to the buyer.

## 🛠 How we built it
- **Backend:** Python 3.12+, **FastAPI**, **SQLAlchemy 2.0**, and **Pydantic v2** for strict schema enforcement.
- **AI Engine:** **Google Gemini 2.5 Flash** (`google-genai`) with structured JSON outputs enforcing our **Zero Hallucination Policy**, paired with a deterministic Ukrainian/Surzhyk/English NLP engine for zero-latency fallback and voice ambiguity detection (missing prices, UAH-to-USD currency mismatches).
- **Payments & Fulfillment:** **PayPal REST API v2** (`/v2/checkout/orders`, `/v2/checkout/orders/{id}/capture`, `/v2/checkout/orders/{id}/track`) with multi-tenant `payee.merchant_id` routing.
- **Seller Interface:** **Aiogram 3.x** Telegram Bot + an interactive **Split-Screen Web Telegram Studio** built right into `/shop/123` so hackathon judges can test all 5 scenarios and Buyer A / Buyer B concurrency races in one browser window.

## 🎬 2-Minute Demo Video Script (Сценарій для відео на Devpost)
1. **0:00 – 0:20 | The Problem & Split-Screen Studio:** Open `http://localhost:8000/shop/123`. Show the live storefront on the left and the Telegram AI Bot Studio on the right.
2. **0:20 – 0:45 | Scenario 1 (Voice + Photo Ingestion):** Click **1️⃣ Створити товар (2 фото + голос)**. Show the AI Draft card, click **👁 Відкрити прев'ю** to highlight the yellow preview banner and `null` Zero-Hallucination audit, then click **🚀 Опублікувати**. Watch the *Green Soft Hoodie* appear live!
3. **0:45 – 1:10 | Scenario 2 (Natural Language Sale):** Click **2️⃣ Розпродаж суконь -10% (Diff)**. Open preview mode to show the yellow price/stock diff badges (`$50 ➔ $45`, Black Dress unchanged at `$70`, Blue Dress Size S Out of Stock), then click **✅ Застосувати**.
4. **1:10 – 1:40 | Scenario 3 (PayPal Checkout & Concurrency Race):** Select **Кросівки Air Pulse (Розмір 43, 1 шт)** as **Покупець А** and click **Сплатити через PayPal**. Switch the top header to **👥 Покупець Б** to show the live *"⏳ Товар зарезервовано іншим покупцем"* lock! Complete the PayPal payment as Buyer A and watch the instant Telegram push notification for **Order #102**.
5. **1:40 – 2:00 | Scenario 4 & Edge Cases (PayPal Tracking API):** Click **4️⃣ ТТН Нової Пошти ➔ PayPal Track** to attach `20450012345678` via `POST /v2/checkout/orders/{id}/track`, and briefly show **⚠️ Без ціни** / **💱 200 грн** edge-case guards.
