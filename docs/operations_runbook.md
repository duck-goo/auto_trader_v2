# auto_trader_v2 운영 Runbook

## 1. 목적
- 이 문서는 `mock` 운영과 실제 운영 전 점검을 같은 순서로 반복하기 위한 최소 운영 절차다.
- 목표는 "한 번 더 확인하고 실행"하는 것이다.
- 기본 원칙은 `preview 먼저`, `JSON 결과 확인 후 execute`, `이상 시 kill switch`다.

## 2. 공통 원칙
- 모든 예시는 PowerShell 기준이다.
- 출력 JSON은 날짜별 폴더에 모아 둔다.
- 예시 폴더: `.\data\ops\YYYY-MM-DD\`
- `run_trading_session.py` 는 preview여도 preopen 단계에서 유니버스 스냅샷을 저장한다.
- `max_daily_order_count` 와 `max_daily_loss` 는 신규 매수만 막는다. 기존 보유 종목 매도는 계속 허용한다.
- `LOCK_BUSY` 가 나오면 같은 종류의 세션이 이미 실행 중일 가능성이 크다. 같은 명령을 다시 겹쳐 실행하지 않는다.

## 3. 하루 운영 순서

### 3-0. 빠른 리허설
- `mock` 모드에서 운영 순서를 빠르게 점검할 때는 아래 래퍼를 먼저 사용할 수 있다.
- 이 스크립트는 `startup_check -> 1회 preview trading session -> 선택적 after-close preview` 순서로 돌고, 각 JSON과 요약 JSON을 한 폴더에 저장한다.
- 장시간 polling 을 돌리지 않기 위해 intraday preview 는 자동으로 `1 cycle` 만 실행한다.

```powershell
.\venv\Scripts\python.exe scripts\run_mock_operational_rehearsal.py `
  --trade-date YYYY-MM-DD `
  --use-db-master `
  --per-order-budget 1000000 `
  --max-holdings 3 `
  --max-daily-order-count 7 `
  --max-daily-loss 500000 `
  --output-dir .\data\ops\YYYY-MM-DD\rehearsal
```

- after-close preview 까지 같이 보고 싶으면 `--include-after-close` 를 추가한다.
- 리허설 결과를 짧게 다시 읽고 싶으면 아래 요약 명령을 사용한다.

```powershell
.\venv\Scripts\python.exe scripts\show_ops_summary.py `
  --output-dir .\data\ops\YYYY-MM-DD\rehearsal
```

- 날짜 폴더 전체 결과를 한 번에 보고 싶으면 아래 보고서 명령을 사용한다.

```powershell
.\venv\Scripts\python.exe scripts\show_daily_ops_report.py `
  --trade-date YYYY-MM-DD `
  --output .\data\ops\YYYY-MM-DD\daily_ops_report.json
```

- 자동 점검에 붙일 때는 `--strict` 를 사용한다.
- `WARNING` 수준이면 종료 코드 `4`, `CRITICAL` 수준이면 종료 코드 `5` 로 끝난다.

```powershell
.\venv\Scripts\python.exe scripts\show_daily_ops_report.py `
  --trade-date YYYY-MM-DD `
  --strict `
  --output .\data\ops\YYYY-MM-DD\daily_ops_report.json
```

### 3-1. 시작 전 공통 확인
- 설정 파일이 `mock` 인지 먼저 확인한다.
- 전일 미해결 주문이나 수동 복구 필요 건이 있으면 먼저 정리한다.
- 운영 결과 파일을 저장할 폴더를 먼저 만든다.

```powershell
New-Item -ItemType Directory -Force .\data\ops\YYYY-MM-DD | Out-Null
```

### 3-2. 장 시작 전 점검
- 가장 먼저 startup gate 결과를 본다.
- `READY` 가 아니면 장중 세션을 시작하지 않는다.

```powershell
.\venv\Scripts\python.exe scripts\startup_check.py `
  --trade-date YYYY-MM-DD `
  --output .\data\ops\YYYY-MM-DD\startup_check.json
```

- 확인할 항목
- `outcome`
- `reason`
- unresolved orders 존재 여부

### 3-3. 장중 세션 시작
- 실제 운영은 `run_trading_session.py` 한 번으로 preopen + polling 을 같이 시작한다.
- 먼저 preview로 같은 파라미터를 확인한다.

```powershell
.\venv\Scripts\python.exe scripts\run_trading_session.py `
  --use-db-master `
  --trade-date YYYY-MM-DD `
  --per-order-budget 1000000 `
  --max-holdings 3 `
  --max-daily-order-count 7 `
  --max-daily-loss 500000 `
  --output .\data\ops\YYYY-MM-DD\run_trading_session.preview.json
```

- preview JSON에서 아래 값이 정상이면 execute로 다시 실행한다.
- `session_outcome`
- `session_reason`
- `preopen_result.readiness_outcome`
- `polling_result.stop_reason`

```powershell
.\venv\Scripts\python.exe scripts\run_trading_session.py `
  --use-db-master `
  --trade-date YYYY-MM-DD `
  --per-order-budget 1000000 `
  --max-holdings 3 `
  --max-daily-order-count 7 `
  --max-daily-loss 500000 `
  --execute `
  --output .\data\ops\YYYY-MM-DD\run_trading_session.execute.json
```

- 자주 보는 차단 사유
- `PREOPEN_BLOCKED`: startup gate 또는 preopen 준비가 막혔다.
- `POLLING_LOCK_BUSY`: 이미 다른 polling 루프가 실행 중이다.
- `POLLING_BLOCKED`: polling 은 시작했지만 내부 stop reason 으로 차단되었다.
- `MAX_DAILY_LOSS_REACHED`: 당일 실현손익 기준 신규 매수 차단 상태다.
- `MAX_DAILY_ORDER_COUNT_REACHED`: 당일 주문 수 기준 신규 매수 차단 상태다.
- `KILL_SWITCH_ENABLED`: kill switch 가 켜져 있어서 자동매매가 막혔다.

## 4. 즉시 중단 절차
- 이상 주문, 반복 재시도, 예상 밖 손실이 보이면 가장 먼저 kill switch 를 켠다.
- kill switch 는 자동매매 신규 진행을 막기 위한 긴급 브레이크다.

```powershell
.\venv\Scripts\python.exe scripts\set_kill_switch.py `
  --enable `
  --note "manual emergency stop" `
  --output .\data\ops\YYYY-MM-DD\kill_switch.enable.json
```

- 현재 상태 확인

```powershell
.\venv\Scripts\python.exe scripts\set_kill_switch.py `
  --output .\data\ops\YYYY-MM-DD\kill_switch.status.json
```

- 재개 전에는 원인 확인 후 kill switch 를 내린다.

```powershell
.\venv\Scripts\python.exe scripts\set_kill_switch.py `
  --disable `
  --note "incident reviewed" `
  --output .\data\ops\YYYY-MM-DD\kill_switch.disable.json
```

## 5. 장중 주문 정리
- 미체결 주문, 브로커 상태 미동기화, 로컬 체결 반영 누락 의심 시 order maintenance 를 먼저 돌린다.
- 먼저 preview 결과를 본다.

```powershell
.\venv\Scripts\python.exe scripts\run_order_maintenance.py `
  --trade-date YYYY-MM-DD `
  --timeout-seconds 300 `
  --output .\data\ops\YYYY-MM-DD\order_maintenance.preview.json
```

- 실제 반영이 필요하면 execute 로 다시 실행한다.

```powershell
.\venv\Scripts\python.exe scripts\run_order_maintenance.py `
  --trade-date YYYY-MM-DD `
  --timeout-seconds 300 `
  --execute `
  --output .\data\ops\YYYY-MM-DD\order_maintenance.execute.json
```

- 확인할 항목
- unresolved order 수
- safe sync 적용 여부
- stale cancel 대상 수
- manual recovery 필요 건 수

## 6. 장 마감 후 정리
- 장 마감 후에는 after-close 세션으로 15분봉 refresh, timing1 convergence, sell MACD 스캔을 순서대로 돌린다.
- 먼저 preview로 확인하고, 이상 없으면 write로 반영한다.

```powershell
.\venv\Scripts\python.exe scripts\run_after_close_session.py `
  --trade-date YYYY-MM-DD `
  --output .\data\ops\YYYY-MM-DD\after_close.preview.json
```

```powershell
.\venv\Scripts\python.exe scripts\run_after_close_session.py `
  --trade-date YYYY-MM-DD `
  --write `
  --output .\data\ops\YYYY-MM-DD\after_close.write.json
```

- 확인할 항목
- `session_outcome`
- `session_reason`
- `steps[].outcome`
- `steps[].reason`

- 주의할 점
- write 모드에서 `Refresh Intraday Bars 15m` 가 실패하면 `Scan Sell MACD Exit Signals` 는 자동으로 건너뛴다.
- 이 경우 stale bar 기준으로 잘못된 매도 신호를 저장하지 않기 위한 정상 방어 동작이다.

## 7. 체결 복구 절차
- 브로커 체결과 로컬 주문/포지션이 맞지 않으면 read-only workflow 로 먼저 진단한다.

```powershell
.\venv\Scripts\python.exe scripts\run_execution_recovery_workflow.py `
  --trade-date YYYY-MM-DD `
  --output .\data\ops\YYYY-MM-DD\execution_recovery.review.json `
  --draft-output .\data\ops\YYYY-MM-DD\execution_recovery.draft.json
```

- `draft-output` 파일을 검토하고 필요한 경우 별도 편집본으로 저장한 뒤 import 를 preview로 먼저 확인한다.

```powershell
.\venv\Scripts\python.exe scripts\import_manual_executions.py `
  --input .\data\ops\YYYY-MM-DD\execution_recovery.draft.edited.json `
  --output .\data\ops\YYYY-MM-DD\execution_recovery.import.preview.json
```

- preview 결과가 맞으면 execute 로 다시 실행한다.

```powershell
.\venv\Scripts\python.exe scripts\import_manual_executions.py `
  --input .\data\ops\YYYY-MM-DD\execution_recovery.draft.edited.json `
  --execute `
  --output .\data\ops\YYYY-MM-DD\execution_recovery.import.execute.json
```

## 8. JSON 결과에서 꼭 보는 값
- `startup_check.json`
- `outcome`
- `reason`

- `run_trading_session.*.json`
- `session_outcome`
- `session_reason`
- `preopen_result.readiness_outcome`
- `polling_result.stop_reason`

- `after_close.*.json`
- `session_outcome`
- `session_reason`
- `steps`

- `order_maintenance.*.json`
- unresolved order 수
- cancelled 수
- manual recovery 필요 수

- `execution_recovery.*.json`
- review item 수
- draft export 수
- import preview ready 수
- import acted 수

## 9. 운영 중단 기준
- startup 결과가 `READY` 가 아니면 장중 세션을 시작하지 않는다.
- `LOCK_BUSY` 가 나오면 기존 실행 중인 프로세스를 먼저 확인한다.
- `MAX_DAILY_LOSS_REACHED` 가 발생하면 신규 매수 재개 전에 손익 계산과 체결 누락 여부를 같이 확인한다.
- `KILL_SWITCH_ENABLED` 상태에서는 원인 확인 전까지 자동 실행을 재개하지 않는다.
- 수동 체결 복구를 execute 하기 전에는 preview JSON과 입력 파일을 다시 대조한다.

## 10. 권장 습관
- 같은 날짜의 모든 실행은 `.\data\ops\YYYY-MM-DD\` 아래에 저장한다.
- preview JSON을 남기고, execute JSON도 따로 남긴다.
- 장중 이상 상황이 있던 날은 kill switch note 와 recovery 파일명을 같이 남긴다.
- 실계좌 전환 전에는 이 runbook 을 mock 모드에서 여러 번 반복해 본다.
