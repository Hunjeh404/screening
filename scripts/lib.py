"""계산 함수 모음 (네트워크 없음). 입력은 SEC companyfacts JSON."""
import datetime as dt
import statistics
from hunbot import latest_signal

DUR_TAGS = {
    'rev': ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax',
            'RevenueFromContractWithCustomerIncludingAssessedTax', 'SalesRevenueNet',
            'SalesRevenueGoodsNet', 'SalesRevenueServicesNet', 'RevenuesNetOfInterestExpense'],
    'op': ['OperatingIncomeLoss'],
    'pretax': ['IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
               'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments'],
    'ni': ['NetIncomeLoss', 'NetIncomeLossAvailableToCommonStockholdersBasic', 'ProfitLoss'],  # ProfitLoss는 비지배지분 포함이라 마지막
}
INST_TAGS = {
    'equity': ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
    'liab': ['Liabilities'],
    'le': ['LiabilitiesAndStockholdersEquity'],
    # F-스코어용 재무상태
    'assets': ['Assets'],
    'ca': ['AssetsCurrent'],
    'cl': ['LiabilitiesCurrent'],
}
# F-스코어용 손익·현금흐름 (현금흐름표는 누적 기간으로만 공시되어 분기값을 차감으로 구한다)
F_DUR_TAGS = {
    'gp': ['GrossProfit'],
    'cogs': ['CostOfRevenue', 'CostOfGoodsAndServicesSold', 'CostOfGoodsSold'],
}
CFO_TAGS = ['NetCashProvidedByUsedInOperatingActivities',
            'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations']
WSH_TAG = 'WeightedAverageNumberOfSharesOutstandingBasic'
FORMS = ('10-K', '10-Q', '10-K/A', '10-Q/A')


def d(s):
    return dt.date.fromisoformat(s)


def label(end):
    """기간 종료일 -> 달력 분기 라벨. 52/53주 회계연도의 며칠 어긋남은 20일 당겨서 흡수"""
    e = d(end) - dt.timedelta(days=20)
    return f'{e.year}Q{(e.month - 1) // 3 + 1}'


def shift_label(lab, n):
    i = int(lab[:4]) * 4 + int(lab[-1]) - 1 + n
    return f'{i // 4}Q{i % 4 + 1}'


def label_seq(first, last):
    seq, lab = [], first
    while lab <= last:
        seq.append(lab)
        lab = shift_label(lab, 1)
    return seq


def usd(facts, tag):
    return facts.get('us-gaap', {}).get(tag, {}).get('units', {}).get('USD', [])


def units(facts, tag, unit):
    return facts.get('us-gaap', {}).get(tag, {}).get('units', {}).get(unit, [])


def latest_by_period(entries, instant=False):
    """같은 기간을 여러 공시가 보고하면 가장 나중에 낸 값(정정 반영)을 쓴다"""
    out = {}
    for e in entries:
        if e.get('form') not in FORMS:
            continue
        key = e['end'] if instant else (e.get('start'), e['end'])
        if not instant and not e.get('start'):
            continue
        if key not in out or e['filed'] > out[key][0]:
            out[key] = (e['filed'], e['val'])
    return {k: v[1] for k, v in out.items()}


def duration_series(facts, tags, combine):
    """여러 태그 -> {(start,end): 값}. combine='max'(매출) 또는 'first'(우선순위)"""
    merged = {}
    for t in tags:
        for k, v in latest_by_period(usd(facts, t)).items():
            if k not in merged:
                merged[k] = v
            elif combine == 'max':
                merged[k] = max(merged[k], v)
    return merged


def split_periods(per):
    """기간값 -> 분기값{end: v}, 연간값{end: (start, v)}. 4분기는 연간에서 빼서 구한다"""
    q, a, ytd9 = {}, {}, {}
    for (s, e), v in per.items():
        n = (d(e) - d(s)).days
        if 80 <= n <= 100:
            q[e] = v
        elif 350 <= n <= 380:
            a[e] = (s, v)
        elif 260 <= n <= 285:
            ytd9[(s, e)] = v
    for e, (s, v) in a.items():
        if e in q:
            continue
        inner = [qe for qe in q if s < qe < e]
        if len(inner) == 3:
            q[e] = v - sum(q[x] for x in inner)
        else:
            nine = [val for (ys, ye), val in ytd9.items()
                    if abs((d(ys) - d(s)).days) <= 7 and s < ye < e]
            if nine:
                q[e] = v - nine[0]
    return q, {e: v for e, (s, v) in a.items()}


def ytd_quarters(per):
    """같은 시작일의 누적 기간값(3·6·9·12개월)을 차례로 빼서 분기값{end: v}을 만든다"""
    groups, q = {}, {}
    for (s, e), v in per.items():
        groups.setdefault(s, []).append((e, v))
    for s, items in groups.items():
        prev_e, prev_v = s, 0
        for e, v in sorted(items):
            if 80 <= (d(e) - d(prev_e)).days <= 100:
                q.setdefault(e, v - prev_v)
            prev_e, prev_v = e, v
    return q


def yoy_pair(per):
    """가장 최근 기간값과 그 1년 전 같은 길이 기간값 (주식 수처럼 더할 수 없는 값용)"""
    rows = []
    for (s, e), v in per.items():
        n = (d(e) - d(s)).days
        kind = 'q' if 80 <= n <= 100 else 'a' if 350 <= n <= 380 else None
        if kind:
            rows.append((e, kind == 'q', kind, v))
    if not rows:
        return None
    e, _, kind, v = max(rows)
    prev = [pv for pe, _, pk, pv in rows if pk == kind and abs((d(e) - d(pe)).days - 365) <= 30]
    return {'now': v, 'prev': prev[0] if prev else None, 'end': e}


def extract(facts):
    """companyfacts -> {'q': {라벨: {rev,op,ni}}, 'a': {연말일: {rev,op,ni}}, 'bs': {라벨: {equity,liab}}}"""
    series = {}
    for k, tags in DUR_TAGS.items():
        series[k] = split_periods(duration_series(facts, tags, 'max' if k == 'rev' else 'first'))
    # 영업이익이 없는 회사(은행 등)는 세전이익으로 대신
    if not series['op'][0]:
        series['op'] = series['pretax']
    q, a = {}, {}
    for k in ('rev', 'op', 'ni'):
        qs, ann = series[k]
        for e, v in qs.items():
            q.setdefault(label(e), {})[k] = v
        for e, v in ann.items():
            a.setdefault(e, {})[k] = v
    inst = {k: {} for k in INST_TAGS}
    for k, tags in INST_TAGS.items():
        for t in tags:
            for e, v in latest_by_period(usd(facts, t), instant=True).items():
                inst[k].setdefault(e, v)
    bs = {}
    for e, eq in inst['equity'].items():
        liab = inst['liab'].get(e)
        if liab is None and e in inst['le']:
            liab = inst['le'][e] - eq
        bs[e] = {'equity': eq, 'liab': liab,
                 **{k: inst[k].get(e) for k in ('assets', 'ca', 'cl')}}
    for e in a:
        if e in bs:
            a[e]['equity'] = bs[e]['equity']
    fq = {}
    for k, tags in F_DUR_TAGS.items():
        for e, v in split_periods(duration_series(facts, tags, 'first'))[0].items():
            fq.setdefault(label(e), {})[k] = v
    for e, v in ytd_quarters(duration_series(facts, CFO_TAGS, 'first')).items():
        fq.setdefault(label(e), {})['cfo'] = v
    return {'q': q, 'a': a, 'bs': {label(e): v for e, v in sorted(bs.items())}, 'fq': fq,
            'wsh': yoy_pair(latest_by_period(units(facts, WSH_TAG, 'shares')))}


def shares_out(facts):
    """주식 수 {'val', 'end'}. 분기 공시의 가중평균 기본주식수(EPS 계산 단위)를 우선 쓰고,
    없으면 표지의 발행주식수(보통주 종류가 여럿이면 같은 공시의 값을 합산)를 쓴다."""
    wa = latest_by_period(facts.get('us-gaap', {}).get('WeightedAverageNumberOfSharesOutstandingBasic', {})
                          .get('units', {}).get('shares', []))
    wa = {k: v for k, v in wa.items() if 80 <= (d(k[1]) - d(k[0])).days <= 380 and v}
    if wa:
        k = max(wa, key=lambda k: (k[1], -(d(k[1]) - d(k[0])).days))
        return {'val': wa[k], 'end': k[1]}
    ent = facts.get('dei', {}).get('EntityCommonStockSharesOutstanding', {}).get('units', {}).get('shares', [])
    if ent:
        last = max(ent, key=lambda e: (e['end'], e['filed']))
        same = {e['val'] for e in ent if e.get('accn') == last.get('accn') and e['end'] == last['end']}
        return {'val': sum(same), 'end': last['end']}
    return None


def ttm(seq, qv, key):
    res = []
    for i in range(len(seq)):
        w = [qv.get(l, {}).get(key) for l in seq[max(0, i - 3):i + 1]]
        res.append(sum(w) if i >= 3 and all(v is not None for v in w) else None)
    return res


def is_record(series, min_prior):
    """마지막 값이 양수이고 이전 모든 값보다 큰가"""
    if not series or series[-1] is None or series[-1] <= 0:
        return False
    prior = [v for v in series[:-1] if v is not None]
    return len(prior) >= min_prior and series[-1] > max(prior)


def weekly(dates, closes, vols, keep=105):
    """일봉 -> 주봉 (주 마지막 종가, 주간 거래량 합계)"""
    wc, wv, key = [], [], None
    for dd, c, v in zip(dates, closes, vols):
        k = dd.isocalendar()[:2]
        if k != key:
            wc.append(c); wv.append(0); key = k
        wc[-1] = c
        wv[-1] += v or 0
    # 마지막 주는 금요일 종가까지 있어야 끝난 주봉 (OBV 판정은 끝난 주봉만 쓴다)
    return {'c': [float(f'{x:.5g}') for x in wc[-keep:]], 'v': [int(x) for x in wv[-keep:]],
            'complete': bool(dates) and dates[-1].weekday() == 4}


def price_metrics(dates, closes, vols=None, highs=None, lows=None):
    n = len(closes)
    if n < 60:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    ma = lambda w: sum(closes[-w:]) / w if n >= w else None
    sd = lambda w: statistics.pstdev(rets[-w:]) if len(rets) >= w else None
    qe = {}
    for dd, c in zip(dates, closes):
        qe[f'{dd.year}Q{(dd.month - 1) // 3 + 1}'] = c
    keep = sorted(qe)[-25:]
    return {
        'close': closes[-1], 'ma120': ma(120), 'ma200': ma(200), 'ma20': ma(20),
        'change_pct': (closes[-1] / closes[-2] - 1) * 100, 'price_date': dates[-1].isoformat(),
        'vol20': sd(20), 'vol60': sd(60), 'high52': max(closes[-250:]),
        'qe': {k: qe[k] for k in keep},
        'wk': weekly(dates, closes, vols) if vols else None,
        'hunbot': latest_signal(closes, highs, lows),
    }


def gp_quarters(ext):
    """분기 매출총이익 (없으면 매출 - 매출원가). 미국만 있음"""
    out = {}
    for l, v in (ext.get('fq') or {}).items():
        rev = ext['q'].get(l, {}).get('rev')
        gp = v.get('gp') if v.get('gp') is not None else (rev - v['cogs'] if v.get('cogs') is not None and rev is not None else None)
        out[l] = {'gp': gp}
    return out


def fscore(ext, seq, ni_t, rev_t):
    """피오트로스키 F-스코어를 최근 4분기 합계와 1년 전 비교로 계산한다.
    미국은 9개 항목, 한국은 DART 일괄 조회에 없는 현금흐름·매출총이익 3개를 뺀 6개 항목.
    계산할 수 없는 항목은 None(0점)."""
    full = 'fq' in ext
    L, P = seq[-1], shift_label(seq[-1], -4)
    now, prev = ext['bs'].get(L) or {}, ext['bs'].get(P) or {}
    div = lambda a, b: a / b if a is not None and b is not None and b > 0 else None
    gt = lambda a, b: None if a is None or b is None else a > b
    at = lambda t, i: t[i] if len(t) >= -i else None
    ni_now, ni_prev, rev_now, rev_prev = at(ni_t, -1), at(ni_t, -5), at(rev_t, -1), at(rev_t, -5)

    def ncl(b):  # 비유동부채
        if b.get('ncl') is not None:
            return b['ncl']
        return b['liab'] - b['cl'] if b.get('liab') is not None and b.get('cl') is not None else None

    roa_now, roa_prev = div(ni_now, now.get('assets')), div(ni_prev, prev.get('assets'))
    lev_now, lev_prev = div(ncl(now), now.get('assets')), div(ncl(prev), prev.get('assets'))
    cr_now, cr_prev = div(now.get('ca'), now.get('cl')), div(prev.get('ca'), prev.get('cl'))
    turn_now, turn_prev = div(rev_now, now.get('assets')), div(rev_prev, prev.get('assets'))
    if full:
        w = ext.get('wsh') or {}
        sh_now, sh_prev = w.get('now'), w.get('prev')
    else:
        sh_now, sh_prev = now.get('cap'), prev.get('cap')
    items = {'ROA 양수': gt(roa_now, 0)}
    if full:
        cfo_t, gp_t = ttm(seq, ext['fq'], 'cfo'), ttm(seq, gp_quarters(ext), 'gp')
        cfo_now = at(cfo_t, -1)
        items['영업현금흐름 양수'] = gt(cfo_now, 0)
    items['ROA 개선'] = gt(roa_now, roa_prev)
    if full:
        items['영업현금흐름 > 순이익'] = gt(cfo_now, ni_now)
    items['장기부채 비중 감소'] = gt(lev_prev, lev_now)
    items['유동비율 개선'] = gt(cr_now, cr_prev)
    items['신주 발행 없음'] = None if sh_now is None or sh_prev is None else sh_now <= sh_prev
    if full:
        items['매출총이익률 개선'] = gt(div(at(gp_t, -1), rev_now), div(at(gp_t, -5), rev_prev))
    items['자산회전율 개선'] = gt(turn_now, turn_prev)
    return sum(v is True for v in items.values()), len(items), items


def analyze(ext, px, sh):
    """한 종목의 지표 계산. 필터·상태 판정은 card_rules에서 한다.
    sh: shares_out() 결과. 주식 수가 없거나 오래됐거나, 계산 결과가 불가능한 값이면
    (공시 단위와 거래 주식 종류가 다른 경우 등) 밸류에이션 지표를 비운다."""
    qv = ext['q']
    labs = sorted(l for l in qv if qv[l].get('rev') is not None or qv[l].get('op') is not None)
    if len(labs) < 5 or not px:
        return None
    seq = label_seq(labs[0], labs[-1])
    rev_q = [qv.get(l, {}).get('rev') for l in seq]
    op_q = [qv.get(l, {}).get('op') for l in seq]
    rev_t, op_t, ni_t = ttm(seq, qv, 'rev'), ttm(seq, qv, 'op'), ttm(seq, qv, 'ni')
    rec = {'rev_q': is_record(rev_q, 8), 'rev_ttm': is_record(rev_t, 4),
           'op_q': is_record(op_q, 8), 'op_ttm': is_record(op_t, 4)}

    years = sorted(ext['a'])
    ann = ext['a']
    roes = []
    for i, e in enumerate(years[-3:]):
        j = len(years) - 3 + i
        ni, eq = ann[e].get('ni'), ann[e].get('equity')
        eq0 = ann[years[j - 1]].get('equity') if j > 0 else None
        base = (eq + eq0) / 2 if (eq and eq0) else eq
        if ni is not None and base and base > 0:
            roes.append(ni / base * 100)
    roe3 = sum(roes) / len(roes) if len(roes) == 3 else None
    profit_years = 0  # 최근부터 연속 흑자(영업이익) 연수
    for e in reversed(years):
        v = ann[e].get('op')
        if v is None or v <= 0:
            break
        profit_years += 1

    bs = next((ext['bs'][l] for l in sorted(ext['bs'], reverse=True) if ext['bs'][l].get('equity')), {})
    eq, liab = bs.get('equity'), bs.get('liab')
    debt = liab / eq * 100 if (eq and eq > 0 and liab is not None) else None

    shares = sh['val'] if sh and label(sh['end']) >= shift_label(seq[-1], -3) else None
    share_warn = not shares
    marcap = None if share_warn else px['close'] * shares
    ni_now = ni_t[-1]
    per_now = marcap / ni_now if marcap and ni_now and ni_now > 0 else None
    pbr = marcap / eq if marcap and eq and eq > 0 else None
    hist = [] if share_warn else [px['qe'][l] * shares / n for l, n in list(zip(seq, ni_t))[-20:]
                                  if n and n > 0 and px['qe'].get(l)]
    per_med = statistics.median(hist) if len(hist) >= 8 else None
    if (per_now is not None and per_now < 1) or (pbr is not None and pbr < 0.05):
        share_warn = True
        marcap = per_now = pbr = per_med = None
    c = px['close']

    fs, fs_max, fs_items = fscore(ext, seq, ni_t, rev_t)
    gp_t = ttm(seq, gp_quarters(ext), 'gp')

    r = lambda v, k=1: None if v is None else round(v, k)
    tail = seq[-12:]
    return {
        **{k: px.get(k) for k in ('close', 'ma20', 'ma120', 'ma200', 'vol20', 'vol60', 'change_pct', 'price_date', 'hunbot')},
        'ni_ttm': ni_now, 'rev_ttm_value': rev_t[-1], 'prev_rev_ttm': rev_t[-2],
        'op_ttm_value': op_t[-1], 'prev_op_ttm': op_t[-2], 'op_ttm_year_ago': op_t[-5] if len(op_t) >= 5 else None,
        'gp_ttm_value': gp_t[-1], 'gp_ttm_year_ago': gp_t[-5] if len(gp_t) >= 5 else None, 'latest_op': op_q[-1], 'prev_op': op_q[-2],
        'prev2_op': op_q[-3] if len(op_q) >= 3 else None,
        'fscore': fs, 'fscore_max': fs_max, 'fscore_items': fs_items,
        'latest_q': seq[-1], **rec, 'marcap': None if marcap is None else round(marcap), 'share_warn': share_warn,
        'roe3': r(roe3), 'debt': r(debt), 'profit_years': profit_years,
        'per': r(per_now), 'per_med5': r(per_med), 'pbr': r(pbr, 2),
        'above120': px['ma120'] is not None and c >= px['ma120'],
        'above200': px.get('ma200') is not None and c >= px['ma200'],
        'vol_down': px['vol20'] is not None and px['vol60'] is not None and px['vol20'] < px['vol60'],
        'dd52': r((c / px['high52'] - 1) * 100),
        'q': {'labels': tail, 'rev': [qv.get(l, {}).get('rev') for l in tail],
              'op': [qv.get(l, {}).get('op') for l in tail]},
    }
