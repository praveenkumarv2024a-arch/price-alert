import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from src.models import Product
from src.notifier import (
    send_telegram_notification,
    format_target_price_hit,
    format_price_drop_trend,
    format_back_in_stock
)

logger = logging.getLogger(__name__)

async def check_and_alert_product(db: AsyncSession, product: Product, scraped_data: dict) -> None:
    """
    Evaluates newly scraped e-commerce details against the stored product state,
    triggers any necessary Telegram alerts, and saves the new state.
    """
    if not scraped_data.get("success"):
        logger.warning(f"Skipping alert evaluation for '{product.url}' due to scraper failure: {scraped_data.get('error')}")
        product.last_checked_at = datetime.utcnow()
        db.add(product)
        await db.commit()
        return

    current_price = scraped_data["price"]
    current_in_stock = scraped_data["is_in_stock"]
    title = scraped_data.get("title", product.title or "Unknown Product")

    # If product title was initialized to a generic value or URL, update it to the live crawled title
    if title and title != "Unknown Product":
        product.title = title

    # Alert Conditions
    trigger_target_alert = False
    trigger_drop_alert = False
    trigger_stock_alert = False

    # Condition A: Target Price Hit
    if product.target_price is not None:
        if current_price <= product.target_price:
            # ONLY triggers if previous price was above target (prevents spamming on every crawl)
            if product.last_scraped_price is not None and product.last_scraped_price > product.target_price:
                trigger_target_alert = True
                logger.info(f"Target Price Hit for Product ID {product.id} (Live: {current_price} <= Target: {product.target_price})")

    # Condition B: Price Drop Trend (if no target price is set)
    else:
        if product.last_scraped_price is not None and current_price < product.last_scraped_price:
            trigger_drop_alert = True
            logger.info(f"Price Drop Trend for Product ID {product.id} (Live: {current_price} < Last: {product.last_scraped_price})")

    # Condition C: Back in Stock
    if current_in_stock is True and product.is_in_stock is False:
        trigger_stock_alert = True
        logger.info(f"Back in Stock Alert for Product ID {product.id}")

    # Process and send notifications
    try:
        # Mutually exclusive price alerts
        if trigger_target_alert:
            msg = format_target_price_hit(
                title=product.title,
                platform=product.platform,
                target_price=product.target_price,
                current_price=current_price,
                url=product.url
            )
            await send_telegram_notification(product.telegram_chat_id, msg)
        elif trigger_drop_alert:
            msg = format_price_drop_trend(
                title=product.title,
                platform=product.platform,
                old_price=product.last_scraped_price,
                current_price=current_price,
                url=product.url
            )
            await send_telegram_notification(product.telegram_chat_id, msg)

        # Independent Stock status alert
        if trigger_stock_alert:
            msg = format_back_in_stock(
                title=product.title,
                platform=product.platform,
                current_price=current_price,
                url=product.url
            )
            await send_telegram_notification(product.telegram_chat_id, msg)
            
    except Exception as e:
        logger.error(f"Error executing Telegram notifier during alert evaluation for Product ID {product.id}: {e}", exc_info=True)

    # State Save: Always save latest live info
    product.last_scraped_price = current_price
    product.is_in_stock = current_in_stock
    product.last_checked_at = datetime.utcnow()
    
    db.add(product)
    await db.commit()
    logger.info(f"State saved successfully for Product ID {product.id}")
