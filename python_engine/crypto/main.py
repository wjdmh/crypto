import asyncio
import logging
import signal
import sys
import os
import traceback
import uvicorn

from api_server import app as webhook_app
from utils import setup_logger

log = setup_logger("main")

# 엔진 글로벌 인스턴스 보관
engine_instance = None


async def start_fastapi():
    """FastAPI 웹훅 서버를 비동기로 실행 — 이것은 절대 죽으면 안 된다"""
    port = int(os.environ.get("PORT", "8000"))
    config = uvicorn.Config(app=webhook_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    log.info(f"Starting Webhook API Server on http://0.0.0.0:{port} ...")
    await server.serve()


async def start_trading_engine():
    """매매 엔진을 안전하게 실행 — 실패해도 API 서버에 영향 없음"""
    global engine_instance
    try:
        from scalper_engine import CryptoScalperEngine
        from telegram_bot import notifier

        engine_instance = CryptoScalperEngine()
        log.info("Starting Crypto Scalper Engine...")
        await notifier.send_message(
            "🟢 <b>[Chronos V2 엔진 가동 시작]</b>\n\n"
            "n8n 웹훅 연결 포트 개방 완료\n"
            "빗썸 WebSocket 연결 및 모니터링 시작합니다."
        )
        await engine_instance.run()
    except Exception as e:
        log.error(f"Trading engine crashed (API server continues): {e}")
        log.error(traceback.format_exc())
        try:
            from telegram_bot import notifier
            await notifier.send_emergency_stop(str(e))
        except Exception:
            pass


async def main():
    """메인 실행 루프 — API 서버 우선, 엔진은 독립 태스크"""

    # API 서버는 반드시 살아있어야 함 (Railway healthcheck 대응)
    api_task = asyncio.create_task(start_fastapi())

    # 엔진은 5초 후 시작 (API 서버가 먼저 올라오도록)
    await asyncio.sleep(5)
    engine_task = asyncio.create_task(start_trading_engine())

    # 엔진이 죽어도 API 서버는 계속 살아있음
    done, pending = await asyncio.wait(
        [api_task, engine_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in done:
        if task == engine_task:
            log.warning("Trading engine stopped. API server continues running.")
            # 엔진이 죽었지만 API 서버는 계속 대기
            await api_task
        elif task == api_task:
            log.error("API server stopped unexpectedly!")
            # API 서버가 죽으면 엔진도 종료
            for t in pending:
                t.cancel()


def handle_shutdown(signum, frame):
    log.info("Shutting down cleanly...")
    if engine_instance:
        engine_instance._running = False
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    log.info("Initializing Chronos V2 AI-Quant System...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        handle_shutdown(None, None)
