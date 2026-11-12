# GEO-KSAT-X 문서 검토 도구

PDF에서 요구사항, 검증항목, VCRM 표, 시험절차, 시험결과를 직접 읽어 후보로 만듭니다. 후보는 수정·선택·저장할 수 있습니다. 규격 질의는 등록된 전체 문서의 후보를 검색하며, 저장된 항목만 추적·AI 검토의 대상이 됩니다. 담당자 판정은 AI 의견과 별도로 기록합니다.

## 로컬 실행

Python 3.9 이상, `pypdf`, Poppler의 `pdftotext`가 필요합니다. PDF 추출은 네트워크를 사용하지 않습니다.

```bash
cd gkx_offline
python3 -m pip install -r requirements.txt
python3 server.py
```

첫 실행 시 빈 `data/gkx.sqlite3`가 자동 생성됩니다. `http://127.0.0.1:8765/`의 **PDF 업로드**에서 `GKX-...pdf` 양식 파일을 추가하면 바로 추출 후보가 생성됩니다. 최대 25 MB이며, 텍스트층이 없는 스캔 PDF와 암호화 PDF는 지원하지 않습니다. 업로드 원본은 `data/uploads/`에 저장됩니다. 기존 문서의 **PDF 다시 추출**은 후보 원본만 갱신하며 사용자의 수정값·선택·저장 기록은 보존합니다.

제공된 GEO-KSAT-X 시연 PDF 37개를 한 번에 등록하려면 아래 명령을 사용합니다. 초기 후보는 정답 파일로 만들어지므로, 이후 문서함의 **PDF 다시 추출**을 눌러 원문에서 새로 추출할 수 있습니다.

```bash
python3 app.py init --sample ../GEO-KSAT-X
```

## AI 제공자

기본값은 로컬 Ollama `qwen3.5:9b-mlx`이며 요청에 `think: false`를 보냅니다.

```bash
python3 server.py --provider ollama --model qwen3.5:9b-mlx
```

일반 컨테이너 배포에서는 서버 환경변수 `OPENAI_API_KEY`를 설정하고 OpenAI 제공자로 실행할 수 있습니다. ChatGPT 구독 로그인과 별개로 OpenAI API 키가 필요합니다. 키는 브라우저로 전달하거나 저장하지 않습니다. OpenAI 요청에는 저장 비활성화(`store: false`)를 적용합니다. 이 모드에서 질문과 검색된 후보의 텍스트는 OpenAI API로 전송됩니다.

```bash
GKX_AI_PROVIDER=openai GKX_AI_MODEL=gpt-6-luna OPENAI_API_KEY=... python3 server.py
```

컨테이너 배포용 `Dockerfile`에는 `pypdf`와 Poppler가 포함됩니다. `PORT`와 `GKX_HOST`를 환경변수로 지정할 수 있습니다. 기본 로컬 실행은 `127.0.0.1`, 컨테이너는 `0.0.0.0`에 바인딩합니다. 배포 플랫폼의 영구 볼륨을 `data/`에 연결하지 않으면 업로드·검토 기록은 재시작 시 사라집니다. 외부 공개 시 접근 제어를 제공하는 배포 환경 뒤에 두세요.

## Vercel 가상자료 시연

Vercel 프로젝트의 Root Directory는 `gkx_offline`입니다. `public/`은 정적 화면, `api/index.py`는 FastAPI 함수입니다. 배포판에는 `demo_sample/`의 가상 PDF 37개와 후보 914건이 포함됩니다. PDF 업로드와 다시 추출은 배포판에서 비활성화합니다. 선택·수정·저장·검토 기록은 브라우저별 세션 ID를 기준으로 비공개 Vercel Blob에 SQLite 스냅샷으로 저장합니다. 시연 세션의 쿠키가 사라지면 이전 기록에 접근할 수 없습니다.

AI 요청은 Vercel AI Gateway의 OpenAI 모델을 사용하며 함수 요청의 OIDC 토큰으로 인증합니다. 질문과 검색 근거의 텍스트가 외부 모델로 전달됩니다. 로컬 Ollama 요청에는 계속 `think: false`를 적용합니다.

## 검증

```bash
python3 -m unittest discover -s tests -v
```

샘플 37개 PDF의 항목 ID·페이지·개수(914건)를 정답 자료와 대조합니다. 추출기 자체는 정답 JSON을 읽지 않습니다. 조판상 줄바꿈으로 인한 본문 공백과 VCRM 검증방법 표시 순서는 원문 후보를 보고 확인할 수 있습니다.
