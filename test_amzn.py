import asyncio
from src.scraper import scrape_product

async def test_amzn():
    url = "https://www.amazon.in/dp/B0CHX1W1XY"
    res = await scrape_product(url)
    print(res)

if __name__ == "__main__":
    asyncio.run(test_amzn())
