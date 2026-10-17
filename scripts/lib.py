"""계산 함수 모음 (네트워크 없음). 입력은 SEC companyfacts JSON."""
import datetime as dt
import statistics

DUR_TAGS = {
    'rev': ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax',
            'RevenueFromContractWithCustomerIncludingAssessedTax', 'SalesRevenueNet',
            'SalesRevenueGoodsNet', 'SalesRevenueServicesNet', 'RevenuesNetOfInterestExpense'],
    'op': ['OperatingIncomeLoss'],
    'pretax': ['IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
               'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments'],
    'ni': ['NetIncomeLoss', 'ProfitLoss'],
}
INST_TAGS = {
    'equity': ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
    'liab': ['Liabilities'],
    'le': ['LiabilitiesAndStockholdersEquity'],
}
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
        bs[e] = {'equity': eq, 'liab': liab}
    for e in a:
        if e in bs:
            a[e]['equity'] = bs[e]['equity']
    return {'q': q, 'a': a, 'bs': {label(e): v for e, v in sorted(bs.items())}}


def shares_out(facts):
    """최신 공시 기준 발행주식수 (보통주 종류가 여럿이면 같은 공시의 값을 합산)"""
    for ns, tag in (('dei', 'EntityCommonStockSharesOutstanding'), ('us-gaap', 'CommonStockSharesOutstanding')):
        ent = [e for e in facts.get(ns, {}).get(tag, {}).get('units', {}).get('shares', [])]
        if ent:
            last = max(ent, key=lambda e: (e['end'], e['filed']))
            same = {e['val'] for e in ent if e.get('accn') == last.get('accn') and e['end'] == last['end']}
            return sum(same)
    return None


def eps_shares(facts):
    """최근 연간 순이익 / 연간 기본 EPS = 공시 기준 실제 주식 수 (주식 종류별 가치 차이 확인용)"""
    eps = latest_by_period(facts.get('us-gaap', {}).get('EarningsPerShareBasic', {})
                           .get('units', {}).get('USD/shares', []))
    ni = latest_by_period(usd(facts, 'NetIncomeLoss'))
    both = [k for k in eps if k in ni and 350 <= (d(k[1]) - d(k[0])).days <= 380 and eps[k]]
    if not both:
        return None
    k = max(both, key=lambda k: k[1])
    return abs(ni[k] / eps[k])


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
    return {'c': [float(f'{x:.5g}') for x in wc[-keep:]], 'v': [int(x) for x in wv[-keep:]]}


def price_metrics(dates, closes, vols=None):
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
        'close': closes[-1], 'ma120': ma(120), 'ma240': ma(240),
        'vol20': sd(20), 'vol60': sd(60), 'high52': max(closes[-250:]),
        'qe': {k: qe[k] for k in keep},
        'wk': weekly(dates, closes, vols) if vols else None,
    }


def analyze(ext, px, shares, implied=None):
    """한 종목의 지표 계산. 필터 판정은 웹앱에서 한다.
    implied: EPS로 역산한 주식 수. 발행주식수와 크게 다르면 주식 종류별 가치가 달라
    시가총액을 믿을 수 없으므로 밸류에이션 지표를 비운다."""
    qv = ext['q']
    labs = sorted(l for l in qv if qv[l].get('rev') is not None or qv[l].get('op') is not None)
    if len(labs) < 5 or not px or not shares:
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

    share_warn = bool(implied) and not (0.6 <= shares / implied <= 1.67)
    marcap = None if share_warn else px['close'] * shares
    ni_now = ni_t[-1]
    per_now = marcap / ni_now if marcap and ni_now and ni_now > 0 else None
    pbr = marcap / eq if marcap and eq and eq > 0 else None
    hist = [] if share_warn else [px['qe'][l] * shares / n for l, n in list(zip(seq, ni_t))[-20:]
                                  if n and n > 0 and px['qe'].get(l)]
    per_med = statistics.median(hist) if len(hist) >= 8 else None
    graham = None
    if marcap and ni_now and ni_now > 0 and eq and eq > 0:
        g = (22.5 * (ni_now / shares) * (eq / shares)) ** 0.5
        graham = (g - px['close']) / g * 100
    c = px['close']

    r = lambda v, k=1: None if v is None else round(v, k)
    tail = seq[-12:]
    return {
        'latest_q': seq[-1], **rec, 'marcap': None if marcap is None else round(marcap), 'share_warn': share_warn,
        'roe3': r(roe3), 'debt': r(debt), 'profit_years': profit_years,
        'per': r(per_now), 'per_med5': r(per_med), 'pbr': r(pbr, 2), 'graham_disc': r(graham),
        'above120': px['ma120'] is not None and c > px['ma120'],
        'above240': px['ma240'] is not None and c > px['ma240'],
        'vol_down': px['vol20'] is not None and px['vol60'] is not None and px['vol20'] < px['vol60'],
        'dd52': r((c / px['high52'] - 1) * 100),
        'q': {'labels': tail, 'rev': [qv.get(l, {}).get('rev') for l in tail],
              'op': [qv.get(l, {}).get('op') for l in tail]},
    }
