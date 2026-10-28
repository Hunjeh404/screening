"""한국: 캐시된 재무·시세로 종목별 지표를 계산해 docs/data_kr.json 생성 (계산은 미국과 같은 lib.analyze)."""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import analyze
from card_rules import persist_cards, write_json, SCHEMA_VERSION
from lib_kr import to_ext
import china

CACHE = sys.argv[1] if len(sys.argv) > 1 else 'data/cache/kr'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'docs/data_kr.json'
load = lambda n: json.load(open(f'{CACHE}/{n}', encoding='utf-8'))

listing, corp, induty, fin, prices = (load(n) for n in
    ('listing.json', 'corp.json', 'induty.json', 'fin2.json', 'prices.json'))
today = dt.date.today().isoformat()
fx = load('fx.json') if os.path.exists(f'{CACHE}/fx.json') else {}
rate = fx.get('krw_per_usd')
if not isinstance(rate, (int, float)) or rate <= 0:
    rate = None

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

if not stocks:
    raise SystemExit('분석 가능한 종목이 없어 기존 데이터와 이력을 보존합니다')
if not any(s.get('ma200') for s in stocks):
    raise SystemExit('200일선 시세를 먼저 새로 수집해야 합니다')
newest = max(s['latest_q'] for s in stocks)
for s in stocks:
    s['record'] = bool((s['rev_q'] and s['rev_ttm']) or (s['op_q'] and s['op_ttm']))
    s['wk'] = prices.get(s['code'], {}).get('wk')
    s['mcap_b'] = s['marcap'] / rate / 1e9 if s['marcap'] is not None and rate else None


# 중국계 기업 제외: 실적 신고가 종목과 이력에 있는 종목만 확인(캐시 30일)
HIST = os.path.join(os.path.dirname(OUT), 'card_history.json')
held = {k[3:] for k in (json.load(open(HIST, encoding='utf-8')).get('tickers', {}) if os.path.exists(HIST) else {})
        if k.startswith('kr:')}
key = os.environ.get('DART_API_KEY')
cn = {}
if key:
    cn = china.kr_flags([s['code'] for s in stocks if s['record'] or s['code'] in held], corp, f'{CACHE}/cn_kr.json', key)
    print('::notice title=china::중국계 제외', len(cn), '종목:', ', '.join(f'{c}({w})' for c, w in sorted(cn.items())), flush=True)
else:
    print('::warning::DART_API_KEY가 없어 중국계 확인을 건너뜀')
stocks = [s for s in stocks if s['code'] not in cn]

meta = {'generated': dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M'),
        'schema_version': SCHEMA_VERSION, 'sample': os.path.exists(f'{CACHE}/SAMPLE'), 'count': len(stocks), 'newest_q': newest, 'fx': fx}
persist_cards(stocks, 'kr', meta['generated'], os.path.join(os.path.dirname(OUT), 'card_history.json'),
             listed=set(listing) - set(cn))   # 제외 종목은 이력도 지움
# 화면에는 매수·보유·매도 상태인 종목만 쓰므로 그 종목만 싣는다 (전체 분석 수는 meta.count)
shown = [s for s in stocks if s.get('state')]
meta['shown'] = len(shown)
write_json(OUT, {'meta': meta, 'stocks': shown})
print(f"{len(stocks)}종목, 실적 신고가 {sum(s['record'] for s in stocks)}종목 -> {OUT}")
