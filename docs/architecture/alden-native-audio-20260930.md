# Alden 음성 입출력과 끼어들기 구현 — 2026-09-30

재생 중 마이크를 모두 버리던 경로를 같은 AVAudioEngine의 voice processing 입출력으로 바꿨다.
Swift C ABI 라이브러리를 기존 Python 음성 프로세스에 포함해 별도 서비스는 추가하지 않는다.
오디오 그래프를 연결하기 전에 voice processing을 켜면 이 Mac에서 OSStatus `-10875`로
초기화가 실패했다. 그래프를 먼저 연결한 뒤 활성화하는 순서로 실제 입출력을 시작했다.

입력은 명시적으로 첫 채널을 선택해 16kHz mono PCM16으로 변환하고 2초 큐에 보관한다.
처리 활성 상태를 확인한 입력에서 VAD의 연속 20ms 발화 3프레임이 나오면 이전 턴과
그 턴의 재생 ticket을 취소하고 첫 3프레임을 새 발화에 보존한다. 재생 중 wake 검출은
돌리지 않는다. 처리가 끊기거나 입력이 유실되면 중단하며 raw 입력으로 우회하지 않는다.
사용자가 음성을 시작할 때만 OS 마이크 접근을 요청하고, 읽기 전용 측정에서는 요청하지 않는다.

실제 하드웨어 결과는 [원시 수치와 범위](alden-native-audio-readback-20260930.json)에 남겼다.
코드 판본 `effa5c6`을 원격에 보존했고 [해당 CI](alden-native-audio-remote-ci-20260930.json)의
4개 작업이 모두 성공했다. Hosted Python은 971개 중 73개를 건너뛰었으며 로컬 2개와 구분한다.
동일 조건의 전체 음성 지연과 성능 개선율은 미측정이다.

| 범위 | 표본 / 결과 | 의미 |
| --- | --- | --- |
| 초기 네이티브 입력 | 3초, 140프레임, overflow 0, RMS 최대 0 | 입출력 시작과 큐 동작. 사람의 발화는 확인하지 못함 |
| 초기 녹음된 기준음 재생 | 1.04초 기준음, 입력 65프레임, 오류 0 | 모델을 새로 실행한 결과가 아님. 이전 source fingerprint 보존 |
| 최종 설치 Python/라이브러리의 재생 취소 | 1회, 150ms 뒤 취소, waiter 종료 12.418ms | 실제 스피커의 잔향 시간이나 전체 음성 턴 지연이 아님 |
| 가짜 어댑터 회귀 | 집중 CI 971개 / 2개 skip / 실패 0; 최종 측정 정리 경로 12개 통과 | 연속/비연속 발화, 자기 출력 출처, 전역 중단, 권한 대기 취소, handle 소유권, 입력 유실 때 정리 |
| 화면 / desktop Rust | 191 / 85 통과 | 네이티브 화면이나 물리 단축키의 증거가 아님 |
| 설치 readback | 29/29 bundle 파일, 26/26 resource, ad-hoc 검증, 단일 PID | [설치 기록](alden-native-audio-install-20260930.json); 공개 서명·공증과 별개 |

일반 sounddevice 입력도 별도 2초 측정에서 모두 0이었다. 마이크 접근은 이미 허용돼 있고,
읽기 전용 CoreAudio 조회에서 입력 mute=false, volume=0.5였다. 설정 변경은 하지 않았다.
무음의 정확한 원인은 확인되지 않았다. 처리 활성 flag나 VAD만으로 사람의 발화,
에코 억제 품질, 실제 끼어들기를 통과 처리하지 않는다. 사람의 참여 시점을 요청했고 답변을 기다린다.

`RELEASED_WAKE_MODEL=None`과 기존 메모리 admission은 그대로다. 따라서 제품의
wake→STT→LLM→TTS 전체 턴은 아직 검증되지 않았고 음성 시작 제어도 계속 비활성이다.
계측 `--barge-in`은 8초의 녹음된 기준음과 실제 pipeline을 사용하되 STT/LLM/TTS 모델을
호출하거나 원음을 저장하지 않는다. 참여하는 사람이 없는 결과를 발화 성공으로 보고하지 않는다.

```sh
sh scripts/build-alden-voice-audio.sh
"$HOME/Library/Application Support/openkakao/runtimes/voice/bin/python3.11" -B \
  scripts/measure_alden_voice_audio.py --wav <24kHz-mono-PCM16-reference.wav> \
  --cancel-after 0.15 --output <receipt.json>
```

기준음 SHA는 receipt에 보존하지만 WAV는 배포물에 포함하지 않는다. 설치 스크립트를
직접 검증할 때도 `-B`를 사용한다. 이번 수동 probe가 만든 bytecache 10개는 번들 밖으로
보존 이동한 뒤 설치 파일 집합과 코드 서명을 다시 검증했다. 제품 bridge는 이미 `-B`로 실행한다.

[음성·취소 흐름](alden-voice-cancellation.html)은 변경된 경로를 반영한다. Archify showcase
9/9, 오류/경고 0이며 실제 브라우저 containment 검사와 두 테마 캡처 판독을 분리해 기록했다.
사용자 콘텐츠는 한국어이며 고정 Viewer UI는 영어 fallback이다.
