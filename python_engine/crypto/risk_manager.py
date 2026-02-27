"""
리스크 관리 — Kelly Criterion + CVaR + Circuit Breaker
───────────────────────────────────────────────────────
References:
  - Kelly, J.L. (1956). Bell System Technical Journal, 35(4).
  - Thorp, E.O. (2006). Handbook of Asset and Liability Management.
  - Rockafellar, R.T. & Uryasev, S. (2000). Journal of Risk, 2(3).
"""
import asyncio
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from config import (
    COOLDOWN_SECONDS,
    DAILY_CVAR_LIMIT,
    KELLY_FRACTION,
    KELLY_MIN_TRADES_FOR_CALC,
    MAX_CONCURRENT_POSITIONS,
    MAX_CONSECUTIVE_LOSSES,
    MAX_SINGLE_POSITION_RATIO,
    MAX_TOTAL_CAPITAL_KRW,
    MIN_CASH_RESERVE_RATIO,
)
from utils import setup_logger, ts_now
from telegram_bot import notifier

log = setup_logger("risk_manager")


@dataclass
class TradeRecord:
    """개별 거래 기록"""
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    timestamp: float


@dataclass
class Position:
    """현재 보유 포지션"""
    symbol: str
    entry_price: float
    quantity: float
    entry_time: float
    highest_price: float = 0.0
    trailing_active: bool = False


class RiskManager:
    """
    4중 방어선 리스크 관리 시스템

    방어선 1 — Fractional Kelly 포지션 사이징:
      Kelly (1956)의 최적 베팅 비율에 Thorp (2006)의 Fractional 보정 적용.
      f* = (bp - q) / b, 실제 사용: f*/4 (파산 확률 최소화)

    방어선 2 — CVaR 일일 한도:
      Rockafellar & Uryasev (2000)의 CVaR 프레임워크.
      95% 신뢰수준에서 꼬리 손실이 한도 초과 시 당일 매매 중단.

    방어선 3 — Circuit Breaker:
      연속 N회 손절 시 쿨다운 기간 강제 적용.

    방어선 4 — 구조적 제한:
      최대 동시 보유 수, 개별 종목 비율 상한, 현금 보유 의무.
    """

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._trade_history: deque[TradeRecord] = deque(maxlen=1000)
        self._daily_pnl: float = 0.0
        self._daily_pnl_history: deque[float] = deque(maxlen=100)
        self._consecutive_losses: int = 0
        self._cooldown_until: float = 0.0
        self._lock = asyncio.Lock()
        self._daily_trades: list[TradeRecord] = []

    # ── Kelly Criterion ──────────────────────────────
    def _calc_kelly_fraction(self) -> float:
        """
        Kelly Criterion: f* = (bp - q) / b
        b = 평균 이익 / 평균 손실 (payoff ratio)
        p = 승률, q = 1 - p
        """
        if len(self._trade_history) < KELLY_MIN_TRADES_FOR_CALC:
            return KELLY_FRACTION  # 데이터 부족 시 기본값

        wins = [t for t in self._trade_history if t.pnl > 0]
        losses = [t for t in self._trade_history if t.pnl <= 0]

        if not wins or not losses:
            return KELLY_FRACTION

        p = len(wins) / len(self._trade_history)
        q = 1 - p
        avg_win = np.mean([t.pnl_pct for t in wins])
        avg_loss = abs(np.mean([t.pnl_pct for t in losses]))

        if avg_loss == 0:
            return KELLY_FRACTION

        b = avg_win / avg_loss  # payoff ratio
        kelly = (b * p - q) / b

        # Fractional Kelly: 실제 Kelly의 1/4만 사용 (Thorp 권고)
        fractional = kelly * KELLY_FRACTION
        # 안전 범위: 0 ~ 최대 개별 비율
        clamped = max(0.0, min(fractional, MAX_SINGLE_POSITION_RATIO))

        log.debug(
            "Kelly: p=%.2f b=%.2f f*=%.4f fractional=%.4f",
            p, b, kelly, clamped,
        )
        return clamped

    # ── CVaR 계산 ────────────────────────────────────
    def _calc_daily_cvar(self, confidence: float = 0.95) -> float:
        """
        Conditional Value at Risk (CVaR) — 95% 신뢰수준
        = VaR를 초과하는 손실들의 평균
        """
        if len(self._daily_pnl_history) < 10:
            return 0.0

        returns = np.array(list(self._daily_pnl_history))
        var_cutoff = np.percentile(returns, (1 - confidence) * 100)
        tail_losses = returns[returns <= var_cutoff]

        if len(tail_losses) == 0:
            return var_cutoff

        cvar = float(np.mean(tail_losses))
        return cvar

    # ── 진입 가능 여부 판단 ──────────────────────────
    async def can_enter(
        self, symbol: str, available_cash: float, regime_cash_ratio: float = 0.20
    ) -> tuple[bool, str, float]:
        """
        진입 가능 여부 + 허용 금액 반환.
        Returns: (can_enter, reason, max_amount_krw)
        """
        async with self._lock:
            now = ts_now()

            # Circuit Breaker 확인
            if now < self._cooldown_until:
                remaining = int(self._cooldown_until - now)
                return False, f"쿨다운 중 ({remaining}초 남음)", 0.0

            # 일일 CVaR 한도 확인
            if self._daily_pnl / MAX_TOTAL_CAPITAL_KRW <= DAILY_CVAR_LIMIT:
                return False, f"일일 CVaR 한도 도달 ({self._daily_pnl:,.0f}원)", 0.0

            # 동시 보유 한도
            if len(self._positions) >= MAX_CONCURRENT_POSITIONS:
                return False, f"동시 보유 {MAX_CONCURRENT_POSITIONS}개 한도", 0.0

            # 이미 보유 중인 종목
            if symbol in self._positions:
                return False, f"{symbol} 이미 보유 중", 0.0

            # 현금 보유 의무
            effective_reserve = max(MIN_CASH_RESERVE_RATIO, regime_cash_ratio)
            min_cash = MAX_TOTAL_CAPITAL_KRW * effective_reserve
            investable = available_cash - min_cash
            if investable <= 0:
                return False, "현금 보유 비율 부족", 0.0

            # Kelly 기반 포지션 사이즈
            kelly_frac = self._calc_kelly_fraction()
            max_amount = min(
                investable,
                MAX_TOTAL_CAPITAL_KRW * kelly_frac,
                MAX_TOTAL_CAPITAL_KRW * MAX_SINGLE_POSITION_RATIO,
            )

            return True, "진입 가능", max_amount

    # ── 포지션 등록/해제 ─────────────────────────────
    async def register_position(
        self, symbol: str, entry_price: float, quantity: float
    ) -> None:
        async with self._lock:
            self._positions[symbol] = Position(
                symbol=symbol,
                entry_price=entry_price,
                quantity=quantity,
                entry_time=ts_now(),
                highest_price=entry_price,
            )
            log.info(
                "포지션 등록: %s @ %,.0f × %.8f",
                symbol, entry_price, quantity,
            )

    async def close_position(
        self, symbol: str, exit_price: float
    ) -> TradeRecord | None:
        async with self._lock:
            pos = self._positions.pop(symbol, None)
            if pos is None:
                return None

            pnl = (exit_price - pos.entry_price) * pos.quantity
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price

            record = TradeRecord(
                symbol=symbol,
                side="long",
                entry_price=pos.entry_price,
                exit_price=exit_price,
                quantity=pos.quantity,
                pnl=pnl,
                pnl_pct=pnl_pct,
                timestamp=ts_now(),
            )
            self._trade_history.append(record)
            self._daily_trades.append(record)
            self._daily_pnl += pnl

            # 연속 손절 카운터
            if pnl < 0:
                self._consecutive_losses += 1
                if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                    self._cooldown_until = ts_now() + COOLDOWN_SECONDS
                    reason_msg = f"연속 {self._consecutive_losses}회 손절로 인한 안전 쿨다운 ({COOLDOWN_SECONDS}초) 발동"
                    log.warning(reason_msg)
                    # 텔레그램 비동기 알림 (fire and forget)
                    asyncio.create_task(notifier.send_emergency_stop(reason=reason_msg))
            else:
                self._consecutive_losses = 0

            emoji = "+" if pnl >= 0 else ""
            log.info(
                "포지션 청산: %s | %,.0f → %,.0f | P&L: %s%,.0f원 (%.2f%%)",
                symbol,
                pos.entry_price,
                exit_price,
                emoji,
                pnl,
                pnl_pct * 100,
            )
            return record

    # ── 트레일링 스탑 업데이트 ───────────────────────
    async def update_price(
        self,
        symbol: str,
        current_price: float,
        realized_volatility: float,
        trailing_mult: float = 1.5,
    ) -> dict | None:
        """
        가격 업데이트 → 트레일링 스탑 / 손절 판단.
        realized_volatility: Andersen & Bollerslev (1998) 실현변동성
        Returns: {"action": "stop_loss"|"trailing_stop"|None, ...}
        """
        async with self._lock:
            pos = self._positions.get(symbol)
            if pos is None:
                return None

            pnl_pct = (current_price - pos.entry_price) / pos.entry_price

            # 고점 갱신
            if current_price > pos.highest_price:
                pos.highest_price = current_price

            # 동적 손절: stop = entry × (1 - k × RV)
            from config import STOP_LOSS_MULTIPLIER, TRAILING_ACTIVATION_PCT, TRAILING_OFFSET_MULTIPLIER

            rv = max(realized_volatility, 0.005)  # 최소 0.5%
            stop_loss_pct = STOP_LOSS_MULTIPLIER * rv
            stop_price = pos.entry_price * (1 - stop_loss_pct)

            # 손절 확인
            if current_price <= stop_price:
                return {
                    "action": "stop_loss",
                    "pnl_pct": pnl_pct,
                    "stop_price": stop_price,
                }

            # 트레일링 활성화
            if pnl_pct >= TRAILING_ACTIVATION_PCT:
                pos.trailing_active = True

            if pos.trailing_active:
                trailing_offset = TRAILING_OFFSET_MULTIPLIER * rv * trailing_mult
                trailing_stop = pos.highest_price * (1 - trailing_offset)
                if current_price <= trailing_stop:
                    return {
                        "action": "trailing_stop",
                        "pnl_pct": pnl_pct,
                        "highest": pos.highest_price,
                        "trailing_stop": trailing_stop,
                    }

            return None

    # ── 일일 리셋 ────────────────────────────────────
    async def daily_reset(self) -> dict:
        """일일 P&L 저장 및 리셋"""
        async with self._lock:
            daily_pnl_pct = self._daily_pnl / MAX_TOTAL_CAPITAL_KRW
            self._daily_pnl_history.append(daily_pnl_pct)

            summary = {
                "daily_pnl": self._daily_pnl,
                "daily_pnl_pct": daily_pnl_pct,
                "trades": len(self._daily_trades),
                "wins": sum(1 for t in self._daily_trades if t.pnl > 0),
                "losses": sum(1 for t in self._daily_trades if t.pnl <= 0),
                "cvar_95": self._calc_daily_cvar(),
            }

            self._daily_pnl = 0.0
            self._daily_trades.clear()
            self._consecutive_losses = 0

            log.info("일일 리셋: P&L %+,.0f원 (%.2f%%)", summary["daily_pnl"], summary["daily_pnl_pct"] * 100)
            return summary

    # ── 현재 상태 ────────────────────────────────────
    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def is_cooldown(self) -> bool:
        return ts_now() < self._cooldown_until

    def get_stats(self) -> dict:
        wins = [t for t in self._trade_history if t.pnl > 0]
        total = len(self._trade_history)
        return {
            "total_trades": total,
            "win_rate": len(wins) / total if total > 0 else 0,
            "avg_pnl_pct": float(np.mean([t.pnl_pct for t in self._trade_history])) if total > 0 else 0,
            "kelly_fraction": self._calc_kelly_fraction(),
            "cvar_95": self._calc_daily_cvar(),
            "consecutive_losses": self._consecutive_losses,
            "active_positions": len(self._positions),
            "daily_pnl": self._daily_pnl,
        }
