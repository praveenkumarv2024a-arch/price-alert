import pytest
from bs4 import BeautifulSoup
from src.scraper import (
    get_platform_from_url,
    clean_price,
    extract_from_json_ld
)

def test_get_platform_from_url():
    """
    Test that domains are parsed correctly to identify target platforms.
    """
    assert get_platform_from_url("https://www.amazon.in/dp/B0CHX1W1XY") == "amazon"
    assert get_platform_from_url("https://amazon.in/some-product") == "amazon"
    assert get_platform_from_url("https://www.flipkart.com/product/p/itm123") == "flipkart"
    assert get_platform_from_url("https://myntra.com/shoes/12345/buy") == "myntra"
    assert get_platform_from_url("https://www.meesho.com/saree-special/p/999") == "meesho"
    
    with pytest.raises(ValueError):
        get_platform_from_url("https://google.com")

def test_clean_price():
    """
    Verify currency symbols, commas, and other formatting characters are cleaned.
    """
    assert clean_price("₹1,299") == 1299.0
    assert clean_price("₹ 4,599.99") == 4599.99
    assert clean_price("Rs. 450") == 450.0
    assert clean_price("Price: 99.50 INR") == 99.50
    assert clean_price("1,200 - 1,500") == 1200.0  # Range fallback (takes first)
    assert clean_price("Out of stock") is None

def test_extract_from_json_ld():
    """
    Verify that JSON-LD structured schema tags can be read correctly.
    """
    # Sample HTML containing a valid schema.org Product
    html = """
    <html>
        <head>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org/",
                "@type": "Product",
                "name": "Super Headphones X",
                "offers": {
                    "@type": "Offer",
                    "priceCurrency": "INR",
                    "price": "1999.00",
                    "availability": "https://schema.org/InStock"
                }
            }
            </script>
        </head>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    title, price, in_stock = extract_from_json_ld(soup)
    
    assert title == "Super Headphones X"
    assert price == 1999.00
    assert in_stock is True

def test_extract_from_json_ld_nested():
    """
    Verify JSON-LD parser handles nested graphs/lists.
    """
    html = """
    <html>
        <head>
            <script type="application/ld+json">
            [
                {
                    "@context": "https://schema.org/",
                    "@type": "BreadcrumbList"
                },
                {
                    "@context": "https://schema.org/",
                    "@type": "Product",
                    "name": "Cool Shirt",
                    "offers": [
                        {
                            "@type": "Offer",
                            "price": "499",
                            "availability": "http://schema.org/OutOfStock"
                        }
                    ]
                }
            ]
            </script>
        </head>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    title, price, in_stock = extract_from_json_ld(soup)
    
    assert title == "Cool Shirt"
    assert price == 499.0
    assert in_stock is False
