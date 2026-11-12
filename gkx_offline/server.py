#!/usr/bin/env python3
"""127.0.0.1 전용 UI/API 서버. 외부 서비스 요청을 하지 않는다."""

import argparse
import json
import mimetypes
import os
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import app


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


class Handler(BaseHTTPRequestHandler):
    db_path = app.DEFAULT_DB
    ai_model = "qwen3.5:9b-mlx"
    ai_provider = "ollama"

    def send_bytes(self, payload, status=200, mime="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'self'; frame-src 'self'; connect-src 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, data, status=200):
        self.send_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"), status)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path = parsed.path
        try:
            if path.startswith("/api/"):
                db = app.connect(self.db_path)
                try:
                    if path == "/api/summary":
                        result = app.summary(db)
                    elif path == "/api/documents":
                        rows = db.execute("""SELECT d.*, (SELECT COUNT(*) FROM candidates c
                            WHERE c.doc_no=d.doc_no AND c.selected=1) selected_count,
                            (SELECT COUNT(*) FROM trace_items t WHERE t.doc_no=d.doc_no) saved_count
                            FROM documents d ORDER BY d.doc_no""").fetchall()
                        result = [{"document": r["doc_no"], "title": r["title"], "revision": r["rev"],
                                   "pages": r["pages"], "candidates": r["expected_count"],
                                   "selected": r["selected_count"], "saved": r["saved_count"],
                                   "source": r["source_label"]} for r in rows]
                    elif path == "/api/dashboard":
                        totals = app.summary(db)
                        pages = db.execute("SELECT COALESCE(SUM(pages),0) FROM documents").fetchone()[0]
                        pending = db.execute("""SELECT COUNT(*) FROM candidates
                            WHERE json_extract(COALESCE(edited_json, original_json), '$.status') IN ('TBD','TBR')""").fetchone()[0]
                        recent = db.execute("""SELECT h.review_id, h.decision, h.reviewer, h.created_at,
                            t.doc_no, t.item_id, t.title FROM human_reviews h
                            JOIN trace_items t ON t.trace_id=h.trace_id ORDER BY h.review_id DESC LIMIT 5""").fetchall()
                        result = {"totals": totals, "pages": pages, "tbd_tbr": pending,
                                  "recent_reviews": [dict(r) for r in recent],
                                  "model": self.ai_model, "provider": self.ai_provider}
                    elif path == "/api/candidates":
                        doc_no = params.get("document", [""])[0]
                        rows = db.execute("SELECT * FROM candidates WHERE doc_no=? ORDER BY candidate_id",
                                          (doc_no,)).fetchall()
                        result = [app.candidate_view(db, r) for r in rows]
                    elif path == "/api/trace":
                        result = app.search(db, params.get("query", [""])[0], 1000)
                    elif path == "/api/ai-queue":
                        rows = db.execute("""SELECT a.*, t.doc_no, t.item_id, t.title,
                            (SELECT h.decision FROM human_reviews h WHERE h.trace_id=t.trace_id
                             ORDER BY h.review_id DESC LIMIT 1) latest_decision
                            FROM ai_reviews a JOIN trace_items t ON t.trace_id=a.trace_id
                            ORDER BY a.review_id DESC""").fetchall()
                        result = [{"review_id": r["review_id"], "document": r["doc_no"],
                                   "item_id": r["item_id"], "title": r["title"], "model": r["model"],
                                   "created_at": r["created_at"], "latest_decision": r["latest_decision"],
                                   "opinion": json.loads(r["review_json"])} for r in rows]
                    elif path == "/api/records":
                        rows = db.execute("""SELECT h.*, t.doc_no, t.item_id, t.title, t.page, t.rev
                            FROM human_reviews h JOIN trace_items t ON t.trace_id=h.trace_id
                            ORDER BY h.review_id DESC""").fetchall()
                        result = [dict(r) for r in rows]
                    elif path == "/api/reviews":
                        doc_no = params.get("document", [""])[0]
                        item_id = params.get("item", [""])[0]
                        rows = db.execute("""SELECT h.* FROM human_reviews h JOIN trace_items t
                             ON t.trace_id=h.trace_id WHERE t.doc_no=? AND t.item_id=?
                             ORDER BY h.review_id DESC""", (doc_no, item_id)).fetchall()
                        result = [dict(r) for r in rows]
                    elif path == "/api/ai-reviews":
                        doc_no = params.get("document", [""])[0]
                        item_id = params.get("item", [""])[0]
                        rows = db.execute("""SELECT a.* FROM ai_reviews a JOIN trace_items t
                             ON t.trace_id=a.trace_id WHERE t.doc_no=? AND t.item_id=?
                             ORDER BY a.review_id DESC""", (doc_no, item_id)).fetchall()
                        result = [{"review_id": r["review_id"], "model": r["model"],
                                   "provider": r["provider"], "opinion": json.loads(r["review_json"]),
                                   "created_at": r["created_at"]} for r in rows]
                    elif path == "/api/ai-config":
                        result = {"provider": "OpenAI" if self.ai_provider == "openai" else "Ollama",
                                  "model": self.ai_model,
                                  "endpoint": "api.openai.com" if self.ai_provider == "openai" else "127.0.0.1:11434",
                                  "external_api_enabled": self.ai_provider == "openai",
                                  "ready": self.ai_provider != "openai" or bool(os.environ.get("OPENAI_API_KEY"))}
                    elif path == "/api/pdf":
                        doc_no = params.get("document", [""])[0]
                        row = db.execute("SELECT pdf_path FROM documents WHERE doc_no=?", (doc_no,)).fetchone()
                        if row is None:
                            raise ValueError("문서를 찾을 수 없습니다.")
                        self.send_bytes(Path(row["pdf_path"]).read_bytes(), mime="application/pdf")
                        return
                    else:
                        self.send_json({"error": "경로를 찾을 수 없습니다."}, 404)
                        return
                    self.send_json(result)
                finally:
                    db.close()
                return
            filename = "index.html" if path == "/" else path.lstrip("/")
            candidate = (STATIC / filename).resolve()
            if not candidate.is_relative_to(STATIC) or not candidate.is_file():
                self.send_json({"error": "경로를 찾을 수 없습니다."}, 404)
                return
            self.send_bytes(candidate.read_bytes(), mime=mimetypes.guess_type(candidate)[0] or "application/octet-stream")
        except (ValueError, OSError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_POST(self):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and origin != f"http://{host}":
            self.send_json({"error": "다른 출처의 요청은 허용하지 않습니다."}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            path = urlparse(self.path).path
            limit = 25_000_000 if path == "/api/upload" else 1_000_000
            if length < 1 or length > limit:
                raise ValueError("요청 크기가 잘못되었습니다.")
            if path == "/api/upload":
                filename = self.headers.get("X-Filename", "")
                doc_no = Path(filename).stem
                if not re.fullmatch(r"GKX-(?:MRD|SRS|SSS|ICD|VP|VCRM|TP|TR)(?:-[A-Z]+)?-\d{3}", doc_no) or not filename.endswith(".pdf"):
                    raise ValueError("GEO-KSAT-X 양식의 GKX-...pdf 파일명을 사용하세요.")
                body = self.rfile.read(length)
                if not body.startswith(b"%PDF-"):
                    raise ValueError("PDF 파일 형식이 아닙니다.")
                folder = self.db_path.parent / "uploads"
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{uuid.uuid4().hex}.pdf"
                target.write_bytes(body)
                db = app.connect(self.db_path)
                try:
                    result = app.register_pdf(db, target, doc_no)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                finally:
                    db.close()
                self.send_json(result)
                return
            data = json.loads(self.rfile.read(length))
            db = app.connect(self.db_path)
            try:
                doc_no = data.get("document", "")
                item_id = data.get("item", "")
                if path == "/api/edit":
                    result = app.edit_candidate(db, doc_no, item_id, data.get("field", ""),
                                                str(data.get("value", "")))
                elif path in ("/api/select", "/api/revert"):
                    row = app.get_candidate(db, doc_no, item_id)
                    with db:
                        if path == "/api/select":
                            if data.get("selected") and app.validate(db, doc_no, app.effective(row), row["candidate_id"]):
                                raise ValueError("오류가 있는 후보는 선택할 수 없습니다.")
                            db.execute("UPDATE candidates SET selected=? WHERE candidate_id=?",
                                       (int(bool(data.get("selected"))), row["candidate_id"]))
                        else:
                            db.execute("UPDATE candidates SET edited_json=NULL WHERE candidate_id=?",
                                       (row["candidate_id"],))
                    result = app.candidate_view(db, app.get_candidate(db, doc_no, item_id))
                elif path == "/api/select-bulk":
                    rows = db.execute("SELECT * FROM candidates WHERE doc_no=?", (doc_no,)).fetchall()
                    count = 0
                    with db:
                        for row in rows:
                            if data.get("selected") and app.validate(db, doc_no, app.effective(row), row["candidate_id"]):
                                continue
                            db.execute("UPDATE candidates SET selected=? WHERE candidate_id=?",
                                       (int(bool(data.get("selected"))), row["candidate_id"]))
                            count += 1
                    result = {"updated": count}
                elif path == "/api/save":
                    result = app.save_selected(db, doc_no, data.get("operation_id"))
                elif path == "/api/extract":
                    result = app.extract_document(db, doc_no)
                elif path == "/api/review":
                    result = app.review(db, doc_no, item_id, data.get("reviewer", ""),
                                        data.get("decision", ""), data.get("comment", ""))
                elif path == "/api/ai-review":
                    result = app.ollama_review(db, doc_no, item_id, self.ai_model, provider=self.ai_provider)
                elif path == "/api/ask":
                    result = app.ollama_answer(db, str(data.get("question", "")), self.ai_model,
                                               provider=self.ai_provider)
                else:
                    self.send_json({"error": "경로를 찾을 수 없습니다."}, 404)
                    return
                self.send_json(result)
            finally:
                db.close()
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=app.DEFAULT_DB)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--host", default=os.environ.get("GKX_HOST", "127.0.0.1"))
    parser.add_argument("--provider", choices=("ollama", "openai"),
                        default=os.environ.get("GKX_AI_PROVIDER", "ollama"))
    parser.add_argument("--model", default=os.environ.get("GKX_AI_MODEL"),
                        help="Ollama 또는 OpenAI 모델 ID")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    Handler.db_path = args.db
    Handler.ai_provider = args.provider
    Handler.ai_model = args.model or ("qwen3.5:9b-mlx" if args.provider == "ollama" else "gpt-6-luna")
    db = app.connect(args.db)
    app.schema(db)
    db.close()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"문서 검토 화면: http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
