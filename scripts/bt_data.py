"""백테스트 원자료: SEC 재무(판정에 쓰는 항목만)와 2010년 이후 일봉(OHLCV·액면분할)을 data/bt에 저장.
GitHub Actions에서 실행 (SEC_USER_AGENT 필요, data/cache/listing.json은 일일 실행 캐시를 씀)."""
import gzip
import json
import os
import sys
import time
import zipfile

import numpy as np
import requests
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
from lib import DUR_TAGS, INST_TAGS, F_DUR_TAGS, CFO_TAGS, WSH_TAG

OUT, START = 'data/bt', '2010-06-01'
UA = {'User-Agent': os.environ.get('SEC_USER_AGENT') or sys.exit('SEC_USER_AGENT 필요')}
TAGS = ({t for ts in (*DUR_TAGS.values(), *INST_TAGS.values(), *F_DUR_TAGS.values()) for t in ts}
        | set(CFO_TAGS) | {WSH_TAG, 'CommonStockSharesOutstanding'})
KEEP = ('start', 'end', 'val', 'form', 'filed', 'accn')
os.makedirs(f'{OUT}/facts', exist_ok=True)
os.makedirs(f'{OUT}/px', exist_ok=True)
listing = json.load(open('data/cache/listing.json', encoding='utf-8'))


def slim(tag_obj):
    return {'units': {u: [{k: e[k] for k in KEEP if k in e} for e in es] for u, es in tag_obj.get('units', {}).items()}}


# 1) 재무: SEC 일괄 파일에서 대상 회사의 필요 항목만 남긴다
if not os.path.exists(f'{OUT}/facts/.done'):
    zp = 'data/tmp/companyfacts.zip'
    os.makedirs('data/tmp', exist_ok=True)
    with requests.get('https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip',
                      headers=UA, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(zp, 'wb') as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    with zipfile.ZipFile(zp) as z:
        names = set(z.namelist())
        for cik in listing:
            n = f'CIK{int(cik):010d}.json'
            if n not in names:
                continue
            facts = json.loads(z.read(n)).get('facts', {})
            red = {'us-gaap': {t: slim(v) for t, v in facts.get('us-gaap', {}).items() if t in TAGS},
                   'dei': {t: slim(v) for t, v in facts.get('dei', {}).items()
                           if t == 'EntityCommonStockSharesOutstanding'}}
            with gzip.open(f'{OUT}/facts/{cik}.json.gz', 'wt', encoding='utf-8') as f:
                json.dump(red, f)
    os.remove(zp)
    open(f'{OUT}/facts/.done', 'w').close()
print('재무', len(os.listdir(f'{OUT}/facts')) - 1, flush=True)

# 2) 일봉: 분할 반영 가격(배당 미반영), 분할 비율 포함
tickers = sorted({v['ticker'] for v in listing.values()} | {'SPY', 'QQQ', 'TQQQ', 'SCHD'})
todo = [t for t in tickers if not os.path.exists(f'{OUT}/px/{t}.npz')]
EPOCH = np.datetime64('1970-01-01')
for i in range(0, len(todo), 100):
    chunk = todo[i:i + 100]
    try:
        df = yf.download(chunk, start=START, auto_adjust=False, actions=True, progress=False,
                         threads=True, group_by='ticker')
    except Exception as e:
        print('시세 실패', chunk[0], type(e).__name__, flush=True)
        continue
    for t in chunk:
        try:
            x = df[t].dropna(subset=['Close'])
        except KeyError:
            continue
        if len(x) < 300:
            continue
        days = ((x.index.values.astype('datetime64[D]') - EPOCH).astype(np.int32))
        sp = x['Stock Splits'].fillna(0).to_numpy(np.float64) if 'Stock Splits' in x else np.zeros(len(x))
        np.savez_compressed(f'{OUT}/px/{t}.npz', d=days,
                            o=x['Open'].to_numpy(np.float64), h=x['High'].to_numpy(np.float64),
                            l=x['Low'].to_numpy(np.float64), c=x['Close'].to_numpy(np.float64),
                            v=x['Volume'].fillna(0).to_numpy(np.float64), s=sp)
    print('시세', i + len(chunk), '/', len(todo), flush=True)
    time.sleep(2)
print('시세 파일', len(os.listdir(f'{OUT}/px')))
# 지수 ETF는 배당(dv)까지 저장해 백테스트에서 배당 재투자 수익률로 쓴다
for t in ('SPY', 'QQQ', 'TQQQ', 'SCHD'):
    if 'dv' in np.load(f'{OUT}/px/{t}.npz').files:
        continue
    x = yf.download(t, start=START, auto_adjust=False, actions=True, progress=False)
    if hasattr(x.columns, 'levels'):
        x.columns = x.columns.get_level_values(0)
    x = x.dropna(subset=['Close'])
    days = ((x.index.values.astype('datetime64[D]') - EPOCH).astype(np.int32))
    col = lambda k: x[k].fillna(0).to_numpy(np.float64) if k in x else np.zeros(len(x))
    np.savez_compressed(f'{OUT}/px/{t}.npz', d=days, o=x['Open'].to_numpy(np.float64), h=x['High'].to_numpy(np.float64),
                        l=x['Low'].to_numpy(np.float64), c=x['Close'].to_numpy(np.float64), v=col('Volume'),
                        s=col('Stock Splits'), dv=col('Dividends'))
    print('배당 포함 시세', t, int((col('Dividends') > 0).sum()), '회', flush=True)

# 3) 실적발표일: SEC 일괄 제출목록(submissions.zip)에서 8-K 중 Item 2.02(실적 공시) 제출일만 남긴다
EP = f'{OUT}/earn.json'
if not os.path.exists(EP):
    zp = 'data/tmp/submissions.zip'
    os.makedirs('data/tmp', exist_ok=True)
    with requests.get('https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip',
                      headers=UA, stream=True, timeout=900) as r:
        r.raise_for_status()
        with open(zp, 'wb') as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    want = {f'CIK{int(c):010d}': c for c in listing}
    earn = {}
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():   # CIK##########.json(최근분), CIK##########-submissions-001.json(과거분)
            c = want.get(n[:13])
            if not c:
                continue
            j = json.loads(z.read(n))
            rec = j.get('filings', {}).get('recent', j)
            for form, day, items in zip(rec.get('form', []), rec.get('filingDate', []), rec.get('items', [])):
                if form == '8-K' and '2.02' in (items or '') and day >= '2010-01-01':
                    earn.setdefault(c, set()).add(day)
    os.remove(zp)
    json.dump({c: sorted(v) for c, v in earn.items()}, open(EP, 'w', encoding='utf-8'))
print('실적발표일', len(json.load(open(EP, encoding='utf-8'))), '종목', flush=True)
