"""가상자료의 저장 경계와 원본 보존을 검증한다."""

import tempfile
import unittest
import json
import io
import os
from unittest.mock import patch
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from pdf_extract import extract


SAMPLE = Path(__file__).resolve().parents[2] / "GEO-KSAT-X"


class OfflineWorkflowTest(unittest.TestCase):
    def setUp(self):
        if not (SAMPLE / "answer_key").is_dir():
            self.skipTest("선택형 GEO-KSAT-X 샘플 PDF가 설치되지 않았습니다.")
        self.tmp = tempfile.TemporaryDirectory()
        self.db = app.connect(Path(self.tmp.name) / "test.sqlite3")
        app.schema(self.db)
        self.loaded = app.init_sample(self.db, SAMPLE)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_fixture_and_no_automatic_save(self):
        self.assertEqual(self.loaded, {"documents": 37, "candidates": 914, "selected": 0, "saved": 0})
        self.assertEqual(app.save_selected(self.db, "GKX-MRD-001")["saved_count"], 0)
        self.assertEqual(app.search(self.db, "MRD-001"), [])

    def test_edit_selection_save_duplicate_and_review(self):
        doc, item = "GKX-MRD-001", "MRD-001"
        original = app.get_candidate(self.db, doc, item)
        before = app.effective(original)["statement"]
        app.edit_candidate(self.db, doc, item, "statement", "검토한 가상 요구문")
        self.assertEqual(app.save_selected(self.db, doc)["saved_count"], 0)
        self.assertEqual(app.search(self.db, "검토한"), [])
        row = app.get_candidate(self.db, doc, item)
        self.assertEqual(json.loads(row["original_json"])["statement"], before)
        self.db.execute("UPDATE candidates SET selected=1 WHERE candidate_id=?", (row["candidate_id"],))
        self.db.commit()
        operation = "test-operation"
        first = app.save_selected(self.db, doc, operation)
        self.assertEqual(first["saved_count"], 1)
        self.assertEqual(app.save_selected(self.db, doc, operation), first)
        self.assertEqual(app.save_selected(self.db, doc)["duplicate_count"], 1)
        self.assertEqual(app.search(self.db, "검토한")[0]["item"]["statement"], "검토한 가상 요구문")
        review = app.review(self.db, doc, item, "담당자", "채택", "원문 대조 완료")
        self.assertEqual(review["decision"], "채택")

    def test_invalid_edit_is_not_saved(self):
        doc, item = "GKX-MRD-001", "MRD-002"
        row = app.get_candidate(self.db, doc, item)
        app.edit_candidate(self.db, doc, item, "page", "999")
        self.db.execute("UPDATE candidates SET selected=1 WHERE candidate_id=?", (row["candidate_id"],))
        self.db.commit()
        result = app.save_selected(self.db, doc)
        self.assertEqual((result["saved_count"], result["failed_count"]), (0, 1))
        self.assertIn("페이지 범위 오류", result["failures"][0]["errors"])
        self.assertEqual(app.search(self.db, "MRD-002"), [])

    def test_revert_keeps_selection_separate(self):
        doc, item = "GKX-MRD-001", "MRD-003"
        row = app.get_candidate(self.db, doc, item)
        app.edit_candidate(self.db, doc, item, "title", "임시 제목")
        self.db.execute("UPDATE candidates SET selected=1 WHERE candidate_id=?", (row["candidate_id"],))
        self.db.execute("UPDATE candidates SET edited_json=NULL WHERE candidate_id=?", (row["candidate_id"],))
        self.db.commit()
        current = app.get_candidate(self.db, doc, item)
        self.assertTrue(current["selected"])
        self.assertIsNone(current["edited_json"])

    def test_ollama_review_stays_draft_and_checks_evidence(self):
        doc, item = "GKX-MRD-001", "MRD-001"
        row = app.get_candidate(self.db, doc, item)
        self.db.execute("UPDATE candidates SET selected=1 WHERE candidate_id=?", (row["candidate_id"],))
        self.db.commit()
        app.save_selected(self.db, doc)
        opinion = {"review_type": "valid_root_or_leaf", "status": "insufficient_evidence",
                   "summary": "최상위 출처를 확인해야 합니다.", "evidence": [],
                   "questions_for_reviewer": ["미션 출처는 무엇입니까?"],
                   "human_decision": "approved"}
        def fake_opener(request, timeout):
            self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
            self.assertEqual(timeout, 120)
            self.assertIs(json.loads(request.data)["think"], False)
            return io.BytesIO(json.dumps({"message": {"content": json.dumps(opinion)}}).encode())
        result = app.ollama_review(self.db, doc, item, "qwen3.5:9b-mlx", fake_opener)
        self.assertEqual(result["opinion"]["human_decision"], "pending")
        self.assertEqual(app.summary(self.db)["ai_reviews"], 1)
        opinion["evidence"] = [{"document_id": "없는문서", "revision": "A", "page": 1}]
        with self.assertRaisesRegex(ValueError, "근거"):
            app.ollama_review(self.db, doc, item, "qwen3.5:9b-mlx", fake_opener)
        opinion["evidence"] = []
        opinion["status"] = "no_issue"
        cautious = app.ollama_review(self.db, doc, item, "qwen3.5:9b-mlx", fake_opener)
        self.assertEqual(cautious["opinion"]["status"], "insufficient_evidence")

    def test_question_uses_only_saved_evidence(self):
        question = "MRD-001 요구사항은?"
        before = app.ollama_answer(self.db, question, "qwen3.5:9b-mlx")
        self.assertEqual(before["status"], "insufficient_evidence")
        self.assertEqual(before["evidence"], [])
        row = app.get_candidate(self.db, "GKX-MRD-001", "MRD-001")
        self.db.execute("UPDATE candidates SET selected=1 WHERE candidate_id=?", (row["candidate_id"],))
        self.db.commit()
        app.save_selected(self.db, "GKX-MRD-001")
        def fake_opener(request, timeout):
            self.assertIs(json.loads(request.data)["think"], False)
            answer = {"answer": "궤도 위치에 대한 요구사항입니다.", "status": "answered",
                      "citations": [{"document_id": "GKX-MRD-001", "revision": "B",
                                     "item_id": "MRD-001", "page": 6}]}
            return io.BytesIO(json.dumps({"message": {"content": json.dumps(answer)}}).encode())
        result = app.ollama_answer(self.db, question, "qwen3.5:9b-mlx", fake_opener)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["citations"][0]["item_id"], "MRD-001")

    def test_pdf_extraction_matches_all_sample_ids_and_pages(self):
        for path in sorted((SAMPLE / "answer_key").glob("GKX-*.json")):
            expected = json.loads(path.read_text(encoding="utf-8"))
            parsed = extract(SAMPLE / expected["document"]["file"])
            self.assertEqual(len(parsed["items"]), len(expected["items"]), path.name)
            self.assertEqual({(x["id"], x["page"]) for x in parsed["items"]},
                             {(x["id"], x["page"]) for x in expected["items"]}, path.name)
            self.assertTrue(all(x["title"] for x in parsed["items"]), path.name)

    def test_reextract_preserves_user_work(self):
        doc = "GKX-MRD-001"
        app.edit_candidate(self.db, doc, "MRD-001", "title", "검토 제목")
        row = app.get_candidate(self.db, doc, "MRD-001")
        self.db.execute("UPDATE candidates SET selected=1 WHERE candidate_id=?", (row["candidate_id"],))
        self.db.commit()
        app.save_selected(self.db, doc)
        result = app.extract_document(self.db, doc)
        self.assertEqual((result["extracted"], result["preserved_edits"]), (15, 1))
        self.assertEqual(app.effective(app.get_candidate(self.db, doc, "MRD-001"))["title"], "검토 제목")
        self.assertEqual(app.summary(self.db)["saved"], 1)

    def test_openai_provider_uses_server_key_and_stateless_json(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            def fake_opener(request, timeout):
                self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
                self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
                payload = json.loads(request.data)
                self.assertIs(payload["store"], False)
                self.assertEqual(payload["text"]["format"]["type"], "json_object")
                return io.BytesIO(json.dumps({"output": [{"type": "message", "content": [
                    {"type": "output_text", "text": '{"ok": true}'}]}]}).encode())
            self.assertEqual(app.ai_json("JSON으로 답하세요.", "질문", "gpt-4.1-mini", "openai", fake_opener),
                             {"ok": True})


if __name__ == "__main__":
    unittest.main()
