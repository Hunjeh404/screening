import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from china import us_reason, kr_reason


class ChinaFilter(unittest.TestCase):
    def test_us(self):
        self.assertEqual(us_reason({'addresses': {'business': {'stateOrCountry': 'F4'}}}), '본사 중국')
        self.assertEqual(us_reason({'addresses': {'business': {'stateOrCountry': 'CA'}}, 'stateOfIncorporation': 'K3'}), '설립지 홍콩')
        self.assertIsNone(us_reason({'addresses': {'business': {'stateOrCountry': 'F5'}}, 'stateOfIncorporation': 'E9'}))  # 대만·케이맨은 해당 없음

    def test_kr(self):
        h = lambda *rows: [{'nm': n, 'trmend_posesn_stock_qota_rt': r} for n, r in rows]
        self.assertIsNone(kr_reason({'adres': '서울특별시 강남구'}, h(('홍길동', '30.1'), ('계', '35'))))
        self.assertTrue(kr_reason({'adres': '중국 복건성 진강시'}, []).startswith('주소'))
        self.assertEqual(kr_reason({'adres': '경기도'}, h(('Shanghai Holdings Ltd.', '41.2'), ('김철수', '3'))), '최대주주 Shanghai Holdings Ltd.')
        self.assertIsNone(kr_reason({'adres': '경기도'}, h(('김철수', '30'), ('China Fund', '5'))))   # 1위만 본다


if __name__ == '__main__':
    unittest.main()
