import requests
import json
import time

def send_mock_n8n_signal(symbol: str, score: float, reason: str):
    """n8n에서 웹훅을 보내는 상황을 파이썬으로 가상 테스트"""
    url = "http://localhost:8000/webhook/n8n"
    payload = {
        "symbol": symbol,
        "sentiment_score": score,
        "reason": reason,
        "secret_token": "n8n_chronos_secret_2026"
    }
    
    print(f"n8n AI가 {symbol} 에 대해 {score} 점을 서버로 전송합니다...")
    try:
        response = requests.post(url, json=payload)
        print(f"서버 응답: {response.json()}")
    except requests.exceptions.ConnectionError:
        print("서버가 켜져있지 않거나 연결할 수 없습니다. 터미널에서 `python api_server.py` 를 먼저 실행해주세요.")

if __name__ == "__main__":
    print("3초 뒤 가상의 n8n 웹훅을 FastAPI 서버로 발송합니다.")
    time.sleep(3)
    send_mock_n8n_signal("SOL", 0.85, "솔라나 기반 주요 디파이 프로토콜 TVL 급변 및 X(트위터) 인플루언서 언급량 300% 폭증 포착. 강력한 매수세 예상.")
