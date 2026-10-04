# Alden WAV 취소·파일 보존 — 2026-10-01

전체 Alden 목표는 진행 중이다. 이 변경은 음성 파일 출력의 취소·게시 경계에 한정된다.

설치된 기존 `Qwen3TtsAdapter.write_wav`에서 합성 결과를 NumPy로 변환하는 중 취소하면, 기존 WAV가 잘리고 새 파일이 저장되며 정상 성공 결과가 반환됐다. 소유한 임시 폴더와 fake synthesis를 사용하고 실제 설치본의 NumPy·WAV writer를 실행해 재현했다. 모델 호출·네트워크·마이크·전송은 0이다.

새 경로는 0600 임시 WAV를 완성한 뒤 취소 및 출력 경로를 확인하고 원자적으로 교체한다. 변환·쓰기 실패와 게시 전에 완료된 취소는 기존 파일을 보존하며 임시 파일을 제거한다. 기존 파일을 정상 완료 시 교체하는 계약은 유지한다. `AbortToken.cancel()`과 준비된 파일 게시에 같은 로컬 잠금을 사용하며, 음성 턴의 잠금은 세션 취소에도 연결된다. 공유 abort epoch의 기존 flock은 유지하고, 합성·오디오 변환·WAV 본문 쓰기는 잠금 밖에서 처리한다. 먼저 게시가 허용된 경우에는 그 교체를 끝낸 뒤 로컬 취소가 완료된다. 취소가 먼저 완료된 경우에는 후속 게시가 불가능하다.

새 출력 경로는 비상 중단 상태 폴더를 기존 guard로 먼저 0700 생성한 뒤 준비한다. 환경 변수와 직접 파일 모드 모두 같은 경로 검증을 사용하고, 검사만으로 폴더를 만들지 않는다. 기존 상태 폴더의 권한을 임의로 변경하지 않는다.

## 실행 범위

- 로컬 fake adapter 집중 검사: 최종 58개 통과. 취소 중 변환·본문 쓰기, 공유 abort 후 명시적 재개, 부분 disk failure, 기존 symlink 보호, 정상 WAV 형식·0600, 새 상태 폴더·중첩 환경 경로 및 로컬/턴/세션 취소의 실제 스레드 순서를 포함한다.
- 실제 STT 어댑터의 admission 실행: 소유한 사전 분할 PCM에서 `voice_memory_budget_low`; transcript/reply 없음, TTS 엔진 미적재, Python network audit 0, heavy inference modules import 0. 이는 STT 모델 추론 성공을 뜻하지 않는다.
- 해당 관측: reclaimable 55,601,364,992 bytes, swap free 1,429,272,002 bytes. STT/TTS 공통 기존 2,147,483,648 bytes swap 조건 미달. `RELEASED_WAKE_MODEL=None`을 유지한다.
- 로컬 checkpoint 파일 헤더 확인: Qwen CustomVoice revision `0c0e3051f131929182e2c023b9537f8b1c68adfe`, 본체 404 tensors BF16 / speech tokenizer 496 tensors F32. Whisper revision `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb`, 586 tensors F16 / 1 tensor I64. 파일과 dtype 확인이며 현재 모델 로딩·생성 근거가 아니다.
- 현재 검사용 menubar interpreter는 CPython 3.11.9다. 배포 sidecar 3.11.16과 현재 설치 runtime은 별도로 기록하며 공유 runtime을 덮어쓰지 않는다.

[검증 JSON](alden-voice-wav-verification-20261001.json): 필수 local Python 1,020개 / 17 skip / 실패0, CPython3.11.9, 120.317초. 최종 Astra/max 재검토의 독립 4건이 통과했다. [code CI](https://github.com/twoimo/openkakao-bot/actions/runs/36753986921)는 bd551b55c7bd3342ebdf6b8d9b24df2752883670의 4/4 성공이며 hosted Python 1,020개 / 74 skip / 실패0, 48.733초다. 건너뜀 환경 차이를 구분한다.

[설치 readback](alden-voice-wav-install-20261001.json): Alden0.1.6, PID60225, build30/30·source resource27/27 byte 일치와 strict/deep ad-hoc signature. 설치된 실제 writer의 NumPy 변환 취소는 이전 파일 보존으로 끝나고 staging이 남지 않았다. 새 중첩 환경 경로의 root0700/file0600 및 정상 RIFF도 확인했다. enrollment hash·기존 queue5개 식별자·localCLI와 private 후보 준비 중 shared stableCLI를 보존했다. 해당 시각 backend3/3ready이며 지속 freshness나 전체 사용 흐름을 뜻하지 않는다.

[산출물](alden-voice-wav-artifact-20261001.json): ZIP11,566,808bytes, SHA256 `9e688164f8eaa5fc2a63a811e10ff03cc08fe2e6a6e316ce20ac4460e1809bbb`, 설치30파일 byte 일치. 이전 package는 history에 보존하고 7/7 checksums를 대조했다. [private worker 후보](alden-voice-wav-worker-candidate-20261001.json)는 21assets/19source·기존3selectors/config 일치이며 활성화하지 않았다.

작업 source는 구현·설치된 fake adapter 경계의 실제 실행 검증·커밋/푸시·로컬 산출물 전달까지 완료했다. 전체 음성 및 공개 production 전달과는 구분한다. 사람 음성, wake 품질, 발화 종료→첫 재생 지연, native 화면·물리 단축키, production worker 교체와 공개 signed/notarized 릴리즈는 미완료다.
