# 올든 음성 MPS 실행과 메모리 진입

현재 변경은 실제 음성 모델의 로컬 실행을 개선한다. 사람 마이크, 한국어 올든 wake 모델, 실제 스피커 재생과 전체 production 성공은 아직 입증하지 않았다.

## 원인과 구현

Qwen3-TTS 어댑터는 장치를 지정하지 않아 실제 1.7B BF16 모델을 CPU에 열었다. macOS의 MPS가 사용 가능하면 같은 가중치·정밀도를 MPS에서 열고, 다른 환경은 CPU를 사용한다. 임의 모델·양자화·클라우드 추론으로 바꾸지 않는다. 영어 남성 Ryan을 명시하여 정렬된 목록의 첫 화자 Aiden을 자동으로 선택하지 않으며 차분한 한국어 지시를 준다. Ryan이 없는 모델은 실패한다. [공식 Qwen SDK와 화자 계약](https://github.com/QwenLM/Qwen3-TTS#custom-voice-generate).

기존 2GiB allocated swap-free 검사는 macOS의 현재 swap 파일 빈 슬롯을 전체 배정 가능량으로 취급했다. 정상 압력이고 회수 가능한 페이지가 충분해도 모델을 막았다. 새 검사는 8/10GiB의 STT/TTS 적재 예산을 유지하고, swap reserve가 부족하면 정상 압력에서 예산 두 배와 2GiB 예비량(18/22GiB reclaimable)을 요구한다. 경고·위험 압력, 잘못된 센서와 부족한 RAM은 차단한다. sysctl은 내부 pressure enum이 아니라 dispatch flag를 반환하므로 normal=1을 사용한다. [Apple XNU 변환 코드](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus_notify.c).

실제 두 번째 턴이 신규 적재 예산에 막힌 것도 재현했다. 정확히 같은 모델이 현재 SDK cache에 있는 Whisper와 실제 TTS engine이 적재된 경우에는 추가 작업 공간만 요구한다. cached STT/TTS workspace는4/6GiB, low-swap 정상 압력 reserve는10/14GiB다. 이 값은 보수적인 추정이며 품질·메모리 상한을 입증하는 값이 아니다. Whisper cache는 현재 모델 경로를 대조하고 알 수 없으면 cold로 검사한다. 생성 후 MLX/MPS의 임시 캐시를 정리한다. TTS 전용 프로세스의 MPS allocator에는10GiB 상한을 적용하며 MLX Core나 다른 작업자 설정을 변경하지 않는다. [PyTorch per-process allocator API](https://docs.pytorch.org/docs/main/generated/torch.mps.set_per_process_memory_fraction.html).

독립 숫자 답변은 음성 입력 문자열만 한국어 표기로 바꾼다(`12입니다`→`십이입니다`). 표시 답과 대화 맥락의 원문은 유지하고 날짜·소수·음수·식별자·주변 문장은 변경하지 않는다. 자동 ASR은 짧은12 발음에 여전히 모호하므로 발음 개선이나 모든 숫자 품질을 주장하지 않는다.

## 실제 실행

전용 CPython3.11.9, mlx0.32.2, mlx-whisper0.4.3, PyTorch2.14.0, qwen-tts0.1.1을 사용했다. 기존 checkpoint의 정확한 revision을 [숫자 receipt](alden-voice-mps-verification-20261001.json)에 기록했다. 합성 입력은 로컬 macOS Yuna의 테스트 파일이며 제품 TTS 대체가 아니다. 모든 모델은 캐시만 사용하고 네트워크 audit는 외부 연결을 거부했다. 마이크나 speaker playback은 열지 않았다.

| 관측 | 결과 | 범위 |
| --- | --- | --- |
| Whisper 첫 실행 | 3.652초, 1.948초 입력을 정확히 인식 | 별도 프로세스1회 |
| 같은1.7B BF16 CPU/MPS SDK 합성 | 149.975 /24.601초, 각각2.56초 WAV | Aiden·같은 문장·AC 전원, 각1회; 별도 실험 진입 조건 |
| 실제 소스 두 턴 | 4입니다 /12입니다, 모두 error 없음 | presegmented PCM→STT→27B→TTS파일, wake/mic/playback 제외 |
| 첫/두 번째 턴 단계 합계 | 12.984 /4.635초 | cold/warm1회씩, 숫자 표기 수정 전 |
| 음성 재인식 | 첫4 확인, 둘째12 모호 | 사람 청취 품질이나 전체 STT 품질의 통과가 아님 |

CPU/MPS의1회 비교를 p50/p95나 앱 전체 속도 개선으로 일반화하지 않는다. 과정에서 physical-free 기반 guard 두 건과 첫 모델 뒤의 cached 모델 재검사 실패를 기록하고 수정했다. 별도 모니터의 free-page-only 중단1건도 실패 자료로 보존하며 합격에 포함하지 않는다. 마지막 실제 두 턴은 kernel pressure normal, loopback 연결4개와 외부 추론 연결0이었다. RSS·MLX peak·MPS last driver allocation은 서로 다른 범위라 합산하거나 메모리 절감률로 해석하지 않는다.

focused 실제 음성 runtime96개가 통과했다. 숫자 표기 직전 필수 전체 Python1106개/27skip/실패0, 이후 해당96개 집중 검사 통과를 구분한다. CI는 unit3의 일부 LLM 클래스 대신 전체 unit3를 검사하며 NumPy 없는 환경의 wake-warmup만 명시적으로 skip한다. 실제 음성 runtime에서는 해당 검사도 실행된다.

## 남은 검증

아직 설치본 실행 근거가 아니며 source와 설치 모듈을 별도로 대조해야 한다. 인간 음성·잡음·침묵·끼어들기·에코·전체 재생 지연과 숫자 발음 품질이 남았다. 공식 openWakeWord는 영어 모델을 제공하며 검증된 올든 한국어 release head가 아직 없다. release marker는None이고 live microphone은 계속 gate된다. [공식 언어 지원](https://github.com/dscripka/openWakeWord#language-support). 기존 production worker 교체 승인과 Apple 공개 서명·공증 자격 증명도 그대로 필요하다. 전체11개 목표는 진행 중이다.
