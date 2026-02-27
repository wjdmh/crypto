import asyncio
import logging
from bithumb_gateway import BithumbGateway
from utils import setup_logger

log = setup_logger("ws_test")

async def test_orderbook(data):
    log.info(f"[Oderbook Event] {data}")

async def test_transaction(data):
    log.info(f"[Transaction Event] {data}")

async def main():
    gateway = BithumbGateway()
    gateway.on("orderbookdepth", test_orderbook)
    gateway.on("transaction", test_transaction)
    
    log.info("Starting Bithumb WebSocket Test for 10 seconds...")
    ws_task = asyncio.create_task(gateway.start_websocket(["BTC"]))
    
    try:
        await asyncio.sleep(10)
    except KeyboardInterrupt:
        pass
    finally:
        ws_task.cancel()
        await gateway.close()
        log.info("Test completed.")

if __name__ == "__main__":
    asyncio.run(main())
