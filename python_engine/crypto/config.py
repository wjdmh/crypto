"""
Project Chronos — Crypto V2 설정
모든 민감 정보는 .env에서 로드합니다.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# .env 로드 (project_chronos 폴더 최상단에 있는 .env 파일 지정)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)


# ── 빗썸 API ────────────────────────────────────────
BITHUMB_API_KEY: str = os.environ.get("BITHUMB_API_KEY", "")
BITHUMB_SECRET_KEY: str = os.environ.get("BITHUMB_SECRET_KEY", "")
BITHUMB_REST_URL: str = "https://api.bithumb.com"
BITHUMB_WS_URL: str = "wss://pubwss.bithumb.com/pub/ws"

# ── 바이낸스 (펀딩비 조회) ──────────────────────────
BINANCE_API_KEY: str = os.environ.get("BINANCE_API_KEY", "")
BINANCE_REST_URL: str = "https://fapi.binance.com"

# ── 텔레그램 ────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── 서버 (크립토 전용 포트) ────────────────────────────
DASHBOARD_PORT: int = int(os.environ.get("CRYPTO_DASHBOARD_PORT", "8000"))
WEBHOOK_PORT: int = int(os.environ.get("CRYPTO_WEBHOOK_PORT", "8001"))

# ── 매매 대상 ───────────────────────────────────────
TARGET_SYMBOLS: list[str] = ["BTC", "ETH", "XRP", "SOL", "DOGE"]

# ── 자본 관리 (Rockafellar & Uryasev, 2000 — CVaR 기반) ──
MAX_TOTAL_CAPITAL_KRW: int = 50_000_000
MIN_CASH_RESERVE_RATIO: float = 0.20
MAX_SINGLE_POSITION_RATIO: float = 0.20
MAX_CONCURRENT_POSITIONS: int = 3
DAILY_CVAR_LIMIT: float = -0.03

# ── OBI 파라미터 (Cont, Stoikov & Talreja, 2010) ─────
OBI_THRESHOLD: float = 0.60
OBI_DEPTH_LEVELS: int = 10
OBI_LOOKBACK: int = 20

# ── VPIN 파라미터 (Easley, López de Prado & O'Hara, 2012) ──
VPIN_BUCKET_SIZE: int = 50
VPIN_NUM_BUCKETS: int = 50
VPIN_DANGER_THRESHOLD: float = 0.80

# ── 손절/익절 (Andersen & Bollerslev, 1998) ──────────
STOP_LOSS_MULTIPLIER: float = 2.0
TRAILING_ACTIVATION_PCT: float = 0.015
TRAILING_OFFSET_MULTIPLIER: float = 1.5

# ── Kelly Criterion (Kelly, 1956; Thorp, 2006) ───────
KELLY_FRACTION: float = 0.25
KELLY_MIN_TRADES_FOR_CALC: int = 20

# ── HMM 레짐 감지 (Giudici & Abu-Hashish, 2020) ─────
HMM_N_STATES: int = 3
HMM_LOOKBACK_HOURS: int = 168
HMM_RETRAIN_INTERVAL: int = 3600

# ── GARCH (Katsiampa, 2017; Ardia et al., 2019) ─────
GARCH_LOOKBACK: int = 500
GARCH_RETRAIN_INTERVAL: int = 1800

# ── Circuit Breaker ──────────────────────────────────
MAX_CONSECUTIVE_LOSSES: int = 3
COOLDOWN_SECONDS: int = 1800

# ── 모멘텀 (Moskowitz, Ooi & Pedersen, 2012) ────────
MOMENTUM_WINDOWS: list[int] = [60, 240, 1440, 10080]
MOMENTUM_WEIGHTS: list[float] = [0.4, 0.3, 0.2, 0.1]

# ── 시그널 앙상블 가중치 ─────────────────────────────
ENSEMBLE_WEIGHTS: dict[str, float] = {
    "obi": 0.30,
    "vpin": 0.15,
    "momentum": 0.15,
    "regime": 0.15,
    "sentiment": 0.10,
    "funding": 0.10,
    "volatility": 0.05,
}

# ── 레짐별 전략 파라미터 ─────────────────────────────
REGIME_PARAMS: dict[int, dict] = {
    0: {  # 상승 추세
        "name": "BULLISH",
        "kelly_mult": 1.0,
        "cash_ratio": 0.20,
        "trailing_mult": 2.0,
    },
    1: {  # 횡보
        "name": "SIDEWAYS",
        "kelly_mult": 0.5,
        "cash_ratio": 0.40,
        "trailing_mult": 1.5,
    },
    2: {  # 하락 추세
        "name": "BEARISH",
        "kelly_mult": 0.25,
        "cash_ratio": 0.80,
        "trailing_mult": 1.0,
    },
}
