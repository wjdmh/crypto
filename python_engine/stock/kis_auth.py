import requests
import json
import logging
from config import URL_BASE, KIS_APP_KEY, KIS_APP_SECRET

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

import os
import time

TOKEN_CACHE_FILE = "kis_token_cache.json"

class KISAuth:
    def __init__(self):
        self.headers = {"content-type": "application/json"}
        self.access_token = None
        self.ws_approval_key = None
        self.token_expired_at = 0
        self.approval_key_expired_at = 0
        self._load_cache()

    def _load_cache(self):
        if os.path.exists(TOKEN_CACHE_FILE):
            try:
                with open(TOKEN_CACHE_FILE, 'r') as f:
                    cache = json.load(f)
                    now = time.time()
                    if cache.get('access_token') and cache.get('token_expired_at', 0) > now:
                        self.access_token = cache['access_token']
                        self.token_expired_at = cache['token_expired_at']
                    if cache.get('ws_approval_key') and cache.get('approval_key_expired_at', 0) > now:
                        self.ws_approval_key = cache['ws_approval_key']
                        self.approval_key_expired_at = cache['approval_key_expired_at']
            except Exception as e:
                logging.warning(f"토큰 캐시 로드 실패: {e}")

    def _save_cache(self):
        cache = {
            'access_token': self.access_token,
            'token_expired_at': self.token_expired_at,
            'ws_approval_key': self.ws_approval_key,
            'approval_key_expired_at': self.approval_key_expired_at
        }
        with open(TOKEN_CACHE_FILE, 'w') as f:
            json.dump(cache, f)

    def get_access_token(self):
        """REST API용 OAuth 접근 토큰 발급"""
        if self.access_token and self.token_expired_at > time.time():
            return self.access_token
            
        path = "/oauth2/tokenP"
        url = f"{URL_BASE}{path}"
        body = {
            "grant_type": "client_credentials",
            "appkey": KIS_APP_KEY,
            "appsecret": KIS_APP_SECRET
        }

        res = requests.post(url, headers=self.headers, data=json.dumps(body))
        if res.status_code == 200:
            self.access_token = res.json().get("access_token")
            # KIS 토큰은 24시간 유효. 23시간으로 안전하게 설정
            self.token_expired_at = time.time() + (23 * 3600)
            self._save_cache()
            logging.info("KIS REST API Access Token 신규 발급 성공.")
            return self.access_token
        else:
            logging.error(f"Access Token 발급 실패: {res.status_code} - {res.text}")
            return self.access_token # 실패 시 캐시에 남아있는 값이 있다면 리턴

    def get_ws_approval_key(self):
        """WebSocket 접속을 위한 승인키(Approval Key) 발급"""
        if self.ws_approval_key and self.approval_key_expired_at > time.time():
            return self.ws_approval_key
            
        path = "/oauth2/Approval"
        url = f"{URL_BASE}{path}"
        body = {
            "grant_type": "client_credentials",
            "appkey": KIS_APP_KEY,
            "secretkey": KIS_APP_SECRET
        }

        res = requests.post(url, headers=self.headers, data=json.dumps(body))
        if res.status_code == 200:
            self.ws_approval_key = res.json().get("approval_key")
            # 보통 24시간 유효
            self.approval_key_expired_at = time.time() + (23 * 3600)
            self._save_cache()
            logging.info("KIS WebSocket Approval Key 신규 발급 성공.")
            return self.ws_approval_key
        else:
            logging.error(f"Approval Key 발급 실패: {res.status_code} - {res.text}")
            return self.ws_approval_key

if __name__ == "__main__":
    # 테스트 로직
    auth = KISAuth()
    print("Testing Auth Module...")
    if KIS_APP_KEY != "YOUR_APP_KEY_HERE":
        auth.get_access_token()
        auth.get_ws_approval_key()
    else:
        print("config.py에 API Key를 먼저 입력해주세요.")
