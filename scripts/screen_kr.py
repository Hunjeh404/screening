"""한국: 캐시된 재무·시세로 종목별 지표를 계산해 docs/data_kr.json 생성 (계산은 미국과 같은 lib.analyze)."""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import analyze, shift_label
from lib_kr import to_ext

CACHE = sys.argv[1] if len(sys.argv) > 1 else 'data/cache/kr'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'docs/data_kr.json'
load = lambda n: json.load(open(f'{CACHE}/{n}', encoding='utf-8'))

listing, corp, induty, fin, prices = (load(n) for n in
    ('listing.json', 'corp.json', 'induty.json', 'fin.json', 'prices.json'))
today = dt.date.today().isoformat()

stocks = []
for code, info in listing.items():
    cc = corp.get(code)
    if not cc or cc not in fin:
        continue
    # 한국은 상장주식수가 시세 목록에서 매일 갱신되므로 오늘 날짜 기준으로 넘긴다
    a = analyze(to_ext(fin[cc]), prices.get(code), {'val': info['shares'], 'end': today})
    if not a:
        continue
    stocks.append({'code': code, 'yahoo': code + ('.KQ' if info['market'] == 'KOSDAQ' else '.KS'),
                   'name': info['name'], 'market': info['market'],
                   'fin': induty.get(cc, '')[:2] in ('64', '65', '66'), **a})

# 실적 발표가 늦어 최신 분기가 2분기 이상 뒤처진 종목은 신고가 판정에서 제외
newest = max(s['latest_q'] for s in stocks)
fresh = shift_label(newest, -1)
for s in stocks:
    s['stale'] = s['latest_q'] < fresh
    s['record'] = not s['stale'] and ((s['rev_q'] and s['rev_ttm']) or (s['op_q'] and s['op_ttm']))
    s['wk'] = prices.get(s['code'], {}).get('wk') if s['record'] else None

meta = {'generated': dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M'),
        'sample': os.path.exists(f'{CACHE}/SAMPLE'), 'count': len(stocks), 'newest_q': newest}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump({'meta': meta, 'stocks': stocks}, open(OUT, 'w', encoding='utf-8'),
          ensure_ascii=False, separators=(',', ':'))
print(f"{len(stocks)}종목, 실적 신고가 {sum(s['record'] for s in stocks)}종목 -> {OUT}")
