"""캐시된 재무·시세로 종목별 지표를 계산해 docs/data.json 생성."""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import analyze
from card_rules import persist_cards, write_json, SCHEMA_VERSION

CACHE = sys.argv[1] if len(sys.argv) > 1 else 'data/cache'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'docs/data.json'
load = lambda n: json.load(open(f'{CACHE}/{n}', encoding='utf-8'))

listing, sic, fin, prices = (load(n) for n in ('listing.json', 'sic.json', 'fin.json', 'prices.json'))

stocks = []
for cik, info in listing.items():
    if cik not in fin:
        continue
    a = analyze(fin[cik], prices.get(cik), fin[cik].get('shares'))
    if not a:
        continue
    code = str(sic.get(cik, ''))
    stocks.append({'_cik': cik, 'code': info['ticker'], 'name': info['name'], 'market': info['exchange'],
                   'fin': code[:2] in ('60', '61', '62', '63', '64', '67'), **a})

if not stocks:
    raise SystemExit('분석 가능한 종목이 없어 기존 데이터와 이력을 보존합니다')
if not any(s.get('ma200') for s in stocks):
    raise SystemExit('200일선 시세를 먼저 새로 수집해야 합니다')
newest = max(s['latest_q'] for s in stocks)
for s in stocks:
    s['record'] = bool((s['rev_q'] and s['rev_ttm']) or (s['op_q'] and s['op_ttm']))
    # 매수 조건에서 벗어난 이력 종목도 OBV 매도 판정 대상이다.
    cik = s.pop('_cik')
    s['wk'] = prices.get(cik, {}).get('wk')
    s['mcap_b'] = s['marcap'] / 1e9 if s['marcap'] is not None else None

meta = {'generated': dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M'),
        'schema_version': SCHEMA_VERSION, 'sample': os.path.exists(f'{CACHE}/SAMPLE'), 'count': len(stocks), 'newest_q': newest}
persist_cards(stocks, 'us', meta['generated'], os.path.join(os.path.dirname(OUT), 'card_history.json'),
             listed={v['ticker'] for v in listing.values()})
# 화면에는 매수·보유·매도 상태인 종목만 쓰므로 그 종목만 싣는다 (전체 분석 수는 meta.count)
shown = [s for s in stocks if s.get('state')]
meta['shown'] = len(shown)
write_json(OUT, {'meta': meta, 'stocks': shown})
print(f"{len(stocks)}종목, 실적 신고가 {sum(s['record'] for s in stocks)}종목 -> {OUT}")
