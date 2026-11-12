"""Vercel 가상자료 시연용 API. 브라우저 세션마다 SQLite 스냅샷을 Blob에 저장한다."""

import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import Response
from vercel import blob

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app as core
from server import Handler

SAMPLE = ROOT / "demo_sample"
PDF_PATHS = {p.stem: p for p in SAMPLE.rglob("GKX-*.pdf")}
SESSION_COOKIE = "gkx_demo_session"
VERSION_COOKIE = "gkx_demo_version"
app = FastAPI()


def prepare_database(path, snapshot):
    if snapshot is None:
        db = core.connect(path)
        try:
            core.schema(db)
            core.init_sample(db, SAMPLE)
        finally:
            db.close()
    else:
        path.write_bytes(snapshot)
    db = core.connect(path)
    try:
        with db:
            for doc_no, pdf in PDF_PATHS.items():
                db.execute("UPDATE documents SET pdf_path=? WHERE doc_no=?", (str(pdf), doc_no))
    finally:
        db.close()


def run_handler(request, path, body, db_path):
    handler = Handler.__new__(Handler)
    handler.path = path
    handler.headers = request.headers
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.db_path = db_path
    handler.ai_provider = "openai" if os.environ.get("OPENAI_API_KEY") else "gateway"
    handler.ai_model = "gpt-6-luna" if handler.ai_provider == "openai" else "openai/gpt-6-luna"
    handler.ai_token = request.headers.get("x-vercel-oidc-token")
    handler.cloud_demo = True
    handler.status_code = 200
    handler.response_headers = {}
    handler.send_response = lambda status, message=None: setattr(handler, "status_code", status)
    handler.send_header = lambda key, value: handler.response_headers.__setitem__(key, value)
    handler.end_headers = lambda: None
    if request.method == "POST":
        handler.do_POST()
    else:
        handler.do_GET()
    return Response(content=handler.wfile.getvalue(), status_code=handler.status_code,
                    headers=handler.response_headers)


@app.api_route("/api/index", methods=["GET", "POST"])
@app.api_route("/api/{route:path}", methods=["GET", "POST"])
async def api_request(request: Request, route: str = ""):
    route = request.query_params.get("path", route)
    if not re.fullmatch(r"[a-z-]+", route):
        return Response(content=b'{"error":"Invalid route"}', status_code=404,
                        media_type="application/json")
    if request.method == "POST" and route in {"upload", "extract"}:
        return Response(content=json.dumps({"error": "클라우드 시연에서는 가상자료만 사용합니다."},
                                           ensure_ascii=False).encode(),
                        status_code=403, media_type="application/json")
    body = await request.body()
    if len(body) > 1_000_000:
        return Response(content=b'{"error":"Request too large"}', status_code=413,
                        media_type="application/json")
    session_id = request.cookies.get(SESSION_COOKIE, "")
    if not re.fullmatch(r"[0-9a-f]{32}", session_id):
        session_id = uuid.uuid4().hex
    version = request.cookies.get(VERSION_COOKIE, "")
    if re.fullmatch(r"[0-9a-f]{32}", version):
        pathname = f"sessions/{session_id}/{version}.sqlite3"
    else:
        pathname = f"sessions/{session_id}.sqlite3"  # 기존 시연 기록 읽기
    try:
        snapshot = (await blob.get_blob(pathname, access="private")).body
    except blob.BlobNotFoundError:
        snapshot = None
    with tempfile.TemporaryDirectory() as folder:
        db_path = Path(folder) / "demo.sqlite3"
        prepare_database(db_path, snapshot)
        query = urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "path"])
        handler_path = f"/api/{route}" + (f"?{query}" if query else "")
        response = run_handler(request, handler_path, body, db_path)
        if request.method == "POST" and response.status_code < 400:
            db = sqlite3.connect(db_path)
            try:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                db.close()
            version = uuid.uuid4().hex
            await blob.put_blob(f"sessions/{session_id}/{version}.sqlite3",
                                db_path.read_bytes(), access="private",
                                content_type="application/x-sqlite3",
                                cache_control_max_age=0)
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, secure=True,
                        samesite="lax", max_age=60 * 60 * 24 * 30)
    if re.fullmatch(r"[0-9a-f]{32}", version):
        response.set_cookie(VERSION_COOKIE, version, httponly=True, secure=True,
                            samesite="lax", max_age=60 * 60 * 24 * 30)
    return response
