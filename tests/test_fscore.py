"""F-스코어 재료(누적 현금흐름 분기화, 1년 전 주식 수)와 점수 계산 검증."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib import ytd_quarters, yoy_pair, fscore, label_seq, ttm
from lib_kr import parse_multi_acnt, to_ext


class FscoreTest(unittest.TestCase):
    def test_ytd_cash_flow_to_quarters(self):
        per = {('2025-01-01', '2025-03-31'): 10, ('2025-01-01', '2025-06-30'): 25,
               ('2025-01-01', '2025-09-30'): 45, ('2025-01-01', '2025-12-31'): 70,
               ('2026-01-01', '2026-06-30'): 30}  # 1분기 누적이 빠진 해는 2분기를 만들지 않는다
        q = ytd_quarters(per)
        self.assertEqual([q[e] for e in ('2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31')], [10, 15, 20, 25])
        self.assertNotIn('2026-06-30', q)

    def test_shares_year_over_year(self):
        per = {('2025-04-01', '2025-06-30'): 100, ('2026-04-01', '2026-06-30'): 98,
               ('2025-01-01', '2025-12-31'): 99}
        self.assertEqual(yoy_pair(per), {'now': 98, 'prev': 100, 'end': '2026-06-30'})

    def ext(self, full, good):
        """good=True면 모든 항목 충족, False면 모든 항목 불충족"""
        labs = label_seq('2024Q1', '2026Q2')
        q, bs, fq = {}, {}, {}
        for i, l in enumerate(labs):
            q[l] = {'rev': 100 + (10 * i if good else -i), 'op': -5, 'ni': (i - 3) if good else -1}
            bs[l] = {'equity': 50, 'assets': 100, 'ca': 40 + (i if good else -i), 'cl': 20}
            noncurrent = 30 - i if good else 30 + i
            if full:
                bs[l]['liab'] = 20 + noncurrent
                fq[l] = {'cfo': 10 if good else -10, 'gp': q[l]['rev'] * (0.3 + 0.01 * i if good else 0.3 - 0.01 * i)}
            else:
                bs[l].update(liab=20 + noncurrent, ncl=noncurrent, cap=10 if good else 10 + i)
        ext = {'q': q, 'a': {}, 'bs': bs}
        if full:
            ext.update(fq=fq, wsh={'now': 100 if good else 120, 'prev': 100, 'end': '2026-06-30'})
        return ext, labs

    def score(self, ext, labs):
        return fscore(ext, labs, ttm(labs, ext['q'], 'ni'), ttm(labs, ext['q'], 'rev'))

    def test_score_us_and_kr(self):
        for full, maximum in ((True, 9), (False, 6)):
            for good, expected in ((True, maximum), (False, 0)):
                with self.subTest(full=full, good=good):
                    score, m, items = self.score(*self.ext(full, good))
                    self.assertEqual(m, maximum)
                    self.assertEqual(score, expected, items)

    def test_missing_items_score_zero(self):
        ext, labs = self.ext(False, True)
        for l in labs:
            ext['bs'][l]['ca'] = None
        score, m, items = self.score(ext, labs)
        self.assertIsNone(items['유동비율 개선'])
        self.assertEqual(score, 5)

    def test_dart_balance_accounts(self):
        rows = [{'corp_code': 'X', 'fs_div': 'CFS', 'account_nm': n, 'thstrm_amount': v}
                for n, v in (('자산총계', '1,000'), ('유동자산', '400'), ('유동부채', '200'),
                             ('비유동부채', '300'), ('자본금', '50'), ('자본총계', '500'), ('부채총계', '500'))]
        b = to_ext({'2026Q2': parse_multi_acnt(rows, 2)['X']})['bs']['2026Q2']
        self.assertEqual(b, {'equity': 500, 'liab': 500, 'assets': 1000, 'ca': 400, 'cl': 200, 'ncl': 300, 'cap': 50})


if __name__ == '__main__':
    unittest.main()
