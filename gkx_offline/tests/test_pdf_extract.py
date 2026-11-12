"""샘플 파일 없이 동작하는 PDF 텍스트 구조 시험."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_extract import parse_regular


class ParserUnitTest(unittest.TestCase):
    def test_requirement_fields_and_page(self):
        pages = ["표지", """3. 요구사항
3.1 임무 요구사항
요구사항 ID MRD-001
제목 궤도 위치
요구문 위성은 정지궤도에서 운용되어야 한다.
근거 궤도 슬롯 활용
상위 요구사항 —
검증방법 A, D
우선순위 필수
상태 확정
"""]
        items = parse_regular("GKX-MRD-001", pages)
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["id"], items[0]["page"]), ("MRD-001", 2))
        self.assertEqual(items[0]["methods"], ["A", "D"])
        self.assertEqual(items[0]["parents"], [])

    def test_vp_links(self):
        pages = ["""4. 검증 항목
4.1 검증 항목 목록
검증항목 ID VER-AOCS-001
검증 제목 자세 지향 정확도 검증
대상 요구사항 AOCS-001
검증방법 A, T
검증 수준 부체계
검증 내용 정확도를 분석하고 시험한다.
합격 기준 0.025도 이내여야 한다.
관련 시험케이스 TC-AOCS-001
"""]
        item = parse_regular("GKX-VP-AOCS-001", pages)[0]
        self.assertEqual(item["parents"], ["AOCS-001"])
        self.assertEqual(item["test_case"], "TC-AOCS-001")


if __name__ == "__main__":
    unittest.main()
