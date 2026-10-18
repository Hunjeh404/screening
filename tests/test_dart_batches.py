import datetime as dt
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dart_batches import fetch_batches


class DartBatchesTest(unittest.TestCase):
    def test_parallel_resume_and_correction_refresh(self):
        today = dt.date(2026, 10, 18)
        codes = [f'{n:08}' for n in range(101)]
        barrier = threading.Barrier(2)
        calls = []

        def request(path, **params):
            batch = params['corp_code'].split(',')
            self.assertLessEqual(len(batch), 100)
            calls.append(batch)
            barrier.wait(timeout=5)  # 직렬 조회로 돌아가면 실패한다.
            if len(batch) == 1:
                raise RuntimeError('temporary failure')
            return {'status': '000', 'list': [
                {'corp_code': cc, 'account_nm': '매출액', 'thstrm_amount': '100'}
                for cc in batch]}

        with tempfile.TemporaryDirectory() as cache:
            with self.assertRaisesRegex(RuntimeError, 'temporary failure'):
                fetch_batches(codes, 2026, 1, '11013', today, cache, request, workers=2)
            self.assertEqual(len(calls), 2)
            calls.clear()

            def retry(path, **params):
                calls.append(params['corp_code'])
                return {'status': '013'}

            result = fetch_batches(codes, 2026, 1, '11013', today, cache, retry)
            self.assertEqual(len(calls), 1)  # 성공한 100개는 재요청하지 않는다.
            self.assertEqual(len(result), 100)
            self.assertEqual(result[codes[0]]['rev'], 100)
            calls.clear()
            fetch_batches(codes, 2026, 1, '11013', today + dt.timedelta(days=1), cache, retry)
            self.assertEqual(len(calls), 2)  # 최근 연도 정정공시 재조회

    def test_api_errors_are_not_cached_as_empty_financials(self):
        with tempfile.TemporaryDirectory() as cache:
            with self.assertRaisesRegex(RuntimeError, '020'):
                fetch_batches(['00000001'], 2020, 1, '11013', dt.date(2026, 10, 18),
                              cache, lambda *a, **k: {'status': '020'})
            self.assertEqual(list(Path(cache).rglob('*.json')), [])


if __name__ == '__main__':
    unittest.main()
