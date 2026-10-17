"""SEC 재무 + 야후 시세 수집. GitHub Actions에서 실행."""
import datetime as dt
import json
import os
import sys
import time
import zipfile

import requests
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
from lib import extract, shares_out, price_metrics

# SEC는 연락처가 담긴 User-Agent를 요구한다
UA = {'User-Agent': os.environ.get('SEC_USER_AGENT') or sys.exit('SEC_USER_AGENT 시크릿이 없습니다 (예: Hong Gildong you@gmail.com)')}
CACHE = 'data/cache'
TMP = 'data/tmp'
os.makedirs(CACHE, exist_ok=True)
os.makedirs(TMP, exist_ok=True)


def load(name, default):
    p = f'{CACHE}/{name}'
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default


def save(name, obj):
    json.dump(obj, open(f'{CACHE}/{name}', 'w', encoding='utf-8'), ensure_ascii=False)


def get(url, **kw):
    for i in range(3):
        r = requests.get(url, headers=UA, timeout=120, **kw)
        if r.status_code == 200:
            return r
        time.sleep(5 * (i + 1))
    r.raise_for_status()


# 1) 뉴욕증권거래소·나스닥 상장 종목 (회사당 대표 티커 하나)
tk = get('https://www.sec.gov/files/company_tickers_exchange.json').json()
cols = tk['fields']
listing = {}
for row in tk['data']:
    x = dict(zip(cols, row))
    if x['exchange'] in ('NYSE', 'Nasdaq') and x['cik'] not in listing:
        listing[x['cik']] = {'ticker': x['ticker'], 'name': x['name'], 'exchange': x['exchange']}
print('종목 수', len(listing))

# 2) 업종코드(SIC): 금융업 판정·스팩 제외용, 없는 것만 조회
sic = load('sic.json', {})
for cik in listing:
    if str(cik) not in sic:
        try:
            sic[str(cik)] = get(f'https://data.sec.gov/submissions/CIK{cik:010d}.json').json().get('sic', '')
        except Exception:
            sic[str(cik)] = ''
        time.sleep(0.12)  # SEC 초당 10회 제한
save('sic.json', sic)
listing = {c: v for c, v in listing.items() if sic.get(str(c)) != '6770'}  # 스팩 제외

# 3) 재무: SEC 일괄 파일(companyfacts.zip)에서 대상 회사만 추출
zp = f'{TMP}/companyfacts.zip'
with get('https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip', stream=True) as r:
    with open(zp, 'wb') as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
fin = {}
with zipfile.ZipFile(zp) as z:
    names = set(z.namelist())
    for cik in listing:
        n = f'CIK{cik:010d}.json'
        if n in names:
            facts = json.loads(z.read(n)).get('facts', {})
            fin[str(cik)] = {**extract(facts), 'shares': shares_out(facts)}
os.remove(zp)
save('fin.json', fin)
save('listing.json', listing)
print('재무', len(fin))

# 4) 시세: 6년치 종가 -> 지표만 저장
tickers = {v['ticker']: c for c, v in listing.items() if str(c) in fin}
prices = {}
tl = list(tickers)
for i in range(0, len(tl), 100):
    chunk = tl[i:i + 100]
    try:
        df = yf.download(chunk, period='6y', interval='1d', auto_adjust=False,
                         progress=False, threads=True, group_by='ticker')
    except Exception as e:
        print('시세 실패', chunk[0], e)
        continue
    for t in chunk:
        try:
            s = df[t]['Close'].dropna()
            m = price_metrics([x.date() for x in s.index], s.astype(float).tolist())
            if m:
                prices[str(tickers[t])] = m
        except KeyError:
            pass
    time.sleep(2)
save('prices.json', prices)
print('시세', len(prices))
