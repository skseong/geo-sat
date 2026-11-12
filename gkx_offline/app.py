#!/usr/bin/env python3
"""GEO-KSAT-X 가상자료를 사용하는 폐쇄망용 SQLite 작업 핵심부."""

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest


HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data" / "gkx.sqlite3"
EDITABLE = {"id", "type", "title", "statement", "rationale", "parents", "methods",
            "priority", "status", "section", "page", "source_doc", "verification_item",
            "test_case", "result"}
ID_PATTERN = re.compile(r"^(?:(?:VER|TC|TR)-)?(?:ICD-)?[A-Z]{2,4}-\d{3}$")


def now():
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def connect(path=DEFAULT_DB):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS documents (
      doc_no TEXT PRIMARY KEY, title TEXT NOT NULL, rev TEXT NOT NULL,
      doc_date TEXT, pages INTEGER NOT NULL, pdf_path TEXT NOT NULL,
      expected_count INTEGER NOT NULL, source_label TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS candidates (
      candidate_id INTEGER PRIMARY KEY, doc_no TEXT NOT NULL REFERENCES documents(doc_no),
      original_id TEXT NOT NULL, original_json TEXT NOT NULL, edited_json TEXT,
      selected INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0, 1)),
      UNIQUE(doc_no, original_id)
    );
    CREATE TABLE IF NOT EXISTS trace_items (
      trace_id INTEGER PRIMARY KEY, doc_no TEXT NOT NULL REFERENCES documents(doc_no),
      rev TEXT NOT NULL, item_type TEXT NOT NULL, item_id TEXT NOT NULL,
      title TEXT NOT NULL, statement TEXT NOT NULL, page INTEGER NOT NULL,
      section TEXT, item_json TEXT NOT NULL, candidate_id INTEGER NOT NULL,
      saved_at TEXT NOT NULL, UNIQUE(doc_no, rev, item_type, item_id)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS trace_fts USING fts5(
      item_id, title, statement, content='trace_items', content_rowid='trace_id'
    );
    CREATE TABLE IF NOT EXISTS save_batches (
      operation_id TEXT PRIMARY KEY, doc_no TEXT NOT NULL, created_at TEXT NOT NULL,
      result_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS human_reviews (
      review_id INTEGER PRIMARY KEY, trace_id INTEGER NOT NULL REFERENCES trace_items(trace_id),
      reviewer TEXT NOT NULL, decision TEXT NOT NULL, comment TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ai_reviews (
      review_id INTEGER PRIMARY KEY, trace_id INTEGER NOT NULL REFERENCES trace_items(trace_id),
      provider TEXT NOT NULL, model TEXT NOT NULL, review_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    """)
    db.commit()


def init_sample(db, sample):
    sample = Path(sample).resolve()
    answer = sample / "answer_key"
    files = sorted(answer.glob("GKX-*.json"))
    if len(files) != 37:
        raise ValueError(f"가상 정답 파일 37개가 필요합니다. 현재 {len(files)}개")
    existing = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    if existing:
        raise ValueError("이미 문서가 있습니다. 초기화가 필요하면 reset-demo를 사용하세요.")
    total = 0
    with db:
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            doc = data["document"]
            pdf = (sample / doc["file"]).resolve()
            if not pdf.is_relative_to(sample) or not pdf.is_file():
                raise ValueError(f"PDF 경로가 잘못되었습니다: {doc['file']}")
            if len(data["items"]) != int(doc["item_count"]):
                raise ValueError(f"항목 수가 맞지 않습니다: {doc['doc_no']}")
            db.execute("""INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (doc["doc_no"], doc["title"], doc["rev"], doc.get("date"),
                        int(doc["pages"]), str(pdf), int(doc["item_count"]), "시연용 가상자료"))
            for item in data["items"]:
                db.execute("""INSERT INTO candidates(doc_no, original_id, original_json)
                              VALUES (?, ?, ?)""",
                           (doc["doc_no"], item["id"], json.dumps(item, ensure_ascii=False)))
                total += 1
    return {"documents": len(files), "candidates": total, "selected": 0, "saved": 0}


def delete_documents(db, doc_no=None, uploads_dir=None):
    """문서와 종속 기록을 삭제하고 직접 업로드한 원본 PDF만 정리한다."""
    if doc_no is None:
        documents = db.execute("SELECT doc_no, pdf_path FROM documents").fetchall()
        condition, params = "1=1", ()
    else:
        documents = db.execute("SELECT doc_no, pdf_path FROM documents WHERE doc_no=?", (doc_no,)).fetchall()
        if not documents:
            raise ValueError("문서를 찾을 수 없습니다.")
        condition, params = "doc_no=?", (doc_no,)
    counts = {
        "deleted_documents": len(documents),
        "deleted_candidates": db.execute(f"SELECT COUNT(*) FROM candidates WHERE {condition}", params).fetchone()[0],
        "deleted_saved": db.execute(f"SELECT COUNT(*) FROM trace_items WHERE {condition}", params).fetchone()[0],
        "deleted_reviews": db.execute(f"""SELECT COUNT(*) FROM human_reviews WHERE trace_id IN
            (SELECT trace_id FROM trace_items WHERE {condition})""", params).fetchone()[0],
        "deleted_ai_reviews": db.execute(f"""SELECT COUNT(*) FROM ai_reviews WHERE trace_id IN
            (SELECT trace_id FROM trace_items WHERE {condition})""", params).fetchone()[0],
    }
    with db:
        for table in ("human_reviews", "ai_reviews"):
            db.execute(f"""DELETE FROM {table} WHERE trace_id IN
                (SELECT trace_id FROM trace_items WHERE {condition})""", params)
        for table in ("save_batches", "trace_items", "candidates", "documents"):
            db.execute(f"DELETE FROM {table} WHERE {condition}", params)
        db.execute("INSERT INTO trace_fts(trace_fts) VALUES ('rebuild')")
    removed = errors = 0
    if uploads_dir is not None:
        upload_root = Path(uploads_dir).resolve()
        for document in documents:
            pdf = Path(document["pdf_path"])
            if pdf.resolve().parent != upload_root or not re.fullmatch(r"[0-9a-f]{32}\.pdf", pdf.name):
                continue
            try:
                if pdf.exists():
                    pdf.unlink()
                    removed += 1
            except OSError:
                errors += 1
    return {**counts, "deleted_uploaded_pdfs": removed, "pdf_cleanup_errors": errors}


def extract_document(db, doc_no):
    """기존 PDF를 다시 읽되 사용자 수정·선택·저장 내역은 보존한다."""
    from pdf_extract import extract
    row = db.execute("SELECT pdf_path FROM documents WHERE doc_no=?", (doc_no,)).fetchone()
    if row is None:
        raise ValueError("문서를 찾을 수 없습니다.")
    parsed = extract(row["pdf_path"], doc_no)
    items = parsed["items"]
    if len({x["id"] for x in items}) != len(items):
        raise ValueError("PDF에서 중복된 항목 ID가 추출되었습니다.")
    updated = added = preserved = 0
    with db:
        for item in items:
            previous = db.execute("SELECT candidate_id, edited_json FROM candidates WHERE doc_no=? AND original_id=?",
                                  (doc_no, item["id"])).fetchone()
            raw = json.dumps(item, ensure_ascii=False)
            if previous:
                db.execute("UPDATE candidates SET original_json=? WHERE candidate_id=?", (raw, previous["candidate_id"]))
                updated += 1
                preserved += int(previous["edited_json"] is not None)
            else:
                db.execute("INSERT INTO candidates(doc_no, original_id, original_json) VALUES (?,?,?)",
                           (doc_no, item["id"], raw))
                added += 1
        db.execute("UPDATE documents SET pages=?, expected_count=?, source_label=? WHERE doc_no=?",
                   (parsed["pages"], len(items), "PDF 직접 추출", doc_no))
    return {"document": doc_no, "extracted": len(items), "updated": updated,
            "added": added, "preserved_edits": preserved}


def register_pdf(db, pdf, doc_no):
    from pdf_extract import extract
    if db.execute("SELECT 1 FROM documents WHERE doc_no=?", (doc_no,)).fetchone():
        raise ValueError("같은 문서번호가 이미 있습니다. 기존 문서의 'PDF 다시 추출'을 사용하세요.")
    parsed = extract(pdf, doc_no)
    if len({x["id"] for x in parsed["items"]}) != len(parsed["items"]):
        raise ValueError("PDF에서 중복된 항목 ID가 추출되었습니다.")
    from pypdf import PdfReader
    reader = PdfReader(str(pdf))
    first = reader.pages[0].extract_text() or ""
    title = re.search(r"^(.+?)\s*$", first, re.M)
    rev = re.search(r"개정\s+Rev\s+([^\s]+)", first)
    date = re.search(r"발행일\s+(\d{4}-\d{2}-\d{2})", first)
    meta_title = (reader.metadata.title if reader.metadata and reader.metadata.title else None)
    display_title = (meta_title or (title.group(1) if title else doc_no)).replace(doc_no, "").strip() or doc_no
    with db:
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                   (doc_no, display_title, rev.group(1) if rev else "-",
                    date.group(1) if date else None, parsed["pages"], str(pdf),
                    len(parsed["items"]), "업로드 PDF 직접 추출"))
        for item in parsed["items"]:
            db.execute("INSERT INTO candidates(doc_no, original_id, original_json) VALUES (?,?,?)",
                       (doc_no, item["id"], json.dumps(item, ensure_ascii=False)))
    return {"document": doc_no, "pages": parsed["pages"], "extracted": len(parsed["items"])}


def effective(row):
    return json.loads(row["edited_json"] or row["original_json"])


def get_candidate(db, doc_no, item_id):
    row = db.execute("SELECT * FROM candidates WHERE doc_no=? AND original_id=?",
                     (doc_no, item_id)).fetchone()
    if row is None:
        raise ValueError("후보를 찾을 수 없습니다.")
    return row


def validate(db, doc_no, item, candidate_id=None):
    errors = []
    doc = db.execute("SELECT pages FROM documents WHERE doc_no=?", (doc_no,)).fetchone()
    if doc is None:
        return ["문서를 찾을 수 없습니다."]
    if not ID_PATTERN.fullmatch(str(item.get("id", ""))):
        errors.append("항목 ID 형식 오류")
    if not str(item.get("type", "")).strip():
        errors.append("항목 유형 필수")
    if not str(item.get("title", "")).strip():
        errors.append("제목 필수")
    if item.get("type") == "VCRM행":
        if not str(item.get("source_doc", "")).strip():
            errors.append("출처 문서 필수")
        if not str(item.get("result", "")).strip():
            errors.append("검증 결과 필수")
    elif not str(item.get("statement", "")).strip():
        errors.append("본문 필수")
    page = item.get("page")
    if not isinstance(page, int) or not 1 <= page <= doc["pages"]:
        errors.append("페이지 범위 오류")
    for parent in item.get("parents", []):
        if not ID_PATTERN.fullmatch(str(parent)):
            errors.append(f"상위 ID 형식 오류: {parent}")
    if not isinstance(item.get("methods", []), list) or any(
            method not in ("I", "A", "D", "T") for method in item.get("methods", [])):
        errors.append("검증방법 형식 오류")
    duplicate = db.execute("SELECT candidate_id FROM candidates WHERE doc_no=? AND candidate_id<>?",
                           (doc_no, candidate_id or -1)).fetchall()
    for other in duplicate:
        row = db.execute("SELECT original_json, edited_json FROM candidates WHERE candidate_id=?",
                         (other[0],)).fetchone()
        other_item = effective(row)
        if (other_item.get("id"), other_item.get("type")) == (item.get("id"), item.get("type")):
            errors.append("같은 문서의 후보 ID 중복")
            break
    return errors


def candidate_view(db, row):
    original = json.loads(row["original_json"])
    item = effective(row)
    return {"candidate_id": row["candidate_id"], "document": row["doc_no"],
            "original_id": row["original_id"], "selected": bool(row["selected"]),
            "modified": item != original, "errors": validate(db, row["doc_no"], item, row["candidate_id"]),
            "original": original, "item": item}


def edit_candidate(db, doc_no, item_id, field, value):
    if field not in EDITABLE:
        raise ValueError("수정할 수 없는 필드입니다.")
    row = get_candidate(db, doc_no, item_id)
    item = effective(row)
    if field == "page":
        try:
            item[field] = int(value)
        except ValueError:
            raise ValueError("페이지는 정수여야 합니다.") from None
    elif field in ("parents", "methods"):
        item[field] = [part.strip() for part in value.split(",") if part.strip()]
    else:
        item[field] = value
    original = json.loads(row["original_json"])
    with db:
        db.execute("UPDATE candidates SET edited_json=? WHERE candidate_id=?",
                   (json.dumps(item, ensure_ascii=False) if item != original else None,
                    row["candidate_id"]))
    return candidate_view(db, get_candidate(db, doc_no, item_id))


def save_selected(db, doc_no, operation_id=None):
    operation_id = operation_id or str(uuid.uuid4())
    with db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT result_json FROM save_batches WHERE operation_id=?",
                              (operation_id,)).fetchone()
        if previous:
            return json.loads(previous[0])
        doc = db.execute("SELECT rev FROM documents WHERE doc_no=?", (doc_no,)).fetchone()
        if doc is None:
            raise ValueError("문서를 찾을 수 없습니다.")
        rows = db.execute("SELECT * FROM candidates WHERE doc_no=? AND selected=1 ORDER BY candidate_id",
                          (doc_no,)).fetchall()
        result = {"operation_id": operation_id, "document": doc_no, "candidate_count": len(rows),
                  "selected_count": len(rows), "saved_count": 0, "duplicate_count": 0,
                  "failed_count": 0, "failures": []}
        for row in rows:
            item = effective(row)
            errors = validate(db, doc_no, item, row["candidate_id"])
            if errors:
                result["failed_count"] += 1
                result["failures"].append({"candidate_id": row["candidate_id"], "errors": errors})
                continue
            try:
                cur = db.execute("""INSERT INTO trace_items
                  (doc_no, rev, item_type, item_id, title, statement, page, section,
                   item_json, candidate_id, saved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (doc_no, doc["rev"], item["type"], item["id"], item["title"],
                   item.get("statement") or item.get("result", ""), item["page"], item.get("section", ""),
                   json.dumps(item, ensure_ascii=False), row["candidate_id"], now()))
            except sqlite3.IntegrityError:
                result["duplicate_count"] += 1
                continue
            db.execute("INSERT INTO trace_fts(rowid, item_id, title, statement) VALUES (?, ?, ?, ?)",
                       (cur.lastrowid, item["id"], item["title"],
                        item.get("statement") or item.get("result", "")))
            result["saved_count"] += 1
        db.execute("INSERT INTO save_batches VALUES (?, ?, ?, ?)",
                   (operation_id, doc_no, now(), json.dumps(result, ensure_ascii=False)))
    return result


def search(db, query, limit=50, doc_no=None):
    query = query.strip()
    conditions = []
    values = []
    if doc_no is not None:
        conditions.append("t.doc_no=?")
        values.append(doc_no)
    if query:
        phrase = '"' + query.replace('"', '""') + '"'
        conditions.append("(t.trace_id IN (SELECT rowid FROM trace_fts WHERE trace_fts MATCH ?) OR t.item_id LIKE ?)")
        values.extend((phrase, f"%{query}%"))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = db.execute(f"SELECT t.* FROM trace_items t{where} ORDER BY t.trace_id LIMIT ?",
                      (*values, limit)).fetchall()
    return [{"document": row["doc_no"], "revision": row["rev"],
             "item": json.loads(row["item_json"]), "saved_at": row["saved_at"]} for row in rows]


def review(db, doc_no, item_id, reviewer, decision, comment):
    if decision not in ("채택", "수정", "기각", "확인필요"):
        raise ValueError("판정은 채택, 수정, 기각, 확인필요 중 하나여야 합니다.")
    if not reviewer.strip() or not comment.strip():
        raise ValueError("담당자와 의견은 필수입니다.")
    row = db.execute("SELECT trace_id FROM trace_items WHERE doc_no=? AND item_id=?",
                     (doc_no, item_id)).fetchone()
    if row is None:
        raise ValueError("저장된 항목을 찾을 수 없습니다.")
    with db:
        cur = db.execute("""INSERT INTO human_reviews
            (trace_id, reviewer, decision, comment, created_at) VALUES (?, ?, ?, ?, ?)""",
            (row["trace_id"], reviewer.strip(), decision, comment.strip(), now()))
    return {"review_id": cur.lastrowid, "document": doc_no, "item_id": item_id,
            "reviewer": reviewer.strip(), "decision": decision, "comment": comment.strip()}


def ai_json(system, content, model, provider="ollama", opener=None, token=None):
    if provider in ("openai", "gateway"):
        api_key = (token or os.environ.get("AI_GATEWAY_API_KEY", "")) if provider == "gateway" else os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("AI Gateway 인증 토큰이 없습니다." if provider == "gateway" else "OPENAI_API_KEY 환경변수가 필요합니다.")
        payload = {"model": model, "store": False, "instructions": system,
                   "input": [{"role": "user", "content": "JSON 형식으로만 답하세요.\n" + content}],
                   "text": {"format": {"type": "json_object"}}}
        if model in ("gpt-6-luna", "openai/gpt-6-luna"):
            payload["reasoning"] = {"effort": "none"}
        url = "https://ai-gateway.vercel.sh/v1/responses" if provider == "gateway" else "https://api.openai.com/v1/responses"
        req = urlrequest.Request(url,
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                                 method="POST")
    elif provider == "ollama":
        payload = {"model": model, "stream": False, "think": False, "format": "json",
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": content}]}
        req = urlrequest.Request("http://127.0.0.1:11434/api/chat",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    else:
        raise ValueError("지원하지 않는 AI 제공자입니다.")
    send = opener or urlrequest.build_opener(urlrequest.ProxyHandler({})).open
    try:
        with send(req, timeout=120) as response:
            raw = json.loads(response.read(1_000_000))
    except urlerror.HTTPError as exc:
        try:
            problem = json.loads(exc.read(4096))
            detail = problem.get("error", problem)
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("code") or exc.reason
        except (ValueError, UnicodeDecodeError):
            detail = exc.reason
        raise ValueError(f"{provider} 오류 ({exc.code}): {str(detail)[:300]}") from exc
    except (urlerror.URLError, TimeoutError) as exc:
        raise ValueError(f"{provider} 연결에 실패했습니다: {getattr(exc, 'reason', exc)}") from exc
    try:
        if provider == "ollama":
            content = raw["message"]["content"]
        else:
            content = "".join(part["text"] for output in raw["output"]
                              if output.get("type") == "message" for part in output.get("content", [])
                              if part.get("type") == "output_text")
        return json.loads(content)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{provider} 응답을 JSON으로 읽을 수 없습니다.") from exc


def ollama_review(db, doc_no, item_id, model, opener=None, provider="ollama", token=None):
    """저장된 한 항목의 최소 문맥만 127.0.0.1 Ollama에 전송한다."""
    if not model or len(model) > 100 or any(c.isspace() for c in model):
        raise ValueError("Ollama 모델 ID가 잘못되었습니다.")
    row = db.execute("SELECT * FROM trace_items WHERE doc_no=? AND item_id=?",
                     (doc_no, item_id)).fetchone()
    if row is None:
        raise ValueError("저장된 항목만 AI 의견을 생성할 수 있습니다.")
    item = json.loads(row["item_json"])
    parent_ids = item.get("parents") or []
    related = []
    for parent_id in parent_ids[:5]:
        parent = db.execute("SELECT * FROM trace_items WHERE item_id=? LIMIT 1", (parent_id,)).fetchone()
        if parent:
            related.append({"document_id": parent["doc_no"], "revision": parent["rev"],
                            "item_id": parent["item_id"], "page": parent["page"],
                            "statement": parent["statement"]})
    context = {"document_id": row["doc_no"], "revision": row["rev"], "item_id": item["id"],
               "type": item["type"], "page": item["page"], "section": item.get("section", ""),
               "statement": item.get("statement") or item.get("result", ""),
               "parent_ids": parent_ids, "methods": item.get("methods", []),
               "related_saved_parents": related}
    system = ("당신은 시스템공학 추적성 검토 보조자입니다. 제공된 한 항목과 저장된 연결 항목만 검토하세요. "
              "현재 자료에서 찾지 못한 사실과 실제 연결 부재를 구분하세요. 최상위 요구사항과 최하위 요구사항은 "
              "상위/하위 ID가 없다는 이유만으로 오류라고 판단하지 마세요. 검증 자료는 하위 요구사항과 구분하세요. "
              "제공되지 않은 ID·문서·쪽수·인용문을 만들지 마세요. 담당자 판정은 항상 pending입니다. "
              "JSON만 반환하며 필드는 review_type, status, summary, evidence, interpretations, risk, "
              "questions_for_reviewer, confidence, human_decision입니다. "
              "review_type은 missing_parent|missing_child|unresolved_reference|link_inconsistency|"
              "verification_link|valid_root_or_leaf 중 하나, status는 candidate|no_issue|insufficient_evidence 중 하나입니다. "
              "근거는 제공된 문서 ID, 개정, 절, 쪽수만 사용하세요. 부족하면 evidence는 빈 배열로 두세요.")
    opinion = ai_json(system, json.dumps(context, ensure_ascii=False), model, provider, opener, token)
    if not isinstance(opinion, dict):
        raise ValueError("Ollama 응답이 의견 객체가 아닙니다.")
    allowed_type = {"missing_parent", "missing_child", "unresolved_reference", "link_inconsistency",
                    "verification_link", "valid_root_or_leaf"}
    if opinion.get("review_type") not in allowed_type or opinion.get("status") not in {
            "candidate", "no_issue", "insufficient_evidence"}:
        raise ValueError("Ollama 의견의 유형 또는 상태가 유효하지 않습니다.")
    allowed_evidence = {(context["document_id"], context["revision"], context["page"])}
    allowed_evidence.update((x["document_id"], x["revision"], x["page"]) for x in related)
    evidence = opinion.get("evidence", [])
    if not isinstance(evidence, list) or any(
            (x.get("document_id"), x.get("revision"), x.get("page")) not in allowed_evidence
            for x in evidence if isinstance(x, dict)) or any(not isinstance(x, dict) for x in evidence):
        raise ValueError("Ollama 의견의 근거가 제공한 자료 범위를 벗어났습니다.")
    source_text = {(context["document_id"], context["revision"], context["page"]):
                   f"{context['item_id']} {context['statement']}"}
    source_text.update({(x["document_id"], x["revision"], x["page"]):
                        f"{x['item_id']} {x['statement']}" for x in related})
    supported = [x for x in evidence if isinstance(x.get("quoted_id_or_fact"), str) and
                 x["quoted_id_or_fact"].strip() and x["quoted_id_or_fact"].strip() in
                 source_text[(x["document_id"], x["revision"], x["page"])]]
    opinion["evidence"] = supported
    if opinion["status"] == "no_issue" and not supported:
        opinion["status"] = "insufficient_evidence"
        opinion["summary"] = "제공된 원문 근거만으로 정상 여부를 확정할 수 없습니다. 담당자 확인이 필요합니다."
    opinion["human_decision"] = "pending"
    opinion["item_id"] = item["id"]
    with db:
        cur = db.execute("""INSERT INTO ai_reviews
          (trace_id, provider, model, review_json, created_at) VALUES (?, ?, ?, ?, ?)""",
          (row["trace_id"], {"openai": "OpenAI", "gateway": "Vercel AI Gateway"}.get(provider, "Ollama"), model,
           json.dumps(opinion, ensure_ascii=False), now()))
    return {"review_id": cur.lastrowid, "provider": {"openai": "OpenAI", "gateway": "Vercel AI Gateway"}.get(provider, "Ollama"), "model": model,
            "opinion": opinion}


def question_evidence(db, question, limit=8):
    """문서명을 지정하지 않아도 등록된 전체 후보에서 근거를 찾는다."""
    words = [x.lower() for x in re.findall(r"[A-Za-z]{2,4}-\d{3}|[가-힣A-Za-z0-9.]+", question)]
    words = [x for x in words if len(x) >= 2 and x not in
             {"요구사항", "규격", "문서", "질문", "알려줘", "무엇", "무엇인가", "어떤", "대한", "관련", "전체"}]
    ranked = []
    rows = db.execute("""SELECT c.*, d.title AS document_title, d.rev, d.doc_no,
        EXISTS(SELECT 1 FROM trace_items t WHERE t.candidate_id=c.candidate_id) AS saved
        FROM candidates c JOIN documents d ON d.doc_no=c.doc_no ORDER BY c.candidate_id""")
    for row in rows:
        item = effective(row)
        item_id = str(item.get("id", ""))
        title = str(item.get("title", ""))
        statement = str(item.get("statement") or item.get("result") or "")
        document = f"{row['doc_no']} {row['document_title']}".lower()
        score = sum(20 if word == item_id.lower() else
                    5 if word in title.lower() else
                    3 if word in statement.lower() else
                    2 if word in document else 0 for word in words)
        if score:
            ranked.append((score, row, item, item_id, title, statement))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["candidate_id"]))
    return [{"document_id": row["doc_no"], "document_title": row["document_title"],
             "revision": row["rev"], "item_id": item_id, "title": title,
             "statement": statement, "section": item.get("section"),
             "page": item.get("page"), "source_state": "저장" if row["saved"] else "후보"}
            for _, row, item, item_id, title, statement in ranked[:limit]]


def ollama_answer(db, question, model, opener=None, provider="ollama", token=None):
    if not question.strip() or len(question) > 1000:
        raise ValueError("질문은 1~1000자로 입력하세요.")
    if not model or len(model) > 100 or any(c.isspace() for c in model):
        raise ValueError("Ollama 모델 ID가 잘못되었습니다.")
    evidence = question_evidence(db, question)
    if not evidence:
        return {"answer": "등록된 문서와 규격 항목에서 관련 근거를 찾지 못했습니다. 질문의 핵심 용어를 바꿔 다시 검색해 주세요.",
                "status": "insufficient_evidence", "citations": [], "evidence": [], "model": model}
    system = ("제공된 등록 문서의 항목만으로 한국어로 답하세요. 후보 상태의 항목은 검토 전 자료임을 밝혀 주세요. 답을 뒷받침하지 못하면 확인 필요라고 쓰세요. "
              "JSON 객체만 반환하세요: answer(문자열), status(answered|insufficient_evidence), "
              "citations(배열: document_id, revision, item_id, page). 제공되지 않은 인용은 만들지 마세요.")
    try:
        result = ai_json(system, json.dumps({"question": question, "evidence": evidence}, ensure_ascii=False),
                         model, provider, opener, token)
    except ValueError as exc:
        if provider == "ollama":
            raise
        first = evidence[0]
        return {"answer": f"AI 답변을 생성할 수 없어 검색된 근거를 표시합니다. {first['item_id']}: {first['statement']}",
                "status": "source_only", "citations": [], "evidence": evidence,
                "model": model, "ai_error": str(exc)}
    if not isinstance(result, dict) or result.get("status") not in {"answered", "insufficient_evidence"}:
        raise ValueError("Ollama 답변 형식이 유효하지 않습니다.")
    if not isinstance(result.get("answer"), str) or not result["answer"].strip():
        raise ValueError("Ollama 답변 본문이 없습니다.")
    allowed = {(x["document_id"], x["revision"], x["item_id"], x["page"]) for x in evidence}
    citations = result.get("citations", [])
    if not isinstance(citations, list) or any(not isinstance(x, dict) or
            (x.get("document_id"), x.get("revision"), x.get("item_id"), x.get("page")) not in allowed
            for x in citations):
        raise ValueError("Ollama 인용이 검색 근거 범위를 벗어났습니다.")
    return {"answer": result["answer"], "status": result["status"],
            "citations": citations, "evidence": evidence, "model": model}


def summary(db):
    counts = {}
    for name, table in (("documents", "documents"), ("candidates", "candidates"),
                        ("saved", "trace_items"), ("reviews", "human_reviews")):
        counts[name] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    counts["selected"] = db.execute("SELECT COUNT(*) FROM candidates WHERE selected=1").fetchone()[0]
    counts["modified"] = db.execute("SELECT COUNT(*) FROM candidates WHERE edited_json IS NOT NULL").fetchone()[0]
    counts["ai_reviews"] = db.execute("SELECT COUNT(*) FROM ai_reviews").fetchone()[0]
    return counts


def pdf_info(db, doc_no):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF 확인에는 pypdf가 필요합니다.") from exc
    row = db.execute("SELECT pdf_path, pages FROM documents WHERE doc_no=?", (doc_no,)).fetchone()
    if row is None:
        raise ValueError("문서를 찾을 수 없습니다.")
    reader = PdfReader(row["pdf_path"])
    if reader.is_encrypted:
        return {"document": doc_no, "encrypted": True, "pages": None, "password_stored": False}
    return {"document": doc_no, "encrypted": False, "pages": len(reader.pages),
            "expected_pages": row["pages"], "password_stored": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 파일 경로")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "reset-demo"):
        p = sub.add_parser(name)
        p.add_argument("--sample", required=True, type=Path)
    sub.add_parser("summary")
    p = sub.add_parser("candidates")
    p.add_argument("--document", required=True)
    p.add_argument("--limit", type=int, default=20)
    for name in ("compare", "revert", "select", "unselect"):
        p = sub.add_parser(name)
        p.add_argument("--document", required=True)
        p.add_argument("--item", required=True)
    p = sub.add_parser("edit")
    p.add_argument("--document", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)
    p = sub.add_parser("save")
    p.add_argument("--document", required=True)
    p.add_argument("--operation-id")
    p = sub.add_parser("search")
    p.add_argument("--query", default="")
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("review")
    p.add_argument("--document", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--decision", required=True)
    p.add_argument("--comment", required=True)
    p = sub.add_parser("pdf-info")
    p.add_argument("--document", required=True)
    args = parser.parse_args(argv)
    if args.command in ("init", "reset-demo"):
        args.db.parent.mkdir(parents=True, exist_ok=True)
    if args.command != "init" and not args.db.exists():
        raise ValueError("DB가 없습니다. 먼저 init을 실행하세요.")
    db = connect(args.db)
    try:
        schema(db)
        if args.command == "init":
            schema(db)
            result = init_sample(db, args.sample)
        elif args.command == "reset-demo":
            if input("모든 시연 수정·선택·검토 기록을 삭제합니다. '초기화'를 입력하세요: ") != "초기화":
                raise ValueError("초기화를 취소했습니다.")
            db.close()
            args.db.unlink()
            db = connect(args.db)
            schema(db)
            result = init_sample(db, args.sample)
        elif args.command == "summary":
            result = summary(db)
        elif args.command == "candidates":
            rows = db.execute("SELECT * FROM candidates WHERE doc_no=? ORDER BY candidate_id LIMIT ?",
                              (args.document, max(0, args.limit))).fetchall()
            result = [candidate_view(db, row) for row in rows]
        elif args.command == "compare":
            row = get_candidate(db, args.document, args.item)
            result = {"original": json.loads(row["original_json"]), "current": effective(row),
                      "selected": bool(row["selected"])}
        elif args.command == "edit":
            result = edit_candidate(db, args.document, args.item, args.field, args.value)
        elif args.command in ("revert", "select", "unselect"):
            row = get_candidate(db, args.document, args.item)
            with db:
                if args.command == "revert":
                    db.execute("UPDATE candidates SET edited_json=NULL WHERE candidate_id=?", (row["candidate_id"],))
                else:
                    db.execute("UPDATE candidates SET selected=? WHERE candidate_id=?",
                               (int(args.command == "select"), row["candidate_id"]))
            result = candidate_view(db, get_candidate(db, args.document, args.item))
        elif args.command == "save":
            result = save_selected(db, args.document, args.operation_id)
        elif args.command == "search":
            result = search(db, args.query, max(0, args.limit))
        elif args.command == "review":
            result = review(db, args.document, args.item, args.reviewer, args.decision, args.comment)
        elif args.command == "pdf-info":
            result = pdf_info(db, args.document)
        dump(result)
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)
