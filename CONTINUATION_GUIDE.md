# Project Chronos — Crypto V2 이어서 작업 가이드

> 이 문서는 다른 Claude 세션(또는 다른 모델)에서 이어서 작업하기 위한 완전한 가이드입니다.
> 이 가이드를 첨부하고 "이 가이드를 읽고 이어서 작업해줘"라고 요청하세요.

---

## 1. 프로젝트 개요

**목적:** 빗썸(Bithumb) 거래소 기반 24시간 크립토 자동매매 시스템
**기반:** 18편의 실제 학술 논문 기반 알고리즘 (허구 논문 없음)
**프로젝트 경로:** `/Users/jeongmuhyeon/Desktop/biseo/project_chronos/`

---

## 2. 현재 완료된 작업 (Phase 1 완료)

### 2.1 계획서 전면 재작성 ✅
- 파일: `/Users/jeongmuhyeon/Desktop/biseo/implementation_plan.md.resolved`
- 허구 논문 6개 제거 → 실존 검증 논문 18편으로 교체
- 12대 핵심 알고리즘을 Tier 1/2/3으로 분류
- 4중 방어선 리스크 관리 체계 설계

### 2.2 구현 완료 파일 (10개) ✅

```
project_chronos/
├── .env                      ← API 키 저장소 (빈 상태, 사용자가 입력해야 함)
├── .env.example              ← 환경변수 템플릿
├── requirements.txt          ← Python 의존성 (미설치 상태)
└── python_engine/
    ├── config.py             ← .env 로드 + 전체 파라미터 정의 (109줄)
    ├── utils.py              ← 로깅 유틸리티 (28줄)
    ├── bithumb_gateway.py    ← 빗썸 REST + WebSocket 통신 (260줄)
    ├── market_microstructure.py ← OBI + OFI + VPIN + Amihud (251줄)
    ├── risk_manager.py       ← Kelly + CVaR + Circuit Breaker (357줄)
    ├── regime_detector.py    ← HMM 3-레짐 감지 (130줄)
    ├── volatility_model.py   ← GARCH(1,1) + 실현변동성 (142줄)
    ├── signal_ensemble.py    ← 7-시그널 가중 앙상블 + 펀딩비 (189줄)
    ├── scalper_engine.py     ← 메인 매매 루프 (336줄)
    └── webhook_server.py     ← FastAPI + Dashboard HTML + n8n Webhook (241줄)
```

### 2.3 각 모듈 상세

| 모듈 | 기반 논문 | 핵심 기능 |
|------|-----------|-----------|
| `market_microstructure.py` | Cont(2010), Easley(2012), Amihud(2002) | 호가 OBI 계산, 체결 VPIN 계산, OFI 추적, Amihud 비유동성 |
| `risk_manager.py` | Kelly(1956), Thorp(2006), Rockafellar(2000) | Fractional Kelly(f*/4) 사이징, 95% CVaR 일일한도, Circuit Breaker(연속 3손절→30분 쿨다운) |
| `regime_detector.py` | Giudici(2020), Bouri(2017) | GaussianHMM 3-상태(상승/횡보/하락), 1시간마다 재학습 |
| `volatility_model.py` | Katsiampa(2017), Andersen(1998) | GARCH(1,1) Student-t 분포, 실현변동성(RV), 동적 손절폭 연동 |
| `signal_ensemble.py` | Moskowitz(2012), Ackerer(2024) | OBI(0.30)+VPIN(0.15)+모멘텀(0.15)+레짐(0.15)+센티먼트(0.10)+펀딩비(0.10)+변동성(0.05) |
| `scalper_engine.py` | 전체 통합 | WebSocket 호가/체결 수신 → 미시구조 분석 → 앙상블 → 주문 실행 |
| `webhook_server.py` | - | FastAPI 서버, n8n 센티먼트 Webhook 수신, 비상정지 API, 실시간 Dashboard |

---

## 3. 아직 미완료 작업 (해야 할 것)

### Phase 2: 지능 강화 (우선)
- [ ] **의존성 설치 및 테스트 실행**: venv에 requirements.txt 패키지 설치
- [ ] **빗썸 API 키 입력**: 사용자에게 키를 받아 `.env`에 입력
- [ ] **빗썸 WebSocket 데이터 파싱 검증**: 실제 빗썸 WS 메시지 포맷과 `_on_orderbook`, `_on_transaction` 콜백의 파싱 로직이 정확히 매칭되는지 확인 필요
- [ ] **모멘텀 시그널 가격 히스토리 연결**: `signal_ensemble.py`의 `calc_momentum_signal()`에 사용할 가격 히스토리를 `scalper_engine.py`에서 올바르게 전달하는지 확인
- [ ] **GARCH/HMM 초기 데이터 문제**: 엔진 시작 직후 데이터가 부족할 때의 초기화 전략 보완 (REST API로 과거 캔들 데이터를 먼저 로드하는 로직 추가)

### Phase 3: 고급 기능
- [ ] **backtesting/data_collector.py**: 빗썸 REST API로 과거 OHLCV 데이터 수집/저장
- [ ] **backtesting/backtester.py**: 수집된 데이터로 전략 시뮬레이션
- [ ] **텔레그램 알림**: python-telegram-bot으로 일일 리포트, 비상정지 연동
- [ ] **교차 거래소 가격 모니터링**: 바이낸스-빗썸 가격 괴리율 실시간 추적
- [ ] **FinRL (강화학습) 에이전트**: Phase 3 최종 단계

### 보안/인프라
- [ ] **기존 KIS 코드 정리**: `kis_auth.py`, `kis_trader.py`, `kis_token_cache.json`은 주식용 레거시 → 삭제 또는 별도 폴더로 이동
- [ ] **대시보드 개선**: `dashboard/index.html` (기존 KIS용 685줄)을 새 `webhook_server.py` 내장 HTML과 통합 또는 교체
- [ ] **단위 테스트 작성**: `tests/` 디렉토리에 각 모듈별 테스트
- [ ] **Railway 배포 설정**: Dockerfile, railway.toml

---

## 4. 알려진 이슈 & 주의사항

### 4.1 빗썸 WebSocket 메시지 포맷
`bithumb_gateway.py`에서 WebSocket 구독 시 응답 포맷이 실제와 다를 수 있음.
빗썸 공식 문서: https://apidocs.bithumb.com/reference/웹소켓-연결
특히 `orderbookdepth` 메시지의 `content.list` 구조가 정확한지 실제 연결 후 검증 필요.

### 4.2 의존성 미설치
기존 venv(`python_engine/venv`)에는 requirements.txt의 새 패키지가 설치되지 않은 상태.
```bash
cd /Users/jeongmuhyeon/Desktop/biseo/project_chronos
pip install -r requirements.txt
```

### 4.3 기존 KIS 코드와의 충돌
`python_engine/` 안에 KIS(한국투자증권) 주식용 레거시 파일이 남아있음:
- `kis_auth.py` (KIS OAuth 인증)
- `kis_trader.py` (KIS 주문/잔고)
- `kis_token_cache.json` (토큰 캐시)

이들은 크립토 엔진과 무관하며, 나중에 주식+크립토 통합을 원하지 않는다면 정리 필요.

### 4.4 하드코딩된 API 키 (보안 위험)
기존 `config.py`에 하드코딩되어 있던 KIS API 키는 이번 작업에서 .env 기반으로 교체됨.
하지만 git 히스토리에 키가 남아있을 수 있으므로 키 재발급 권장.
n8n 워크플로우 JSON 안에도 Naver API 키가 하드코딩되어 있음:
- `n8n_workflows/agentic_narrative.json` → `X-Naver-Client-Id`, `X-Naver-Client-Secret`

---

## 5. 아키텍처 흐름도 (데이터 플로우)

```
[빗썸 WebSocket] ─── 호가(orderbookdepth) ──→ market_microstructure.py → OBI, OFI
                 ─── 체결(transaction) ──────→ market_microstructure.py → VPIN, Amihud
                                              volatility_model.py → RV, GARCH
                                              regime_detector.py → HMM 레짐
                                                        ↓
                                              signal_ensemble.py ← 센티먼트(n8n webhook)
                                                        ↓        ← 펀딩비(바이낸스 API)
                                              compute_final_score()
                                                        ↓
                                              risk_manager.py → can_enter? Kelly sizing
                                                        ↓
                                              bithumb_gateway.py → place_order()
                                                        ↓
                                              risk_manager.py → register_position()
                                                        ↓ (보유 중)
                                              risk_manager.py → update_price() → 트레일링/손절
                                                        ↓
                                              bithumb_gateway.py → place_order(매도)
```

---

## 6. 시그널 앙상블 의사결정 테이블

```python
final_score = (
    0.30 × OBI      +   # 미시구조: Cont et al. (2010)
    0.15 × VPIN     +   # 독성 필터: Easley et al. (2012)
    0.15 × Momentum +   # 추세: Moskowitz et al. (2012)
    0.15 × Regime   +   # HMM: Giudici & Abu-Hashish (2020)
    0.10 × Sentiment +  # NLP: n8n → Gemini
    0.10 × Funding  +   # 수급: Ackerer et al. (2024)
    0.05 × Volatility   # GARCH: Katsiampa (2017)
)

score ≥  0.7 → strong_buy (Kelly × 1.0)
score ≥  0.5 → buy        (Kelly × 0.5)
score ≤ -0.7 → strong_sell (즉시 전량 청산)
score ≤ -0.3 → sell        (포지션 축소)
else         → hold
VPIN > 0.8   → 신규 진입 차단
CVaR ≤ -3%   → 당일 매매 중단
```

---

## 7. 환경변수 (사용자에게 받아야 할 키)

```env
# 필수
BITHUMB_API_KEY=          # 빗썸 Connect Key
BITHUMB_SECRET_KEY=       # 빗썸 Secret Key

# 권장
TELEGRAM_BOT_TOKEN=       # 텔레그램 봇 토큰 (알림용)
TELEGRAM_CHAT_ID=         # 텔레그램 채팅 ID

# 선택
GEMINI_API_KEY=           # Google Gemini (n8n 센티먼트 분석)
BINANCE_API_KEY=          # 바이낸스 (펀딩비, 공개 API라 없어도 동작)
```

---

## 8. 실행 방법

```bash
cd /Users/jeongmuhyeon/Desktop/biseo/project_chronos/python_engine

# 1. 의존성 설치 (최초 1회)
pip install -r ../requirements.txt

# 2. .env에 API 키 입력 (필수)

# 3. 엔진 실행
python webhook_server.py

# 대시보드: http://localhost:8000
# n8n 센티먼트 Webhook: POST http://localhost:8000/webhook/sentiment
# 비상 정지: POST http://localhost:8000/webhook/emergency {"action": "stop"}
# 상태 조회: GET http://localhost:8000/api/status
```

---

## 9. 다음 세션에서 할 일 (우선순위순)

1. **사용자에게 빗썸 API 키 요청** → `.env`에 입력
2. **`pip install -r requirements.txt`** 실행
3. **빗썸 WebSocket 실제 연결 테스트** → 메시지 파싱 로직 검증/수정
4. **과거 데이터 로더 추가** → 엔진 시작 시 REST API로 캔들 데이터 로드 (HMM/GARCH 초기화용)
5. **텔레그램 알림 연동**
6. **단위 테스트 작성**
7. **백테스팅 프레임워크 구축**
8. **Railway 배포**
