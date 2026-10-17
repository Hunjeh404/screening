"""캐시된 재무·시세로 종목별 지표를 계산해 docs/data.json 생성."""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import analyze, shift_label

CACHE = sys.argv[1] if len(sys.argv) > 1 else 'data/cache'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'docs/data.json'
load = lambda n: json.load(open(f'{CACHE}/{n}', encoding='utf-8'))

listing, sic, fin, prices = (load(n) for n in ('listing.json', 'sic.json', 'fin.json', 'prices.json'))

stocks = []
for cik, info in listing.items():
    if cik not in fin:
        continue
    a = analyze(fin[cik], prices.get(cik), fin[cik].get('shares'), fin[cik].get('eps_shares'))
    if not a:
        continue
    code = str(sic.get(cik, ''))
    stocks.append({'_cik': cik, 'code': info['ticker'], 'name': info['name'], 'market': info['exchange'],
                   'fin': code[:2] in ('60', '61', '62', '63', '64', '67'), **a})

# 실적 발표가 늦어 최신 분기가 2분기 이상 뒤처진 종목은 신고가 판정에서 제외
newest = max(s['latest_q'] for s in stocks)
fresh = shift_label(newest, -1)
for s in stocks:
    s['stale'] = s['latest_q'] < fresh
    s['record'] = not s['stale'] and ((s['rev_q'] and s['rev_ttm']) or (s['op_q'] and s['op_ttm']))
    # 주봉 OBV 다이버전스 판정용 주봉 데이터는 화면에서 걸러질 수 있는 신고가 종목에만 싣는다
    cik = s.pop('_cik')
    s['wk'] = prices.get(cik, {}).get('wk') if s['record'] else None

meta = {'generated': dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M'),
        'sample': os.path.exists(f'{CACHE}/SAMPLE'), 'count': len(stocks), 'newest_q': newest}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump({'meta': meta, 'stocks': stocks}, open(OUT, 'w', encoding='utf-8'),
          ensure_ascii=False, separators=(',', ':'))
print(f"{len(stocks)}종목, 실적 신고가 {sum(s['record'] for s in stocks)}종목 -> {OUT}")
