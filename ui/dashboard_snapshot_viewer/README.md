# Dashboard Snapshot Viewer

프론트 초기 검증용 정적 뷰어입니다.

## 목적
- `dashboard_snapshot.json` 계약을 브라우저에서 바로 확인
- React 도입 전, 백엔드 상태 구조가 UI에서 읽기 좋은지 빠르게 검증

## 사용 방법
1. snapshot 생성
   - `.\venv\Scripts\python.exe scripts\build_dashboard_snapshot.py --trade-date 2026-04-20 --ops-dir .\data\ops\2026-04-20 --output .\data\ops\2026-04-20\dashboard_snapshot.json`
2. [index.html](C:/python/auto_trader_v2/ui/dashboard_snapshot_viewer/index.html) 열기
3. `JSON 파일 열기` 버튼으로 `dashboard_snapshot.json` 선택

## 현재 범위
- 파일 업로드 기반 로컬 뷰어
- overview / controls / scan / executions / recovery / rehearsal / actions 표시
- 샘플 snapshot 기본 내장

## 다음 단계 후보
- React 대시보드로 이관
- snapshot auto-refresh
- kill switch / 전략 선택 UI 연결
