import json
import logging
import datetime
import traceback
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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

# 글로벌 상태 저장소 — 매매 엔진(scalper_engine)이 이 값을 읽어감
global_n8n_signals = {
    "target_symbol": None,
    "ai_sentiment": 0.0,
    "last_updated": None
}

# ── Pydantic 모델 (secret_token을 선택적으로 변경) ───
class WebhookPayload(BaseModel):
    symbol: str
    sentiment_score: float
    reason: str = ""
    secret_token: str = ""  # 선택적 — Gemini가 누락해도 파싱 실패 방지

WEBHOOK_SECRET = "n8n_chronos_secret_2026"


# ── 글로벌 예외 핸들러 — 모든 에러를 JSON으로 반환 (502 방지) ──
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled exception on %s: %s\n%s", request.url.path, exc, traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": str(exc)},
    )


# ── 메인 Webhook 엔드포인트 ─────────────────────────
@app.post("/webhook/n8n")
async def receive_n8n_signal(request: Request):
    """
    n8n에서 Gemini AI 평가 결과를 수신하는 엔드포인트.

    3가지 수신 방식을 모두 지원 (n8n 설정 오류에 견고하게 대응):
      1. 정상 JSON body: {"symbol":"BTC", "sentiment_score":0.85, ...}
      2. 이중 직렬화 문자열: "{\"symbol\":\"BTC\", ...}" (n8n raw body 이슈)
      3. text 래핑: {"text": "{\"symbol\":\"BTC\", ...}"} (n8n expression 이슈)
    """
    # ── Step 1: raw body 읽기 ────────────────────────
    raw_body = await request.body()
    body_str = raw_body.decode("utf-8").strip()
    log.info("Webhook 수신 [raw body]: %s", body_str[:500])

    # ── Step 2: JSON 파싱 (여러 형태 대응) ───────────
    parsed = None
    try:
        parsed = json.loads(body_str)
    except json.JSONDecodeError:
        log.error("JSON 파싱 실패: %s", body_str[:200])
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Invalid JSON"})

    # Case A: 이중 직렬화 — parsed가 문자열이면 한 번 더 파싱
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            log.error("이중 직렬화 JSON 파싱 실패: %s", parsed[:200])
            return JSONResponse(status_code=400, content={"status": "error", "detail": "Double-serialized JSON parse failed"})

    # Case B: n8n이 {"text": "..."} 형태로 보낸 경우
    if isinstance(parsed, dict) and "text" in parsed and isinstance(parsed["text"], str):
        try:
            inner = json.loads(parsed["text"])
            if isinstance(inner, dict) and "symbol" in inner:
                parsed = inner
        except json.JSONDecodeError:
            pass

    # ── Step 3: 필수 필드 검증 ───────────────────────
    if not isinstance(parsed, dict):
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Expected JSON object"})

    symbol = parsed.get("symbol", "").upper()
    sentiment_score = parsed.get("sentiment_score")
    reason = parsed.get("reason", "")
    secret_token = parsed.get("secret_token", "")

    if not symbol:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Missing 'symbol' field"})
    if sentiment_score is None:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Missing 'sentiment_score' field"})

    try:
        sentiment_score = float(sentiment_score)
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"status": "error", "detail": "sentiment_score must be a number"})

    if not -1.0 <= sentiment_score <= 1.0:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "sentiment_score must be -1.0 ~ 1.0"})

    # ── Step 4: 보안 토큰 검증 (있으면 확인, 없으면 경고만) ──
    if secret_token and secret_token != WEBHOOK_SECRET:
        log.warning("잘못된 보안 토큰: %s", secret_token)
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not secret_token:
        log.warning("secret_token 누락 — Gemini 프롬프트에 추가 권장")

    # ── Step 5: 상태 업데이트 ────────────────────────
    global_n8n_signals["target_symbol"] = symbol
    global_n8n_signals["ai_sentiment"] = sentiment_score
    global_n8n_signals["last_updated"] = datetime.datetime.now().isoformat()

    log.info("n8n AI 신호 수신 완료: %s 점수: %.2f (사유: %s)", symbol, sentiment_score, reason)

    # ── Step 6: 텔레그램 알림 ────────────────────────
    try:
        emoji = "🔥" if sentiment_score >= 0.5 else ("🧊" if sentiment_score <= -0.5 else "👀")
        msg = f"<b>[n8n AI 시그널 수신]</b>\n\n"
        msg += f"🎯 <b>타겟 종목:</b> {symbol}\n"
        msg += f"🧠 <b>AI 센티먼트:</b> {sentiment_score} {emoji}\n"
        msg += f"📝 <b>AI 분석 요약:</b> {reason}\n\n"
        msg += f"파이썬 트레이딩 엔진이 해당 종목을 포커싱하여 매수 타점을 모니터링합니다."
        await notifier.send_message(msg)
    except Exception as e:
        log.error("텔레그램 알림 실패 (무시하고 계속): %s", e)

    return {"status": "success", "message": f"Signal for {symbol} received.", "sentiment_score": sentiment_score}


@app.get("/health")
def health_check():
    return {"status": "running", "n8n_signal": global_n8n_signals}


@app.get("/debug/n8n")
def debug_n8n_state():
    """n8n 연동 상태 디버그용"""
    return {
        "current_signal": global_n8n_signals,
        "webhook_url": "POST /webhook/n8n",
        "expected_format": {
            "symbol": "BTC",
            "sentiment_score": 0.85,
            "reason": "optional description",
            "secret_token": "n8n_chronos_secret_2026 (optional)"
        }
    }

if __name__ == "__main__":
    # 포트 8000에서 FastAPI 서버 실행 (백그라운드에서 실행해야 함)
    log.info("Starting n8n Webhook Receiver API server on port 8000...")
    log.info(f"Webhook URL: http://localhost:8000/webhook/n8n (POST)")
    uvicorn.run(app, host="0.0.0.0", port=8000)
