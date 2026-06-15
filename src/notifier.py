import logging
import httpx
from src.config import settings

logger = logging.getLogger(__name__)

def escape_markdown_v2(text: str) -> str:
    """
    Escapes special characters for Telegram MarkdownV2.
    """
    if not text:
        return ""
    # Characters that must be escaped outside link definitions
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join(f"\\{c}" if c in escape_chars else c for c in text)

def escape_markdown_v2_url(url: str) -> str:
    """
    Escapes special characters in the URL part of MarkdownV2 inline links.
    Only ')' and '\\' need to be escaped in link URL brackets.
    """
    if not url:
        return ""
    return url.replace("\\", "\\\\").replace(")", "\\)")

async def send_telegram_notification(chat_id: str, text: str) -> bool:
    """
    Sends a message via the Telegram Bot API using MarkdownV2 parsing.
    Returns True if successful, False otherwise.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    
    # If bot token is empty, placeholder, or in testing, mock send by logging
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN" or token.startswith("mock_"):
        logger.info(f"[MOCK TELEGRAM] Target Chat: {chat_id}\nMessage:\n{text}")
        return True

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                logger.info(f"Successfully sent Telegram notification to {chat_id}")
                return True
            else:
                logger.error(f"Failed to send Telegram notification to {chat_id}. Status code: {response.status_code}, Response: {response.text}")
                return False
    except Exception as e:
        logger.error(f"Exception raised while sending Telegram notification to {chat_id}: {e}", exc_info=True)
        return False

def format_target_price_hit(title: str, platform: str, target_price: float, current_price: float, url: str) -> str:
    """
    🎯 *TARGET PRICE HIT\!*
    • *Product:* {Product Title}
    • *Platform:* {Platform Name}
    • *Your Target:* ₹{target_price}
    • *Current Live Price:* 🔥 *₹{current_scraped_price}*
    • *Link:* [Buy Now]({URL})
    """
    escaped_title = escape_markdown_v2(title)
    escaped_platform = escape_markdown_v2(platform.capitalize())
    escaped_target = escape_markdown_v2(f"{target_price:,.2f}")
    escaped_current = escape_markdown_v2(f"{current_price:,.2f}")
    escaped_url = escape_markdown_v2_url(url)
    
    return (
        rf"🎯 *TARGET PRICE HIT\!*" + "\n" +
        rf"• *Product:* {escaped_title}" + "\n" +
        rf"• *Platform:* {escaped_platform}" + "\n" +
        rf"• *Your Target:* ₹{escaped_target}" + "\n" +
        rf"• *Current Live Price:* 🔥 *₹{escaped_current}*" + "\n" +
        rf"• *Link:* [Buy Now]({escaped_url})"
    )

def format_back_in_stock(title: str, platform: str, current_price: float, url: str) -> str:
    """
    📦 *BACK IN STOCK ALERT\!*
    • *Product:* {Product Title}
    • *Platform:* {Platform Name}
    • *Current Price:* ₹{current_scraped_price}
    • *Status:* Available right now\!
    • *Link:* [Buy Now]({URL})
    """
    escaped_title = escape_markdown_v2(title)
    escaped_platform = escape_markdown_v2(platform.capitalize())
    escaped_price = escape_markdown_v2(f"{current_price:,.2f}")
    escaped_url = escape_markdown_v2_url(url)
    
    return (
        rf"📦 *BACK IN STOCK ALERT\!*" + "\n" +
        rf"• *Product:* {escaped_title}" + "\n" +
        rf"• *Platform:* {escaped_platform}" + "\n" +
        rf"• *Current Price:* ₹{escaped_price}" + "\n" +
        rf"• *Status:* Available right now\!" + "\n" +
        rf"• *Link:* [Buy Now]({escaped_url})"
    )

def format_price_drop_trend(title: str, platform: str, old_price: float, current_price: float, url: str) -> str:
    """
    📉 *PRICE DROP ALERT\!*
    • *Product:* {Product Title}
    • *Platform:* {Platform Name}
    • *Old Price:* ₹{old_price}
    • *New Price:* 🔥 *₹{current_price}*
    • *Price Drop:* ₹{drop_amount} ({drop_percentage}%)
    • *Link:* [Buy Now]({URL})
    """
    drop_amount = old_price - current_price
    drop_percentage = (drop_amount / old_price) * 100 if old_price > 0 else 0.0
    
    escaped_title = escape_markdown_v2(title)
    escaped_platform = escape_markdown_v2(platform.capitalize())
    escaped_old = escape_markdown_v2(f"{old_price:,.2f}")
    escaped_current = escape_markdown_v2(f"{current_price:,.2f}")
    escaped_drop = escape_markdown_v2(f"{drop_amount:,.2f}")
    escaped_percentage = escape_markdown_v2(f"{drop_percentage:.1f}")
    escaped_url = escape_markdown_v2_url(url)
    
    return (
        rf"📉 *PRICE DROP ALERT\!*" + "\n" +
        rf"• *Product:* {escaped_title}" + "\n" +
        rf"• *Platform:* {escaped_platform}" + "\n" +
        rf"• *Old Price:* ₹{escaped_old}" + "\n" +
        rf"• *New Price:* 🔥 *₹{escaped_current}*" + "\n" +
        rf"• *Price Drop:* ₹{escaped_drop} \({escaped_percentage}%\)" + "\n" +
        rf"• *Link:* [Buy Now]({escaped_url})"
    )
