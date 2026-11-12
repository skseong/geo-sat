# GEO-KSAT-X 문서 검토 도구

가상 위성 문서의 요구사항·검증·시험 정보를 PDF에서 추출하고, 후보 검토와 추적성 확인을 수행하는 도구입니다.

- [실행 및 배포 방법](gkx_offline/README.md)
- [설계문서](GEO-KSAT-X_문서분석도구_설계문서.md)

`gkx_offline/`은 로컬 PDF 추출과 SQLite 저장을 사용합니다. 로컬 AI는 Ollama를, 임시 배포의 AI는 서버에 설정한 OpenAI API를 사용합니다. 시연용 PDF 원본과 작업 DB는 이 저장소에 포함하지 않습니다.
