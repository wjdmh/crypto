from python_engine.kis_auth import KISAuth
from python_engine.kis_trader import KISTrader
auth = KISAuth()
auth.get_access_token()
trader = KISTrader(auth)
import jsbeautifier # if available, or just pprint
import pprint
import requests
path = "/uapi/domestic-stock/v1/trading/inquire-balance"
from python_engine.config import URL_BASE, KIS_ACCOUNT_NO, KIS_ACCOUNT_PRDT_CD
url = f"{URL_BASE}{path}"
trader.headers["authorization"] = f"Bearer {trader.auth.get_access_token()}"
trader.headers["tr_id"] = "VTTC8434R"
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
res = requests.get(url, headers=trader.headers, params=params)
pprint.pprint(res.json().get("output2", []))
