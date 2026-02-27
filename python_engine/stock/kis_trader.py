import requests
import json
import logging
from config import URL_BASE, KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, KIS_ACCOUNT_PRDT_CD
from kis_auth import KISAuth

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] %(message)s')

class KISTrader:
    def __init__(self, auth: KISAuth):
        self.auth = auth
        self.headers = {
            "content-type": "application/json",
            "appkey": KIS_APP_KEY,
            "appsecret": KIS_APP_SECRET,
            "tr_id": "" # 주문 유형에 따라 동적 할당 (VTTC0802U: 모의투자 매수, VTTC0801U: 모의투자 매도)
        }

    def order(self, symbol: str, qty: int, is_buy: bool = True, price: int = 0, ord_dvsn: str = "01"):
        """
        주식 주문 실행
        is_buy: True(매수), False(매도)
        price: 지정가 매매 시 단가 (시장가는 0)
        ord_dvsn: "00"(지정가), "01"(시장가), "03"(최유리지정가), "04"(최우선지정가)
        """
        path = "/uapi/domestic-stock/v1/trading/order-cash"
        url = f"{URL_BASE}{path}"
        
        # 24시간 장기 구동을 위한 동적 토큰 재할당
        self.headers["authorization"] = f"Bearer {self.auth.get_access_token()}"
        
        # 모의투자 TR_ID 설정 (매수: VTTC0802U, 매도: VTTC0801U)
        self.headers["tr_id"] = "VTTC0802U" if is_buy else "VTTC0801U"
        
        # 시장가/지정가 자동 방어 로직
        if price > 0 and ord_dvsn == "01":
            ord_dvsn = "00"
        
        body = {
            "CANO": KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD": KIS_ACCOUNT_PRDT_CD,
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price)
        }
        
        order_type = "매수" if is_buy else "매도"
        
        price_log_map = {"00": f"{price}원(지정가)", "01": "시장가", "03": "최유리지정가", "04": "최우선지정가"}
        price_log = price_log_map.get(ord_dvsn, "시장가")
        
        try:
            res = requests.post(url, headers=self.headers, data=json.dumps(body))
            if res.status_code == 200:
                res_json = res.json()
                if res_json.get("rt_cd") == "0":
                    logging.info(f"✅ [{symbol}] {order_type} 주문 성공! (수량: {qty}, 단가: {price_log})")
                else:
                    logging.error(f"❌ [{symbol}] {order_type} 주문 에러: {res.text}")
                return res_json
            else:
                logging.error(f"❌ [{symbol}] HTTP 통신 에러: {res.text}")
                return None
        except Exception as e:
            logging.error(f"주문 중 예외 발생: {str(e)}")
            return None

    def get_balance(self):
        """계좌 주문 가능 현금 (예수금) 조회 API - 복리 자본 관리를 위함"""
        path = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        url = f"{URL_BASE}{path}"
        
        # 동적 토큰 재할당
        self.headers["authorization"] = f"Bearer {self.auth.get_access_token()}"
        
        # 모의투자 매수 가능 금액 조회 ID (실전은 TTTC8908R)
        self.headers["tr_id"] = "VTTC8908R" if URL_BASE.find("vts") != -1 else "TTTC8908R"
        
        params = {
            "CANO": KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD": KIS_ACCOUNT_PRDT_CD,
            "PDNO": "005930", # 단가 확인용 임시 종목 (필수값)
            "ORD_UNPR": "",
            "ORD_DVSN": "01", # 시장가
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N"
        }
        
        try:
            res = requests.get(url, headers=self.headers, params=params)
            if res.status_code == 200 and res.json().get("rt_cd") == "0":
                output = res.json().get("output", {})
                cash = int(output.get("ord_psbl_cash", 0))
                logging.info(f"💰 현재 KIS 계좌 주문 가능 현금: {cash:,}원")
                return cash
            else:
                logging.error(f"❌ KIS 잔고 조회 에러: {res.text}")
                return 500000 # 실패 시 기본 시작 시드 반환
        except Exception as e:
            logging.error(f"⚠️ 잔고 조회 중 예외 발생: {str(e)}")
            return 500000

    def get_total_asset(self):
        """계좌 총 자산(총평가금액) 조회 API - 대시보드 표시용"""
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"
        url = f"{URL_BASE}{path}"
        
        # 동적 토큰 재할당
        self.headers["authorization"] = f"Bearer {self.auth.get_access_token()}"
        
        self.headers["tr_id"] = "VTTC8434R" if URL_BASE.find("vts") != -1 else "TTTC8434R"
        params = {
            "CANO": KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD": KIS_ACCOUNT_PRDT_CD,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        try:
            res = requests.get(url, headers=self.headers, params=params)
            if res.status_code == 200 and res.json().get("rt_cd") == "0":
                output2 = res.json().get("output2", [])
                if output2:
                    return {
                        "total": int(output2[0].get("tot_evlu_amt", 0)),
                        "prev_total": int(output2[0].get("bfdy_tot_asst_evlu_amt", 0)),
                        "initial_capital": 100000000,  # 1억 (사용자 설정 기준 자본)
                        "unrealized_pnl": int(output2[0].get("evlu_pfls_smtl_amt", 0)),
                        "total_purchase": int(output2[0].get("pchs_amt_smtl_amt", 0))
                    }
            return {"total": 0, "prev_total": 0, "initial_capital": 100000000, "unrealized_pnl": 0, "total_purchase": 0}
        except Exception:
            return {"total": 0, "prev_total": 0, "initial_capital": 100000000, "unrealized_pnl": 0, "total_purchase": 0}

    def get_holdings(self):
        """계좌 현재 보유 종목 조회 API"""
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"
        url = f"{URL_BASE}{path}"
        
        # 동적 토큰 재할당
        self.headers["authorization"] = f"Bearer {self.auth.get_access_token()}"
        
        # 모의투자 잔고 조회 ID (실전은 TTTC8434R)
        self.headers["tr_id"] = "VTTC8434R" if URL_BASE.find("vts") != -1 else "TTTC8434R"
        
        params = {
            "CANO": KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD": KIS_ACCOUNT_PRDT_CD,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        try:
            res = requests.get(url, headers=self.headers, params=params)
            if res.status_code == 200 and res.json().get("rt_cd") == "0":
                output1 = res.json().get("output1", [])
                holdings = {}
                for item in output1:
                    symbol = item.get("pdno")
                    qty = int(item.get("hldg_qty", 0))
                    avg_price = float(item.get("pchs_avg_pric", 0))
                    if qty > 0:
                        holdings[symbol] = {
                            "qty": qty,
                            "buy_price": avg_price
                        }
                return holdings
            else:
                logging.error(f"❌ KIS 보유종목 조회 에러: {res.text}")
                return {}
        except Exception as e:
            logging.error(f"⚠️ 보유종목 조회 중 예외 발생: {str(e)}")
            return {}

if __name__ == "__main__":
    # 테스트 로직 (장이 닫혀 있으면 "거래시간이 아닙니다" 에러 반환됨)
    auth = KISAuth()
    if auth.get_access_token():
        trader = KISTrader(auth)
        # 005930(삼성전자) 1주 시장가 모의 매수 테스트
        # trader.order("005930", 1, is_buy=True, price=0)
        print(trader.get_holdings())
