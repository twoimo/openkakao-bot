# Alden 상시 설치본의 실제 메뉴바·설정 화면

Alden0.1.6 소스`51f71c2`의 **기존 상시 프로세스**에서 메뉴바 입력·창별 캡처·최소 크기 변경을 실행했다. 설치 executable SHA는 기존 검증본과 동일하다. 별도 감사 인스턴스와 unavailable settings bridge를 사용했던 [이전 기록](alden-graph-navigation-20261001.md)과 구분한다. 제품 소스·공유 MLX·운영 작업자를 교체하지 않았다.

## 실제 화면 검토

| 화면 | 캡처 크기 | 확인한 범위 |
| --- | --- | --- |
| 메뉴바 왼쪽 클릭 → 코어 |276×260| 금색 코어·링, 진단 문구 없이 실제 설치본 표시 |
| 오른쪽 클릭 → 기본 설정 |960×880| normal-level 창, 실제 운영 목록·상태와 저장된 그래프 표시, 음성 준비 조건 안내 |
| AX 최소 크기 변경 → 설정 |640×680| 위쪽 제어가 한 열로 표시되고 가로 잘림 없음; 아래 그래프는 캡처 밖 |

세 이미지 모두 부모가 픽셀로 읽었다. 실제 채팅방 이름이 포함된 raw PNG와 원본 receipt는 private에 보존하고 Git·릴리즈에서 제외한다. [숫자·범위 기록](alden-primary-ui-20261001.json)에는 room/graph identity, PID, window ID, 화면 좌표와 개인 실행 경로가 없다.

최소 크기를 실제로640×680에 적용한 뒤960×880으로 복원했다. 창 닫기 AX 성공과 native 가시 창0을 확인했다. 커서 복원 API는 각0으로 성공했다. 포커스 복원 요청은 받아들여졌지만 즉시 readback은 지연됐으며 나중에 원래 앱이 확인된 뒤 다른 foreground 앱으로 바뀌었다. 포커스가 계속 유지됐다는 주장은 하지 않는다. 사용자 Terminal을 숨기거나 음성·모델·전송 제어를 실행하지 않았다.

## 제어·측정의 한계

직접 ApplicationServices/CoreGraphics 조회에서 접근성·화면 캡처 preflight가 통과했다. 기존 CUA timeout을 성공으로 바꾸어 보고하지 않는다. 소유한 메뉴바 항목의 AXPress는 code0이지만 약2.027초 동안 창이 열리지 않았다. 그 후 같은 항목의 실제 위치에 CG 왼쪽/오른쪽 down/up 이벤트를 전달해 실제 창이 열렸다. 이 방법은 전역 마우스를 짧게 사용하며 독립 가상 커서가 아니다.

창 표시 관측 상한은 코어107.642ms, 기본 설정129.144ms, 최소 검사 전 설정 열기131.688ms다. **각1회**, mouseDown 전부터40ms hold·mouseUp·poll·창 열기 애니메이션까지 포함한다. 그린 프레임의 UI 응답 시간, p95나100ms 목표 달성 여부·전후 개선율·배터리 절감 수치로 해석하지 않는다. [기존 Archify 흐름](alden-graph-navigation-20261001.html)의 제품 구조는 그대로다.

## 남은 전체 목표

상시 창에서 graph back/reset·focus 조회와 최소 viewport의 아래 그래프, primary frame counter·Retina·물리 단축키/잠금/세션·사람 음성은 미검증이다. 기존 controlled native auditor의 navigation/숨김 검증을 이 상시 프로세스의 결과로 옮겨 쓰지 않는다. 운영 worker 교체 승인·공유 no-vision 서버의 실제 media transport, wake/음성 admission, Developer ID 및 Apple secret6개·공개 공증/production release와 과거 foreign-exception2회 원인도 남아 있다. 전체11개 목표와 이미지·링크·파일 맥락 실패 범위를 유지한다.
