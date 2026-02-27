"""
다중 시그널 앙상블
──────────────────
각 모듈(OBI, VPIN, 모멘텀, 레짐, 센티먼트, 펀딩비, 변동성)의
시그널을 가중 합산하여 최종 매매 결정을 내린다.

Reference:
  - Moskowitz, T.J., Ooi, Y.H., & Pedersen, L.H. (2012). JFE, 104(2).
    (Time-Series Momentum: 다중 타임프레임 모멘텀 가중)
"""
import asyncio
from collections import deque

import numpy as np

import aiohttp

from config import (
    BINANCE_REST_URL,
    ENSEMBLE_WEIGHTS,
    MOMENTUM_WEIGHTS,
    MOMENTUM_WINDOWS,
)
from utils import setup_logger, ts_now

log = setup_logger("ensemble")


class SignalEnsemble:
    """다중 시그널 가중 앙상블"""

    def __init__(self) -> None:
        self._sentiment_score: float = 0.0
        self._funding_rate: dict[str, float] = {}
        self._price_histories: dict[str, deque] = {}
        self._lock = asyncio.Lock()

    # ── 모멘텀 시그널 (Moskowitz et al., 2012) ───────
    def calc_momentum_signal(self, symbol: str, prices: list[float]) -> float:
        """
        Time-Series Momentum: 여러 룩백 기간의 수익률 가중 합산.
        windows: [60, 240, 1440, 10080] (1h, 4h, 1d, 7d in minutes)
        """
        if len(prices) < max(MOMENTUM_WINDOWS):
            # 데이터 부족 시 사용 가능한 범위로 계산
            if len(prices) < MOMENTUM_WINDOWS[0]:
                return 0.0

        total_signal = 0.0
        total_weight = 0.0

        for window, weight in zip(MOMENTUM_WINDOWS, MOMENTUM_WEIGHTS):
            if len(prices) >= window:
                past_price = prices[-window]
                current_price = prices[-1]
                if past_price > 0:
                    ret = (current_price - past_price) / past_price
                    # 수익률을 [-1, 1]로 클리핑
                    normalized = np.clip(ret * 10, -1.0, 1.0)  # 10% → ±1.0
                    total_signal += normalized * weight
                    total_weight += weight

        if total_weight > 0:
            return total_signal / total_weight
        return 0.0

    # ── 센티먼트 시그널 (n8n Webhook에서 수신) ───────
    async def update_sentiment(self, score: float) -> None:
        """n8n에서 전송한 센티먼트 점수 업데이트 (-1.0 ~ +1.0)"""
        async with self._lock:
            self._sentiment_score = np.clip(score, -1.0, 1.0)
            log.info("센티먼트 업데이트: %.2f", self._sentiment_score)

    def get_sentiment_signal(self) -> float:
        return self._sentiment_score

    # ── 펀딩비 시그널 (Ackerer et al., 2024) ─────────
    async def fetch_funding_rate(self, symbol: str = "BTCUSDT") -> float:
        """
        바이낸스 무기한 선물 펀딩비 조회.
        펀딩비 > +0.1%: 과도한 롱 → 하락 경고
        펀딩비 < -0.1%: 과도한 숏 → 숏 스퀴즈 기회
        (API 키가 없으면 0.0으로 기본 처리하여 무시함)
        """
        import os
        if not os.environ.get("BINANCE_API_KEY"):
            # 바이낸스 키가 없다면 스킵
            return 0.0

        try:
            url = f"{BINANCE_REST_URL}/fapi/v1/premiumIndex?symbol={symbol}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rate = float(data.get("lastFundingRate", 0))
                        self._funding_rate[symbol] = rate
                        return rate
        except Exception as e:
            log.debug("펀딩비 조회 실패 (%s): %s", symbol, e)
        return self._funding_rate.get(symbol, 0.0)

    def get_funding_signal(self, symbol: str = "BTCUSDT") -> float:
        """
        펀딩비 → 시그널 변환:
          > +0.1%: -0.5 (하락 경고)
          > +0.3%: -1.0 (강한 하락 경고)
          < -0.1%: +0.5 (숏 스퀴즈 기대)
          < -0.3%: +1.0 (강한 매수 기회)
        """
        rate = self._funding_rate.get(symbol, 0.0)
        if rate > 0.003:
            return -1.0
        elif rate > 0.001:
            return -0.5
        elif rate < -0.003:
            return 1.0
        elif rate < -0.001:
            return 0.5
        return 0.0

    # ── 최종 앙상블 ──────────────────────────────────
    def compute_final_score(
        self,
        obi_signal: float,
        vpin_signal: float,
        momentum_signal: float,
        regime_signal: float,
        sentiment_signal: float,
        funding_signal: float,
        volatility_signal: float,
    ) -> dict:
        """
        가중 합산 최종 스코어 계산.
        Returns: {
            "score": float,          # -1.0 ~ +1.0
            "action": str,           # "strong_buy" | "buy" | "hold" | "sell" | "strong_sell"
            "confidence": float,     # 0 ~ 1
            "components": dict,      # 개별 시그널
            "vpin_warning": bool,    # VPIN 위험 여부
        }
        """
        w = ENSEMBLE_WEIGHTS
        w_obi = w["obi"]
        w_vpin = w["vpin"]
        w_mom = w["momentum"]
        w_reg = w["regime"]
        w_sent = w["sentiment"]
        w_fund = w["funding"]
        w_vol = w["volatility"]

        # 만약 센티먼트나 펀딩비가 0.0으로 통제되어 제공되지 않는 환경(예: 백테스트)이라면 
        # 나머지 기술적 지표들의 비중을 그만큼 비례해서 올려준다. (Max score 보정)
        missing_weight = 0.0
        if sentiment_signal == 0.0:
            missing_weight += w_sent
            w_sent = 0.0
        if funding_signal == 0.0:
            missing_weight += w_fund
            w_fund = 0.0
            
        if missing_weight > 0:
            # 남은 가중치(0.8)에 missing_weight(0.2)를 배분
            active_weight_sum = w_obi + w_vpin + w_mom + w_reg + w_vol
            if active_weight_sum > 0:
                scale = 1.0 + (missing_weight / active_weight_sum)
                w_obi *= scale
                w_vpin *= scale
                w_mom *= scale
                w_reg *= scale
                w_vol *= scale

        score = (
            w_obi * obi_signal
            + w_vpin * vpin_signal
            + w_mom * momentum_signal
            + w_reg * regime_signal
            + w_sent * sentiment_signal
            + w_fund * funding_signal
            + w_vol * volatility_signal
        )

        # 행동 결정
        if score >= 0.7:
            action = "strong_buy"
        elif score >= 0.5:
            action = "buy"
        elif score <= -0.7:
            action = "strong_sell"
        elif score <= -0.3:
            action = "sell"
        else:
            action = "hold"

        # 신뢰도: 시그널 방향 일치도
        signals = [
            obi_signal, momentum_signal, regime_signal,
            sentiment_signal, funding_signal,
        ]
        positive = sum(1 for s in signals if s > 0.1)
        negative = sum(1 for s in signals if s < -0.1)
        total_directional = positive + negative
        if total_directional > 0:
            confidence = max(positive, negative) / len(signals)
        else:
            confidence = 0.0

        vpin_warning = vpin_signal < -0.5

        return {
            "score": float(np.clip(score, -1.0, 1.0)),
            "action": action,
            "confidence": confidence,
            "vpin_warning": vpin_warning,
            "components": {
                "obi": obi_signal,
                "vpin": vpin_signal,
                "momentum": momentum_signal,
                "regime": regime_signal,
                "sentiment": sentiment_signal,
                "funding": funding_signal,
                "volatility": volatility_signal,
            },
        }
