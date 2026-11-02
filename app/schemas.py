from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class StrictProductAttributes(BaseModel):
    """Zero Hallucination Policy: Only explicitly stated facts are populated; everything else is None."""

    brand: Optional[str] = Field(default=None, description="Explicitly stated brand name or null")
    material: Optional[str] = Field(default=None, description="Explicitly stated fabric/material or null")
    country_of_origin: Optional[str] = Field(default=None, description="Explicitly stated country of origin or null")
    care_instructions: Optional[str] = Field(default=None, description="Explicitly stated care instructions or null")


class ExtractedVariant(BaseModel):
    size: str = Field(description="Size label, e.g., S, M, L, XL, 42, 43, One Size")
    stock_quantity: int = Field(default=1, ge=0, description="Stock quantity for this variant")


class ExtractedProductPayload(BaseModel):
    title: str = Field(description="English or international storefront title, e.g., Green Soft Hoodie")
    title_uk: str = Field(description="Ukrainian product title, e.g., Зелена м'яка кофта")
    description: str = Field(description="Concise factual product description based strictly on voice/text and photo")
    category: str = Field(default="Одяг", description="Product category, e.g., Кофти та худі, Літні сукні, Взуття")
    price: float = Field(default=0.0, ge=0.0, description="Numeric price (0.0 if not specified by seller)")
    detected_currency: str = Field(default="USD", description="Detected currency in seller's input: USD, UAH, EUR")
    original_spoken_price: Optional[float] = Field(default=None, description="Raw spoken amount before currency conversion")
    variants: list[ExtractedVariant] = Field(default_factory=list)
    attributes: StrictProductAttributes = Field(default_factory=StrictProductAttributes)


class BulkEditMutation(BaseModel):
    product_id: int
    product_title: str
    change_type: Literal["PRICE_CHANGE", "VARIANT_STOCK_CHANGE", "PRICE_AND_STOCK_CHANGE", "EXCLUDED_NO_CHANGE"]
    old_price: float
    new_price: float
    variant_changes: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class FulfillmentExtraction(BaseModel):
    order_id: int
    tracking_number: str
    carrier: str = "NOVA_POSHTA"
    carrier_name_other: str = "Nova Poshta"


class AIIntentResult(BaseModel):
    intent: Literal["CREATE_PRODUCT", "BULK_EDIT", "FULFILLMENT_TRACKING", "NON_COMMERCIAL"]
    confidence: float = 0.98
    reasoning: str = ""
    transcription: Optional[str] = None
    product_data: Optional[ExtractedProductPayload] = None
    bulk_mutations: list[BulkEditMutation] = Field(default_factory=list)
    fulfillment_data: Optional[FulfillmentExtraction] = None
    error_message: Optional[str] = None


class BotInputRequest(BaseModel):
    shop_id: int = 123
    text: str = ""
    voice_transcript: Optional[str] = None
    image_urls: list[str] = Field(default_factory=list)
    scenario_preset: Optional[str] = None


class DraftActionRequest(BaseModel):
    shop_id: int = 123
    draft_id: int
    action: Literal["PUBLISH", "CANCEL", "SET_CURRENCY_USD", "SET_CURRENCY_UAH", "SET_PRICE"]
    custom_price: Optional[float] = None


class CheckoutReserveRequest(BaseModel):
    shop_id: int = 123
    product_id: int
    variant_id: int
    buyer_session_id: str
    buyer_label: str = "Покупець А"


class CheckoutCaptureRequest(BaseModel):
    shop_id: int = 123
    reservation_id: int
    paypal_order_id: Optional[str] = None
    buyer_email: str = "buyer@example.com"
    buyer_name: str = "Alex Buyer"


class CheckoutCancelRequest(BaseModel):
    reservation_id: int
    reason: str = "Buyer cancelled PayPal window"


class RuntimeConfigUpdateRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    paypal_client_id: Optional[str] = None
    paypal_client_secret: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    shop_currency: Optional[str] = None
    paypal_merchant_id: Optional[str] = None
