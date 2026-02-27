"""
텔레그램 봇 알림 모듈
─────────────────────
매매 엔진의 일일 리포트, 에러, 비상 정지(Circuit Breaker) 알림을 전송.
"""
import aiohttp
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils import setup_logger

log = setup_logger("telegram")

class TelegramNotifier:
    """텔레그램 비동기 알림 클래스"""
    
    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        self._enabled = bool(self.token and self.chat_id)

    async def send_message(self, text: str) -> bool:
        """마크다운(V2) 포맷으로 메시지 전송"""
        if not self._enabled:
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML", # HTML 포맷이 MarkdownV2보다 오류 확률이 적음
            "disable_web_page_preview": True
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.base_url, json=payload, timeout=5) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        resp_text = await resp.text()
                        log.error("텔레그램 전송 실패: %s", resp_text)
                        return False
        except Exception as e:
            log.error("텔레그램 통신 에러: %s", e)
            return False

    async def send_daily_report(self, date_str: str, pnl: float, pnl_pct: float, trades: int) -> None:
        """일일 리포트 전송"""
        emoji = "📈" if pnl >= 0 else "📉"
        msg = f"<b>[Project Chronos - Crypto V2]</b>\n\n"
        msg += f"📅 <b>일자:</b> {date_str}\n"
        msg += f"💰 <b>일일 PnL:</b> {pnl:,.0f} KRW ({pnl_pct * 100:.2f}%)\n"
        msg += f"🔄 <b>총 거래 횟수:</b> {trades}회\n\n"
        msg += f"{emoji} 오늘도 수고하셨습니다."
        await self.send_message(msg)

    async def send_emergency_stop(self, reason: str) -> None:
        """비상 정지 알림 전송 (연속 손절, 잔고 부족 등)"""
        msg = f"🚨 <b>[비상 정지 발동]</b> 🚨\n\n"
        msg += f"<b>사유:</b> {reason}\n"
        msg += f"매매 엔진이 모든 신규 진입을 일시적으로 차단합니다."
        await self.send_message(msg)
        log.warning("텔레그램 비상 정지 메시지 발송 완료")

# 싱글톤 인스턴스
notifier = TelegramNotifier()
