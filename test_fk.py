import asyncio
from src.scraper import scrape_product_http
from bs4 import BeautifulSoup
import httpx

async def test_fk():
    url = "https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4"
    res = await scrape_product_http(url, "flipkart")
    print(res)

if __name__ == "__main__":
    asyncio.run(test_fk())
