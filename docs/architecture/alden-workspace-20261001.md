# Alden 화면 잠자기·세션 전환 처리

소스 `8791ea1cc23073a58806bed5e6537a4512dbb54d`의 Alden 0.1.6을 설치했다. 설치와 빌드의 **30/30 파일·27/27 리소스**가 일치하며 ad-hoc 서명을 strict 검증했다. 상시 앱 PID는 77434다. 실제 화면 근거는 같은 설치 바이너리의 **별도 인스턴스 PID82885**이며 상시 앱의 현재 화면을 확인한 것은 아니다.

## 원인과 수정

기존 코드는 창 닫기·포커스 상실을 처리했지만 네이티브 화면 잠자기·사용자 세션 전환 알림을 직접 구독하지 않았다. DOM 포커스/가시성 이벤트가 네이티브 숨김 뒤 렌더링을 재개할 수도 있었다.

- `NSWorkspace.notificationCenter`의 두 고정 알림을 mainQueue에서 구독한다. Alden과 설정 창을 각각 숨기고 실제 `is_visible=false`를 확인한 뒤 기존 가시성 이벤트를 보낸다.
- 구독 토큰은 앱 수명 동안 보관한다. `run_return` 종료 뒤 같은 센터에서 제거하고 이미 예약된 콜백을 비활성화한다.
- 프런트엔드는 네이티브 숨김 상태를 기억한다. DOM 포커스·가시성만으로 재개하지 않으며 오래된 부팅 조회나 조회 실패도 최신 숨김을 덮어쓰지 못한다.
- 깨우기·세션 활성화에 자동으로 창을 열지 않는다. 기존 명시적 열기 동작에서 단일 루프가 재개된다. 음성·추론·자동화 작업과 큐는 이 가시성 경로에서 중단하지 않는다.

Apple은 [화면 잠자기 알림](https://developer.apple.com/documentation/appkit/nsworkspace/screensdidsleepnotification)을 화면이 잠드는 신호로, [세션 비활성 알림](https://developer.apple.com/documentation/appkit/nsworkspace/sessiondidresignactivenotification)을 사용자 세션이 전환되기 전의 신호로 정의한다. 이를 물리적인 화면 잠금 검증으로 보고하지 않는다.

## 실제 실행과 측정

전용 감사 경로는 PythonBridge·생산 명령·전역 단축키를 등록하지 않는다. 설치된 실제 WKWebView/Three 코어를 실행하고 네트워크는 sandbox에서 차단했다. OS 전체 알림이나 잠자기/잠금을 실행하지 않고 **이 프로세스의 NSWorkspace 센터에만 합성 알림**을 게시했다. 매 게시 전 두 창의 실제 가시성과 코어의 실행 상태를 확인했다.

| 경로 | 표본 | 중단 확인 상한 | 숨김 350ms 추가 렌더 |
| --- | --- | --- | --- |
| 합성 화면 잠자기 알림 | 1 | 13.304ms | 0 |
| 합성 세션 비활성 알림 | 1 | 16.683ms | 0 |
| 일반 네이티브 숨김·복원 | 10 | median 11.911ms / max 18.825ms | 모두 0 |

두 합성 알림 모두 수신 횟수 +1, 두 네이티브 창 숨김, 새로운 pause 시각, 현재 루프/예약 프레임 중단, 이후 명시적 재개를 확인했다. 일반 복원에서는 250ms 동안 3–4프레임을 관측했다. 측정은 요청부터 중단 관측까지의 **상한**이다. 서로 다른 시계의 OS 이벤트→마지막 프레임 정밀 지연, p95, 전력 절감률이나 전후 성능 개선율은 산출하지 않았다.

전체 감사 8.499초 1회, 주 프로세스 peak RSS 108,658,688 bytes다. WebKit/GPU 자식·다른 앱·상시 앱은 RSS 범위에 포함되지 않는다. 프로세스 종료 0과 보고서 success=true를 함께 확인했다. 물리 모니터 배율은 [1.0]이고 Retina 2×는 미검증이다. 설정은 네이티브 가시성만 확인했으며 backend 없는 설정 렌더러는 실행 검증하지 않았다.

Rust 90/90, UI 194/194, Clippy all-targets `-D warnings`, TypeScript/Vite 및 네이티브 app 빌드를 통과했다. UI 회귀에는 숨김 뒤 DOM 재개 차단, 부팅 조회 대기와 조회 실패 경합을 포함한다. Foundation 실제 private notification center에서 이름 필터와 Drop 후 구독 해제를 확인했다. 변경되지 않은 Python 검사는 로컬에서 반복하지 않았으며 [원격 CI](https://github.com/twoimo/openkakao-bot/actions/runs/36793165993) 4/4 작업 성공, hosted Python 1,054개/83skip/실패0(48.110초)을 확인했다. 이전 로컬 pinned Python 실행과 구분한다.

## 화면과 다이어그램

부모가 아래 실제 설치 바이너리의 두 캡처를 이미지로 읽었다. 코어·링의 잘림이나 레이아웃 넘침이 없었다. 실제 작업량을 꾸며서 표시하지 않은 idle 화면이다.

![설치 바이너리 코어](alden-workspace-before-20261001.png)
![숨김·복원 뒤 코어](alden-workspace-restored-20261001.png)

기존 [검증된 Archify 감사 흐름](alden-native-render-20261001.html)은 당시의 숨김·복원/캡처 경로를 보존한 자료이며 새 NSWorkspace 구독을 포함하지 않는다. 새 도식은 가독성 검사 1건이 남아 전달하지 않았다. [Archify delivery contract](https://github.com/tt-a1i/archify)의 로컬 규칙인 “never exceed a maximum of two focused correction rounds”에 따라 2회 수정 후 중단했다. 후보와 진단은 private 작업 폴더에 보존했다. 기존 Archify의 한국어 본문과 영어 고정 Viewer UI를 구분한다.

## 전달 상태

전달: [Alden 0.1.6 화면 가시성 후보 초안](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-6730d9c69d5ac88931de)에 7개 파일을 올리고 다시 다운로드하여 모두 바이트 일치를 확인했다. 앱 ZIP 내부30파일·소스/증거 overlay23파일도 대조했다. 소스 `8791ea1`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36793165993)는 4/4 성공이다. 초안 target/overlay는 증거 checkout `b7bdda9`이며 공개 공증/프로덕션 완료를 뜻하지 않는다. [릴리즈 대조 기록](alden-workspace-release-20261001.json).

| 항목 | 단계 | 범위 |
| --- | --- | --- |
| 네이티브 구독·가시성 fence | 구현 | 코드와 집중 회귀 통과 |
| 설치·별도 바이너리 감사 | 실제 실행 검증 | 프로세스 내부 합성 알림과 일반 hide/resume |
| Git push·초안 파일 전달 | 전달 완료 | 공개 서명 릴리즈/프로덕션과 구분 |
| 물리 잠자기·전환·잠금 | 미착수 | 이 감사에서 실제 OS 전환을 실행하지 않음 |
| 새 Archify 도식 | 구현 | 가독성 검사 실패로 미전달 |

## 증거와 남은 범위

- [원본 감사 JSON](alden-workspace-20261001.json), [측정 요약](alden-workspace-summary-20261001.json), [설치 대조](alden-workspace-install-20261001.json), [검사](alden-workspace-tests-20261001.json), [CI](alden-workspace-ci-20261001.json), [독립 검토](alden-workspace-review-20261001.json).
- 물리 잠자기·사용자 전환·화면 잠금, 상시 앱 화면·tray·실제 단축키·설정 렌더링, Retina 2×, 사람 음성 턴은 미완료다.
- MLX PID3273과 작업자 PID87952/87955/88035를 보존했다. 상시 worker 교체는 기존 승인 요청의 답변을 기다린다. 음성 admission의 2GiB swap-free와 wake release gate를 유지한다.
- 공개 서명·공증 릴리즈는 기존 workflow의 Apple secrets 6개와 Developer ID Application 인증서가 없다. 로컬 ad-hoc 설치나 초안 릴리즈는 공개 릴리즈/프로덕션 완료가 아니다. 전체 11개 항목 목표는 계속 진행 중이다.
