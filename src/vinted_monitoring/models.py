from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=200, pattern=r"\S")
    marketplace: Literal["IT"] = "IT"
    min_price: Decimal | None = Field(default=None, ge=0, le=1000000, decimal_places=2)
    max_price: Decimal | None = Field(default=None, ge=0, le=1000000, decimal_places=2)
    currency: Literal["EUR"] = "EUR"
    order: Literal["newest_first", "relevance", "price_low_to_high", "price_high_to_low"] = (
        "newest_first"
    )

    @model_validator(mode="after")
    def price_range(self):
        if self.min_price is not None and self.max_price is not None:
            if self.min_price > self.max_price:
                raise ValueError("min_price must not exceed max_price")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VM_", extra="forbid", hide_input_in_errors=True)
    database: str = Field(default="data/monitor.sqlite3", min_length=1)
    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "vinted-monitoring:8000"]
    http_token: SecretStr | None = None
    telegram_token: SecretStr | None = None
    telegram_chat_id: SecretStr | None = None
    page_size: int = Field(default=50, ge=1, le=96)
    max_pages: int = Field(default=5, ge=1, le=20)
    request_timeout: float = Field(default=15, ge=1, le=60)
    request_attempts: int = Field(default=3, ge=1, le=5)
    scan_timeout: float = Field(default=300, ge=5, le=1800)
    tick_seconds: float = Field(default=5, ge=0.1, le=60)
    failure_alert_seconds: int = Field(default=3600, ge=60)

    @model_validator(mode="after")
    def credentials(self):
        if self.database == ":memory:":
            raise ValueError("persistent SQLite file required")
        if bool(self.telegram_token) != bool(self.telegram_chat_id):
            raise ValueError("configure both Telegram variables or neither")
        if self.transport == "http" and (
            self.http_token is None or len(self.http_token.get_secret_value()) < 32
        ):
            raise ValueError("HTTP requires VM_HTTP_TOKEN with at least 32 characters")
        if self.http_token:
            value = self.http_token.get_secret_value()
            if not value.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in value):
                raise ValueError("HTTP token must be printable ASCII without whitespace")
        if self.telegram_token:
            import re

            if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", self.telegram_token.get_secret_value()):
                raise ValueError("invalid Telegram bot token format")
            if not self.telegram_chat_id.get_secret_value().strip():
                raise ValueError("empty Telegram destination")
        return self


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[1-9][0-9]*$")
    title: str = Field(min_length=1, max_length=1000)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: Literal["EUR"]
    url: str

    @model_validator(mode="after")
    def safe_url(self):
        from urllib.parse import urlsplit

        url = urlsplit(self.url)
        if (
            url.scheme != "https"
            or url.netloc != "www.vinted.it"
            or not (url.path == f"/items/{self.id}" or url.path.startswith(f"/items/{self.id}-"))
            or url.query
            or url.fragment
        ):
            raise ValueError("invalid item URL")
        return self
