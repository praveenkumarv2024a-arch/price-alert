from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from src.database import Base

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True, nullable=False)
    platform = Column(String, nullable=False)
    title = Column(String, nullable=False)
    target_price = Column(Float, nullable=True)
    last_scraped_price = Column(Float, nullable=True)
    is_in_stock = Column(Boolean, default=True, nullable=False)
    telegram_chat_id = Column(String, nullable=False)
    last_checked_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
