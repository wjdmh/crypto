import asyncio
import logging
import signal
import sys
import uvicorn
from contextlib import asynccontextmanager

from scalper_engine import CryptoScalperEngine
from api_server import app as webhook_app
from telegram_bot import notifier
from utils import setup_logger

log = setup_logger("main")

# 엔진 글로벌 인스턴스 보관
engine_instance = None

async def start_fastapi():
    """FastAPI 웹훅 서버를 비동기로 실행"""
    config = uvicorn.Config(app=webhook_app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    log.info("Starting Webhook API Server on http://0.0.0.0:8000 ...")
    await server.serve()

async def start_trading_engine():
    """매매 엔진 스레드 실행"""
    global engine_instance
    try:
        engine_instance = CryptoScalperEngine()
        log.info("Starting Crypto Scalper Engine...")
        await notifier.send_message("🟢 <b>[Chronos V2 엔진 가동 시작]</b>\n\nn8n 웹훅 연결 포트 개방 완료(8000)\n빗썸 WebSocket 연결 및 모니터링 시작합니다.")
        await engine_instance.run()
    except Exception as e:
        log.error(f"Engine Exception: {e}")
        await notifier.send_emergency_stop(str(e))

async def main():
    """메인 실행 루프 - API 서버와 트레이딩 엔진 동시 가동"""
    
    # 두 개의 비동기 태스크를 백그라운드로 동시에 띄운다
    api_task = asyncio.create_task(start_fastapi())
    engine_task = asyncio.create_task(start_trading_engine())
    
    # 종료 시그널 핸들링 시 부드러운 종료를 위해 대기
    try:
        await asyncio.gather(api_task, engine_task)
    except asyncio.CancelledError:
        log.info("Main shutdown triggered.")

def handle_shutdown(signum, frame):
    log.info("Shutting down cleanly...")
    asyncio.create_task(notifier.send_message("🔴 <b>[Chronos V2 엔진 정지]</b>\n\n서버 종료가 감지되어 매매 봇이 안전하게 종료되었습니다."))
    # 엔진 루프 플래그 끄기
    if engine_instance:
        engine_instance._running = False
    sys.exit(0)

if __name__ == "__main__":
    # Ctrl+C 혹은 종료 시그널 대응
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    log.info("Initializing Chronos V2 AI-Quant System...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        handle_shutdown(None, None)
