import asyncio
from src.scraper import scrape_product

async def test_fk_playwright():
    url = "https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4"
    res = await scrape_product(url)
    print(res)

if __name__ == "__main__":
    asyncio.run(test_fk_playwright())
