"""
HMM 기반 레짐 감지
──────────────────
References:
  - Giudici, P. & Abu-Hashish, I. (2020). Finance Research Letters, 28.
  - Bouri, E., Azzi, G., & Dyhrberg, A.H. (2017). Economics E-Journal, 11.

크립토 시장을 3개 레짐(상승/횡보/하락)으로 분류하여
전략 파라미터를 동적으로 전환한다.
"""
import asyncio
from collections import deque

import numpy as np

from config import HMM_LOOKBACK_HOURS, HMM_N_STATES, HMM_RETRAIN_INTERVAL, REGIME_PARAMS
from utils import setup_logger, ts_now

log = setup_logger("regime_detector")

try:
    from hmmlearn.hmm import GaussianHMM
    HMM_AVAILABLE = True
except ImportError:
    HMM_AVAILABLE = False
    log.warning("hmmlearn 미설치 — HMM 레짐 감지 비활성화 (pip install hmmlearn)")


class RegimeDetector:
    """
    Gaussian HMM 기반 시장 레짐 분류

    관측값: [수익률, 변동성(|수익률|)]
    상태:
      0 — 상승 추세 (높은 양의 수익률, 중간 변동성)
      1 — 횡보 (낮은 수익률, 낮은 변동성)
      2 — 하락 추세 (음의 수익률, 높은 변동성)
    """

    def __init__(self) -> None:
        self._model: "GaussianHMM | None" = None
        self._prices: deque = deque(maxlen=HMM_LOOKBACK_HOURS * 60)  # 분봉 기준
        self._current_regime: int = 1  # 기본: 횡보
        self._last_train_time: float = 0.0
        self._lock = asyncio.Lock()
        self._regime_history: deque = deque(maxlen=1000)

    async def update_price(self, price: float) -> int:
        """가격 업데이트 → 필요 시 재학습 → 현재 레짐 반환"""
        async with self._lock:
            self._prices.append(price)

            now = ts_now()
            time_since_train = now - self._last_train_time

            # 재학습 주기 확인 + 최소 데이터 확보
            if (
                time_since_train >= HMM_RETRAIN_INTERVAL
                and len(self._prices) >= 120  # 최소 2시간 데이터
                and HMM_AVAILABLE
            ):
                self._train()
                self._last_train_time = now

            return self._current_regime

    def _train(self) -> None:
        """HMM 학습 및 현재 레짐 판단"""
        prices = np.array(list(self._prices))
        if len(prices) < 60:
            return

        # 수익률 계산
        returns = np.diff(np.log(prices))
        if len(returns) < 30:
            return

        # 관측값: [수익률, 절대수익률(변동성 프록시)]
        abs_returns = np.abs(returns)
        observations = np.column_stack([returns, abs_returns])

        try:
            model = GaussianHMM(
                n_components=HMM_N_STATES,
                covariance_type="full",
                n_iter=100,
                random_state=42,
            )
            model.fit(observations)

            # 현재 상태 추론
            hidden_states = model.predict(observations)
            current_state = int(hidden_states[-1])

            # 상태를 수익률 평균 기준으로 정렬 (상승=0, 횡보=1, 하락=2)
            state_means = model.means_[:, 0]  # 수익률 평균
            sorted_indices = np.argsort(state_means)[::-1]  # 내림차순
            state_mapping = {old: new for new, old in enumerate(sorted_indices)}

            self._current_regime = state_mapping.get(current_state, 1)
            self._regime_history.append(self._current_regime)
            self._model = model

            regime_name = REGIME_PARAMS.get(self._current_regime, {}).get("name", "?")
            log.info(
                "HMM 재학습 완료 | 레짐: %s (%d) | 데이터: %d개",
                regime_name,
                self._current_regime,
                len(observations),
            )

        except Exception as e:
            log.error("HMM 학습 실패: %s", e)

    @property
    def current_regime(self) -> int:
        return self._current_regime

    @property
    def regime_name(self) -> str:
        return REGIME_PARAMS.get(self._current_regime, {}).get("name", "UNKNOWN")

    def get_regime_params(self) -> dict:
        """현재 레짐에 해당하는 전략 파라미터 반환"""
        return REGIME_PARAMS.get(self._current_regime, REGIME_PARAMS[1])

    def get_signal(self) -> float:
        """레짐을 정규화 시그널로 변환 (-1 ~ +1)"""
        mapping = {0: 1.0, 1: 0.0, 2: -1.0}
        return mapping.get(self._current_regime, 0.0)
