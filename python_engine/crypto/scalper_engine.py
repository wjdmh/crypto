"""
Project Chronos — Crypto V2 메인 매매 엔진
──────────────────────────────────────────
빗썸 WebSocket으로 실시간 호가/체결 수신 →
미시구조 분석 → 시그널 앙상블 → 주문 실행

모든 매매 결정은 학술 논문 기반 알고리즘의 합의에 의해 이루어진다.
"""
import asyncio

from bithumb_gateway import BithumbGateway
from market_microstructure import MarketMicrostructure
from regime_detector import RegimeDetector
from risk_manager import RiskManager
from signal_ensemble import SignalEnsemble
from volatility_model import VolatilityModel
from config import TARGET_SYMBOLS
from utils import setup_logger, ts_now
from api_server import global_n8n_signals # n8n 실시간 웹훅 신호 상태 반입

log = setup_logger("engine")


class CryptoScalperEngine:
    """크립토 스캘핑 메인 엔진"""

    def __init__(self) -> None:
        self.gateway = BithumbGateway()
        self.microstructure = MarketMicrostructure()
        self.regime = RegimeDetector()
        self.volatility = VolatilityModel()
        self.ensemble = SignalEnsemble()
        self.risk = RiskManager()

        self._entry_locks: dict[str, asyncio.Lock] = {
            s: asyncio.Lock() for s in TARGET_SYMBOLS
        }
        self._tick_count = 0
        self._last_heartbeat = ts_now()
        self._last_funding_fetch = 0.0
        self._running = True

        # WebSocket 콜백 등록
        self.gateway.on("orderbookdepth", self._on_orderbook)
        self.gateway.on("transaction", self._on_transaction)

    # ── WebSocket 콜백 ───────────────────────────────
    async def _on_orderbook(self, data: dict) -> None:
        """호가 수신 → OBI/OFI 갱신"""
        symbol = data.get("symbol", "").replace("_KRW", "")
        if not symbol or symbol not in TARGET_SYMBOLS:
            return

        order_list = data.get("list", [])
        bids = [
            {"price": item["price"], "quantity": item["quantity"]}
            for item in order_list
            if item.get("orderType") == "bid"
        ]
        asks = [
            {"price": item["price"], "quantity": item["quantity"]}
            for item in order_list
            if item.get("orderType") == "ask"
        ]

        await self.microstructure.update_orderbook(symbol, bids, asks)
        self._tick_count += 1

        # 주기적 하트비트
        now = ts_now()
        if now - self._last_heartbeat > 30:
            log.info(
                "엔진 가동 중 | %d틱 처리 | 포지션: %d개 | 레짐: %s",
                self._tick_count,
                len(self.risk.positions),
                self.regime.regime_name,
            )
            self._tick_count = 0
            self._last_heartbeat = now

    async def _on_transaction(self, data: dict) -> None:
        """체결 수신 → VPIN 갱신 + 가격 업데이트 + 매매 판단"""
        symbol = data.get("symbol", "").replace("_KRW", "")
        if not symbol or symbol not in TARGET_SYMBOLS:
            return

        tx_list = data.get("list", [])
        for tx in tx_list:
            price = float(tx.get("contPrice", 0))
            qty = float(tx.get("contQty", 0))
            side = tx.get("buySellGb", "")  # 1=매도, 2=매수
            side_str = "bid" if side == "2" else "ask"

            if price <= 0 or qty <= 0:
                continue

            # VPIN 갱신
            await self.microstructure.update_trade(symbol, price, qty, side_str)

            # 변동성 모델 업데이트
            await self.volatility.update_price(price)

            # 레짐 감지 업데이트
            await self.regime.update_price(price)

            # 보유 포지션 가격 업데이트 → 손절/트레일링 판단
            await self._check_exit(symbol, price)

            # 진입 시그널 평가
            await self._check_entry(symbol, price)

    # ── 진입 판단 ────────────────────────────────────
    async def _check_entry(self, symbol: str, price: float) -> None:
        """다중 시그널 앙상블 → 진입 판단"""
        async with self._entry_locks[symbol]:
            if symbol in self.risk.positions:
                return

            # 개별 시그널 수집
            obi_data = self.microstructure.get_obi_signal(symbol)
            vpin_data = self.microstructure.get_vpin_signal(symbol)
            prices = self.microstructure.get_prices(symbol)
            momentum_sig = self.ensemble.calc_momentum_signal(symbol, prices)
            regime_sig = self.regime.get_signal()
            
            # n8n AI 센티먼트 점수 반영 로직 (현재 관찰중인 코인이 AI 강추 코인인지)
            sentiment_sig = self.ensemble.get_sentiment_signal()
            if global_n8n_signals["target_symbol"] == symbol:
                sentiment_sig = global_n8n_signals["ai_sentiment"]
            
            funding_sig = self.ensemble.get_funding_signal()
            vol_sig = self.volatility.get_signal()

            # 앙상블
            result = self.ensemble.compute_final_score(
                obi_signal=obi_data["signal"],
                vpin_signal=vpin_data["signal"],
                momentum_signal=momentum_sig,
                regime_signal=regime_sig,
                sentiment_signal=sentiment_sig,
                funding_signal=funding_sig,
                volatility_signal=vol_sig,
            )

            # VPIN 위험 시 진입 차단
            if result["vpin_warning"]:
                return

            # 매수 조건
            if result["action"] not in ("buy", "strong_buy"):
                return

            # 리스크 매니저 승인
            regime_params = self.regime.get_regime_params()
            cash = await self._get_available_cash()
            can, reason, max_amount = await self.risk.can_enter(
                symbol, cash, regime_params["cash_ratio"]
            )

            if not can:
                log.debug("진입 거부 [%s]: %s", symbol, reason)
                return

            # Kelly × 레짐 보정
            kelly_adjusted = max_amount * regime_params["kelly_mult"]
            if result["action"] == "strong_buy":
                kelly_adjusted *= 1.0  # 풀 켈리
            else:
                kelly_adjusted *= 0.5  # 하프 켈리

            quantity = kelly_adjusted / price
            if quantity <= 0:
                return

            log.warning(
                "매수 시그널! %s | score=%.2f (%s) | conf=%.2f | amount=%,.0f원",
                symbol,
                result["score"],
                result["action"],
                result["confidence"],
                kelly_adjusted,
            )
            log.info(
                "  시그널: OBI=%.2f VPIN=%.2f Mom=%.2f Regime=%s Vol=%.2f",
                obi_data["signal"],
                vpin_data["signal"],
                momentum_sig,
                self.regime.regime_name,
                vol_sig,
            )

            # 주문 실행
            order_result = await self.gateway.place_order(
                symbol=symbol,
                side="bid",
                quantity=quantity,
                order_type="market",
            )

            if order_result.get("status") == "0000":
                await self.risk.register_position(symbol, price, quantity)
                log.info(
                    "매수 체결 완료: %s @ %,.0f × %.8f",
                    symbol, price, quantity,
                )
            else:
                log.error("매수 실패: %s | %s", symbol, order_result)

    # ── 청산 판단 ────────────────────────────────────
    async def _check_exit(self, symbol: str, price: float) -> None:
        """트레일링 스탑 / 손절 판단"""
        if symbol not in self.risk.positions:
            return

        regime_params = self.regime.get_regime_params()
        rv = self.volatility.realized_volatility

        exit_signal = await self.risk.update_price(
            symbol, price, rv, regime_params["trailing_mult"]
        )

        if exit_signal is None:
            return

        action = exit_signal["action"]
        pnl_pct = exit_signal["pnl_pct"]

        log.warning(
            "%s 발동: %s | P&L: %.2f%%",
            action.upper().replace("_", " "),
            symbol,
            pnl_pct * 100,
        )

        # 시장가 매도
        pos = self.risk.positions.get(symbol)
        if pos is None:
            return

        order_result = await self.gateway.place_order(
            symbol=symbol,
            side="ask",
            quantity=pos.quantity,
            order_type="market",
        )

        if order_result.get("status") == "0000":
            record = await self.risk.close_position(symbol, price)
            if record:
                log.info(
                    "매도 체결: %s | P&L: %+,.0f원 (%.2f%%)",
                    symbol,
                    record.pnl,
                    record.pnl_pct * 100,
                )
        else:
            log.error("매도 실패: %s | %s", symbol, order_result)

    # ── 보조 기능 ────────────────────────────────────
    async def _get_available_cash(self) -> float:
        """빗썸 잔고에서 KRW 잔액 조회"""
        try:
            balance = await self.gateway.get_balance("BTC")
            data = balance.get("data", {})
            return float(data.get("available_krw", 0))
        except Exception as e:
            log.error("잔고 조회 실패: %s", e)
            return 0.0

    async def _funding_rate_loop(self) -> None:
        """바이낸스 펀딩비 주기적 조회 (5분마다)"""
        symbol_map = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}
        while self._running:
            for sym, binance_sym in symbol_map.items():
                if sym in TARGET_SYMBOLS:
                    await self.ensemble.fetch_funding_rate(binance_sym)
            await asyncio.sleep(300)

    # ── 메인 루프 ────────────────────────────────────
    async def _load_initial_data(self) -> None:
        """엔진 시작 시 사용할 과거 캔들 데이터(종가)를 REST API로 로드"""
        log.info("과거 캔들 파싱 중... (HMM, GARCH, Momentum 초기화 목적)")
        for symbol in TARGET_SYMBOLS:
            # 1분봉 데이터 500개 확보 (최대 24h 지원 등 제한 고려 시 적절한 interval 사용)
            candles = await self.gateway.get_candlestick(symbol, interval="1m")
            if not candles:
                continue
            
            # 응답 구조 [시간, 시가, 고가, 저가, 종가, 거래량]
            # 최신이 배열 뒤쪽에 위치함.
            prices = []
            for c in candles:
                try:
                    prices.append(float(c[4])) # 종가
                except (IndexError, ValueError):
                    pass
            
            # 모멘텀용 1분봉 데이터 저장 (market_microstructure의 get_prices 용)
            if prices:
                self.microstructure._price_history[symbol] = prices[-1440:] # 최근 24시간 분량만
                
                # Volatility, Regime 초기화는 마지막 몇 개 틱으로 시뮬레이션
                for p in prices[-100:]:
                    await self.volatility.update_price(p)
                    await self.regime.update_price(p)
                    
        log.info("초기 데이터 로드 완료")

    async def run(self) -> None:
        """엔진 시작"""
        log.info("=" * 60)
        log.info("Project Chronos — Crypto V2 엔진 시작")
        log.info("대상 심볼: %s", TARGET_SYMBOLS)
        log.info("레짐: %s | RV: %.4f", self.regime.regime_name, self.volatility.realized_volatility)
        log.info("=" * 60)
        
        await self._load_initial_data()

        # 백그라운드 태스크
        tasks = [
            asyncio.create_task(self.gateway.start_websocket(TARGET_SYMBOLS)),
            asyncio.create_task(self._funding_rate_loop()),
        ]

        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            log.info("엔진 종료 요청")
        finally:
            self._running = False
            await self.gateway.close()
            log.info("엔진 종료 완료")

    def get_status(self) -> dict:
        """현재 엔진 상태 (Dashboard용)"""
        positions = []
        for sym, pos in self.risk.positions.items():
            current = self.microstructure.get_last_price(sym)
            pnl_pct = (
                (current - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
            )
            positions.append({
                "symbol": sym,
                "entry_price": pos.entry_price,
                "current_price": current,
                "quantity": pos.quantity,
                "pnl_pct": pnl_pct,
                "trailing_active": pos.trailing_active,
            })

        surveillance = []
        for sym in TARGET_SYMBOLS:
            obi = self.microstructure.get_obi_signal(sym)
            vpin = self.microstructure.get_vpin_signal(sym)
            surveillance.append({
                "symbol": sym,
                "price": self.microstructure.get_last_price(sym),
                "obi": obi["obi"],
                "ofi": obi["ofi"],
                "vpin": vpin["vpin"],
            })

        stats = self.risk.get_stats()

        return {
            "engine_active": self.gateway.is_connected,
            "regime": self.regime.regime_name,
            "realized_vol": self.volatility.realized_volatility,
            "garch_vol": self.volatility.garch_volatility,
            "positions": positions,
            "surveillance": surveillance,
            "risk_stats": stats,
        }
