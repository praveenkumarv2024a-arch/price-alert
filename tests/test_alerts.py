import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime
from src.models import Product
from src.alerts import check_and_alert_product

@pytest.mark.asyncio
async def test_alert_condition_a_target_price_hit(test_db):
    """
    Condition A: Test target price hit alert triggers ONLY if last_scraped_price > target_price.
    """
    prod = Product(
        url="https://www.amazon.in/dp/1",
        platform="amazon",
        title="Test Phone",
        target_price=10000.0,
        last_scraped_price=12000.0,  # Previously above target
        is_in_stock=True,
        telegram_chat_id="111"
    )
    test_db.add(prod)
    await test_db.commit()
    await test_db.refresh(prod)

    scraped_data = {
        "success": True,
        "price": 9500.0,  # Now below target
        "is_in_stock": True,
        "title": "Test Phone Updated"
    }

    with patch("src.alerts.send_telegram_notification", new_callable=AsyncMock) as mock_send:
        await check_and_alert_product(test_db, prod, scraped_data)
        
        # Should trigger alert because last price (12000) > target (10000) >= live (9500)
        assert mock_send.call_count == 1
        message = mock_send.call_args[0][1]
        assert "TARGET PRICE HIT" in message
        assert "₹9,500\\.00" in message
        assert "₹10,000\\.00" in message

        # Verify DB state was saved
        await test_db.refresh(prod)
        assert prod.last_scraped_price == 9500.0
        assert prod.title == "Test Phone Updated"

@pytest.mark.asyncio
async def test_alert_condition_a_no_spam(test_db):
    """
    Condition A: Test target price hit alert does NOT trigger if previous price was already below target.
    """
    prod = Product(
        url="https://www.amazon.in/dp/2",
        platform="amazon",
        title="Test Phone",
        target_price=10000.0,
        last_scraped_price=9800.0,  # Already below target
        is_in_stock=True,
        telegram_chat_id="111"
    )
    test_db.add(prod)
    await test_db.commit()
    await test_db.refresh(prod)

    scraped_data = {
        "success": True,
        "price": 9500.0,  # Drop further, still below
        "is_in_stock": True
    }

    with patch("src.alerts.send_telegram_notification", new_callable=AsyncMock) as mock_send:
        await check_and_alert_product(test_db, prod, scraped_data)
        
        # Should NOT trigger alert because last price was already below target
        assert mock_send.call_count == 0

        # DB state should still update
        await test_db.refresh(prod)
        assert prod.last_scraped_price == 9500.0

@pytest.mark.asyncio
async def test_alert_condition_b_price_drop(test_db):
    """
    Condition B: General price drop alert when target_price is not set.
    """
    prod = Product(
        url="https://www.flipkart.com/dp/1",
        platform="flipkart",
        title="Test Watch",
        target_price=None,  # No target price
        last_scraped_price=5000.0,
        is_in_stock=True,
        telegram_chat_id="222"
    )
    test_db.add(prod)
    await test_db.commit()
    await test_db.refresh(prod)

    scraped_data = {
        "success": True,
        "price": 4500.0,  # Price drop
        "is_in_stock": True
    }

    with patch("src.alerts.send_telegram_notification", new_callable=AsyncMock) as mock_send:
        await check_and_alert_product(test_db, prod, scraped_data)
        
        # Should trigger price drop trend alert
        assert mock_send.call_count == 1
        message = mock_send.call_args[0][1]
        assert "PRICE DROP ALERT" in message
        assert "₹4,500\\.00" in message
        assert "₹5,000\\.00" in message

@pytest.mark.asyncio
async def test_alert_condition_c_back_in_stock(test_db):
    """
    Condition C: High priority back in stock alert.
    """
    prod = Product(
        url="https://www.myntra.com/dp/1",
        platform="myntra",
        title="Test Shoe",
        target_price=2000.0,
        last_scraped_price=2500.0,
        is_in_stock=False,  # Previously out of stock
        telegram_chat_id="333"
    )
    test_db.add(prod)
    await test_db.commit()
    await test_db.refresh(prod)

    scraped_data = {
        "success": True,
        "price": 2500.0,
        "is_in_stock": True  # Back in stock
    }

    with patch("src.alerts.send_telegram_notification", new_callable=AsyncMock) as mock_send:
        await check_and_alert_product(test_db, prod, scraped_data)
        
        # Should trigger stock status alert
        assert mock_send.call_count == 1
        message = mock_send.call_args[0][1]
        assert "BACK IN STOCK" in message
        assert "Available right now" in message

        await test_db.refresh(prod)
        assert prod.is_in_stock is True
