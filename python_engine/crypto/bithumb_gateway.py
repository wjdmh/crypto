"""
빗썸 REST + WebSocket 게이트웨이
──────────────────────────────────
빗썸 Public API (WebSocket 호가/체결) + Private API (주문/잔고) 통신 전담.
"""
import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse
from collections import defaultdict
from typing import Any, Callable, Coroutine

import aiohttp

from config import (
    BITHUMB_API_KEY,
    BITHUMB_REST_URL,
    BITHUMB_SECRET_KEY,
    BITHUMB_WS_URL,
)
from utils import setup_logger

log = setup_logger("bithumb_gw")


# ── 인증 헤더 생성 (HMAC-SHA512) ────────────────────
def _make_signature(endpoint: str, params: dict, secret: str) -> tuple[str, str, str]:
    """빗썸 Private API 서명 생성"""
    nonce = str(int(time.time() * 1000))
    query_string = urllib.parse.urlencode(params) if params else ""
    hmac_data = f"{endpoint}\x00{query_string}\x00{nonce}"
    signature = hmac.new(
        secret.encode("utf-8"),
        hmac_data.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    return nonce, signature, query_string


class BithumbGateway:
    """빗썸 REST + WebSocket 클라이언트"""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ws_connected = False
        self._callbacks: dict[str, list[Callable]] = defaultdict(list)
        self._reconnect_delay = 1.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    # ── REST: Public ─────────────────────────────────
    async def get_ticker(self, symbol: str = "BTC") -> dict:
        """현재가 조회"""
        session = await self._get_session()
        url = f"{BITHUMB_REST_URL}/public/ticker/{symbol}_KRW"
        async with session.get(url) as resp:
            data = await resp.json()
            if data.get("status") != "0000":
                log.warning("ticker 조회 실패: %s", data)
            return data.get("data", {})

    async def get_orderbook(self, symbol: str = "BTC") -> dict:
        """호가창 조회"""
        session = await self._get_session()
        url = f"{BITHUMB_REST_URL}/public/orderbook/{symbol}_KRW"
        async with session.get(url) as resp:
            data = await resp.json()
            return data.get("data", {})

    async def get_transaction_history(self, symbol: str = "BTC") -> list:
        """최근 체결 내역"""
        session = await self._get_session()
        url = f"{BITHUMB_REST_URL}/public/transaction_history/{symbol}_KRW"
        async with session.get(url) as resp:
            data = await resp.json()
            return data.get("data", [])

    async def get_candlestick(
        self, symbol: str = "BTC", interval: str = "1m"
    ) -> list:
        """캔들스틱 (차트) 데이터 — interval: 1m/3m/5m/10m/30m/1h/6h/12h/24h"""
        session = await self._get_session()
        url = f"{BITHUMB_REST_URL}/public/candlestick/{symbol}_KRW/{interval}"
        async with session.get(url) as resp:
            data = await resp.json()
            return data.get("data", [])

    # ── REST: Private ────────────────────────────────
    async def _private_post(self, endpoint: str, params: dict | None = None) -> dict:
        """Private API POST 요청"""
        if not BITHUMB_API_KEY or not BITHUMB_SECRET_KEY:
            log.error("빗썸 API 키가 .env에 설정되지 않았습니다")
            return {"status": "9999", "message": "API key not configured"}

        params = params or {}
        params["endpoint"] = endpoint
        nonce, signature, query_string = _make_signature(
            endpoint, params, BITHUMB_SECRET_KEY
        )

        headers = {
            "Api-Key": BITHUMB_API_KEY,
            "Api-Sign": signature,
            "Api-Nonce": nonce,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        session = await self._get_session()
        url = f"{BITHUMB_REST_URL}{endpoint}"
        async with session.post(url, data=query_string, headers=headers) as resp:
            return await resp.json()

    async def get_balance(self, symbol: str = "BTC") -> dict:
        """잔고 조회"""
        return await self._private_post(
            "/info/balance", {"order_currency": symbol, "payment_currency": "KRW"}
        )

    async def get_account(self) -> dict:
        """계좌 정보"""
        return await self._private_post("/info/account", {"order_currency": "BTC"})

    async def place_order(
        self,
        symbol: str,
        side: str,
        price: float | None = None,
        quantity: float | None = None,
        order_type: str = "limit",
    ) -> dict:
        """주문 실행
        side: 'bid' (매수) / 'ask' (매도)
        order_type: 'limit' (지정가) / 'market' (시장가)
        """
        endpoint = f"/trade/place"
        params: dict[str, Any] = {
            "order_currency": symbol,
            "payment_currency": "KRW",
            "type": side,
        }

        if order_type == "market":
            if side == "bid":
                # 시장가 매수: units 필요
                params["units"] = str(quantity)
            else:
                # 시장가 매도: units 필요
                params["units"] = str(quantity)
        else:
            params["price"] = str(int(price)) if price else "0"
            params["units"] = str(quantity)

        result = await self._private_post(endpoint, params)
        log.info(
            "주문 %s %s | side=%s qty=%s price=%s | result=%s",
            order_type,
            symbol,
            side,
            quantity,
            price,
            result.get("status"),
        )
        return result

    async def cancel_order(self, order_id: str, symbol: str, side: str) -> dict:
        """주문 취소"""
        return await self._private_post(
            "/trade/cancel",
            {
                "order_id": order_id,
                "type": side,
                "order_currency": symbol,
                "payment_currency": "KRW",
            },
        )

    # ── WebSocket ────────────────────────────────────
    def on(self, event: str, callback: Callable[..., Coroutine]) -> None:
        """이벤트 콜백 등록 (event: 'orderbookdepth' | 'transaction')"""
        self._callbacks[event].append(callback)

    async def _dispatch(self, event: str, data: dict) -> None:
        for cb in self._callbacks.get(event, []):
            try:
                await cb(data)
            except Exception as e:
                log.error("콜백 에러 [%s]: %s", event, e)

    async def start_websocket(self, symbols: list[str]) -> None:
        """WebSocket 연결 시작 — 호가 + 체결 구독"""
        while True:
            try:
                session = await self._get_session()
                log.info("빗썸 WebSocket 연결 시도 → %s", BITHUMB_WS_URL)
                self._ws = await session.ws_connect(
                    BITHUMB_WS_URL, heartbeat=30, timeout=30
                )
                self._ws_connected = True
                self._reconnect_delay = 1.0
                log.info("빗썸 WebSocket 연결 성공")

                # 호가 구독
                subscribe_orderbook = {
                    "type": "orderbookdepth",
                    "symbols": [f"{s}_KRW" for s in symbols],
                    "tickTypes": ["1H"],  # 1호가
                }
                await self._ws.send_json(subscribe_orderbook)

                # 체결 구독
                subscribe_transaction = {
                    "type": "transaction",
                    "symbols": [f"{s}_KRW" for s in symbols],
                    "tickTypes": ["1H"],
                }
                await self._ws.send_json(subscribe_transaction)

                log.info("구독 완료: %s", symbols)

                async for msg in self._ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            payload = json.loads(msg.data)
                            content = payload.get("content", {})
                            msg_type = payload.get("type", "")
                            if msg_type in ("orderbookdepth", "transaction"):
                                await self._dispatch(msg_type, content)
                        except json.JSONDecodeError:
                            pass
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        log.warning("WebSocket 종료/에러: %s", msg.type)
                        break

            except Exception as e:
                log.error("WebSocket 에러: %s", e)

            self._ws_connected = False
            log.info("재연결 대기 %.1f초...", self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)

    @property
    def is_connected(self) -> bool:
        return self._ws_connected

    async def close(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        log.info("게이트웨이 종료")
