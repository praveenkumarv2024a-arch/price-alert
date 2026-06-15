import logging
from contextlib import asynccontextmanager
from typing import List, Optional
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, Header, status, BackgroundTasks
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from src.config import settings
from src.database import engine, Base, get_db, AsyncSessionLocal
from src.models import Product
from src.schemas import ProductCreate, ProductResponse, ScrapeTriggerResponse, ProductUpdateTargetPrice
from src.scraper import get_platform_from_url, scrape_product
from src.alerts import check_and_alert_product
from src.scheduler import start_scheduler, shutdown_scheduler, run_scraper_job

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup operations
    logger.info("Initializing database and tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Database migration: Check if created_at column exists in products table
        # Since SQLAlchemy metadata.create_all does not add new columns to existing tables.
        try:
            result = await conn.execute(text("PRAGMA table_info(products);"))
            columns = [row[1] for row in result.fetchall()]
            if "created_at" not in columns:
                logger.info("Database migration: Adding 'created_at' column to 'products' table...")
                await conn.execute(text("ALTER TABLE products ADD COLUMN created_at DATETIME;"))
                await conn.execute(text("UPDATE products SET created_at = last_checked_at WHERE created_at IS NULL;"))
                await conn.execute(text("UPDATE products SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL;"))
                logger.info("Database migration complete.")
        except Exception as e:
            logger.error(f"Error during database migration: {e}")
        
    if settings.RUN_LOCAL_SCHEDULER:
        start_scheduler()
        
    yield
    
    # Shutdown operations
    if settings.RUN_LOCAL_SCHEDULER:
        shutdown_scheduler()

app = FastAPI(
    title="E-Commerce Price Tracker API",
    description="Track prices and inventory on Amazon, Flipkart, Myntra, and Meesho",
    version="1.0.0",
    lifespan=lifespan
)

# API Security Verification
async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if settings.API_KEY and x_api_key != settings.API_KEY:
        logger.warning("Failed API Key authentication attempt.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key in X-API-Key header"
        )

# API: Create product
async def background_scrape_new_product(product_id: int):
    async with AsyncSessionLocal() as session:
        stmt = select(Product).where(Product.id == product_id)
        res = await session.execute(stmt)
        db_product = res.scalar_one_or_none()
        if not db_product:
            return
            
        logger.info(f"Triggering background scrape for new product: {db_product.url}")
        scraped_data = await scrape_product(db_product.url)
        
        if scraped_data.get("success"):
            db_product.title = scraped_data["title"]
            db_product.last_scraped_price = scraped_data["price"]
            db_product.is_in_stock = scraped_data["is_in_stock"]
        else:
            logger.warning(f"Initial background scrape failed for {db_product.url}: {scraped_data.get('error')}")
            db_product.title = f"{db_product.platform.capitalize()} Product"
            
        await session.commit()


@app.post("/api/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(product_in: ProductCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    # Check if product already exists
    stmt = select(Product).where(Product.url == product_in.url)
    res = await db.execute(stmt)
    existing_product = res.scalar_one_or_none()
    
    if existing_product:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This product URL is already being tracked."
        )
        
    try:
        platform = get_platform_from_url(product_in.url)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        
    # Create the Product record
    db_product = Product(
        url=product_in.url,
        platform=platform,
        title="Pending Scrape...",
        target_price=product_in.target_price,
        is_in_stock=True,
        telegram_chat_id=product_in.telegram_chat_id,
        last_checked_at=datetime.utcnow(),
        created_at=datetime.utcnow()
    )
    
    db.add(db_product)
    await db.commit()
    await db.refresh(db_product)
    
    background_tasks.add_task(background_scrape_new_product, db_product.id)
    
    return db_product
# API: List products
@app.get("/api/products", response_model=List[ProductResponse])
async def list_products(db: AsyncSession = Depends(get_db)):
    stmt = select(Product).order_by(Product.id.desc())
    res = await db.execute(stmt)
    return res.scalars().all()

# API: Get single product
@app.get("/api/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Product).where(Product.id == product_id)
    res = await db.execute(stmt)
    product = res.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product

# API: Delete product
@app.delete("/api/products/{product_id}", status_code=status.HTTP_200_OK)
async def delete_product(product_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Product).where(Product.id == product_id)
    res = await db.execute(stmt)
    product = res.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
        
    await db.delete(product)
    await db.commit()
    return {"detail": "Product tracking deleted successfully"}

# API: Update target price
@app.patch("/api/products/{product_id}/target-price", response_model=ProductResponse)
async def update_target_price(product_id: int, payload: ProductUpdateTargetPrice, db: AsyncSession = Depends(get_db)):
    stmt = select(Product).where(Product.id == product_id)
    res = await db.execute(stmt)
    product = res.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
        
    product.target_price = payload.target_price
    db.add(product)
    await db.commit()
    await db.refresh(product)
    
    # Check if target price alert triggers immediately based on the last scraped price
    if product.last_scraped_price is not None:
        scraped_data = {
            "success": True,
            "price": product.last_scraped_price,
            "is_in_stock": product.is_in_stock,
            "title": product.title
        }
        await check_and_alert_product(db, product, scraped_data)
        
    return product

# API: Trigger scrape
@app.post("/api/scrape", response_model=ScrapeTriggerResponse, dependencies=[Depends(verify_api_key)])
async def trigger_scrape():
    """
    Manually triggers the background worker to scrape all pages.
    Can be configured securely via Google Cloud Scheduler.
    """
    result = await run_scraper_job(AsyncSessionLocal)
    return result

from pydantic import BaseModel

class TestNotificationRequest(BaseModel):
    telegram_chat_id: str

@app.post("/api/test-notification", status_code=status.HTTP_200_OK)
async def test_notification(payload: TestNotificationRequest):
    from src.notifier import send_telegram_notification
    msg = (
        "🔔 *PriceGuard Connection Test\\!*\n\n"
        "Your Telegram notifications are successfully connected to this Chat ID\\.\n"
        "Live tracking alerts will be delivered here\\."
    )
    success = await send_telegram_notification(payload.telegram_chat_id, msg)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to send Telegram test message. Verify Bot Token and Chat ID."
        )
    return {"detail": "Test notification sent successfully"}

# Minimalist Elegant Dashboard
@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PriceGuard AI - Premium E-Commerce Price Tracker</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        html {
            font-size: 19px;
        }

        @media (max-width: 768px) {
            html {
                font-size: 16px;
            }
        }

        :root {
            --bg-primary: #070913;
            --bg-secondary: #0c0f24;
            --bg-card: rgba(13, 17, 34, 0.45);
            --bg-card-hover: rgba(20, 26, 51, 0.55);
            --border-color: rgba(99, 102, 241, 0.08);
            --border-hover: rgba(99, 102, 241, 0.25);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent-primary: #6366f1;
            --accent-secondary: #8b5cf6;
            --accent-tertiary: #d946ef;
            --accent-gradient: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #d946ef 100%);
            --accent-glow: 0 0 30px rgba(99, 102, 241, 0.2);
            --success: #10b981;
            --success-glow: 0 0 20px rgba(16, 185, 129, 0.15);
            --danger: #ef4444;
            --danger-glow: 0 0 20px rgba(239, 68, 68, 0.15);
            --warning: #f59e0b;
            --warning-glow: 0 0 20px rgba(245, 158, 11, 0.15);
            --radius-sm: 10px;
            --radius-md: 16px;
            --radius-lg: 24px;
            --font-sans: 'Outfit', sans-serif;
            --font-display: 'Plus Jakarta Sans', sans-serif;
            --transition-fast: 0.15s ease;
            --transition-normal: 0.25s ease;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: var(--font-sans);
        }

        body {
            background-color: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            padding: 2.5rem 1.5rem;
            position: relative;
            overflow-x: hidden;
        }

        @media (max-width: 768px) {
            body {
                padding: 1.5rem 1rem;
            }
        }

        /* Decorative glowing orbs in background */
        .bg-glow {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: -1;
            overflow: hidden;
            pointer-events: none;
        }

        .orb {
            position: absolute;
            border-radius: 50%;
            filter: blur(130px);
            opacity: 0.45;
            mix-blend-mode: screen;
            animation: float 25s infinite alternate ease-in-out;
        }

        .orb-1 {
            top: -10%;
            left: 10%;
            width: 500px;
            height: 500px;
            background: radial-gradient(circle, var(--accent-primary) 0%, transparent 70%);
            animation-duration: 25s;
        }

        .orb-2 {
            bottom: -10%;
            right: 10%;
            width: 600px;
            height: 600px;
            background: radial-gradient(circle, var(--accent-secondary) 0%, transparent 70%);
            animation-duration: 32s;
        }

        .orb-3 {
            top: 35%;
            left: 55%;
            width: 450px;
            height: 450px;
            background: radial-gradient(circle, var(--accent-tertiary) 0%, transparent 70%);
            animation-duration: 20s;
        }

        @keyframes float {
            0% { transform: translate(0, 0) scale(1); }
            50% { transform: translate(6%, 8%) scale(1.1); }
            100% { transform: translate(-4%, -6%) scale(0.9); }
        }

        .container {
            max-width: 1140px;
            margin: 0 auto;
            width: 100%;
            z-index: 10;
        }

        /* Header bar */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2.5rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--border-color);
        }

        @media (max-width: 650px) {
            header {
                flex-direction: column;
                gap: 1.25rem;
                text-align: center;
                margin-bottom: 2rem;
            }
        }

        .logo {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            font-family: var(--font-display);
            font-size: 1.8rem;
            font-weight: 800;
            color: var(--text-primary);
        }

        .logo-icon-wrapper {
            background: var(--accent-gradient);
            width: 2.2rem;
            height: 2.2rem;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #ffffff;
            box-shadow: var(--accent-glow);
        }

        .logo span {
            background: var(--accent-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .header-actions {
            display: flex;
            align-items: center;
            gap: 0.8rem;
        }

        @media (max-width: 650px) {
            .header-actions {
                width: 100%;
                justify-content: center;
            }
        }

        /* General Card & Panels styling */
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-lg);
            padding: 2rem;
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            box-shadow: 0 20px 40px -15px rgba(0, 0, 0, 0.5);
            transition: border-color var(--transition-normal), box-shadow var(--transition-normal);
        }

        .card:hover {
            border-color: var(--border-hover);
        }

        /* Stats Bar Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 1.5rem;
            margin-bottom: 2.5rem;
        }

        @media (max-width: 768px) {
            .stats-grid {
                gap: 1rem;
            }
        }

        @media (max-width: 580px) {
            .stats-grid {
                grid-template-columns: 1fr;
                gap: 0.75rem;
            }
        }

        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 1.25rem 1.5rem;
            display: flex;
            align-items: center;
            gap: 1.25rem;
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            transition: transform var(--transition-normal), border-color var(--transition-normal);
        }

        .stat-card:hover {
            transform: translateY(-2px);
            border-color: var(--border-hover);
        }

        .stat-icon-wrapper {
            background: rgba(99, 102, 241, 0.1);
            border: 1px solid rgba(99, 102, 241, 0.15);
            color: var(--accent-primary);
            width: 2.5rem;
            height: 2.5rem;
            border-radius: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }

        .stat-card:nth-child(2) .stat-icon-wrapper {
            background: rgba(239, 68, 68, 0.1);
            border-color: rgba(239, 68, 68, 0.15);
            color: var(--danger);
        }

        .stat-card:nth-child(3) .stat-icon-wrapper {
            background: rgba(16, 185, 129, 0.1);
            border-color: rgba(16, 185, 129, 0.15);
            color: var(--success);
        }

        .stat-info {
            display: flex;
            flex-direction: column;
        }

        .stat-value {
            font-size: 1.7rem;
            font-weight: 800;
            line-height: 1.1;
            font-family: var(--font-display);
            color: var(--text-primary);
        }

        .stat-label {
            font-size: 0.8rem;
            color: var(--text-secondary);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 2px;
        }

        /* Two-column layout grid */
        .dashboard-grid {
            display: grid;
            grid-template-columns: 19rem 1fr;
            gap: 2rem;
            align-items: start;
        }

        @media (max-width: 992px) {
            .dashboard-grid {
                grid-template-columns: 1fr;
                gap: 2rem;
            }
        }

        .sticky-card {
            position: sticky;
            top: 2rem;
        }

        @media (max-width: 992px) {
            .sticky-card {
                position: static;
            }
        }

        /* Form Components */
        .form-title {
            font-size: 1.25rem;
            font-family: var(--font-display);
            font-weight: 700;
            margin-bottom: 1.8rem;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .input-group {
            margin-bottom: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .input-label {
            font-size: 0.8rem;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .input-control-wrapper {
            position: relative;
            display: flex;
            align-items: center;
        }

        .input-icon-left {
            position: absolute;
            left: 1.1rem;
            color: var(--text-muted);
            pointer-events: none;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .input-control {
            width: 100%;
            background: rgba(8, 10, 20, 0.55);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 0.9rem 1.1rem;
            padding-left: 2.8rem;
            color: var(--text-primary);
            font-size: 0.95rem;
            font-family: var(--font-sans);
            transition: border-color var(--transition-fast), box-shadow var(--transition-fast), background var(--transition-fast);
        }

        .input-control:focus {
            outline: none;
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
            background: rgba(8, 10, 20, 0.8);
        }

        .input-control::placeholder {
            color: var(--text-muted);
        }

        /* Buttons Styling */
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.6rem;
            padding: 0.85rem 1.4rem;
            font-family: var(--font-display);
            font-size: 0.95rem;
            font-weight: 600;
            border-radius: var(--radius-md);
            border: none;
            cursor: pointer;
            transition: transform var(--transition-fast), box-shadow var(--transition-fast), filter var(--transition-fast);
        }

        .btn-primary {
            background: var(--accent-gradient);
            color: #ffffff;
            box-shadow: var(--accent-glow);
        }

        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 0 30px rgba(99, 102, 241, 0.45);
        }

        .btn-primary:active {
            transform: translateY(0);
        }

        .btn-primary:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none !important;
            box-shadow: none !important;
        }

        .btn-secondary {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }

        .btn-secondary:hover {
            background: rgba(99, 102, 241, 0.12);
            border-color: var(--accent-primary);
            transform: translateY(-2px);
        }

        .btn-secondary:active {
            transform: translateY(0);
        }

        /* Listings Layout */
        .products-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.8rem;
        }

        .products-header h2 {
            font-size: 1.25rem;
            font-family: var(--font-display);
            font-weight: 700;
        }

        .product-count-badge {
            background: rgba(99, 102, 241, 0.1);
            border: 1px solid rgba(99, 102, 241, 0.15);
            color: #a5b4fc;
            padding: 0.35rem 0.8rem;
            border-radius: 999px;
            font-size: 0.8rem;
            font-weight: 700;
        }

        .products-list-container {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        /* Product Cards */
        .product-card {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: rgba(15, 23, 42, 0.25);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 1.25rem 1.5rem;
            gap: 1.5rem;
            transition: transform var(--transition-normal), border-color var(--transition-normal), box-shadow var(--transition-normal), background var(--transition-normal);
        }

        .product-card:hover {
            transform: translateY(-2px);
            border-color: var(--border-hover);
            background: rgba(15, 23, 42, 0.4);
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.35);
        }

        @media (max-width: 768px) {
            .product-card {
                flex-direction: column;
                align-items: stretch;
                gap: 1.25rem;
            }
        }

        .product-title {
            font-weight: 600;
            font-size: 1.05rem;
            margin-bottom: 0.5rem;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: normal;
            word-break: break-word;
            line-height: 1.4;
            color: var(--text-primary);
        }

        .product-meta {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 0.6rem;
            font-size: 0.8rem;
            color: var(--text-secondary);
        }

        .check-time {
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
            color: var(--text-muted);
            font-weight: 500;
        }

        /* Platform Badges */
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 0.37rem;
            padding: 0.3rem 0.6rem;
            border-radius: 6px;
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }

        .platform-amazon {
            background: linear-gradient(135deg, rgba(255, 153, 0, 0.12) 0%, rgba(255, 85, 0, 0.12) 100%);
            border: 1px solid rgba(255, 153, 0, 0.25);
            color: #ffb74d;
        }

        .platform-flipkart {
            background: linear-gradient(135deg, rgba(40, 116, 240, 0.12) 0%, rgba(0, 75, 160, 0.12) 100%);
            border: 1px solid rgba(40, 116, 240, 0.25);
            color: #60a5fa;
        }

        .platform-myntra {
            background: linear-gradient(135deg, rgba(255, 63, 108, 0.12) 0%, rgba(209, 18, 63, 0.12) 100%);
            border: 1px solid rgba(255, 63, 108, 0.25);
            color: #f472b6;
        }

        .platform-meesho {
            background: linear-gradient(135deg, rgba(147, 51, 234, 0.12) 0%, rgba(104, 16, 179, 0.12) 100%);
            border: 1px solid rgba(147, 51, 234, 0.25);
            color: #c084fc;
        }

        /* Stock Status Pill badges */
        .badge-success {
            background: rgba(16, 185, 129, 0.08);
            border: 1px solid rgba(16, 185, 129, 0.2);
            color: var(--success);
        }

        .badge-danger {
            background: rgba(239, 68, 68, 0.08);
            border: 1px solid rgba(239, 68, 68, 0.2);
            color: var(--danger);
        }

        .badge-pulse-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background-color: var(--success);
            box-shadow: var(--success-glow);
            animation: pulse 1.8s infinite;
        }

        .badge-danger .badge-pulse-dot {
            background-color: var(--danger);
            box-shadow: var(--danger-glow);
            animation: none;
        }

        @keyframes pulse {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.5); }
            70% { transform: scale(1); box-shadow: 0 0 0 5px rgba(16, 185, 129, 0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }

        /* Price display columns */
        .price-section {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            text-align: right;
            min-width: 140px;
        }

        @media (max-width: 768px) {
            .price-section {
                flex-direction: row;
                align-items: center;
                justify-content: space-between;
                text-align: left;
                width: 100%;
                border-top: 1px solid rgba(255, 255, 255, 0.05);
                padding-top: 0.75rem;
            }
        }

        .price-live {
            font-size: 1.35rem;
            font-family: var(--font-display);
            font-weight: 800;
            color: var(--text-primary);
        }

        .price-target {
            font-size: 0.8rem;
            font-weight: 500;
            color: var(--text-secondary);
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
            margin-top: 2px;
        }

        .price-target.hit {
            color: var(--success);
            font-weight: 700;
            text-shadow: var(--success-glow);
        }

        .price-target.hit svg {
            filter: drop-shadow(var(--success-glow));
        }

        /* Proximity progress bar */
        .proximity-container {
            display: flex;
            flex-direction: column;
            gap: 0.3rem;
            width: 100%;
            max-width: 280px;
            margin-top: 0.75rem;
        }

        @media (max-width: 768px) {
            .proximity-container {
                max-width: 100%;
            }
        }

        .proximity-bar-bg {
            width: 100%;
            height: 6px;
            background: rgba(255, 255, 255, 0.06);
            border-radius: 3px;
            overflow: hidden;
        }

        .proximity-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--accent-primary) 0%, var(--accent-secondary) 100%);
            border-radius: 3px;
            transition: width var(--transition-normal);
        }

        .proximity-text {
            font-size: 0.75rem;
            font-weight: 600;
            color: var(--text-secondary);
        }

        /* Quick Action buttons */
        .actions-section {
            display: flex;
            align-items: center;
            gap: 0.6rem;
        }

        @media (max-width: 768px) {
            .actions-section {
                justify-content: flex-end;
                width: 100%;
                border-top: 1px solid rgba(255, 255, 255, 0.05);
                padding-top: 0.75rem;
            }
        }

        .action-btn {
            width: 2.1rem;
            height: 2.1rem;
            border-radius: var(--radius-sm);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: transform var(--transition-fast), background var(--transition-fast), border-color var(--transition-fast);
            text-decoration: none;
        }

        .action-btn:active {
            transform: scale(0.95);
        }

        .action-btn-link {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
        }

        .action-btn-link:hover {
            background: rgba(99, 102, 241, 0.12);
            border-color: var(--accent-primary);
            color: var(--text-primary);
            transform: translateY(-2px);
        }

        .action-btn-delete {
            background: rgba(239, 68, 68, 0.04);
            border: 1px solid rgba(239, 68, 68, 0.15);
            color: var(--danger);
        }

        .action-btn-delete:hover {
            background: rgba(239, 68, 68, 0.18);
            border-color: var(--danger);
            color: #ffffff;
            transform: translateY(-2px);
        }

        /* No Listings state */
        .no-products {
            text-align: center;
            padding: 4rem 2rem;
            color: var(--text-secondary);
        }

        .no-products-icon {
            color: var(--text-muted);
            margin-bottom: 1.2rem;
            display: flex;
            justify-content: center;
        }

        .no-products p {
            font-size: 1.05rem;
            font-weight: 500;
        }

        /* Loaders & Spinners */
        .loader {
            width: 18px;
            height: 18px;
            border: 2px solid rgba(255, 255, 255, 0.25);
            border-top-color: currentColor;
            border-radius: 50%;
            animation: spin 0.6s linear infinite;
            display: inline-block;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        /* Toast system */
        .toast {
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            padding: 0.95rem 1.4rem;
            border-radius: var(--radius-md);
            color: white;
            font-weight: 600;
            font-size: 0.95rem;
            z-index: 3000;
            display: flex;
            align-items: center;
            gap: 0.6rem;
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.4);
            transform: translateY(100px);
            opacity: 0;
            pointer-events: none;
            transition: transform 0.35s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.35s ease;
        }

        @media (max-width: 600px) {
            .toast {
                left: 1rem;
                right: 1rem;
                bottom: 1.5rem;
            }
        }

        .toast.show {
            transform: translateY(0);
            opacity: 1;
            pointer-events: auto;
        }

        .toast-success {
            background: #10b981;
            border: 1px solid rgba(255, 255, 255, 0.15);
        }

        .toast-error {
            background: #ef4444;
            border: 1px solid rgba(255, 255, 255, 0.15);
        }

        /* Settings Dialog / Modal overlay */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(4, 6, 12, 0.85);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 2000;
            opacity: 0;
            pointer-events: none;
            transition: opacity var(--transition-normal);
            padding: 1.25rem;
        }

        .modal-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }

        .modal-content {
            width: 100%;
            max-width: 440px;
            background: var(--bg-secondary);
            border: 1px solid var(--border-hover);
            border-radius: var(--radius-lg);
            padding: 2.2rem;
            transform: scale(0.92);
            transition: transform var(--transition-normal);
            box-shadow: 0 35px 60px -15px rgba(0, 0, 0, 0.8);
            position: relative;
        }

        .modal-overlay.active .modal-content {
            transform: scale(1);
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.8rem;
        }

        .modal-title {
            font-size: 1.25rem;
            font-family: var(--font-display);
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .accent-icon {
            color: var(--accent-primary);
        }

        .close-btn {
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 1.2rem;
            cursor: pointer;
            width: 1.7rem;
            height: 1.7rem;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background var(--transition-fast), color var(--transition-fast);
        }

        .close-btn:hover {
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-primary);
        }
    </style>
</head>
<body>
    <!-- Background Ambient Glow spots -->
    <div class="bg-glow">
        <div class="orb orb-1"></div>
        <div class="orb orb-2"></div>
        <div class="orb orb-3"></div>
    </div>

    <div class="container">
        <!-- Header bar -->
        <header>
            <div class="logo">
                <div class="logo-icon-wrapper">
                    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>
                </div>
                <span>PriceGuard AI</span>
            </div>
            <div class="header-actions">
                <button class="btn btn-secondary" onclick="toggleSettingsModal()" style="padding: 0.7rem 1.1rem; font-size: 0.9rem;">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.1a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
                    Settings
                </button>
                <button id="global-refresh-btn" class="btn btn-primary" onclick="triggerGlobalScrape()" style="padding: 0.7rem 1.1rem; font-size: 0.9rem;">
                    <span class="loader" id="refresh-loader" style="display: none;"></span>
                    <span id="refresh-text" style="display: inline-flex; align-items: center; gap: 0.4rem;">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 3h5v5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 21H3v-5"/></svg>
                        Scan Live
                    </span>
                </button>
            </div>
        </header>

        <!-- Stats Grid Dashboard -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-icon-wrapper">
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><polygon points="12 22.08 12 12 3 6.92 3 17.08 12 22.08"/><polygon points="12 12 21 6.92 21 17.08 12 22.08"/><polygon points="12 2 21 6.92 12 12 3 6.92 12 2"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
                </div>
                <div class="stat-info">
                    <div class="stat-value" id="stat-tracked">0</div>
                    <div class="stat-label">Total Tracked</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon-wrapper">
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                </div>
                <div class="stat-info">
                    <div class="stat-value" id="stat-out-of-stock">0</div>
                    <div class="stat-label">Out of Stock</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon-wrapper">
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/></svg>
                </div>
                <div class="stat-info">
                    <div class="stat-value" id="stat-hits">0</div>
                    <div class="stat-label">Target Hits</div>
                </div>
            </div>
        </div>

        <!-- Layout Grid -->
        <div class="dashboard-grid">
            <!-- Left: Add form -->
            <div class="card sticky-card">
                <div class="form-title">
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="accent-icon"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                    Track New Product
                </div>
                <form id="add-product-form" onsubmit="addProduct(event)">
                    <div class="input-group">
                        <label for="url" class="input-label">Product URL</label>
                        <div class="input-control-wrapper">
                            <span class="input-icon-left">
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
                            </span>
                            <input type="url" id="url" class="input-control" required placeholder="Amazon, Flipkart, Myntra, or Meesho link">
                        </div>
                    </div>
                    <div class="input-group">
                        <label for="target_price" class="input-label">Target Price (₹, Optional)</label>
                        <div class="input-control-wrapper">
                            <span class="input-icon-left">
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                            </span>
                            <input type="number" id="target_price" class="input-control" min="0" step="0.01" placeholder="Alert target, e.g. 999">
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary" id="submit-btn" style="width: 100%; margin-top: 0.5rem; height: 48px;">
                        <span class="loader" id="submit-loader" style="display: none;"></span>
                        <span id="submit-text" style="display: inline-flex; align-items: center; gap: 0.5rem;">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="22 11.08 12 19 2 11.08"/><polyline points="22 4 12 12 2 4"/></svg>
                            Start Tracking
                        </span>
                    </button>
                </form>
            </div>

            <!-- Right: Product list -->
            <div class="card">
                <div class="products-header">
                    <h2>Tracked Listings</h2>
                    <span class="product-count-badge" id="product-count">0 Products</span>
                </div>
                <div class="products-list-container" id="products-list">
                    <div class="no-products" id="no-products-placeholder">
                        <div class="no-products-icon">
                            <svg xmlns="http://www.w3.org/2000/svg" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
                        </div>
                        <p>You aren't tracking any e-commerce products yet.</p>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Notification Toast -->
    <div id="toast" class="toast"></div>

    <!-- Settings Modal overlay -->
    <div id="settings-modal" class="modal-overlay" onclick="handleOutsideClick(event)">
        <div class="modal-content">
            <div class="modal-header">
                <h3 class="modal-title">
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="accent-icon"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.1a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
                    Global Settings
                </h3>
                <button class="close-btn" onclick="toggleSettingsModal()">✕</button>
            </div>
            <div class="input-group" style="margin-bottom: 1.5rem;">
                <label for="default_telegram_id" class="input-label">Default Telegram Chat ID</label>
                <div class="input-control-wrapper">
                    <span class="input-icon-left">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                    </span>
                    <input type="text" id="default_telegram_id" class="input-control" placeholder="e.g. 1530807112">
                </div>
            </div>
            <button onclick="saveSettings()" class="btn btn-primary" style="width: 100%; margin-bottom: 1.5rem; height: 46px;">Save Settings</button>
            
            <div style="border-top: 1px solid var(--border-color); padding-top: 1.5rem;">
                <label class="input-label" style="margin-bottom: 0.75rem; display: block;">Connection Testing</label>
                <button onclick="sendTestNotification()" id="test-notif-btn" class="btn btn-secondary" style="width: 100%; height: 46px;">
                    <span class="loader" id="test-loader" style="display: none; margin-right: 0.5rem;"></span>
                    <span id="test-text" style="display: flex; align-items: center; justify-content: center; gap: 0.5rem;">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>
                        Send Test Notification
                    </span>
                </button>
            </div>
        </div>
    </div>

    <script>
        // Toast notification system helper
        let toastTimeout;
        function showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            
            // Render specific inline SVG icon matching context
            const successIcon = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
            const errorIcon = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`;
            
            toast.innerHTML = `${type === 'success' ? successIcon : errorIcon} <span>${message}</span>`;
            toast.className = `toast toast-${type} show`;
            
            clearTimeout(toastTimeout);
            toastTimeout = setTimeout(() => {
                toast.classList.remove('show');
            }, 4200);
        }

        // Platform Badges lookup mapping
        const platformBadges = {
            amazon: '<span class="badge platform-amazon">Amazon</span>',
            flipkart: '<span class="badge platform-flipkart">Flipkart</span>',
            myntra: '<span class="badge platform-myntra">Myntra</span>',
            meesho: '<span class="badge platform-meesho">Meesho</span>'
        };

        // Fetch & render tracked items list
        async function fetchProducts() {
            try {
                const response = await fetch('/api/products');
                const products = await response.json();
                
                const listContainer = document.getElementById('products-list');
                const countBadge = document.getElementById('product-count');
                const placeholder = document.getElementById('no-products-placeholder');

                // Dynamic metric card counters
                document.getElementById('stat-tracked').textContent = products.length;
                document.getElementById('stat-out-of-stock').textContent = products.filter(p => !p.is_in_stock).length;
                document.getElementById('stat-hits').textContent = products.filter(p => p.target_price && p.last_scraped_price && p.last_scraped_price <= p.target_price).length;

                countBadge.textContent = `${products.length} Product${products.length === 1 ? '' : 's'}`;

                // Remove existing cards, keeping placeholder
                const existingCards = listContainer.querySelectorAll('.product-card');
                existingCards.forEach(card => card.remove());

                if (products.length === 0) {
                    placeholder.style.display = 'block';
                    return;
                }

                placeholder.style.display = 'none';

                products.forEach(product => {
                    const card = document.createElement('div');
                    card.className = 'product-card';
                    
                    const livePriceText = product.last_scraped_price 
                        ? `₹${product.last_scraped_price.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`
                        : 'Scraping...';
                        
                    const targetPriceText = product.target_price
                        ? `₹${product.target_price.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`
                        : 'None';
                        
                    const isTargetHit = product.target_price && product.last_scraped_price && (product.last_scraped_price <= product.target_price);
                    const targetClass = isTargetHit ? 'hit' : '';

                    const checkTime = product.last_checked_at 
                        ? new Date(product.last_checked_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})
                        : 'Never';
                        
                    let addedDateStr = product.created_at;
                    if (addedDateStr && !addedDateStr.endsWith('Z') && !addedDateStr.includes('+')) {
                        addedDateStr += 'Z';
                    }
                    const addedDate = addedDateStr ? new Date(addedDateStr) : null;
                    const formattedAddedAt = addedDate 
                        ? addedDate.toLocaleString('en-IN', {
                            timeZone: 'Asia/Kolkata',
                            day: '2-digit',
                            month: 'short',
                            year: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: true
                          })
                        : 'Never';
                        
                    const platformBadgeHtml = platformBadges[product.platform] || `<span class="badge">${product.platform}</span>`;
                    
                    const stockClass = product.is_in_stock ? 'badge-success' : 'badge-danger';
                    const stockText = product.is_in_stock ? 'In Stock' : 'Out of Stock';

                    let proximityHtml = '';
                    if (product.target_price && product.last_scraped_price) {
                        if (product.last_scraped_price <= product.target_price) {
                            proximityHtml = `
                                <div class="proximity-container">
                                    <span class="proximity-text" style="color: var(--success); display: flex; align-items: center; gap: 0.25rem;">
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" style="vertical-align:middle;"><polyline points="20 6 9 17 4 12"/></svg>
                                        Target price reached!
                                    </span>
                                </div>
                            `;
                        } else {
                            const proximity = Math.round((product.target_price / product.last_scraped_price) * 100);
                            proximityHtml = `
                                <div class="proximity-container">
                                    <div class="proximity-bar-bg">
                                        <div class="proximity-bar-fill" style="width: ${proximity}%"></div>
                                    </div>
                                    <span class="proximity-text">${proximity}% of target reached</span>
                                </div>
                            `;
                        }
                    }

                    card.innerHTML = `
                        <div class="product-info" style="flex-grow: 1; min-width: 0;">
                            <div class="product-title" title="${product.title}">${product.title}</div>
                            <div class="product-meta">
                                ${platformBadgeHtml}
                                <span class="badge ${stockClass}">
                                    <span class="badge-pulse-dot"></span>
                                    ${stockText}
                                </span>
                                <span class="check-time" title="Last Checked Time">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:middle;margin-right:2px;"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                                    ${checkTime}
                                </span>
                                <span class="check-time" title="Added at (Indian Standard Time)">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:middle;margin-right:2px;"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                                    Added: ${formattedAddedAt}
                                </span>
                            </div>
                            ${proximityHtml}
                        </div>
                        <div class="price-section">
                            <div class="price-live">${livePriceText}</div>
                            <div class="price-target ${targetClass}" id="target-display-${product.id}">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:middle;margin-right:1px;"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="2"/></svg>
                                Target: <span id="target-val-${product.id}">${targetPriceText}</span>
                                <button onclick="startEditTargetPrice(${product.id}, ${product.target_price || 0})" class="edit-target-btn" title="Edit Target Price">
                                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
                                </button>
                            </div>
                            <div class="price-target-edit" id="target-edit-${product.id}" style="display: none; align-items: center; gap: 4px; margin-top: 4px;">
                                <input type="number" id="target-input-${product.id}" class="target-edit-input" value="${product.target_price || ''}" placeholder="Alert price">
                                <button onclick="saveTargetPrice(${product.id})" class="save-target-btn" style="background: none; border: none; color: var(--success); cursor: pointer; padding: 0 4px; display: inline-flex; align-items: center;" title="Save">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                                </button>
                                <button onclick="cancelEditTargetPrice(${product.id})" class="cancel-target-btn" style="background: none; border: none; color: var(--danger); cursor: pointer; padding: 0 4px; display: inline-flex; align-items: center;" title="Cancel">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                </button>
                            </div>
                        </div>
                        <div class="actions-section">
                            <a href="${product.url}" target="_blank" class="action-btn action-btn-link" title="View Store Page">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                            </a>
                            <button onclick="deleteProduct(${product.id})" class="action-btn action-btn-delete" title="Delete Tracked Product">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                            </button>
                        </div>
                    `;
                    listContainer.appendChild(card);
                });

            } catch (error) {
                console.error('Error fetching products:', error);
                showToast('Failed to load tracked products list.', 'error');
            }
        }

        // Submit product tracking
        async function addProduct(event) {
            event.preventDefault();
            
            const telegramId = localStorage.getItem('default_telegram_id');
            if (!telegramId) {
                showToast('Save Telegram Chat ID in Settings (⚙️) first!', 'error');
                toggleSettingsModal();
                return;
            }
            
            const url = document.getElementById('url').value;
            const targetPriceInput = document.getElementById('target_price').value;

            const submitBtn = document.getElementById('submit-btn');
            const loader = document.getElementById('submit-loader');
            const text = document.getElementById('submit-text');

            submitBtn.disabled = true;
            loader.style.display = 'inline-block';
            text.style.display = 'none';

            const payload = {
                url: url,
                target_price: targetPriceInput ? parseFloat(targetPriceInput) : null,
                telegram_chat_id: telegramId
            };

            try {
                const response = await fetch('/api/products', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(payload)
                });

                const data = await response.json();

                if (response.status === 201) {
                    showToast(`Successfully registered product tracking!`);
                    document.getElementById('add-product-form').reset();
                    fetchProducts();
                } else {
                    let errorMsg = 'Failed to register product url.';
                    if (data && data.detail) {
                        if (typeof data.detail === 'string') {
                            errorMsg = data.detail;
                        } else if (Array.isArray(data.detail) && data.detail.length > 0) {
                            errorMsg = data.detail.map(err => err.msg || JSON.stringify(err)).join(', ');
                        } else if (typeof data.detail === 'object') {
                            errorMsg = data.detail.message || JSON.stringify(data.detail);
                        }
                    }
                    showToast(errorMsg, 'error');
                }
            } catch (error) {
                console.error('Error creating product:', error);
                showToast('Network error, please try again.', 'error');
            } finally {
                submitBtn.disabled = false;
                loader.style.display = 'none';
                text.style.display = 'inline-flex';
            }
        }

        // Target Price Inline Editing
        function startEditTargetPrice(productId, currentVal) {
            document.getElementById(`target-display-${productId}`).style.display = 'none';
            document.getElementById(`target-edit-${productId}`).style.display = 'flex';
            const input = document.getElementById(`target-input-${productId}`);
            input.value = currentVal || '';
            input.focus();
        }

        function cancelEditTargetPrice(productId) {
            document.getElementById(`target-display-${productId}`).style.display = 'inline-flex';
            document.getElementById(`target-edit-${productId}`).style.display = 'none';
        }

        async function saveTargetPrice(productId) {
            const input = document.getElementById(`target-input-${productId}`);
            const newVal = input.value.trim() === '' ? null : parseFloat(input.value);
            
            if (newVal !== null && (isNaN(newVal) || newVal < 0)) {
                showToast('Target price must be a valid positive number.', 'error');
                return;
            }

            try {
                const response = await fetch(`/api/products/${productId}/target-price`, {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ target_price: newVal })
                });

                if (response.ok) {
                    showToast('Target price updated successfully.');
                    fetchProducts();
                } else {
                    const data = await response.json();
                    showToast(data.detail || 'Failed to update target price.', 'error');
                }
            } catch (error) {
                console.error('Error saving target price:', error);
                showToast('Network error, please try again.', 'error');
            }
        }

        // Delete tracked item
        async function deleteProduct(productId) {
            if (!confirm("Are you sure you want to stop tracking this product?")) {
                return;
            }

            try {
                const response = await fetch(`/api/products/${productId}`, {
                    method: 'DELETE'
                });
                
                if (response.ok) {
                    showToast('Listing removed successfully.');
                    fetchProducts();
                } else {
                    const data = await response.json();
                    showToast(data.detail || 'Failed to remove listing.', 'error');
                }
            } catch (error) {
                console.error('Error deleting product:', error);
                showToast('Failed to remove product tracking.', 'error');
            }
        }

        // Trigger manual crawl
        async function triggerGlobalScrape() {
            const refreshBtn = document.getElementById('global-refresh-btn');
            const loader = document.getElementById('refresh-loader');
            const text = document.getElementById('refresh-text');

            refreshBtn.disabled = true;
            loader.style.display = 'inline-block';
            text.style.display = 'none';

            try {
                const headers = {};
                const storedApiKey = localStorage.getItem('priceguard_api_key');
                if (storedApiKey) {
                    headers['X-API-Key'] = storedApiKey;
                }

                const response = await fetch('/api/scrape', {
                    method: 'POST',
                    headers: headers
                });

                if (response.status === 403) {
                    const apiKey = prompt("This endpoint is secured. Enter your X-API-Key:");
                    if (apiKey) {
                        localStorage.setItem('priceguard_api_key', apiKey);
                        triggerGlobalScrape();
                        return;
                    }
                } else if (response.ok) {
                    const data = await response.json();
                    showToast(`Scan complete! Success: ${data.success_count}, Failed: ${data.failed_count}`);
                    fetchProducts();
                } else {
                    showToast('Scraping run failed. Verify server config.', 'error');
                }
            } catch (error) {
                console.error('Error triggering scrape:', error);
                showToast('Failed to trigger live price scan.', 'error');
            } finally {
                refreshBtn.disabled = false;
                loader.style.display = 'none';
                text.style.display = 'inline-flex';
            }
        }

        // Settings modal dialog interactions
        function toggleSettingsModal() {
            const modal = document.getElementById('settings-modal');
            if (modal.classList.contains('active')) {
                modal.classList.remove('active');
            } else {
                const defaultId = localStorage.getItem('default_telegram_id') || '';
                document.getElementById('default_telegram_id').value = defaultId;
                modal.classList.add('active');
            }
        }

        function handleOutsideClick(event) {
            const modal = document.getElementById('settings-modal');
            if (event.target === modal) {
                toggleSettingsModal();
            }
        }

        function saveSettings() {
            const defaultId = document.getElementById('default_telegram_id').value.trim();
            localStorage.setItem('default_telegram_id', defaultId);
            showToast('Settings saved successfully!');
            toggleSettingsModal();
        }

        // Test Telegram Alert connection
        async function sendTestNotification() {
            const chatId = document.getElementById('default_telegram_id').value.trim();
            if (!chatId) {
                showToast('Enter a Telegram Chat ID first.', 'error');
                return;
            }

            const btn = document.getElementById('test-notif-btn');
            const loader = document.getElementById('test-loader');
            const text = document.getElementById('test-text');

            btn.disabled = true;
            loader.style.display = 'inline-block';
            text.style.display = 'none';

            try {
                const response = await fetch('/api/test-notification', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ telegram_chat_id: chatId })
                });

                const data = await response.json();

                if (response.ok) {
                    showToast('Test notification sent successfully!');
                } else {
                    showToast(data.detail || 'Failed to send test notification.', 'error');
                }
            } catch (error) {
                console.error('Error sending test notification:', error);
                showToast('Failed to connect to notification service.', 'error');
            } finally {
                btn.disabled = false;
                loader.style.display = 'none';
                text.style.display = 'inline-flex';
            }
        }

        // Startup configurations
        const defaultChatId = localStorage.getItem('default_telegram_id');
        if (defaultChatId) {
            document.getElementById('default_telegram_id').value = defaultChatId;
        }
        fetchProducts();
        // Poll for background scrape updates
        setInterval(fetchProducts, 5000);
    </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)

