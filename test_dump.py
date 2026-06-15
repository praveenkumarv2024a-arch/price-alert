import asyncio
from playwright.async_api import async_playwright
import logging

logging.basicConfig(level=logging.INFO)

async def dump_html():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.goto("https://amzn.in/d/cXYYI8H", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        html = await page.content()
        with open("dump.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Dumped HTML.")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(dump_html())
