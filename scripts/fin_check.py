"""점검: 시가총액 200억 달러 이상 미국 금융주가 매수 조건(금융업)을 몇 개 통과하는지. 결과는 out.txt"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import analyze
import card_rules as cr

CACHE = 'data/cache'
load = lambda n: json.load(open(f'{CACHE}/{n}', encoding='utf-8'))
listing, sic, fin, prices = (load(n) for n in ('listing.json', 'sic.json', 'fin.json', 'prices.json'))
rows = []
for cik, info in listing.items():
    code = str(sic.get(cik, ''))
    if cik not in fin or code[:2] not in ('60', '61', '62', '63', '64', '67'):
        continue
    a = analyze(fin[cik], prices.get(cik), fin[cik].get('shares'))
    if not a or not a.get('marcap') or a['marcap'] < 20e9:
        continue
    s = {'code': info['ticker'], 'name': info['name'], 'fin': True, **a, 'wk': prices.get(cik, {}).get('wk')}
    s['record'] = bool((s['rev_q'] and s['rev_ttm']) or (s['op_q'] and s['op_ttm']))
    s['obv_signal'] = cr.obv_signal(s.get('wk'))
    c, m1, m2 = s.get('close'), s.get('ma120'), s.get('ma200')
    chk = {
        '실적신고가': s['record'],
        '이평선(120·200 중 하나 위)': bool(c and m1 and m2 and (c >= m1 or c >= m2)),
        '변동성축소': bool(s.get('vol_down')),
        'OBV이탈아님': s['obv_signal'] != '이탈',
        'PBR≤1': cr.positive(s.get('pbr')) and s['pbr'] <= 1,
        'ROE3≥10': cr.valid(s.get('roe3')) and s['roe3'] >= 10,
        '세전이익흑자': cr.positive(s.get('op_ttm_value')),
        '이익유지': cr.profit_holding(s),
    }
    fails = [k for k, v in chk.items() if not v]
    rows.append((len(fails), -s['marcap'], s['code'], s['name'][:28], s['marcap'] / 1e9, s.get('pbr'), s.get('roe3'), fails))
rows.sort()
out = [f'시총 200억$ 이상 미국 금융주 {len(rows)}개 · 못 넘은 조건 수 적은 순']
for n, _, t, nm, mc, pbr, roe, fails in rows[:40]:
    out.append(f"{t} {nm} 시총 {mc:.0f}B PBR {pbr if pbr is None else round(pbr, 2)} ROE3 {roe if roe is None else round(roe, 1)} · 미달 {n}: {', '.join(fails) or '없음'}")
open('out.txt', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
print('\n'.join(out))
