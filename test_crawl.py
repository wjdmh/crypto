import requests
from bs4 import BeautifulSoup

def fetch_top_10():
    try:
        url_kospi = "https://finance.naver.com/sise/sise_deal_rank.naver?maco=foreign&bbs_maco_req=1&sosok=01"
        url_kosdaq = "https://finance.naver.com/sise/sise_deal_rank.naver?maco=foreign&bbs_maco_req=1&sosok=02"
        headers = {'User-Agent': 'Mozilla/5.0'}
        targets = []
        
        # Get top 5 from Kospi
        res = requests.get(url_kospi, headers=headers)
        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.select('a.tltle'):
            href = a.get('href', '')
            if 'code=' in href:
                code = href.split('code=')[-1]
                targets.append(code)
                if len(targets) >= 5: break
                
        # Get top 5 from Kosdaq
        res2 = requests.get(url_kosdaq, headers=headers)
        soup2 = BeautifulSoup(res2.text, 'html.parser')
        for a in soup2.select('a.tltle'):
            href = a.get('href', '')
            if 'code=' in href:
                code = href.split('code=')[-1]
                targets.append(code)
                if len(targets) >= 10: break
                
        print("Crawled:", targets)
    except Exception as e:
        print("Error:", e)

fetch_top_10()
