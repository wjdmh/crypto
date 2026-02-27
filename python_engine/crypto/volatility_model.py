"""
변동성 모델 — GARCH + 실현변동성
─────────────────────────────────
References:
  - Katsiampa, P. (2017). Economics Letters, 158.
  - Ardia, D., Bluteau, K., & Rüede, M. (2019). Finance Research Letters, 29.
  - Andersen, T.G. & Bollerslev, T. (1998). International Economic Review, 39(4).
"""
import asyncio
from collections import deque

import numpy as np

from config import GARCH_LOOKBACK, GARCH_RETRAIN_INTERVAL
from utils import setup_logger, ts_now

log = setup_logger("volatility")

try:
    from arch import arch_model
    ARCH_AVAILABLE = True
except ImportError:
    ARCH_AVAILABLE = False
    log.warning("arch 미설치 — GARCH 비활성화 (pip install arch)")


class VolatilityModel:
    """
    이중 변동성 추정:

    1. 실현변동성 (Realized Volatility) — Andersen & Bollerslev (1998)
       고빈도 수익률의 제곱합으로 변동성을 비모수적으로 추정.
       RV = sqrt(Σ r_i²)

    2. GARCH(1,1) 조건부 변동성 — Katsiampa (2017)
       σ²_t = ω + α × ε²_{t-1} + β × σ²_{t-1}
       크립토의 변동성 클러스터링을 포착.
    """

    def __init__(self) -> None:
        self._prices: deque = deque(maxlen=GARCH_LOOKBACK + 100)
        self._returns: deque = deque(maxlen=GARCH_LOOKBACK + 100)
        self._rv_window: deque = deque(maxlen=60)  # 최근 60개 수익률 (RV 계산)
        self._current_rv: float = 0.01  # 기본 1%
        self._current_garch_vol: float = 0.01
        self._forecast_vol: float = 0.01
        self._last_train_time: float = 0.0
        self._lock = asyncio.Lock()

    async def update_price(self, price: float) -> float:
        """
        가격 업데이트 → RV 계산 + 주기적 GARCH 재학습
        Returns: 현재 실현변동성
        """
        async with self._lock:
            self._prices.append(price)

            if len(self._prices) >= 2:
                ret = np.log(self._prices[-1] / self._prices[-2])
                self._returns.append(ret)
                self._rv_window.append(ret)

                # 실현변동성 (RV) 갱신
                if len(self._rv_window) >= 10:
                    rv_arr = np.array(list(self._rv_window))
                    self._current_rv = float(np.sqrt(np.sum(rv_arr ** 2)))
                    # 0에 너무 가까우면 하한 설정
                    self._current_rv = max(self._current_rv, 0.001)

            # GARCH 주기적 재학습
            now = ts_now()
            if (
                now - self._last_train_time >= GARCH_RETRAIN_INTERVAL
                and len(self._returns) >= 100
                and ARCH_AVAILABLE
            ):
                self._train_garch()
                self._last_train_time = now

            return self._current_rv

    def _train_garch(self) -> None:
        """GARCH(1,1) 학습 및 1-step 예측"""
        try:
            returns_pct = np.array(list(self._returns)[-GARCH_LOOKBACK:]) * 100

            model = arch_model(
                returns_pct,
                vol="Garch",
                p=1,
                q=1,
                mean="Constant",
                dist="t",  # Student-t (크립토 fat tail)
            )
            result = model.fit(disp="off", show_warning=False)

            # 현재 조건부 변동성
            self._current_garch_vol = float(result.conditional_volatility[-1]) / 100

            # 1-step 예측
            forecast = result.forecast(horizon=1)
            self._forecast_vol = float(np.sqrt(forecast.variance.values[-1, 0])) / 100
            self._forecast_vol = max(self._forecast_vol, 0.001)

            log.info(
                "GARCH 재학습 | 현재 vol: %.4f | 예측 vol: %.4f",
                self._current_garch_vol,
                self._forecast_vol,
            )
        except Exception as e:
            log.error("GARCH 학습 실패: %s", e)

    @property
    def realized_volatility(self) -> float:
        """현재 실현변동성"""
        return self._current_rv

    @property
    def garch_volatility(self) -> float:
        """GARCH 조건부 변동성"""
        return self._current_garch_vol

    @property
    def forecast_volatility(self) -> float:
        """GARCH 1-step 예측 변동성"""
        return self._forecast_vol

    def get_signal(self) -> float:
        """
        변동성 기반 시그널:
        높은 변동성 → 음의 시그널 (포지션 축소 권고)
        낮은 변동성 → 양의 시그널 (정상 사이징)
        """
        # 변동성 백분위: 최근 변동성이 역사적으로 어디쯤인지
        if self._current_rv > 0.05:  # 5% 이상 → 극도로 높음
            return -1.0
        elif self._current_rv > 0.03:  # 3% 이상 → 높음
            return -0.5
        elif self._current_rv > 0.01:  # 1% 이상 → 보통
            return 0.0
        else:  # 1% 미만 → 낮음
            return 0.5
