import asyncio
import os
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from telegram_bot import notifier

async def test_telegram():
    print(f"Token (First 10 chars): {TELEGRAM_BOT_TOKEN[:10]}...")
    print(f"Chat ID: {TELEGRAM_CHAT_ID}")
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Error: Token or Chat ID is empty in .env file.")
        return

    print("Sending test message to Telegram...")
    success = await notifier.send_message("🔔 <b>[Telegram Test]</b>\n\n이 메시지가 보인다면 텔레그램 연동이 정상적으로 완료된 것입니다!")
    
    if success:
        print("✅ Message sent successfully!")
    else:
        print("❌ Failed to send message. Please check logs.")

if __name__ == "__main__":
    asyncio.run(test_telegram())
