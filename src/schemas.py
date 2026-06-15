from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl, field_validator

class ProductCreate(BaseModel):
    url: str = Field(..., description="E-commerce product URL from Amazon, Flipkart, Myntra, or Meesho")
    target_price: Optional[float] = Field(None, ge=0, description="Optional target price alert threshold in INR")
    telegram_chat_id: str = Field(..., description="Telegram Chat ID to receive alerts")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v_lower = v.lower()
        allowed_domains = ["amazon.in", "amazon.com", "amazon.co", "amzn.in", "amzn.to", "flipkart.com", "fkrt.it", "myntra.com", "meesho.com"]
        if not any(domain in v_lower for domain in allowed_domains):
            raise ValueError("URL must belong to Amazon.in, Flipkart.com, Myntra.com, or Meesho.com")
        return v

class ProductResponse(BaseModel):
    id: int
    url: str
    platform: str
    title: str
    target_price: Optional[float]
    last_scraped_price: Optional[float]
    is_in_stock: bool
    telegram_chat_id: str
    last_checked_at: Optional[datetime]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True

class ScrapeTriggerResponse(BaseModel):
    total_checked: int
    success_count: int
    failed_count: int

class ProductUpdateTargetPrice(BaseModel):
    target_price: Optional[float] = Field(None, ge=0, description="New target price alert threshold in INR")
