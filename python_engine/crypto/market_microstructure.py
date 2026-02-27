"""
시장 미시구조 분석 — OBI + OFI + VPIN
──────────────────────────────────────
References:
  - Cont, R., Stoikov, S., & Talreja, R. (2010). Operations Research, 58(3).
  - Easley, D., López de Prado, M., & O'Hara, M. (2012). Review of Financial Studies, 25(5).
  - Amihud, Y. (2002). Journal of Financial Markets, 5(1).
"""
import asyncio
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from config import (
    OBI_DEPTH_LEVELS,
    OBI_LOOKBACK,
    OBI_THRESHOLD,
    VPIN_BUCKET_SIZE,
    VPIN_DANGER_THRESHOLD,
    VPIN_NUM_BUCKETS,
)
from utils import setup_logger, ts_now

log = setup_logger("microstructure")


@dataclass
class SymbolMicroState:
    """심볼별 미시구조 상태"""
    symbol: str
    # OBI
    obi_history: deque = field(default_factory=lambda: deque(maxlen=200))
    current_obi: float = 0.0
    obi_sma: float = 0.0
    # OFI
    ofi_history: deque = field(default_factory=lambda: deque(maxlen=200))
    current_ofi: float = 0.0
    prev_best_bid_qty: float = 0.0
    prev_best_ask_qty: float = 0.0
    prev_best_bid_price: float = 0.0
    prev_best_ask_price: float = 0.0
    # VPIN
    trade_bucket: list = field(default_factory=list)
    vpin_buckets: deque = field(default_factory=lambda: deque(maxlen=100))
    current_vpin: float = 0.0
    # Amihud
    amihud_history: deque = field(default_factory=lambda: deque(maxlen=100))
    current_amihud: float = 0.0
    # 가격
    last_price: float = 0.0
    prices: deque = field(default_factory=lambda: deque(maxlen=2000))
    last_update: float = 0.0


class MarketMicrostructure:
    """
    OBI (Order Book Imbalance) — Cont et al. (2010)
    ────────────────────────────────────────────────
    가장 강력한 단기 예측 지표. 매수/매도 호가의 수량 불균형으로
    향후 수 초~수 분의 가격 방향을 예측한다.

    OBI = (V_bid - V_ask) / (V_bid + V_ask)
    범위: [-1, +1], +1에 가까울수록 매수 압도적

    VPIN (Volume-Synchronized Probability of Informed Trading) — Easley et al. (2012)
    ────────────────────────────────────────────────────────────────────────────────
    거래량 기반으로 "정보 거래자"의 비율을 추정.
    VPIN이 높으면 시장에 정보 비대칭이 커져 급변 위험이 높다.

    Amihud Illiquidity — Amihud (2002)
    ──────────────────────────────────
    ILLIQ = |r| / Volume — 거래량 대비 가격 충격.
    높을수록 유동성이 낮아 슬리피지 위험 증가.
    """

    def __init__(self) -> None:
        self._states: dict[str, SymbolMicroState] = {}
        self._lock = asyncio.Lock()
        self._price_history: dict[str, list[float]] = {}

    def _get_state(self, symbol: str) -> SymbolMicroState:
        if symbol not in self._states:
            self._states[symbol] = SymbolMicroState(symbol=symbol)
        return self._states[symbol]

    # ── OBI 업데이트 ─────────────────────────────────
    async def update_orderbook(self, symbol: str, bids: list, asks: list) -> float:
        """
        호가 데이터로 OBI + OFI 갱신.
        bids/asks: [{"price": float, "quantity": float}, ...]
        Returns: current OBI
        """
        async with self._lock:
            state = self._get_state(symbol)
            state.last_update = ts_now()

            # 상위 N단계 호가 합산
            depth = min(OBI_DEPTH_LEVELS, len(bids), len(asks))
            if depth == 0:
                return 0.0

            total_bid = sum(float(b.get("quantity", 0)) for b in bids[:depth])
            total_ask = sum(float(a.get("quantity", 0)) for a in asks[:depth])
            total = total_bid + total_ask

            if total == 0:
                return 0.0

            # OBI 계산
            obi = (total_bid - total_ask) / total
            state.current_obi = obi
            state.obi_history.append(obi)

            # OBI 이동평균
            if len(state.obi_history) >= OBI_LOOKBACK:
                state.obi_sma = float(
                    np.mean(list(state.obi_history)[-OBI_LOOKBACK:])
                )

            # OFI (Order Flow Imbalance) 계산
            best_bid_price = float(bids[0].get("price", 0)) if bids else 0
            best_ask_price = float(asks[0].get("price", 0)) if asks else 0
            best_bid_qty = float(bids[0].get("quantity", 0)) if bids else 0
            best_ask_qty = float(asks[0].get("quantity", 0)) if asks else 0

            if state.prev_best_bid_price > 0:
                # OFI = ΔBid_qty - ΔAsk_qty (가격 변동 고려)
                delta_bid = 0.0
                if best_bid_price > state.prev_best_bid_price:
                    delta_bid = best_bid_qty
                elif best_bid_price == state.prev_best_bid_price:
                    delta_bid = best_bid_qty - state.prev_best_bid_qty
                else:
                    delta_bid = -state.prev_best_bid_qty

                delta_ask = 0.0
                if best_ask_price < state.prev_best_ask_price:
                    delta_ask = best_ask_qty
                elif best_ask_price == state.prev_best_ask_price:
                    delta_ask = best_ask_qty - state.prev_best_ask_qty
                else:
                    delta_ask = -state.prev_best_ask_qty

                ofi = delta_bid - delta_ask
                state.current_ofi = ofi
                state.ofi_history.append(ofi)

            state.prev_best_bid_price = best_bid_price
            state.prev_best_ask_price = best_ask_price
            state.prev_best_bid_qty = best_bid_qty
            state.prev_best_ask_qty = best_ask_qty

            return obi

    # ── VPIN 업데이트 ────────────────────────────────
    async def update_trade(
        self, symbol: str, price: float, quantity: float, side: str
    ) -> float:
        """
        체결 데이터로 VPIN 갱신.
        Bulk Volume Classification (BVC): 체결 side로 매수/매도 분류.
        Returns: current VPIN
        """
        async with self._lock:
            state = self._get_state(symbol)
            state.last_price = price
            state.prices.append(price)

            # Amihud 비유동성 계산
            if len(state.prices) >= 2:
                ret = abs(
                    (state.prices[-1] - state.prices[-2]) / state.prices[-2]
                )
                if quantity > 0:
                    illiq = ret / (quantity * price)
                    state.amihud_history.append(illiq)
                    if len(state.amihud_history) >= 20:
                        state.current_amihud = float(
                            np.mean(list(state.amihud_history)[-20:])
                        )

            # VPIN 버킷 적재
            trade_info = {
                "buy_volume": quantity if side == "bid" else 0.0,
                "sell_volume": quantity if side == "ask" else 0.0,
            }
            state.trade_bucket.append(trade_info)

            if len(state.trade_bucket) >= VPIN_BUCKET_SIZE:
                bucket_buy = sum(t["buy_volume"] for t in state.trade_bucket)
                bucket_sell = sum(t["sell_volume"] for t in state.trade_bucket)
                state.vpin_buckets.append(abs(bucket_buy - bucket_sell))
                state.trade_bucket.clear()

                if len(state.vpin_buckets) >= VPIN_NUM_BUCKETS:
                    recent = list(state.vpin_buckets)[-VPIN_NUM_BUCKETS:]
                    total_volume = sum(
                        t["buy_volume"] + t["sell_volume"]
                        for t in state.trade_bucket
                    )
                    # VPIN = Σ|V_buy - V_sell| / (n × V_bucket)
                    # 간소화: 최근 버킷의 |buy-sell| 평균 / 최대값
                    max_imbalance = max(recent) if recent else 1
                    if max_imbalance > 0:
                        state.current_vpin = float(np.mean(recent)) / max_imbalance
                    else:
                        state.current_vpin = 0.0

            return state.current_vpin

    # ── 시그널 조회 ──────────────────────────────────
    def get_obi_signal(self, symbol: str) -> dict:
        """OBI 기반 시그널 반환"""
        state = self._get_state(symbol)
        is_strong_buy = (
            state.current_obi >= OBI_THRESHOLD
            and state.current_obi > state.obi_sma + 0.1
        )
        is_strong_sell = (
            state.current_obi <= -OBI_THRESHOLD
            and state.current_obi < state.obi_sma - 0.1
        )
        # 정규화: [-1, 1] → [-1, 1]
        normalized = np.clip(state.current_obi, -1.0, 1.0)
        return {
            "obi": state.current_obi,
            "obi_sma": state.obi_sma,
            "ofi": state.current_ofi,
            "signal": normalized,
            "is_strong_buy": is_strong_buy,
            "is_strong_sell": is_strong_sell,
        }

    def get_vpin_signal(self, symbol: str) -> dict:
        """VPIN 기반 시그널 반환 (높으면 위험)"""
        state = self._get_state(symbol)
        is_danger = state.current_vpin >= VPIN_DANGER_THRESHOLD
        # VPIN은 역시그널: 높을수록 위험 → 음수로 변환
        signal = -state.current_vpin if is_danger else 0.0
        return {
            "vpin": state.current_vpin,
            "is_danger": is_danger,
            "signal": signal,
            "amihud": state.current_amihud,
        }

    def get_last_price(self, symbol: str) -> float:
        return self._get_state(symbol).last_price

    def get_prices(self, symbol: str) -> list[float]:
        return list(self._get_state(symbol).prices)
