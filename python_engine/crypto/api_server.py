import logging
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from config import TELEGRAM_BOT_TOKEN
from telegram_bot import notifier
from signal_ensemble import SignalEnsemble
import uvicorn

# 로거 설정
log = logging.getLogger("api_server")
log.setLevel(logging.INFO)
formatter = logging.Formatter('[%(asctime)s] %(name)-15s %(levelname)-8s %(message)s', "%Y-%m-%d %H:%M:%S")
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
if not log.handlers:
    log.addHandler(console_handler)

app = FastAPI(title="Chronos V2 Webhook Receiver", version="2.0")

# 글로벌 상태 저장소 (실제로는 Redis나 DB를 권장하지만 메모리에 임시 저장)
# 매매 봇 메인 루프에서 이 값을 주기적으로 읽어감
global_n8n_signals = {
    "target_symbol": None,
    "ai_sentiment": 0.0,
    "last_updated": None
}

class WebhookPayload(BaseModel):
    symbol: str  # 예: "SOL", "BTC"
    sentiment_score: float # -1.0 ~ 1.0 점수
    reason: str = "" # AI가 해당 점수를 매긴 이유 (텔레그램 알림용)
    secret_token: str # 보안 검증용 토큰

# 환경변수 등에서 설정한 보안 토큰 (임시 하드코딩, 실제 운영시 .env 사용 권장)
WEBHOOK_SECRET = "n8n_chronos_secret_2026"

@app.post("/webhook/n8n")
async def receive_n8n_signal(payload: WebhookPayload):
    """
    n8n 워크플로우에서 AI 긍정/부정 판단 결과를 웹훅으로 쏴주는 엔드포인트
    예: {"symbol": "SOL", "sentiment_score": 0.85, "reason": "생태계 폭발적 성장", "secret_token": "..."}
    """
    if payload.secret_token != WEBHOOK_SECRET:
        log.warning(f"잘못된 보안 토큰 접근 시도: {payload.secret_token}")
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not -1.0 <= payload.sentiment_score <= 1.0:
        raise HTTPException(status_code=400, detail="sentiment_score must be between -1.0 and 1.0")
        
    symbol = payload.symbol.upper()
    
    # 1. 상태 업데이트 (매매 엔진이 읽어갈 수 있도록)
    global_n8n_signals["target_symbol"] = symbol
    global_n8n_signals["ai_sentiment"] = payload.sentiment_score
    import datetime
    global_n8n_signals["last_updated"] = datetime.datetime.now().isoformat()
    
    log.info(f"n8n AI 신호 수신 완료: {symbol} 점수: {payload.sentiment_score} (사유: {payload.reason})")
    
    # 2. 텔레그램으로 즉시 알림 전송
    emoji = "🔥" if payload.sentiment_score >= 0.5 else ("🧊" if payload.sentiment_score <= -0.5 else "👀")
    msg = f"<b>[n8n AI 시그널 수신]</b>\n\n"
    msg += f"🎯 <b>타겟 종목:</b> {symbol}\n"
    msg += f"🧠 <b>AI 센티먼트:</b> {payload.sentiment_score} {emoji}\n"
    msg += f"📝 <b>AI 분석 요약:</b> {payload.reason}\n\n"
    msg += f"파이썬 트레이딩 엔진이 해당 종목을 포커싱하여 매수 타점을 모니터링합니다."
    
    await notifier.send_message(msg)

    return {"status": "success", "message": f"Signal for {symbol} received and broadcasted."}

@app.get("/health")
def health_check():
    return {"status": "running"}

if __name__ == "__main__":
    # 포트 8000에서 FastAPI 서버 실행 (백그라운드에서 실행해야 함)
    log.info("Starting n8n Webhook Receiver API server on port 8000...")
    log.info(f"Webhook URL: http://localhost:8000/webhook/n8n (POST)")
    uvicorn.run(app, host="0.0.0.0", port=8000)
