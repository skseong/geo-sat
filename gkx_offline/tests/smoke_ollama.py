"""선택 실행: 가상 항목 1건을 로컬 Ollama로 검토한다."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


with tempfile.TemporaryDirectory() as folder:
    db = app.connect(Path(folder) / "smoke.sqlite3")
    try:
        app.schema(db)
        app.init_sample(db, Path(__file__).resolve().parents[2] / "GEO-KSAT-X")
        row = app.get_candidate(db, "GKX-MRD-001", "MRD-001")
        with db:
            db.execute("UPDATE candidates SET selected=1 WHERE candidate_id=?", (row["candidate_id"],))
        app.save_selected(db, "GKX-MRD-001")
        result = app.ollama_review(db, "GKX-MRD-001", "MRD-001", "qwen3.5:9b-mlx")
        print(json.dumps({"model": result["model"], "status": result["opinion"]["status"],
                          "human_decision": result["opinion"]["human_decision"],
                          "evidence_count": len(result["opinion"].get("evidence", []))}, ensure_ascii=False))
    finally:
        db.close()
