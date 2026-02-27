"""
과거 데이터 수집 스크립트 (Data Collector)
───────────────────────────────────
빗썸 REST API를 사용하여 과거 캔들(OHLCV) 데이터를 다운로드하고 
로컬 CSV 파일로 저장합니다.

사용법:
    python data_collector.py --symbol BTC --timeframe 1m --limit 1000 --output btc_1m.csv
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
import pandas as pd

# 로깅 유틸리티 (utils.py) 경로 추가
sys.path.append(str(Path(__file__).resolve().parent.parent))
from utils import setup_logger

log = setup_logger("data_collector")

BITHUMB_REST_URL = "https://api.bithumb.com"

async def fetch_candlestick(symbol: str, timeframe: str = "1m") -> list:
    """
    빗썸 캔들 데이터를 API에서 가져온다.
    timeframe: 1m, 3m, 5m, 10m, 30m, 1h, 6h, 12h, 24h
    """
    api_symbol = f"{symbol}_KRW"
    url = f"{BITHUMB_REST_URL}/public/candlestick/{api_symbol}/{timeframe}"
    
    log.info(f"{api_symbol} {timeframe} 캔들 데이터 요청 ({url})")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    log.error(f"API 요청 실패: HTTP {response.status}")
                    return []
                
                data = await response.json()
                if data.get("status") != "0000":
                    log.error(f"API 에러 응답: {data}")
                    return []
                
                return data.get("data", [])
        except Exception as e:
            log.error(f"캔들 데이터 가져오기 실패: {e}")
            return []

def process_and_save(data: list, output_path: str, limit: int = None):
    """
    원시 API 응답 데이터를 DataFrame으로 변환 후 CSV로 저장.
    빗썸 응답 포맷: [기준시간매초, 시가, 종가, 고가, 저가, 거래량]
    ※ 최신 문서 기준 일부 종목은 시간 포맷이 timestamp 문자열이거나 int일 수 있음.
    """
    if not data:
        log.warning("저장할 데이터가 없습니다.")
        return

    # 최신 데이터만 필요한 경우 자르기
    if limit and limit > 0:
        data = data[-limit:]

    # 컬럼 매핑
    columns = ["timestamp", "open", "close", "high", "low", "volume"]
    df = pd.DataFrame(data, columns=columns)

    # 데이터 타입 변환
    df["timestamp"] = pd.to_numeric(df["timestamp"])
    df["open"] = pd.to_numeric(df["open"])
    df["close"] = pd.to_numeric(df["close"])
    df["high"] = pd.to_numeric(df["high"])
    df["low"] = pd.to_numeric(df["low"])
    df["volume"] = pd.to_numeric(df["volume"])

    # timestamp 변환 (ms를 datetime으로 변환)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    
    # 정렬 및 인덱스 리셋
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    
    # 보기 좋게 컬럼 순서 재배치 (open, high, low, close, volume)
    df = df[["timestamp", "datetime", "open", "high", "low", "close", "volume"]]

    # CSV 저장
    # 상위 경로에 디렉토리가 없으면 생성
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    df.to_csv(output_path, index=False)
    log.info(f"데이터 수집 완료: 총 {len(df)} 캔들 -> {output_path}")
    log.info(f"데이터 범위: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")


async def main():
    parser = argparse.ArgumentParser(description="빗썸 과거 OHLCV 데이터 수집기")
    parser.add_argument("--symbol", type=str, required=True, help="코인 심볼 (예: BTC)")
    parser.add_argument("--timeframe", type=str, default="1m", choices=["1m","3m","5m","10m","30m","1h","6h","12h","24h"], help="캔들 간격")
    parser.add_argument("--limit", type=int, default=1000, help="가져올 최신 캔들 수 (0=전부)")
    parser.add_argument("--output", type=str, default="data.csv", help="저장할 CSV 파일 경로")
    
    args = parser.parse_args()

    raw_data = await fetch_candlestick(symbol=args.symbol.upper(), timeframe=args.timeframe)
    process_and_save(raw_data, args.output, args.limit)

if __name__ == "__main__":
    asyncio.run(main())
