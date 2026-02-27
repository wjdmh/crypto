"""
백테스팅 엔진 (Backtester)
──────────────────────────
수집된 과거 OHLCV 데이터를 불러와 모의 투자를 진행하고 성과를 분석합니다.
실시간 호가창/체결 데이터(tick)가 없으므로 OBI 예측과 VPIN 검증은 캔들스틱 기반 
휴리스틱 모델로 대체하여 시뮬레이션 합니다.

사용법:
    python backtester.py --data test_btc.csv --initial-capital 10000000
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 상위 경로의 모듈들을 import 하기 위한 설정
sys.path.append(str(Path(__file__).resolve().parent.parent))
from signal_ensemble import SignalEnsemble
from utils import setup_logger

log = setup_logger("backtester")

class BacktestEngine:
    def __init__(self, data_path: str, initial_capital: float = 1_000_000.0, fee_rate: float = 0.0004, use_mock_external: bool = False, ai_strong_buy: bool = False):
        self.data_path = data_path
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.use_mock_external = use_mock_external
        self.ai_strong_buy = ai_strong_buy
        
        # 계좌 잔고 관리
        self.cash = initial_capital
        self.position = 0.0
        self.avg_buy_price = 0.0
        
        # 거래 기록
        self.trades = []
        self.portfolio_history = []
        
        # 모델
        self.ensemble = SignalEnsemble()

        # OBI, VPIN 대용 휴리스틱 변수
        self.volume_history = []
        self.price_history = []

    def load_data(self) -> pd.DataFrame:
        log.info(f"데이터 로드 중: {self.data_path}")
        df = pd.read_csv(self.data_path)
        df['datetime'] = pd.to_datetime(df['datetime'])
        return df

    def _synthetic_obi(self, row: pd.Series) -> float:
        """캔들 모양을 통해 가상의 Order Book Imbalance(OBI) 추출"""
        body = row['close'] - row['open']
        spread = row['high'] - row['low']
        if spread == 0:
            return 0.0
        # 종가가 시가보다 높을수록(양봉) 매수세 강함
        return np.clip(body / spread, -1.0, 1.0)

    def _synthetic_vpin(self, row: pd.Series) -> float:
        """가상의 VPIN 계산 (단기 거래량 급등 시 독성 경고)"""
        self.volume_history.append(row['volume'])
        if len(self.volume_history) > 20:
            self.volume_history.pop(0)
            avg_vol = sum(self.volume_history[:-1]) / max(len(self.volume_history)-1, 1)
            # 최근 거래량이 평균 대비 3배 이상 폭증하고 음봉이면 독성(위험)으로 간주
            if row['volume'] > avg_vol * 3 and (row['close'] < row['open']):
                return 1.0  # High toxicity
        return 0.0

    def run(self):
        df = self.load_data()
        
        if len(df) == 0:
            log.error("데이터가 비어있습니다.")
            return

        log.info(f"백테스팅 시작. 총 캔들 수: {len(df)}")
        log.info(f"초기 자본: {self.initial_capital:,.0f} KRW")

        for idx, row in df.iterrows():
            current_price = row['close']
            self.price_history.append(current_price)
            if len(self.price_history) > 10080:  # 최대 보관 기간 설정
                self.price_history.pop(0)

            # 포트폴리오 가치 추적
            current_value = self.cash + (self.position * current_price)
            self.portfolio_history.append({
                "datetime": row['datetime'],
                "close": current_price,
                "cash": self.cash,
                "position": self.position,
                "total_value": current_value
            })

            # 시그널 추출을 위한 데이터 확보 (최소 60건)
            if len(self.price_history) < 60:
                continue

            # 1. 모의 시그널 계산
            obi_signal = self._synthetic_obi(row)
            vpin_signal = self._synthetic_vpin(row)
            momentum_signal = self.ensemble.calc_momentum_signal("BTC", self.price_history)
            
            # 레짐과 변동성은 여기서는 간단하게 모멘텀 척도 및 H/L 편차로 모방
            regime_signal = 0.5 if momentum_signal > 0 else -0.5
            volatility_signal = (row['high'] - row['low']) / current_price * 10 
            
            # API 센티먼트, 펀딩비 가상 시뮬레이션 (외부 변수가 긍정적인 상황 가정 가능하도록)
            if hasattr(self, 'ai_strong_buy') and self.ai_strong_buy:
                # n8n AI가 완벽한 상승 내러티브와 호재를 포착하여 강하게 매수를 추천(0.7 ~ 1.0)하는 상황을 10일간 가정
                mock_sentiment = np.clip(np.random.normal(0.85, 0.1), 0.5, 1.0) 
            elif self.use_mock_external:
                # 일반적인 노이즈 장세 (-0.2 ~ +0.2)
                mock_sentiment = np.clip(np.random.normal(0, 0.2), -1.0, 1.0)
            else:
                mock_sentiment = 0.0
                
            mock_funding = 0.0 # 펀딩비는 추세에 큰 영향을 주므로 여기서는 0.0으로 통제
            mock_funding = 0.0 # 펀딩비는 추세에 큰 영향을 주므로 여기서는 0.0으로 통제
            
            decision = self.ensemble.compute_final_score(
                obi_signal=obi_signal,
                vpin_signal=vpin_signal,
                momentum_signal=momentum_signal,
                regime_signal=regime_signal,
                sentiment_signal=mock_sentiment,
                funding_signal=mock_funding,
                volatility_signal=volatility_signal
            )

            action = decision['action']
            vpin_warning = decision['vpin_warning']

            # 2. 주문 체결 모의
            if action in ["buy", "strong_buy"] and not vpin_warning:
                # 현금의 10% 단위 매수 가정 (Kelly fraction 모의)
                invest_amt = self.cash * 0.10
                if invest_amt > 10000:  # 최소 주문 금액
                    qty = (invest_amt * (1 - self.fee_rate)) / current_price
                    self.cash -= invest_amt
                    
                    # 평단가 계산
                    total_value_old = self.position * self.avg_buy_price
                    new_value = qty * current_price
                    self.position += qty
                    self.avg_buy_price = (total_value_old + new_value) / self.position if self.position > 0 else 0
                    
                    self.trades.append({"type": "BUY", "time": row['datetime'], "price": current_price, "qty": qty})

            elif action in ["sell", "strong_sell"] and self.position > 0:
                # 전량 청산 모의
                revenue = self.position * current_price * (1 - self.fee_rate)
                profit = revenue - (self.position * self.avg_buy_price)
                self.cash += revenue
                
                self.trades.append({"type": "SELL", "time": row['datetime'], "price": current_price, "qty": self.position, "profit": profit})
                self.position = 0.0
                self.avg_buy_price = 0.0

        # 백테스트 종결 처리 (강제 전체 청산)
        if self.position > 0:
            final_price = self.price_history[-1]
            revenue = self.position * final_price * (1 - self.fee_rate)
            profit = revenue - (self.position * self.avg_buy_price)
            self.cash += revenue
            self.trades.append({"type": "SELL (EXIT)", "time": df.iloc[-1]['datetime'], "price": final_price, "qty": self.position, "profit": profit})
            self.position = 0.0
            self.portfolio_history[-1]['cash'] = self.cash
            self.portfolio_history[-1]['total_value'] = self.cash

        self._print_report()

    def _print_report(self):
        print("\n" + "="*40)
        print("📊 백테스트 성과 리포트")
        print("="*40)
        
        if not self.portfolio_history:
            print("거래 데이터가 부족합니다.")
            return

        final_value = self.portfolio_history[-1]['total_value']
        net_profit = final_value - self.initial_capital
        roi = (final_value / self.initial_capital - 1) * 100
        
        # Max Drawdown 계산
        history_df = pd.DataFrame(self.portfolio_history)
        history_df['cummax'] = history_df['total_value'].cummax()
        history_df['drawdown'] = history_df['total_value'] / history_df['cummax'] - 1
        mdd = history_df['drawdown'].min() * 100

        # 승률 계산
        sell_trades = [t for t in self.trades if "profit" in t]
        if sell_trades:
            wins = sum(1 for t in sell_trades if t['profit'] > 0)
            win_rate = wins / len(sell_trades) * 100
        else:
            win_rate = 0.0

        print(f"초기 자본:       {self.initial_capital:,.0f} KRW")
        print(f"최종 자산:       {final_value:,.0f} KRW")
        print(f"순수익:          {net_profit:,.0f} KRW")
        print(f"수익률(ROI):     {roi:.2f}%")
        print(f"최대낙폭(MDD):   {mdd:.2f}%")
        print(f"총 거래 횟수:    {len(sell_trades)} 회")
        print(f"승률(Win Rate):  {win_rate:.2f}%")
        print("="*40 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, required=True, help="백테스팅할 CSV 데이터 파일 경로")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0, help="초기 자본 (원)")
    parser.add_argument("--fee", type=float, default=0.0004, help="수수료율 (기본 0.04%%)")
    parser.add_argument("--mock-external", action="store_true", help="가상의 외부 노이즈 센티먼트 사용 여부")
    parser.add_argument("--ai-strong-buy", action="store_true", help="AI가 지속적으로 강한 매수를 추천하는 상황 시뮬레이션")
    
    args = parser.parse_args()
    
    if not Path(args.data).exists():
        log.error(f"데이터 파일이 존재하지 않습니다: {args.data}")
        sys.exit(1)
        
    engine = BacktestEngine(data_path=args.data, initial_capital=args.initial_capital, fee_rate=args.fee, use_mock_external=args.mock_external, ai_strong_buy=args.ai_strong_buy)
    engine.run()
