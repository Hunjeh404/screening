"""한국: DART 재무 + 시세 수집. GitHub Actions에서 실행 (환경변수 DART_API_KEY 필요)."""
import datetime as dt
import io
import json
import os
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests
import FinanceDataReader as fdr
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
from lib import price_metrics
from hunbot import completed_day
from dart_batches import fetch_batches

KEY = os.environ.get('DART_API_KEY') or sys.exit('DART_API_KEY 시크릿이 없습니다')
API = 'https://opendart.fss.or.kr/api'
CACHE = 'data/cache/kr'
REPORTS = {1: '11013', 2: '11012', 3: '11014', 4: '11011'}  # 1분기, 반기, 3분기, 사업보고서
FIRST_YEAR = 2015  # DART 재무 API 제공 시작 연도
os.makedirs(CACHE, exist_ok=True)


def load(name, default):
    p = f'{CACHE}/{name}'
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default


def save(name, obj):
    path = f'{CACHE}/{name}'
    with open(path + '.tmp', 'w', encoding='utf-8') as handle:
        json.dump(obj, handle, ensure_ascii=False)
    os.replace(path + '.tmp', path)


class LimitReached(Exception):
    pass


def dart(path, **params):
    # 일시적인 연결 실패(시간 초과 등)는 간격을 늘려 가며 다시 시도 (대기 합계 약 5분)
    waits = (10, 20, 30, 60, 90, 120)
    for i in range(len(waits) + 1):
        try:
            r = requests.get(f'{API}/{path}', params={'crtfc_key': KEY, **params}, timeout=60)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            if i == len(waits):
                raise
            print(f'DART 연결 실패({type(e).__name__}), {waits[i]}초 후 재시도', flush=True)
            time.sleep(waits[i])
    if path.endswith('.xml'):
        return r.content
    j = r.json()
    if j.get('status') == '020':
        raise LimitReached(j.get('message'))
    if j.get('status') not in ('000', '013'):
        raise RuntimeError(f"DART 오류 {j.get('status')}: {j.get('message')}")
    time.sleep(0.07)
    return j


# 1) 상장 종목 목록 (보통주만, 스팩 제외)
lst = fdr.StockListing('KRX')
lst = lst[lst['Market'].isin(['KOSPI', 'KOSDAQ', 'KOSDAQ GLOBAL'])]
lst = lst[lst['Code'].str.endswith('0') & ~lst['Name'].str.contains('스팩')]
listing = {r.Code: {'name': r.Name, 'market': 'KOSDAQ' if 'KOSDAQ' in r.Market else 'KOSPI',
                    'shares': int(r.Stocks), 'marcap': int(r.Marcap)}
           for r in lst.itertuples()}
print('종목 수', len(listing))

# 2) 종목코드 -> DART 고유번호
z = zipfile.ZipFile(io.BytesIO(dart('corpCode.xml')))
root = ET.fromstring(z.read(z.namelist()[0]))
corp = {}
for e in root.iter('list'):
    sc = (e.findtext('stock_code') or '').strip()
    if sc in listing:
        corp[sc] = e.findtext('corp_code')
save('corp.json', corp)
save('listing.json', listing)

induty = load('induty.json', {})
fin = load('fin2.json', {})
done = set(load('fin2_done.json', []))
try:
    # 3) 업종코드 (금융업 판정용, 없는 것만 조회)
    for sc, cc in corp.items():
        if cc not in induty:
            j = dart('company.json', corp_code=cc)
            induty[cc] = j.get('induty_code', '')
            if len(induty) % 50 == 0:
                save('induty.json', induty)
    save('induty.json', induty)

    # 4) 분기 재무: 과거는 캐시 재사용, 최근 2개 연도는 매번 다시 받음(정정공시 반영)
    today = dt.date.today()
    codes = sorted(set(corp.values()))
    for y in range(today.year, FIRST_YEAR - 1, -1):
        for q, rc in REPORTS.items():
            lab = f'{y}Q{q}'
            if dt.date(y, q * 3, 1) > today:
                continue
            if lab in done and y < today.year - 1:
                continue
            print('재무 조회 시작', lab, flush=True)
            values = fetch_batches(codes, y, q, rc, today, CACHE, dart)
            for cc, vals in values.items():
                if vals:
                    fin.setdefault(cc, {})[lab] = vals
            done.add(lab)
            save('fin2.json', fin)
            save('fin2_done.json', sorted(done))
            print('재무', lab)
    save('fin2.json', fin)
    save('fin2_done.json', sorted(done))
except LimitReached as e:
    print('DART 일일 호출 한도 도달, 다음 실행에서 이어 받음:', e)
    save('induty.json', induty)
    save('fin2.json', fin)
    save('fin2_done.json', sorted(done))
    raise SystemExit('재무 수집이 완료되지 않아 기존 공개 데이터를 유지합니다')

# 5) 시세 (6년치 종가 -> 지표만 저장)
start = (dt.date.today() - dt.timedelta(days=365 * 6 + 30)).isoformat()


def px(code):
    try:
        df = fdr.DataReader(code, start).dropna(subset=['Close'])
        df = df.loc[[completed_day(d.date(), 'kr') for d in df.index]]
        return code, price_metrics([d.date() for d in df.index], df['Close'].astype(float).tolist(),
                                   df['Volume'].fillna(0).astype(float).tolist(),
                                   df['High'].astype(float).tolist(), df['Low'].astype(float).tolist())
    except Exception:
        return code, None


with ThreadPoolExecutor(8) as ex:
    prices = {c: m for c, m in ex.map(px, listing) if m}
save('prices.json', prices)
print('시세', len(prices))

# 손절폭의 $0.3B 기준을 한국에도 동일하게 적용하기 위한 실제 원/달러 환율
try:
    fx_df = yf.download('USDKRW=X', period='5d', auto_adjust=False, progress=False, group_by='ticker')
    fx_close = fx_df['USDKRW=X']['Close'].dropna()
    if not fx_close.empty and float(fx_close.iloc[-1]) > 0:
        save('fx.json', {'krw_per_usd': float(fx_close.iloc[-1]), 'date': fx_close.index[-1].date().isoformat()})
except Exception as e:
    print('환율 조회 실패, 이전 환율이 있으면 재사용:', type(e).__name__)
