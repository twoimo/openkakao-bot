# Alden 첨부 문서 읽기 — 2026-10-01

파일 내용 읽기를 구현하고 설치본의 파서까지 검증했다. 실제 카카오 첨부 전송 경로와 운영 worker 교체는 미완료다. 전체 11개 목표는 계속 진행 중이다.

## 원인과 변경

기존 watcher는 파일 이름·크기·작성자와 첨부 JSON digest만 보존했다. worker는 내용을 다운로드하지 않고 항상 텍스트를 요청했다. 짧은 후속 질문은 파일을 정확히 참조해도 내용에 접근할 수 없었다.

이제 `download --local --file`은 원본 DB를 기존 읽기 전용 reader로 조회한다. 대화방·메시지·작성자·첨부 JSON SHA-256이 일치하고 자기 메시지가 아닌 경우에만 기존 Kakao CDN downloader를 사용한다. signed URL에는 계정 자격 증명을 보내지 않는다. 다운로드에는 리다이렉트 금지·5 MiB 한도·선언 크기 일치를 적용하고, 파일 내용 SHA-256을 별도로 기록한다. Kakao의 opaque `cs`를 발신자 서명이나 내용 체크섬 검증으로 해석하지 않는다.

worker는 기존 링크 다운로드 동의·privacy attestation·최신 턴·취소 gate를 유지한다. 동일 작성자가 300초 내 바로 앞 파일을 지칭한 경우에만 그 원본 행을 읽는다. 원래 파일명은 파서 argv에 넣지 않는다. private 임시 파일은 작업 뒤 제거하며, 원문 전체를 큐나 공용 캐시에 저장하지 않는다.

문서별 추출 범위는 다음과 같다.

| 형식 | 실제 추출 범위 | 제한 |
| --- | --- | --- |
| TXT·MD·CSV·JSON·코드 등 | UTF-8/UTF-16 BOM 텍스트 | 실행하지 않음; 지원되지 않는 인코딩은 확인 불가 |
| DOCX | 본문·각주·미주·헤더·푸터의 텍스트 | 이미지·배치·도형 의미는 읽지 않음 |
| PPTX | presentation 관계가 정한 슬라이드 순서의 텍스트 | 최대 50장; notes는 읽지 않음 |
| XLSX | 시트·셀 주소·문자열·수식과 저장된 값 | 계산하지 않음; 외부 관계는 거부 |
| PDF | macOS PDFKit 텍스트 | 최대 50쪽; 이미지 PDF OCR은 지원하지 않음 |
| RTF·기존 DOC | macOS textutil 텍스트 | 형식 signature 및 소유한 private 입력만 허용 |

ZIP에는 256항목·전체 해제 크기 20 MiB 한도를 적용한다. 추출 결과는 누적 시점에 UTF-8 16,000 bytes로 제한해 엑셀 공유 문자열 반복으로 출력 메모리가 폭증하지 않게 했다. XML의 DTD/ENTITY와 중복 ZIP 이름을 거부한다. 순수 Python 파싱도 8초 한도의 소유한 별도 프로세스에서 수행하며 기존 중단 adapter가 그룹을 종료한다. 전체 파서 RSS 상한이나 배터리 절감은 측정하지 않았다.

모델 입력은 문서와 보조 맥락의 예산을 분리한다. 실제로 잘린 텍스트의 digest·byte 수·truncated를 기록하고, 부분 읽기 고지와 문서 내 명령 무시 지침을 system prompt에 유지한다. 최종 답변의 필수 출처 receipt에 `file:<내용 SHA-256>`을 보존한다. 이는 전달한 근거의 기록이며 모델이 스스로 인용을 정확하게 생성했다는 별도 증거는 아니다.

## 검증과 측정

- [기준선](alden-file-baseline-20261001.json): 승인된 3방에서 각 최근 200행, 총 600행을 읽었으나 파일 후보가 없었다. 더 오래된 파일이 없다는 뜻은 아니다. 원문 임시 복제본은 삭제하고 집계만 남겼다.
- [검사](alden-file-source-verification-20261001.json): mandatory Python 63 selectors / 1011 tests / 16 skip / 실패 0. 마지막 argv 개인정보 수정 뒤 파일 집중 31개 / skip 0 / 실패 0. Rust 1141개 통과·1 ignored, clippy all-targets 통과. 최종 코드 `c29e263`의 [원격 CI](https://github.com/twoimo/openkakao-bot/actions/runs/36747487368) 4/4 작업 성공; hosted Python은 최종 1012개/73 skip/실패 0이었다. gpt-6-astra/max 독립 검토에서 발견한 5문제를 수정하고 재검토했다.
- [실제 로컬 모델](alden-file-model-probe-20261001.json): 기존 resident `mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit`에 합성 문서 4개를 질의했다. parsed reply의 날짜·수량 및 엑셀의 저장된 값 표현을 고정 기준으로 검사해 4/4 통과했다. 이 probe의 모델 HTTP 요청은 loopback 12건이며 클라우드 모델 요청은 0건이다.

| 합성 문서 | 추출 시간 | 모델 생성 시간 | 횟수 |
| --- | ---: | ---: | ---: |
| TXT | 0.00093초 | 11.396초 | 1 |
| DOCX | 0.00254초 | 6.984초 | 1 |
| XLSX | 0.00083초 | 4.342초 | 1 |
| PDF | 0.18041초 | 4.937초 | 1 |

서로 다른 입력의 1회 측정이며 호스트 부하·cache·전원 상태를 통제하지 않았다. p95나 성능 개선율로 해석하지 않는다. 기본 실패 경로가 내용을 읽지 않았으므로 같은 조건의 추출 성능 기준값은 없다.

## 설치와 남은 전달

[설치 대조](alden-file-install-20261001.json): `/Applications/Alden.app` 0.1.6, bundle 30/30파일·resource 27/27 일치, strict/deep ad-hoc signature, 단일 LaunchAgent PID 11451을 확인했다. 설치 파서의 4개 fixture 텍스트 digest가 실제 모델에 전달한 입력과 같았고, 설치된 opaque subprocess entrypoint와 embedded CLI의 새 flags도 실행 확인했다. 설치 화면의 자연 대화나 카카오 파일 다운로드를 검증한 것은 아니다.

enrollment·5개 queue 파일 식별자·공유 stable CLI를 보존했다. 관측 시 backend는 3/3 ready였다. [새 worker 후보](alden-file-worker-candidate-20261001.json)는 private state에 21/21 assets·19/19 source/data·기존 3 selectors/config와 일치하도록 준비했고 활성화하지 않았다.

운영 교체는 기존 supervisor를 채택하거나 종료하지 않는 `AGENTS.md`의 제약과 별도 소유 세션을 보존한다. 이미 제시한 교체 승인, 사람 음성 시험, 서명·공증에 필요한 `ALDEN_APPLE_*` 자격 증명이 아직 해결되지 않았다. native UI/Retina·전체 음성 턴·실제 파일 transport·운영 적용·signed release는 완료로 표시하지 않는다.
