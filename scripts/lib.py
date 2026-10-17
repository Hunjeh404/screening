"""계산 함수 모음 (네트워크 없음)."""
import datetime as dt
import statistics

ACCT = {
    'rev': ('매출액', '영업수익', '수익(매출액)', '매출'),
    'op': ('영업이익',),
    'ni': ('당기순이익',),
    'assets': ('자산총계',),
    'liab': ('부채총계',),
    'equity': ('자본총계',),
}
IS_KEYS = ('rev', 'op', 'ni')
BS_KEYS = ('assets', 'liab', 'equity')


def acct_key(name):
    n = name.replace(' ', '').replace('(손실)', '')
    for k, names in ACCT.items():
        if n in names:
            return k
    return None


def to_num(s):
    s = (s or '').replace(',', '').strip()
    if s in ('', '-'):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def span_days(dtxt):
    """'2024.07.01 ~ 2024.09.30' -> 91"""
    try:
        a, b = [x.strip() for x in dtxt.split('~')]
        fa = dt.datetime.strptime(a, '%Y.%m.%d')
        fb = dt.datetime.strptime(b, '%Y.%m.%d')
        return (fb - fa).days
    except Exception:
        return None


def parse_multi_acnt(items, q):
    """DART 다중회사 주요계정 응답 -> {corp_code: {key: 값}}.
    손익 항목은 회계연도 누적값으로 맞춘다(2·3분기 3개월값이면 누적값 사용)."""
    out = {}
    for it in items:
        k = acct_key(it.get('account_nm', ''))
        if not k:
            continue
        amt = to_num(it.get('thstrm_amount'))
        if k in IS_KEYS and q in (2, 3):
            d = span_days(it.get('thstrm_dt', ''))
            if d is not None and d < 120:
                amt = to_num(it.get('thstrm_add_amount'))
        if amt is None:
            continue
        fs = it.get('fs_div', 'OFS')
        out.setdefault(it['corp_code'], {}).setdefault(fs, {}).setdefault(k, amt)
    return {c: (d.get('CFS') or d.get('OFS')) for c, d in out.items()}


def label_seq(first, last):
    y, q = int(first[:4]), int(first[-1])
    seq = []
    while True:
        lab = f'{y}Q{q}'
        seq.append(lab)
        if lab == last:
            return seq
        q += 1
        if q == 5:
            y, q = y + 1, 1


def shift_label(lab, n):
    i = int(lab[:4]) * 4 + int(lab[-1]) - 1 + n
    return f'{i // 4}Q{i % 4 + 1}'


def quarterly(per):
    """누적 손익 -> 분기 손익. per: {'2024Q3': {rev, op, ni(누적), assets, liab, equity}}"""
    out = {}
    for y in sorted({int(p[:4]) for p in per}):
        prev = {k: 0 for k in IS_KEYS}
        ok = {k: True for k in IS_KEYS}
        for q in range(1, 5):
            p = per.get(f'{y}Q{q}')
            if not p:
                ok = {k: False for k in IS_KEYS}
                continue
            row = {}
            for k in IS_KEYS:
                c = p.get(k)
                row[k] = c - prev[k] if (c is not None and ok[k]) else None
                ok[k] = c is not None
                if c is not None:
                    prev[k] = c
            for k in BS_KEYS:
                row[k] = p.get(k)
            out[f'{y}Q{q}'] = row
    return out


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


def price_metrics(dates, closes):
    n = len(closes)
    if n < 60:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    ma = lambda w: sum(closes[-w:]) / w if n >= w else None
    sd = lambda w: statistics.pstdev(rets[-w:]) if len(rets) >= w else None
    qe = {}
    for d, c in zip(dates, closes):
        qe[f'{d.year}Q{(d.month - 1) // 3 + 1}'] = c
    keep = sorted(qe)[-25:]
    return {
        'close': closes[-1], 'ma120': ma(120), 'ma240': ma(240),
        'vol20': sd(20), 'vol60': sd(60), 'high52': max(closes[-250:]),
        'qe': {k: qe[k] for k in keep},
    }


def analyze(per, px, shares, marcap):
    """한 종목의 지표 계산. 필터 판정은 웹앱에서 한다."""
    qv = quarterly(per)
    labs = [l for l in sorted(qv) if qv[l].get('rev') is not None or qv[l].get('op') is not None]
    if len(labs) < 5:
        return None
    seq = label_seq(labs[0], labs[-1])
    latest = seq[-1]
    rev_q = [qv.get(l, {}).get('rev') for l in seq]
    op_q = [qv.get(l, {}).get('op') for l in seq]
    rev_t, op_t, ni_t = ttm(seq, qv, 'rev'), ttm(seq, qv, 'op'), ttm(seq, qv, 'ni')

    rec = {
        'rev_q': is_record(rev_q, 8), 'rev_ttm': is_record(rev_t, 4),
        'op_q': is_record(op_q, 8), 'op_ttm': is_record(op_t, 4),
    }

    # 연간 (4분기 누적값 = 연간)
    years = sorted(int(p[:4]) for p in per if p.endswith('Q4'))
    ann = {y: per[f'{y}Q4'] for y in years}
    roes = []
    for y in years[-3:]:
        ni, eq = ann[y].get('ni'), ann[y].get('equity')
        eq0 = ann.get(y - 1, {}).get('equity')
        base = (eq + eq0) / 2 if (eq and eq0) else eq
        if ni is not None and base:
            roes.append(ni / base * 100)
    roe3 = sum(roes) / len(roes) if len(roes) == 3 else None
    ops5 = [ann[y].get('op') for y in years[-5:]]
    nolos5 = len(ops5) == 5 and all(v is not None and v > 0 for v in ops5)

    bs = next((qv[l] for l in reversed(seq) if qv.get(l, {}).get('equity')), {})
    eq, liab = bs.get('equity'), bs.get('liab')
    debt = liab / eq * 100 if (eq and eq > 0 and liab is not None) else None

    ni_now = ni_t[-1]
    per_now = marcap / ni_now if (ni_now and ni_now > 0 and marcap) else None
    pbr = marcap / eq if (eq and eq > 0 and marcap) else None

    hist = []
    if px and shares:
        for lab, n in list(zip(seq, ni_t))[-20:]:
            c = px['qe'].get(lab)
            if n and n > 0 and c:
                hist.append(c * shares / n)
    per_med = statistics.median(hist) if len(hist) >= 8 else None

    graham = None
    if ni_now and ni_now > 0 and eq and eq > 0 and shares and px:
        g = (22.5 * (ni_now / shares) * (eq / shares)) ** 0.5
        graham = (g - px['close']) / g * 100

    chart = {}
    if px:
        c = px['close']
        chart = {
            'above120': px['ma120'] is not None and c > px['ma120'],
            'above240': px['ma240'] is not None and c > px['ma240'],
            'vol_down': (px['vol20'] is not None and px['vol60'] is not None
                         and px['vol20'] < px['vol60']),
            'dd52': (c / px['high52'] - 1) * 100,
        }

    r = lambda v, d=1: None if v is None else round(v, d)
    tail = seq[-12:]
    return {
        'latest_q': latest, **rec,
        'roe3': r(roe3), 'debt': r(debt), 'nolos5': nolos5,
        'per': r(per_now), 'per_med5': r(per_med), 'pbr': r(pbr, 2),
        'graham_disc': r(graham), **{k: (r(v) if k == 'dd52' else v) for k, v in chart.items()},
        'q': {'labels': tail,
              'rev': [qv.get(l, {}).get('rev') for l in tail],
              'op': [qv.get(l, {}).get('op') for l in tail]},
    }
