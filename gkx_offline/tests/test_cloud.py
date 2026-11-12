"""Blob 덮어쓰기 캐시가 있어도 전체 선택 상태가 즉시 보이는지 확인한다."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@unittest.skipIf(sys.version_info < (3, 12), "Vercel Python 3.12 환경에서 실행")
class CloudSelectionTest(unittest.TestCase):
    def test_bulk_selection_reads_the_new_snapshot(self):
        from fastapi.testclient import TestClient
        from api import index

        stored = {}
        written = []

        async def get_blob(pathname, **_kwargs):
            if pathname not in stored:
                raise index.blob.BlobNotFoundError()
            return SimpleNamespace(body=stored[pathname])

        async def put_blob(pathname, body, **_kwargs):
            written.append(pathname)
            stored.setdefault(pathname, body)  # 같은 경로 덮어쓰기는 캐시가 예전 값을 반환
            return SimpleNamespace(pathname=pathname)

        with patch.object(index.blob, "get_blob", get_blob), patch.object(index.blob, "put_blob", put_blob):
            client = TestClient(index.app, base_url="https://testserver")
            target = "GKX-SSS-AOCS-001"
            selected = client.post("/api/index?path=select-bulk",
                                   json={"document": target, "selected": True})
            self.assertEqual(selected.status_code, 200)
            self.assertEqual(selected.json()["updated"], 20)
            candidates = client.get(f"/api/index?path=candidates&document={target}").json()
            self.assertEqual(sum(row["selected"] for row in candidates), 20)

            cleared = client.post("/api/index?path=select-bulk",
                                  json={"document": target, "selected": False})
            self.assertEqual(cleared.status_code, 200)
            candidates = client.get(f"/api/index?path=candidates&document={target}").json()
            self.assertEqual(sum(row["selected"] for row in candidates), 0)
            self.assertEqual(len(set(written)), 2)


if __name__ == "__main__":
    unittest.main()
