import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings with runtime override support for Hackathon Demo UI."""

    def __init__(self) -> None:
        self.app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./ias_storefront.db")
        self.default_shop_id: int = int(os.getenv("DEFAULT_SHOP_ID", "123"))
        self.preview_token_ttl_minutes: int = int(os.getenv("PREVIEW_TOKEN_TTL_MINUTES", "15"))
        self.reservation_ttl_minutes: int = int(os.getenv("RESERVATION_TTL_MINUTES", "15"))
        self.default_uah_to_usd_rate: float = float(os.getenv("DEFAULT_UAH_TO_USD_RATE", "40.0"))

        # Google Gemini Multimodal AI
        self.gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

        # PayPal Developer Sandbox
        self.paypal_mode: str = os.getenv("PAYPAL_MODE", "sandbox").strip()
        self.paypal_client_id: str = os.getenv("PAYPAL_CLIENT_ID", "").strip()
        self.paypal_client_secret: str = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
        self.paypal_partner_merchant_id: str = os.getenv(
            "PAYPAL_PARTNER_MERCHANT_ID", "IAS_PARTNER_PLATFORM_HQ"
        ).strip()

        # Telegram Bot
        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    @property
    def paypal_api_base(self) -> str:
        if self.paypal_mode.lower() == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @property
    def has_real_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_real_paypal(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def has_real_telegram(self) -> bool:
        return bool(self.telegram_bot_token)


settings = Settings()
