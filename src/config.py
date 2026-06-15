import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Telegram settings
    TELEGRAM_BOT_TOKEN: str
    
    # Database settings (support sqlite+aiosqlite by default for local development, postgresql+asyncpg for production)
    DATABASE_URL: str = "sqlite+aiosqlite:///./tracker.db"
    
    # Security setting for triggering scraper API endpoint
    API_KEY: Optional[str] = None
    
    # Scheduler settings
    RUN_LOCAL_SCHEDULER: bool = False
    SCRAPE_INTERVAL_MINUTES: int = 15
    
    # Playwright headless setting
    PLAYWRIGHT_HEADLESS: bool = True
    PLAYWRIGHT_TIMEOUT_MS: int = 30000  # 30 seconds
    
    # Allow loading from environment variables or .env file
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instantiate settings globally
settings = Settings()
