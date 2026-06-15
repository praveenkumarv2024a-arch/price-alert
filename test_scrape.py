import asyncio
from src.scraper import scrape_product
print(asyncio.run(scrape_product("https://amzn.in/d/cXYYI8H")))
