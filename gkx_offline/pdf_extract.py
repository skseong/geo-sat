"""GEO-KSAT-X 양식 PDF를 오프라인에서 구조화한다. 정답 JSON은 읽지 않는다."""

import re
import subprocess
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


ID = r"(?:MRD|SYS|BUS|AOCS|EPS|PL|PROP|TCS|TTC|ICD-[A-Z]+)-\d{3}"
VER = r"VER-[A-Z]+-\d{3}"
TC = r"TC-[A-Z]+-\d{3}"
TR = r"TR-[A-Z]+-\d{3}"
SECTION = re.compile(r"^([34]\.\d+(?:\.\d+)?\s+.+)$")
FIELD = re.compile(r"^(요구사항 ID|제목|요구문|근거|상위 요구사항|검증방법|우선순위|상태|검증항목 ID|검증 제목|대상 요구사항|검증 수준|검증 내용|합격 기준|관련 시험케이스|시험케이스 ID|시험케이스명|검증항목|시험 구성|전제 조건|시험결과 ID|시험케이스|수행일|시험 장소|종합 판정)\s+(.+)$")
STARTS = {"요구사항 ID", "검증항목 ID", "시험케이스 ID", "시험결과 ID"}


def clean(value):
    return re.sub(r"\s+", " ", value).strip()


def ids(value, pattern=ID):
    return re.findall(pattern, value or "")


def pages(pdf):
    try:
        reader = PdfReader(str(pdf))
    except (PdfReadError, ValueError) as exc:
        raise ValueError("PDF 파일을 읽을 수 없습니다.") from exc
    if reader.is_encrypted:
        raise ValueError("암호화된 PDF는 추출할 수 없습니다.")
    try:
        return [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError) as exc:
        raise ValueError("PDF 텍스트를 읽을 수 없습니다.") from exc


def blocks(text_pages):
    """라벨 사이의 줄바꿈을 합치고 절/물리 페이지를 기록한다."""
    section = ""
    found = []
    current = None
    appendix = False
    for page_no, content in enumerate(text_pages, 1):
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("가상 샘플 문서") or re.match(r"^GKX-\S+ Rev ", line):
                continue
            if line.startswith("부록 ") and (current or found):
                appendix = True
                if current:
                    found.append(current)
                    current = None
            if appendix:
                continue
            if re.match(r"^[5-9]\.\s", line) and (current or found):
                if current:
                    found.append(current)
                return found
            if re.match(r"^\d+(?:\.\d+)*\.?\s", line) and not re.match(r"^\d+\s+(?:시험 구성|전원 인가|전원 차단|\[VER-)", line):
                if current:
                    current["last"] = None
                if not SECTION.match(line):
                    continue
            heading = SECTION.match(line)
            if heading:
                section = clean(heading.group(1))
            match = FIELD.match(line)
            if match and match.group(1) in STARTS:
                if current:
                    found.append(current)
                current = {"fields": {}, "section": section, "page": page_no, "steps": [], "measurements": []}
            if not current:
                continue
            if match:
                key, value = match.groups()
                current["fields"][key] = value.strip()
                current["last"] = key
            elif line == "단계 수행 내용 기대 결과" or line == "검증항목 요구사항 측정값 판정":
                current["last"] = None
            elif current["fields"].get("시험케이스 ID") and re.match(r"^\d+\s+", line):
                current["steps"].append(line)
                current["last"] = None
            elif current["fields"].get("시험결과 ID") and re.match(rf"^{VER}\s+{ID}\s+", line):
                current["measurements"].append(line)
                current["last"] = None
            elif current["fields"].get("시험결과 ID") and current["measurements"] and not re.match(r"^\d+(?:\.\d+)*\s", line):
                current["measurements"][-1] += line
            elif current["last"] and not re.match(r"^\d+(?:\.\d+)*\s", line):
                previous = current["fields"][current["last"]]
                # 조판 중 한글 단어가 행 끝에서 끊긴 경우를 결합한다.
                joiner = "" if re.search(r"[가-힣]$", previous) and re.match(r"^[가-힣]{1,3}(?:\s|[.,])", line) else " "
                current["fields"][current["last"]] += joiner + line
    if current:
        found.append(current)
    return found


def parse_regular(doc_no, text_pages):
    kind = doc_no.split("-")[1]
    result = []
    for block in blocks(text_pages):
        f = block["fields"]
        base = {"doc_id": doc_no, "section": block["section"], "page": block["page"]}
        if kind in ("MRD", "SRS", "SSS", "ICD") and "요구사항 ID" in f:
            item = {"id": f["요구사항 ID"], **base,
                    "type": "인터페이스요구사항" if kind == "ICD" else "요구사항",
                    "title": clean(f.get("제목", "")), "statement": clean(f.get("요구문", "")),
                    "rationale": clean(f.get("근거", "")),
                    "parents": ids(f.get("상위 요구사항", "")),
                    "methods": re.findall(r"[IADT]", f.get("검증방법", "")),
                    "priority": clean(f.get("우선순위", "")), "status": clean(f.get("상태", ""))}
        elif kind == "VP" and "검증항목 ID" in f:
            item = {"id": f["검증항목 ID"], **base, "type": "검증항목",
                    "title": clean(f.get("검증 제목", "")), "statement": clean(f.get("검증 내용", "")),
                    "criteria": clean(f.get("합격 기준", "")),
                    "parents": ids(f.get("대상 요구사항", "")),
                    "methods": re.findall(r"[IADT]", f.get("검증방법", "")),
                    "level": clean(f.get("검증 수준", "")),
                    "test_case": f.get("관련 시험케이스") if f.get("관련 시험케이스") not in (None, "—", "-") else None}
        elif kind == "TP" and "시험케이스 ID" in f:
            step_lines = [clean(raw.split(" ", 1)[1]) for raw in block["steps"]]
            item = {"id": f["시험케이스 ID"], **base, "type": "시험케이스",
                    "title": clean(f.get("시험케이스명", "")), "statement": "; ".join(step_lines),
                    "parents": ids(f.get("검증항목", ""), VER),
                    "requirements": ids(f.get("대상 요구사항", ""))}
        elif kind == "TR" and "시험결과 ID" in f:
            measurements = []
            for raw in block["measurements"]:
                m = re.match(rf"^({VER})\s+({ID})\s+(.+?)\s+(합격|부적합(?:\([^)]*\))?|조건부합격|미실시)$", raw)
                if m:
                    measurements.append(dict(zip(("ver", "requirement", "measured", "verdict"), m.groups())))
            verdict = clean(f.get("종합 판정", ""))
            item = {"id": f["시험결과 ID"], **base, "type": "시험결과",
                    "title": clean(f.get("시험케이스명", "")), "statement": verdict,
                    "parents": ids(f.get("시험케이스", ""), TC),
                    "test_date": clean(f.get("수행일", "")), "verdict": verdict,
                    "measurements": measurements}
        else:
            continue
        result.append(item)
    return result


def parse_vcrm(pdf, doc_no, text_pages):
    """Poppler의 고정 폭 표에서 I/A/D/T 열의 ● 위치를 보존한다."""
    try:
        proc = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True,
                              text=True, timeout=60, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("VCRM 표 추출에는 로컬 Poppler pdftotext가 필요합니다.") from exc
    result = []
    section = ""
    for page_no, content in enumerate(proc.stdout.split("\f"), 1):
        if page_no > len(text_pages):
            break
        header = None
        for raw in content.splitlines():
            line = raw.strip()
            if result and not re.match(rf"^\s*{ID}\s+GKX-", raw) and not line.startswith("요구사항 ID"):
                if raw[:45].strip() == "" and line and not SECTION.match(line):
                    result[-1]["result"] = clean(result[-1]["result"] + " " + line)
                    continue
            heading = SECTION.match(line)
            if heading:
                section = clean(heading.group(1))
            if "요구사항 ID" in raw and "출처 문서" in raw and "검증항목" in raw:
                # 공백으로 분리된 단문자 열명을 제목과 분리해 찾는다.
                header = {m.group(): m.start() for m in re.finditer(r"(?<!\S)[IADT](?!\S)", raw)}
                continue
            if not header:
                continue
            m = re.match(rf"^\s*({ID})\s+(GKX-[A-Z0-9-]+)\s+", raw)
            if not m:
                continue
            item_id, source_doc = m.groups()
            source_start = raw.find(source_doc, m.start(2))
            cols = sorted(header.items(), key=lambda kv: kv[1])
            first_col = cols[0][1]
            title = clean(raw[source_start + len(source_doc):first_col])
            method_list = [name for name, position in cols if abs(position - raw.find("●", position - 2, position + 3)) <= 2 and raw.find("●", position - 2, position + 3) >= 0]
            tail = raw[cols[-1][1] + 1:].strip()
            ver = re.search(VER, tail)
            tc = re.search(TC, tail)
            # 마지막 열의 결과는 VER/TC 뒤에 온다.
            end = (tc.end() if tc else ver.end() if ver else 0)
            outcome = clean(re.sub(r"^(?:—\s*)+", "", tail[end:].strip()))
            result.append({"id": item_id, "doc_id": doc_no, "type": "VCRM행",
                           "title": title, "source_doc": source_doc, "methods": method_list,
                           "verification_item": ver.group() if ver else None,
                           "test_case": tc.group() if tc else None, "result": outcome,
                           "section": section, "page": page_no})
    return result


def tp_steps(pdf):
    """기대 결과 열을 제외한 수행 내용만 고정 폭 조판에서 읽는다."""
    try:
        output = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True,
                                text=True, timeout=60, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("시험절차 표 추출에는 로컬 Poppler pdftotext가 필요합니다.") from exc
    collected = {}
    current = None
    result_col = None
    for line in output.splitlines():
        m = re.search(rf"시험케이스 ID\s+({TC})", line)
        if m:
            current = m.group(1)
            collected.setdefault(current, [])
            result_col = None
            continue
        if "단계" in line and "수행 내용" in line and "기대 결과" in line:
            result_col = line.index("기대 결과")
            continue
        if current and result_col and re.match(r"^\s*\d+\s+", line):
            action = re.sub(r"^\s*\d+\s+", "", line[:result_col])
            collected[current].append(clean(action))
    return collected


def extract(pdf, doc_no=None):
    pdf = Path(pdf).resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise ValueError("PDF 파일을 찾을 수 없습니다.")
    doc_no = doc_no or pdf.stem
    text_pages = pages(pdf)
    if not any(text.strip() for text in text_pages):
        raise ValueError("텍스트가 없는 PDF입니다. OCR이 필요합니다.")
    items = parse_vcrm(pdf, doc_no, text_pages) if "-VCRM-" in doc_no else parse_regular(doc_no, text_pages)
    if "-TP-" in doc_no:
        step_map = tp_steps(pdf)
        for item in items:
            item["statement"] = "; ".join(step_map.get(item["id"], []))
    if not items:
        raise ValueError("지원하는 GEO-KSAT-X 양식 항목을 찾지 못했습니다.")
    return {"document": doc_no, "pages": len(text_pages), "items": items}
