# 작업 진행 기록

작업을 마칠 때나 중요한 결정/변경 사항이 생길 때마다 아래에 새 항목을 추가합니다.
최신 항목이 맨 위로 오도록 기록합니다.

---

## 2026-09-23 -- Phase 3 완료: 잔고 / 보유종목 / 매수가능금액 조회

- `broker/kis_client.py`에 `get_balance()` (`/trading/inquire-balance`,
  `tr_id=VTTC8434R`/`TTTC8434R`)와 `get_buying_power()`
  (`/trading/inquire-psbl-order`, `tr_id=VTTC8908R`/`TTTC8908R`) 추가.
  `market/data_collector.py`에 대응 래퍼 추가.
- `get_balance()`는 보유수량 0인 항목(당일 전량매도 등)을 필터링해서
  `holdings`로, 계좌 요약(예수금/평가금액 등)을 `summary`로 분리 반환.
- **버그 발견/수정:** 계좌번호를 8+2(10자리)로만 파싱하도록 만들었는데
  사용자가 실제로 가진 건 8자리 CANO뿐이었음 (`********`). KIS 표준
  주식계좌 상품코드 `01`을 기본값으로 써서 8자리만 와도 동작하도록 수정.
- 실제 모의투자 계좌로 검증: 예수금 9,997,251원, 보유종목 0개, 삼성전자
  매수가능금액 9,995,841원(27주, 미수 없음) / 최대 49,986,255원(139주).
- 테스트 13개(신규 5개 포함) 전부 mock 기반으로 통과.
- **Phase 3 종료.** 다음은 PHASE 4 (Telegram 알림).

## 2026-09-23 -- Phase 2 완료: 실제 KIS 모의투자 서버로 검증

- 사용자가 `.env`에 `KIS_PAPER_APP_KEY/APP_SECRET/ACCOUNT_NO`를 채운 뒤,
  실제 KIS 모의투자 서버(`openapivts.koreainvestment.com`)로 토큰 발급 +
  삼성전자(005930) 현재가 조회를 실행 -> 성공 (`stck_prpr=282000`).
- `KisClient.environment == "paper"`로 정상 감지됨. 토큰은
  `.kis_token_cache.json`에 캐시됨 (gitignored).
- **Phase 2 종료.** 다음은 PHASE 3 (잔고/보유종목/주문가능금액 조회).

## 2026-09-23 -- Phase 2: KIS 모의/실전 자격증명 분리

- `.env`에 `KIS_PAPER_APP_KEY/APP_SECRET/ACCOUNT_NO`와
  `KIS_REAL_APP_KEY/APP_SECRET/ACCOUNT_NO`를 별도 칸으로 분리.
- `config/settings.py`의 `Settings.resolve_kis_credentials()`가 두 세트 중
  어느 쪽이 완전히 채워졌는지에 따라 자동으로 모의/실전을 결정. 코드 변경 없이
  `.env`만 바꿔서 환경을 전환할 수 있음.
- 둘 다 채워지거나 둘 다 비어있으면 fail-closed로 `KisCredentialError` 발생
  (어느 쪽인지 추측하지 않음).
- `broker/kis_client.py`가 이 로직을 사용하도록 리팩터링, `KisClient.environment`
  / `is_real_trading` 프로퍼티 추가.
- **결정 사항 (사용자 확인 완료):** 이 전환 스위치는 지금은 현재가 조회에만
  영향을 준다. 실제 주문 실행 코드(`broker/order_executor.py`)와 Risk
  Manager(`risk/risk_manager.py`)는 아직 없으므로 `KIS_REAL_*`를 채워도 지금
  당장 실주문이 나갈 위험은 없음. Phase 6~8에서 주문 경로를 만들 때도 이
  환경 감지 로직을 재사용하되, 원래 스펙(섹션 8)의 fail-closed 원칙에 따라
  실전 주문 활성화 자체는 Phase 11 검증 이후로 미룬다.

## 2026-09-23 -- Phase 2: KIS 인증 + 현재가 조회

- `broker/kis_client.py`: OAuth 토큰 발급(`/oauth2/tokenP`) + 로컬 파일
  캐싱(만료 5분 전 자동 갱신, 재발급 시마다 오는 카카오톡 알림 방지),
  현재가 조회(`/uapi/domestic-stock/v1/quotations/inquire-price`,
  `tr_id=FHKST01010100`) 구현. 엔드포인트/응답 스키마는 한국투자증권 공식
  `open-trading-api` 저장소의 `examples_user`를 참고해 검증.
- `market/data_collector.py`에 `get_current_price()` 래퍼 추가.
- `tests/test_kis_client.py`: HTTP를 전부 mock 처리해서 실제 자격증명/네트워크
  없이 실행 가능 (`pytest` 8개 통과, 이후 9개로 증가).
- 참고: 공식 저장소에 `KIS Trading MCP` / `KIS Code Assistant MCP`도 포함되어
  있어 추후 MCP 연동 시 별도 설치 없이 재사용 가능.
- 커밋: `Phase 2: KIS auth + current price lookup`, push 완료.

## 2026-09-23 -- Phase 1: 프로젝트 기본 구조

- 저장소 `lammal01/lammal0921` 확인 -- 커밋 1개(README만)뿐인 빈 상태, 인증 없이
  clone되는 것으로 보아 Public 상태였음 -> 사용자 확인 후 Private로 전환.
- 이 머신(현재 세션)에 git 계정 정보/gh CLI/venv용 pip 모두 없었음. sudo가
  인터랙티브 인증을 요구해서 자동 실행이 막혀, 사용자가 직접 `!` 명령으로
  `gh auth login`을 실행해 인증 완료 (device-flow, 최초 시도는 백그라운드로
  넘어가며 유실되어 재시도함).
- `pip`/`ensurepip`가 없는 환경이라 `get-pip.py`로 venv 안에 sudo 없이
  부트스트랩. `gh` CLI도 apt 대신 GitHub 릴리즈 바이너리를 `~/.local/bin`에
  직접 설치 (root 권한 불필요).
- 섹션 14 구조대로 `agents/ broker/ market/ risk/ telegram/ memory/ models/
  config/ tests/` 패키지 + `main.py` 스캐폴드 생성 (전부 TODO 스텁).
- `telegram/` 패키지명이 `python-telegram-bot` 라이브러리의 import 이름과
  충돌 -> 해당 라이브러리 대신 `requests`로 Telegram Bot API 직접 호출하기로
  결정 (폴더 구조는 스펙대로 유지).
- `.gitignore`, `.env.example`, `pyproject.toml`, README(아키텍처/동기화
  절차/Phase 로드맵) 작성. `pytest`(3 skipped), `python main.py` 정상 동작 확인.
- 커밋: `Phase 1: project scaffold`, push 완료.

---

## 다음에 할 일 (Pending)

- 사용자가 `.env`의 `KIS_PAPER_APP_KEY/APP_SECRET/ACCOUNT_NO`를 채우면, 실제
  KIS 모의투자 서버로 현재가 조회를 1회 호출해 Phase 2를 최종 검증할 것.
- PHASE 3: 잔고 / 보유종목 / 주문가능금액 조회.
