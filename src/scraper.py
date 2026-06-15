import json
import logging
import re
from typing import Optional, Tuple, Dict, Any
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from src.config import settings
import httpx

logger = logging.getLogger(__name__)

# Fallback selectors for Price, Title, and Stock Status
FALLBACK_SELECTORS = {
    "amazon": {
        "price": [
            "span.a-price-whole",
            "span.a-price .a-offscreen",
            "span#priceblock_ourprice",
            "span#priceblock_dealprice",
            ".a-color-price"
        ],
        "stock": [
            "#availability span",
            "#outOfStock",
            "span.a-color-state"
        ],
        "title": [
            "span#productTitle",
            "h1#title",
            "meta[property='og:title']"
        ]
    },
    "flipkart": {
        "price": [
            "div.Nx931A",      # Newer Flipkart Price class
            "div._30jeq3",      # Classic Flipkart Price class
            "div[class*='_30jeq3']",
            "span._30jeq3"
        ],
        "stock": [
            "div._1uxZvi",      # Out of stock indicator
            "div._3906sl",
            "div[class*='out-of-stock']"
        ],
        "title": [
            "span.B_NuCI",      # Classic title class
            "h1.yhB1nd",
            "span.VU-ZEz",      # Newer title class
            "meta[property='og:title']"
        ]
    },
    "myntra": {
        "price": [
            "span.pdp-price",
            "strong.pdp-price"
        ],
        "stock": [
            "span.pdp-out-of-stock",
            "div.pdp-out-of-stock"
        ],
        "title": [
            "h1.pdp-title",
            "h1.pdp-name",
            "meta[property='og:title']"
        ]
    },
    "meesho": {
        "price": [
            "h3[class*='Price']",
            "h4[class*='Price']",
            "h5[class*='Price']",
            "span[class*='Price']"
        ],
        "stock": [
            "span[class*='OutOfStock']",
            "div[class*='OutOfStock']",
            "p[class*='OutOfStock']"
        ],
        "title": [
            "span[class*='ProductTitle']",
            "p[class*='ProductTitle']",
            "h1",
            "meta[property='og:title']"
        ]
    }
}

def get_platform_from_url(url: str) -> str:
    """
    Parses the URL to dynamically adapt the parsing strategy.
    Supports short domains like amzn.in, amzn.to, and fkrt.it.
    """
    url_lower = url.lower()
    if any(domain in url_lower for domain in ["amazon.in", "amazon.com", "amazon.co", "amzn.in", "amzn.to"]):
        return "amazon"
    elif any(domain in url_lower for domain in ["flipkart.com", "fkrt.it"]):
        return "flipkart"
    elif "myntra.com" in url_lower:
        return "myntra"
    elif "meesho.com" in url_lower:
        return "meesho"
    else:
        raise ValueError("Unsupported platform URL. Must be Amazon, Flipkart, Myntra, or Meesho.")

def clean_price(price_str: str) -> Optional[float]:
    """
    Strips currency symbols, text characters, and commas, and parses to float.
    """
    if not price_str:
        return None
    # Remove commas
    price_str = price_str.replace(",", "")
    # Use regular expression to find numbers
    match = re.search(r'\d+(?:\.\d+)?', price_str)
    if match:
        try:
            return float(match.group(0))
        except ValueError:
            return None
    return None

def find_product_in_json(data: Any) -> Optional[Dict[str, Any]]:
    """
    Recursively searches nested JSON-LD structure for an object representing a Product.
    """
    if isinstance(data, dict):
        if data.get("@type") == "Product" or "offers" in data:
            return data
        if "@graph" in data:
            for item in data["@graph"]:
                res = find_product_in_json(item)
                if res:
                    return res
        for val in data.values():
            if isinstance(val, (dict, list)):
                res = find_product_in_json(val)
                if res:
                    return res
    elif isinstance(data, list):
        for item in data:
            res = find_product_in_json(item)
            if res:
                return res
    return None

def extract_from_json_ld(soup: BeautifulSoup) -> Tuple[Optional[str], Optional[float], Optional[bool]]:
    """
    Attempts to extract title, price, and availability from embedded JSON-LD scripts.
    """
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            if not script.string:
                continue
            data = json.loads(script.string)
            product_data = find_product_in_json(data)
            
            if product_data:
                title = product_data.get("name") or product_data.get("title")
                offers = product_data.get("offers")
                price = None
                in_stock = None
                
                if offers:
                    if isinstance(offers, list) and len(offers) > 0:
                        offer = offers[0]
                    elif isinstance(offers, dict):
                        offer = offers
                    else:
                        offer = {}
                    
                    raw_price = offer.get("price")
                    if raw_price is not None:
                        price = clean_price(str(raw_price))
                    
                    avail = offer.get("availability")
                    if avail:
                        avail_str = str(avail).lower()
                        if "instock" in avail_str or "in_stock" in avail_str:
                            in_stock = True
                        elif "outofstock" in avail_str or "out_of_stock" in avail_str or "soldout" in avail_str:
                            in_stock = False
                
                # Title might be present even if price is not, but we need price to succeed
                if price is not None:
                    return title, price, in_stock
        except Exception as e:
            logger.debug(f"Failed parsing JSON-LD script block: {e}")
            
    return None, None, None

async def extract_price_fallback(page, platform: str, soup: BeautifulSoup) -> Optional[float]:
    """
    Extracts price using fallback CSS selectors.
    """
    selectors = FALLBACK_SELECTORS.get(platform, {}).get("price", [])
    for selector in selectors:
        try:
            # Attempt via Playwright locator
            element = page.locator(selector).first
            if await element.count() > 0:
                text = await element.text_content()
                if text:
                    price = clean_price(text)
                    if price is not None:
                        return price
            
            # Attempt via static BeautifulSoup
            bs_elem = soup.select_one(selector)
            if bs_elem:
                price = clean_price(bs_elem.get_text())
                if price is not None:
                    return price
        except Exception as e:
            logger.debug(f"Price fallback failed on selector {selector}: {e}")
            
    # Last-ditch search for Rupee text
    try:
        elements = soup.find_all(string=re.compile(r'₹\s*\d+'))
        for element in elements:
            price = clean_price(element)
            if price is not None:
                return price
    except Exception as e:
        logger.debug(f"Price last-ditch scanning failed: {e}")

    return None

async def extract_title_fallback(page, platform: str, soup: BeautifulSoup) -> str:
    """
    Extracts title using fallback CSS selectors.
    """
    selectors = FALLBACK_SELECTORS.get(platform, {}).get("title", [])
    for selector in selectors:
        try:
            if "meta" in selector:
                meta_elem = soup.find("meta", property="og:title")
                if meta_elem and meta_elem.get("content"):
                    return meta_elem.get("content").strip()
            
            element = page.locator(selector).first
            if await element.count() > 0:
                text = await element.text_content()
                if text:
                    return text.strip()
            
            bs_elem = soup.select_one(selector)
            if bs_elem:
                return bs_elem.get_text().strip()
        except Exception:
            pass
            
    try:
        title = await page.title()
        if title:
            return title.strip()
    except Exception:
        pass
        
    return "Unknown Product"

async def extract_stock_fallback(page, platform: str, soup: BeautifulSoup) -> bool:
    """
    Extracts stock status using fallback CSS selectors.
    """
    selectors = FALLBACK_SELECTORS.get(platform, {}).get("stock", [])
    out_of_stock_indicators = ["out of stock", "sold out", "currently unavailable", "temporarily unavailable", "coming soon"]
    
    for selector in selectors:
        try:
            element = page.locator(selector).first
            if await element.count() > 0:
                text = await element.text_content()
                if text and any(ind in text.lower() for ind in out_of_stock_indicators):
                    return False
            
            bs_elem = soup.select_one(selector)
            if bs_elem:
                text = bs_elem.get_text()
                if text and any(ind in text.lower() for ind in out_of_stock_indicators):
                    return False
        except Exception:
            pass
            
    # Check general text patterns in the document body
    try:
        page_text = await page.inner_text("body")
        page_text_lower = page_text.lower()
        if platform == "amazon" and "currently unavailable" in page_text_lower:
            return False
        elif platform == "flipkart" and ("sold out" in page_text_lower or "this item is currently out of stock" in page_text_lower):
            return False
        elif platform == "myntra" and "out of stock" in page_text_lower:
            return False
        elif platform == "meesho" and ("out of stock" in page_text_lower or "sold out" in page_text_lower):
            return False
    except Exception:
        pass
        
    return True

async def scrape_product_http(url: str, platform: str) -> Dict[str, Any]:
    """
    Attempts to scrape the product details using a fast HTTP GET request.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0"
    }
    
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"success": False, "error": f"HTTP status {resp.status_code}"}
                
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Check for generic blockages/captchas on Amazon/Flipkart
            body_text = resp.text.lower()
            if "captcha" in body_text or "robot" in body_text or "automated access" in body_text:
                return {"success": False, "error": "Blocked by captcha/robot detection"}
                
            # 1. Try JSON-LD
            title, price, in_stock = extract_from_json_ld(soup)
            
            if price is not None:
                # If title is missing, try fallback selectors
                if not title:
                    selectors = FALLBACK_SELECTORS.get(platform, {})
                    for title_sel in selectors.get("title", []):
                        elem = soup.select_one(title_sel)
                        if elem:
                            title = elem.get_text().strip()
                            break
                    if not title:
                        meta = soup.find("meta", property="og:title")
                        if meta:
                            title = meta.get("content", "").strip()
                
                # If stock status is missing, try fallback selectors
                if in_stock is None:
                    in_stock = True
                    selectors = FALLBACK_SELECTORS.get(platform, {})
                    for stock_sel in selectors.get("stock", []):
                        elem = soup.select_one(stock_sel)
                        if elem:
                            text = elem.get_text().lower()
                            if any(ind in text for ind in ["out of stock", "sold out", "currently unavailable", "temporarily unavailable"]):
                                in_stock = False
                                break
                                
                return {
                    "success": True,
                    "title": title or "Unknown Product",
                    "price": price,
                    "is_in_stock": in_stock
                }
                
            # 2. Try Fallback CSS Selectors directly
            selectors = FALLBACK_SELECTORS.get(platform, {})
            price = None
            for price_sel in selectors.get("price", []):
                elem = soup.select_one(price_sel)
                if elem:
                    price = clean_price(elem.get_text())
                    if price is not None:
                        break
                        
            if price is not None:
                title = None
                for title_sel in selectors.get("title", []):
                    elem = soup.select_one(title_sel)
                    if elem:
                        title = elem.get_text().strip()
                        break
                if not title:
                    meta = soup.find("meta", property="og:title")
                    if meta:
                        title = meta.get("content", "").strip()
                        
                in_stock = True
                for stock_sel in selectors.get("stock", []):
                    elem = soup.select_one(stock_sel)
                    if elem:
                        text = elem.get_text().lower()
                        if any(ind in text for ind in ["out of stock", "sold out", "currently unavailable", "temporarily unavailable"]):
                            in_stock = False
                            break
                            
                return {
                    "success": True,
                    "title": title or "Unknown Product",
                    "price": price,
                    "is_in_stock": in_stock
                }
                
            return {"success": False, "error": "Price not found via JSON-LD or CSS selectors"}
    except Exception as e:
        return {"success": False, "error": f"HTTP GET error: {str(e)}"}

async def scrape_product(url: str) -> Dict[str, Any]:
    """
    Main entrypoint for browser scraping using Playwright.
    Returns parsed dictionary of result.
    """
    try:
        platform = get_platform_from_url(url)
    except Exception as e:
        return {"url": url, "success": False, "error": str(e)}

    # 1. Attempt fast HTTP GET first
    logger.info(f"Attempting fast HTTP GET scrape for URL: {url}")
    http_result = await scrape_product_http(url, platform)
    if http_result.get("success"):
        logger.info(f"HTTP GET scrape succeeded for URL: {url}")
        return {
            "url": url,
            "platform": platform,
            "title": http_result["title"],
            "price": http_result["price"],
            "is_in_stock": http_result["is_in_stock"],
            "success": True
        }
    
    logger.warning(f"HTTP GET scrape failed for URL: {url} | Reason: {http_result.get('error')}. Falling back to Playwright browser automation...")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=settings.PLAYWRIGHT_HEADLESS,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            
            # Setup realistic browser context
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Connection": "keep-alive"
                }
            )
            
            page = await context.new_page()
            
            logger.info(f"Navigating to: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=settings.PLAYWRIGHT_TIMEOUT_MS)
            await page.wait_for_timeout(3000)  # Wait for scripts to execute and prices to load
            
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            
            # 1. Primary: JSON-LD
            title, price, in_stock = extract_from_json_ld(soup)
            
            if price is not None:
                if not title:
                    title = await extract_title_fallback(page, platform, soup)
                if in_stock is None:
                    in_stock = await extract_stock_fallback(page, platform, soup)
                
                await browser.close()
                return {
                    "url": url,
                    "platform": platform,
                    "title": title or "Unknown Product",
                    "price": price,
                    "is_in_stock": in_stock if in_stock is not None else True,
                    "success": True
                }
            
            # 2. Fallback: CSS Selectors
            logger.info(f"JSON-LD parsing failed for {url}. Invoking Fallback CSS Engine.")
            price = await extract_price_fallback(page, platform, soup)
            
            if price is not None:
                title = await extract_title_fallback(page, platform, soup)
                in_stock = await extract_stock_fallback(page, platform, soup)
                
                await browser.close()
                return {
                    "url": url,
                    "platform": platform,
                    "title": title or "Unknown Product",
                    "price": price,
                    "is_in_stock": in_stock if in_stock is not None else True,
                    "success": True
                }
                
            await browser.close()
            return {
                "url": url,
                "platform": platform,
                "success": False,
                "error": "Failed to parse price via JSON-LD or CSS fallbacks."
            }
            
    except Exception as e:
        logger.error(f"Failed to scrape URL {url}: {e}", exc_info=True)
        return {
            "url": url,
            "platform": platform,
            "success": False,
            "error": str(e)
        }
