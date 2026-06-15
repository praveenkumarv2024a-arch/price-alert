import logging
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from src.config import settings
from src.database import AsyncSessionLocal
from src.models import Product
from src.scraper import scrape_product
from src.alerts import check_and_alert_product

logger = logging.getLogger(__name__)

# Instantiate local scheduler
scheduler = AsyncIOScheduler()

async def run_scraper_job(session_maker=AsyncSessionLocal) -> dict:
    """
    Scrapes all registered products in sequence, processes alerting logic,
    and returns a summary report of the run.
    """
    logger.info("Executing batch e-commerce scraper task...")
    
    async with session_maker() as db:
        try:
            # Query all products from database
            stmt = select(Product)
            result = await db.execute(stmt)
            products = result.scalars().all()
            
            total = len(products)
            success_count = 0
            failed_count = 0
            
            logger.info(f"Retrieved {total} product URLs from database.")
            
            for product in products:
                logger.info(f"Processing Product ID {product.id} | Platform: {product.platform} | URL: {product.url}")
                
                scraped_data = await scrape_product(product.url)
                
                if scraped_data.get("success"):
                    success_count += 1
                else:
                    failed_count += 1
                    logger.error(f"Failed to scrape Product ID {product.id}: {scraped_data.get('error')}")
                
                # Check transitions and send alerts
                await check_and_alert_product(db, product, scraped_data)
                
            logger.info(f"Batch task execution completed. Checked: {total}, Success: {success_count}, Failed: {failed_count}")
            return {
                "total_checked": total,
                "success_count": success_count,
                "failed_count": failed_count
            }
            
        except Exception as e:
            logger.error(f"Error occurred during batch scraper task: {e}", exc_info=True)
            return {
                "total_checked": 0,
                "success_count": 0,
                "failed_count": 0,
                "error": str(e)
            }

def start_scheduler():
    """
    Registers and boots the local background cron task.
    """
    if scheduler.running:
        return
        
    scheduler.add_job(
        run_scraper_job,
        "interval",
        minutes=settings.SCRAPE_INTERVAL_MINUTES,
        id="batch_scraper_job",
        replace_existing=True
    )
    scheduler.start()
    logger.info(f"Local APScheduler launched successfully. Task scheduled every {settings.SCRAPE_INTERVAL_MINUTES} minutes.")

def shutdown_scheduler():
    """
    Shuts down the local scheduler during teardown.
    """
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Local APScheduler stopped.")
