from datetime import datetime, timezone
import json
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Shop(Base):
    """Multi-tenant storefront linked to a Telegram Seller and PayPal Merchant ID."""

    __tablename__ = "shops"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(160), nullable=False, default="Kiev Streetwear & Dress Studio")
    slug = Column(String(100), unique=True, index=True, nullable=False, default="shop-123")
    description = Column(
        Text,
        nullable=False,
        default="AI-Powered Instant Storefront • Миттєва вітрина з голосового керування в Telegram",
    )
    telegram_id = Column(String(64), index=True, nullable=True, default="tg_seller_123")
    telegram_username = Column(String(100), nullable=True, default="@ias_fashion_seller")
    currency = Column(String(10), nullable=False, default="USD")
    exchange_rate_uah = Column(Float, nullable=False, default=40.0)

    # PayPal Partner Referrals / Multi-tenancy
    paypal_merchant_id = Column(String(128), nullable=True, default="MERCHANT_SANDBOX_UA_123")
    paypal_email = Column(String(160), nullable=True, default="seller-facilitator@ias-store.com")
    paypal_onboarding_status = Column(String(50), nullable=False, default="CONNECTED")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    products = relationship("Product", back_populates="shop", cascade="all, delete-orphan")
    drafts = relationship("Draft", back_populates="shop", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="shop", cascade="all, delete-orphan")
    messages = relationship("BotMessage", back_populates="shop", cascade="all, delete-orphan")


class Product(Base):
    """Storefront catalog item created strictly from verified seller facts & photos."""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    title_uk = Column(String(200), nullable=True)
    description = Column(Text, nullable=False, default="")
    category = Column(String(100), nullable=False, default="General")
    price = Column(Float, nullable=False, default=0.0)
    currency = Column(String(10), nullable=False, default="USD")
    images_json = Column(Text, nullable=False, default="[]")
    # Strict Fact Extracting: unmentioned fields remain null (never hallucinated)
    attributes_json = Column(
        Text,
        nullable=False,
        default='{"brand": null, "material": null, "country_of_origin": null, "care_instructions": null}',
    )
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    shop = relationship("Shop", back_populates="products")
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")

    @property
    def images(self) -> list[str]:
        try:
            return json.loads(self.images_json or "[]")
        except Exception:
            return []

    @property
    def attributes(self) -> dict:
        try:
            return json.loads(self.attributes_json or "{}")
        except Exception:
            return {"brand": None, "material": None, "country_of_origin": None}


class ProductVariant(Base):
    """Size/option variant with physical stock and 15-minute reservation lock counter."""

    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    size = Column(String(50), nullable=False)
    stock_quantity = Column(Integer, nullable=False, default=0)
    stock_reserved = Column(Integer, nullable=False, default=0)

    product = relationship("Product", back_populates="variants")

    @property
    def available_stock(self) -> int:
        return max(0, self.stock_quantity - self.stock_reserved)


class Draft(Base):
    """Draft-and-Merge staging record with 15-minute expiring preview_token."""

    __tablename__ = "drafts"

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    preview_token = Column(String(128), unique=True, index=True, nullable=False)
    action_type = Column(String(50), nullable=False)  # CREATE_PRODUCT | BULK_EDIT
    payload_json = Column(Text, nullable=False, default="{}")
    diff_summary_text = Column(Text, nullable=False, default="")
    status = Column(String(32), nullable=False, default="PENDING")  # PENDING | PUBLISHED | CANCELLED | EXPIRED
    has_price_warning = Column(Boolean, default=False, nullable=False)
    currency_ambiguity_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    published_at = Column(DateTime, nullable=True)

    shop = relationship("Shop", back_populates="drafts")

    @property
    def payload(self) -> dict:
        try:
            return json.loads(self.payload_json or "{}")
        except Exception:
            return {}

    @property
    def currency_ambiguity(self) -> dict | None:
        if not self.currency_ambiguity_json:
            return None
        try:
            return json.loads(self.currency_ambiguity_json)
        except Exception:
            return None

    @property
    def is_expired(self) -> bool:
        return utcnow() > self.expires_at or self.status == "EXPIRED"


class Reservation(Base):
    """15-minute temporary inventory lock during PayPal Checkout to prevent overselling."""

    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True)
    buyer_session_id = Column(String(128), nullable=False, index=True)
    buyer_label = Column(String(64), nullable=False, default="Buyer A")
    quantity = Column(Integer, nullable=False, default=1)
    paypal_order_id = Column(String(128), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="ACTIVE")  # ACTIVE | COMPLETED | CANCELLED | EXPIRED
    created_at = Column(DateTime, default=utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class Order(Base):
    """Completed PayPal Order with native tracking sync (/v2/checkout/orders/{id}/track)."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, nullable=False)
    variant_id = Column(Integer, nullable=False)
    product_title = Column(String(200), nullable=False)
    variant_size = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), nullable=False, default="USD")
    buyer_email = Column(String(160), nullable=False, default="buyer@example.com")
    buyer_name = Column(String(160), nullable=False, default="Alex Buyer")
    paypal_order_id = Column(String(128), nullable=False, index=True)
    paypal_capture_id = Column(String(128), nullable=True)
    payee_merchant_id = Column(String(128), nullable=True)
    tracking_number = Column(String(128), nullable=True)
    carrier = Column(String(64), nullable=True)
    tracking_status = Column(String(32), nullable=False, default="PENDING")  # PENDING | SHIPPED
    paypal_tracker_synced = Column(Boolean, default=False, nullable=False)
    paypal_tracking_response_json = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="PAID")  # PAID | SHIPPED
    created_at = Column(DateTime, default=utcnow, nullable=False)
    shipped_at = Column(DateTime, nullable=True)

    shop = relationship("Shop", back_populates="orders")


class BotMessage(Base):
    """Telegram & Web Simulator conversation log with interactive inline buttons."""

    __tablename__ = "bot_messages"

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    sender = Column(String(32), nullable=False)  # seller | bot | system
    message_type = Column(String(32), nullable=False, default="text")  # text | voice_photo | draft_card | order_push | tracking_confirm | error
    text = Column(Text, nullable=False)
    media_json = Column(Text, nullable=False, default="[]")
    buttons_json = Column(Text, nullable=False, default="[]")
    draft_id = Column(Integer, nullable=True, index=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    shop = relationship("Shop", back_populates="messages")

    @property
    def media(self) -> list[dict]:
        try:
            return json.loads(self.media_json or "[]")
        except Exception:
            return []

    @property
    def buttons(self) -> list[dict]:
        try:
            return json.loads(self.buttons_json or "[]")
        except Exception:
            return []

    @property
    def meta(self) -> dict:
        try:
            return json.loads(self.metadata_json or "{}")
        except Exception:
            return {}
