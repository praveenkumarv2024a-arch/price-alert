import pytest
from unittest.mock import patch, AsyncMock
from src.notifier import (
    escape_markdown_v2,
    escape_markdown_v2_url,
    format_target_price_hit,
    format_back_in_stock,
    format_price_drop_trend,
    send_telegram_notification
)

def test_escape_markdown_v2():
    """
    Test that MarkdownV2 reserved characters are escaped with a backslash.
    """
    raw_text = "Hello! [World] - We have 10% off. Check it out (now)."
    # Characters that should be escaped: !, [, ], -, ., (, )
    expected = r"Hello\! \[World\] \- We have 10% off\. Check it out \(now\)\."
    assert escape_markdown_v2(raw_text) == expected

def test_escape_markdown_v2_url():
    """
    Test that URL fields only escape ')' and '\'.
    """
    raw_url = "https://example.com/product(123)/detail?x=abc\\123"
    expected = "https://example.com/product(123\\)/detail?x=abc\\\\123"
    assert escape_markdown_v2_url(raw_url) == expected

def test_format_target_price_hit():
    """
    Verify the layout of the Target Price Hit message.
    """
    title = "Cool Phone [Blue]"
    platform = "amazon"
    target_price = 10000.0
    current_price = 9500.0
    url = "https://amazon.in/p(1)"

    message = format_target_price_hit(title, platform, target_price, current_price, url)
    
    # Must support MarkdownV2 escaping and contain bold formatting
    assert "🎯 *TARGET PRICE HIT\\!*" in message
    assert "• *Product:* Cool Phone \\[Blue\\]" in message
    assert "• *Platform:* Amazon" in message
    assert "• *Your Target:* ₹10,000\\.00" in message
    assert "• *Current Live Price:* 🔥 *₹9,500\\.00*" in message
    assert "• *Link:* [Buy Now](https://amazon.in/p(1\\))" in message

def test_format_back_in_stock():
    """
    Verify the layout of the Back in Stock message.
    """
    title = "Saree Elegant"
    platform = "meesho"
    current_price = 499.0
    url = "https://meesho.com/p"

    message = format_back_in_stock(title, platform, current_price, url)
    
    assert "📦 *BACK IN STOCK ALERT\\!*" in message
    assert "• *Product:* Saree Elegant" in message
    assert "• *Platform:* Meesho" in message
    assert "• *Current Price:* ₹499\\.00" in message
    assert "• *Status:* Available right now\\!" in message
    assert "• *Link:* [Buy Now](https://meesho.com/p)" in message

def test_format_price_drop_trend():
    """
    Verify the layout of the Price Drop Trend message.
    """
    title = "Elegant Shoes"
    platform = "myntra"
    old_price = 1000.0
    current_price = 800.0
    url = "https://myntra.com/p"

    message = format_price_drop_trend(title, platform, old_price, current_price, url)
    
    assert "📉 *PRICE DROP ALERT\\!*" in message
    assert "• *Product:* Elegant Shoes" in message
    assert "• *Platform:* Myntra" in message
    assert "• *Old Price:* ₹1,000\\.00" in message
    assert "• *New Price:* 🔥 *₹800\\.00*" in message
    assert "• *Price Drop:* ₹200\\.00 \\(20\\.0%\\)" in message
    assert "• *Link:* [Buy Now](https://myntra.com/p)" in message

@pytest.mark.asyncio
async def test_send_telegram_notification_mock():
    """
    Test send_telegram_notification runs in mock mode when token starts with mock_ or is placeholder.
    """
    with patch("src.notifier.settings") as mock_settings:
        mock_settings.TELEGRAM_BOT_TOKEN = "mock_token"
        
        # In mock mode, should log and return True without making a network call
        res = await send_telegram_notification("12345", "test message")
        assert res is True
