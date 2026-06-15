import pytest
from unittest.mock import patch
from src.models import Product

@pytest.mark.asyncio
async def test_create_product(client):
    """
    Test successful product tracking creation with a mocked scraper run.
    """
    with patch("src.main.scrape_product") as mock_scrape:
        mock_scrape.return_value = {
            "url": "https://www.amazon.in/dp/B0CHX1W1XY",
            "platform": "amazon",
            "title": "iPhone 15 Pro Max",
            "price": 140000.0,
            "is_in_stock": True,
            "success": True
        }
        
        payload = {
            "url": "https://www.amazon.in/dp/B0CHX1W1XY",
            "target_price": 135000.0,
            "telegram_chat_id": "987654321"
        }
        
        response = await client.post("/api/products", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "iPhone 15 Pro Max"
        assert data["platform"] == "amazon"
        assert data["last_scraped_price"] == 140000.0
        assert data["is_in_stock"] is True
        assert data["target_price"] == 135000.0
        assert data["telegram_chat_id"] == "987654321"

@pytest.mark.asyncio
async def test_create_product_invalid_domain(client):
    """
    Test that domains outside the allowed 4 Indian e-commerce sites are rejected.
    """
    payload = {
        "url": "https://ebay.com/itm/123",
        "target_price": 100.0,
        "telegram_chat_id": "123"
    }
    response = await client.post("/api/products", json=payload)
    assert response.status_code == 422
    data = response.json()
    assert "URL must belong to Amazon.in, Flipkart.com, Myntra.com, or Meesho.com" in data["detail"][0]["msg"]

@pytest.mark.asyncio
async def test_list_products(client, test_db):
    """
    Test listing products from database.
    """
    # Create product records directly in mock DB
    prod1 = Product(
        url="https://www.flipkart.com/p1",
        platform="flipkart",
        title="Laptop Pro",
        target_price=60000.0,
        last_scraped_price=62000.0,
        is_in_stock=True,
        telegram_chat_id="123"
    )
    test_db.add(prod1)
    await test_db.commit()
    
    response = await client.get("/api/products")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Laptop Pro"

@pytest.mark.asyncio
async def test_get_product_detail(client, test_db):
    """
    Test fetching a single product details.
    """
    prod = Product(
        url="https://www.myntra.com/p1",
        platform="myntra",
        title="Sneakers Elite",
        target_price=2999.0,
        last_scraped_price=3499.0,
        is_in_stock=True,
        telegram_chat_id="456"
    )
    test_db.add(prod)
    await test_db.commit()
    await test_db.refresh(prod)

    response = await client.get(f"/api/products/{prod.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Sneakers Elite"
    
    # 404 test
    response_404 = await client.get("/api/products/9999")
    assert response_404.status_code == 404

@pytest.mark.asyncio
async def test_delete_product(client, test_db):
    """
    Test deleting a product tracking configuration.
    """
    prod = Product(
        url="https://www.meesho.com/p1",
        platform="meesho",
        title="T-Shirt Alpha",
        target_price=299.0,
        last_scraped_price=350.0,
        is_in_stock=True,
        telegram_chat_id="789"
    )
    test_db.add(prod)
    await test_db.commit()
    await test_db.refresh(prod)

    response = await client.delete(f"/api/products/{prod.id}")
    assert response.status_code == 200
    assert response.json()["detail"] == "Product tracking deleted successfully"

    # Confirm it was deleted
    response_verify = await client.get(f"/api/products/{prod.id}")
    assert response_verify.status_code == 404

@pytest.mark.asyncio
async def test_trigger_scrape_secured_endpoints(client):
    """
    Test that API key security works on the trigger scrape endpoint.
    """
    # If settings.API_KEY is not set, this test checks if we bypass or authenticate successfully.
    # To test security, we patch settings.API_KEY to a mock key.
    with patch("src.main.settings") as mock_settings:
        mock_settings.API_KEY = "super_secret"
        
        # Test without header
        res_no_key = await client.post("/api/scrape")
        assert res_no_key.status_code == 403
        
        # Test with wrong header
        res_wrong_key = await client.post("/api/scrape", headers={"X-API-Key": "wrong_key"})
        assert res_wrong_key.status_code == 403
        
        # Test with correct key (mock batch scraper execution to avoid actual work)
        with patch("src.main.run_scraper_job") as mock_job:
            mock_job.return_value = {"total_checked": 0, "success_count": 0, "failed_count": 0}
            res_correct_key = await client.post("/api/scrape", headers={"X-API-Key": "super_secret"})
            assert res_correct_key.status_code == 200
            data = res_correct_key.json()
            assert data["total_checked"] == 0
