# 작업 진행 기록

작업을 마칠 때나 중요한 결정/변경 사항이 생길 때마다 아래에 새 항목을 추가합니다.
최신 항목이 맨 위로 오도록 기록합니다.

---

## 2026-09-23 -- Phase 6 완료: Risk Manager

- **한도값 확인 (사용자 결정):** 코드에 임의로 숫자를 넣지 않고 먼저 질문 ->
  포지션 크기는 계좌 평가금액의 N%(기본 10%, `.env`로 조정), 일일 손실
  한도는 N% 초과 시 그날 신규 BUY 전면 차단(SELL은 허용, 기본 3%), 중복
  주문 방지는 시간 기반 쿨다운이 아니라 `decision_id` 기준 1회성 실행만
  허용하는 방식으로 결정.
- `risk/risk_manager.py`: `RiskManager.evaluate(decision, decision_id,
  account)` -- AI의 `confidence`/`risk_score`는 신뢰하지 않고
  `AccountState`(총평가금액/당일 시작 평가금액/현재가/보유수량)와
  `RiskLimits`만으로 승인/거부. HOLD는 항상 거부, SELL은 보유수량 초과 시
  거부, BUY는 포지션 한도·일일 손실 한도 체크. `engage_emergency_stop()`/
  `disengage_emergency_stop()`으로 비상 정지 추가 (코드가 스스로 풀지 않음).
  `mark_executed()`는 평가 시점이 아니라 `order_executor`가 실제로 주문을
  제출한 뒤에만 호출하도록 분리 -- 승인만 되고 미실행된 주문까지 중복으로
  오인해 영구 차단하지 않기 위함.
- `config/settings.py`에 `risk_max_position_pct`/`risk_daily_loss_limit_pct`
  추가 (`.env`의 `RISK_MAX_POSITION_PCT`/`RISK_DAILY_LOSS_LIMIT_PCT`, 기본값
  0.10/0.03). `.env.example`에 설명과 함께 추가.
- **범위 노트:** `RiskManager`는 KIS API를 직접 호출하지 않고
  `AccountState`를 그대로 받는 순수 로직 -- 실제 계좌 상태를 채워 넘기는
  일은 Phase 7(주문 흐름)의 몫. `day_start_equity` 영속 저장은 아직 없어
  프로세스 재시작 시 초기화됨 (Phase 9 Trade Memory에서 영속화 예정).
- 테스트 14개 추가(position/daily-loss/duplicate/emergency-stop/경계값 등),
  전체 53/53 통과 (order_executor 자리는 여전히 skip, Phase 7/8 몫).
- **Phase 6 종료.** 다음은 PHASE 7 (모의 BUY / SELL) -- `RiskManager`가
  승인한 주문만 `broker/order_executor.py`에서 실제 KIS 주문 API를 호출하게
  연결.

## 2026-09-23 -- Phase 5 완료: AI structured decision (OpenAI -> Claude API로 변경)

- **결정 사항 (사용자 요청):** 원래 스펙(섹션 2)은 OpenAI API였지만, 사용자가
  OpenAI 계정에 크레딧이 없어 "클로드 콘솔 API로 하면 안되나?"라고 요청 ->
  `claude-api` 스킬을 로드해 최신 API 모양(`output_config.format` 기반
  structured output, 모델 ID 등)을 확인한 뒤 OpenAI를 완전히 대체.
  OpenAI 관련 코드/의존성/`.env` 변수 전부 제거, `anthropic` SDK로 교체.
  (처음에 OpenAI로 구현했다가 크레딧 문제로 419/429 확인 후 전환한 것이라
  히스토리에는 OpenAI 버전 커밋도 남아있음.)
- `models/decision.py`: `Decision`/`Decision.from_dict()` -- action(BUY/SELL/HOLD),
  symbol, quantity(BUY/SELL은 0 초과 강제), confidence(0~1), reason(비어있지
  않은 문자열 리스트), 선택 필드(risk_score/stop_loss/take_profit/
  invalidation_condition) 검증. `DecisionParseError`로 실패 이유 명시.
- `agents/trading_agent.py`: `TradingAgent.decide()`가 `anthropic` SDK의
  `client.messages.create(..., output_config={"effort": "low", "format":
  {"type": "json_schema", ...}})`로 구조화된 JSON을 강제받고, 받은 값도
  `Decision.from_dict()`로 다시 검증 (스키마 통과 != 값이 맞다는 뜻 아님).
  기본 모델 `claude-opus-5`, 비용 절감을 위해 `effort="low"`.
- `ANTHROPIC_API_KEY`를 처음에 사용자가 콘솔에 표시된 축약형(`sk-ant-api03-...`
  형태, 23자)을 그대로 붙여넣어서 401 발생 -> 길이 확인해서 문제 파악 ->
  전체 키(108자) 재입력 후 정상 동작.
- 실제 삼성전자 시세로 라이브 검증: Claude가 데이터 부족을 스스로 인지하고
  `HOLD`, `confidence=0.55`, 근거에 "추세/이동평균 데이터 없음"을 정확히
  언급 -- structured output이 의도대로 작동함을 확인.
- 테스트 17개(decision parser 14개 + trading agent 6개, 기존 skip 스텁 대체)
  추가, 전체 38/38 통과 (mock 기반, API 키 불필요).
- **Phase 5 종료.** 다음은 PHASE 6 (Risk Manager) -- 스펙에서 "가장 중요한
  원칙"으로 강조한 부분이라 특히 신중하게 진행.

## 2026-09-23 -- Phase 4 완료: Telegram 알림

- `telegram/notifier.py`: `TelegramNotifier.send_message()`가 Telegram
  Bot HTTP API(`sendMessage`)를 `requests`로 직접 호출 (python-telegram-bot
  라이브러리 미사용, Phase 1의 `telegram/` 폴더명 충돌 결정과 일관됨).
  실패 시 `TelegramError`를 던지며, 이후 Phase에서 알림 실패가 매매 로직을
  막지 않도록 호출부에서 이 예외를 잡아야 함(섹션 13: Telegram 오류는 로그만).
- 결정/체결/거부용 메시지 템플릿(섹션 10)은 아직 안 만듦 -- `models.decision`/
  `models.order`가 실제로 생기는 Phase 5+ 에서 추가 (미리 모양 짐작 안 함).
- 사용자가 BotFather로 봇을 만들고 `.env`에 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
  입력 -> 실제 메시지 발송 테스트 -> 사용자가 수신 확인.
- 테스트 4개 추가(mock 기반), 전체 17/17 통과.
- **Phase 4 종료.** 다음은 PHASE 5 (AI structured decision).

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

- PHASE 7: 모의 BUY / SELL -- `broker/order_executor.py` 구현, `RiskManager`가
  승인한 주문만 KIS 주문 API로 전달. `models/order.py`의 Order/Fill 모델도
  이때 함께 정의 (Phase 8과 겹치는 부분은 실제로 필요해질 때 결정).
