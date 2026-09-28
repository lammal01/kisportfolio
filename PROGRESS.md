# 작업 진행 기록

작업을 마칠 때나 중요한 결정/변경 사항이 생길 때마다 아래에 새 항목을 추가합니다.
최신 항목이 맨 위로 오도록 기록합니다.

---

## 2026-09-28 -- Phase 11: 첫 비정상 종료 후 재시작

- 12:06 사이클 직후 Claude Code 업데이트로 루프/봇 프로세스가 함께 종료됨 (첫
  "예상치 못한 재시작" 사례).
- 재시작 전 DB 점검: 비상 정지/일시정지 없음, UNKNOWN 주문 없음, NAVER 4주
  (`0000024111`) 전량 체결 확인. 락 파일은 남아 있었지만 OS 락이라 재실행에 문제 없음.
- 12:13 루프+봇 재시작 -> 12:14 사이클에서 "오늘 첫 점검"을 반복하지 않고 5종목 모두
  신호 없음(AI 호출 0회) -- 상태가 DB에서 정상 복원됨을 확인.
- **참고:** 지금은 Claude Code 세션 안에서 띄운 프로세스라 세션/WSL이 종료되면 같이
  꺼짐. 며칠 연속 운영하려면 사용자가 별도 터미널에서 README의 `nohup` 명령으로
  실행하는 것이 안전.
- 미푸시 커밋 2개(Phase 10, 11) 있음 -- `git push` 필요.

## 2026-09-28 -- Phase 11 시작: 모의 매매 루프 구현 + 첫 자동 주문

- **사용자 결정:** 감시 종목 대형주 5개(삼성전자 005930, SK하이닉스 000660, 현대차
  005380, NAVER 035420, LG에너지솔루션 373220), 10분 간격, AI는 신호 있을 때만,
  하루 AI 호출 상한 50회. 모두 `.env`(`WATCHLIST`/`CYCLE_MINUTES`/
  `AI_DAILY_CALL_LIMIT`)로 조정 가능.
- `broker/kis_client.py`: `get_daily_prices()` (주식현재가 일자별, 최근 30거래일,
  수정주가). 당일 행이 없으면 휴장일.
- `market/indicators.py`: MA5/MA20(+전일), Wilder RSI14, 시간대 보정 거래량 비율,
  골든/데드크로스. `market/signal_filter.py`: 트리거 규칙 + 30분 쿨다운 + 일일 상한.
  상태는 전부 DB(`ai_calls` 테이블 신설 -- 실패한 AI 호출도 기록/집계)에서 읽음.
- `main.py --loop` (`PaperTradingLoop`): 장중 사이클, 비상 정지/일시정지 준수(상태
  변화만 알림), 종목별 오류 격리 + 알림 30분 1회, 장 마감 후 일일 요약, 휴장일 감지,
  실전 환경이면 시작 거부. **단일 실행 락** 추가 -- 루프 두 개가 동시에 돌면 주문이
  중복될 수 있으므로 OS 락(비정상 종료 시 자동 해제)으로 두 번째 실행 거부.
- AI 입력에 지표/호출 이유/`max_buy_value`(1회 최대 매수 금액) 추가 -- 한도 집행은
  여전히 Risk Manager.
- **라이브 첫 사이클 (12:05):** 5종목 모두 "오늘 첫 점검"으로 AI 호출. 4종목 HOLD,
  **NAVER 4주 매수를 AI가 스스로 결정** -> Risk Manager 승인(약 79만원 < 한도
  999,876원) -> KIS 접수 `0000024111` -> 체결 확인 4/4주 평균 198,625원 -> Telegram
  알림 -> DB 기록까지 전 과정 자동으로 동작. SK하이닉스는 1주 178만원이 포지션 한도
  (평가금액 10%, 약 100만원)를 넘어 **매수 자체가 불가** -- AI도 `max_buy_value`를 보고
  그렇게 판단함. 한도를 올릴지는 사용자 판단 사항.
- 테스트 27개 추가(지표 계산, 필터 규칙별, 쿨다운/상한, 루프 시간대/휴장일/상태 전환/
  오류 격리/일일 요약, 락), 전체 162/162 통과.
- **남은 일 (Phase 11 완료 조건):** 사용자 PC에서 루프+봇을 며칠간 계속 돌려 안정성
  확인 (오류/재시작/휴장일/일일 요약/한도 동작 관찰).

## 2026-09-28 -- Phase 10 완료: Telegram Control + 알림 한국어화

- `telegram/bot.py`: `python -m telegram.bot`로 따로 실행하는 long-polling 봇
  (`getUpdates`, 라이브러리 없이 `requests`). 명령: `/status` `/balance`
  `/positions` `/price` `/trades` `/decision` `/pause` `/resume` `/stop`
  `/release_stop` `/help`. main.py와는 SQLite(Trade Memory)로만 상태 공유.
- **안전 설계:**
  - `TELEGRAM_CHAT_ID` 외 채팅은 무시 (응답 없음).
  - 봇 시작 전에 보낸 메시지는 버림 -- 실제로 시작하자마자 사용자가 미리 보낸
    "잔고"가 버려지는 것 확인.
  - `/stop`은 영속 비상 정지. `RiskManager.evaluate()`가 매 평가 직전에 저장소를
    다시 읽어, 다른 프로세스에서 이미 돌고 있는 사이클도 다음 주문 전에 차단됨
    (다시 읽기는 정지를 **걸기만** 하고 절대 풀지 않음, 읽기 실패 = 정지).
  - `/release_stop`은 1회용 6자리 확인 코드를 60초 안에 다시 보내야 해제 (틀리면
    코드 폐기).
  - `/resume`은 비상 정지 중이면 거부, KIS 조회 점검 통과해야 재개.
  - 일시정지(`pause` 테이블)는 새 사이클만 막는 소프트 정지, `run_once`가 확인.
- **버그 수정 (보안):** Telegram 전송 중 네트워크 오류가 나면 `requests` 예외가 그대로
  올라왔는데, 그 메시지에 **봇 토큰이 포함된 URL**이 들어 있어 로그에 토큰이 찍힐 수
  있었고, `main._notify`가 `TelegramError`만 잡아서 주문 후 알림 단계에서 프로그램이
  죽을 수도 있었음 -> 모든 오류를 `TelegramError`로 감싸고 토큰을 가림.
- **한국어화 (사용자 요청):** 매매 사이클 알림(`[주문 접수]`/`[리스크 거부]`/
  `[증권사 거부]`/`[결과 불명]`/`[관망]`, 체결), Risk Manager 거부 사유, AI 판단
  근거(시스템 프롬프트에 한국어 작성 지시)를 한국어로. 내부 상태값/로그/DB는 영어
  유지. 명령어는 텔레그램 규칙상 영어(`/status` 등) -- 한글 별칭은 이번에 선택하지 않음.
- 라이브 검증: 모든 명령을 실제 KIS/DB/Telegram으로 점검 (응답은 사용자 텔레그램으로
  전송). `/resume`이 KIS 모의 서버 타임아웃으로 한 번 **거부됨** -> 점검 로직이 의도대로
  동작, 재시도 시 통과. 화이트리스트 외 채팅 무시 확인. 실제 사이클 알림과 AI 근거가
  한국어로 오는 것 확인.
- 테스트 22개 추가, 전체 135/135 통과. **Phase 10 종료.** 다음은 PHASE 11
  (장시간 Paper Trading).

## 2026-09-28 -- Phase 9 완료: Trade Memory (SQLite)

- `memory/database.py`: 스키마 -- `decisions`(AI 판단+스냅샷), `orders`(모든
  OrderResult, Risk 거부 포함), `fills`(주문번호별 최신 체결 상태, SELL 실현손익),
  `daily_equity`, `executed_decisions`, `emergency_stop`. 표준 라이브러리 `sqlite3`만
  사용, 파일은 `TRADE_DB_PATH`(기본 `trade_memory.sqlite3`, gitignored, PC별).
- `memory/trade_memory.py`: `TradeMemory` -- 기록 + `recent_trades(symbol)` (같은
  종목 최근 5건 요약만, 원본 스냅샷 제외 -> AI 프롬프트용) + `day_start_equity()`
  (그날 첫 값 고정, 재시작해도 손실 기준 유지).
- **안전 개선:** 비상 정지가 지금까지 메모리에만 있어서 **재시작하면 풀렸음**
  (UNKNOWN 주문 뒤 재시작 = 확인 없이 매매 재개). `RiskManager`에
  `RiskStateStore` 프로토콜을 두고 Trade Memory가 구현 -> 실행된 `decision_id`와
  비상 정지가 재시작 후에도 유지. 저장 실패 시 예외 대신 즉시 비상 정지(주문 직후일
  수 있으므로), 로드 실패 시 시작 자체를 거부, 해제는 저장소를 먼저 지워 실패하면
  정지 유지. 해제는 `python main.py --release-emergency-stop`(사람이 직접)으로만.
- `AccountState.avg_cost`(매입평균가, 한도 계산엔 미사용) 추가 -> SELL 실현손익
  기록용.
- `main.py --once`: 비상 정지 중이면 AI 호출 없이 건너뜀(비용 절약), 판단은 주문
  **전에** 기록(실패하면 주문 안 나감), 주문/체결 기록 실패는 알림에 경고로 포함.
  AI 스냅샷에 `recent_trades`/`avg_cost` 추가, 시스템 프롬프트에 "과거 판단은
  참고일 뿐 반복 이유가 아님" 명시.
- 라이브 검증: `--once` 2회 -> 두 판단(HOLD) 모두 DB 기록, 당일 시작 평가금액
  9,998,763원 기록, 두 번째 호출의 AI 입력에 첫 판단이 `recent_trades`로 포함됨.
- 테스트 17개 추가(재시작 후 decision_id/비상 정지 유지, 저장 실패 시 비상 정지,
  시작 기준값 고정, 실현손익, run_once 전체 흐름 등), 전체 113/113 통과.
- **Phase 9 종료.** 다음은 PHASE 10 (Telegram Control -- `/status`, `/balance`,
  `/pause`, `/resume` 등).

## 2026-09-28 -- Phase 8 완료: 주문/체결 확인 및 중복주문 방지

- `broker/kis_client.py`: `get_daily_orders()` -- 주식일별주문체결조회
  (`inquire-daily-ccld`, 모의 `VTTC0081R`/실전 `TTTC0081R`). 모의는 페이지당
  15건이라 `tr_cont` 헤더로 끝까지 따라가고, 20페이지 넘으면 잘린 목록 대신 에러.
  모든 호출에 최소 간격(throttle, 모의 0.5초) 추가. 조회(GET)는 초당 한도 초과/
  네트워크 오류 시 재시도, 주문은 절대 재시도 안 함.
- `models/order.py`: `OrderFill` (FILLED/PARTIALLY_FILLED/PENDING/CANCELLED/
  REJECTED, 체결수량/평균가), 정정·취소 기록 행 제외(`is_original_order`),
  `OrderResult.requested_at` 추가.
- `broker/order_executor.py`: `confirm_fill()` (체결될 때까지 폴링),
  `reconcile_unknown()` (UNKNOWN 주문을 당일 주문 목록에서 찾기 ->
  PLACED/NOT_FOUND/AMBIGUOUS). **비상 정지는 자동 해제하지 않음** (사람이 해제).
- `risk/risk_manager.py`: `AccountState.open_order_count` -- 해당 종목 KIS
  미체결 주문이 있으면 새 주문 거부, 모르면(None) 거부. 재시작해도 유지되는
  중복 방지 (decision_id 기록은 메모리뿐이라 재시작 시 사라짐).
- `main.py --once`: 접수 후 체결 확인, UNKNOWN이면 대사 결과를 Telegram에 포함.
  조회 실패해도 알림은 나가도록 처리.
- **실서버에서 실제로 겪은 일 (모의 서버가 불안정했음):**
  1. 11:09:50 BUY 요청이 `ConnectTimeout` -> UNKNOWN + 비상 정지. 대사 결과
     NOT_FOUND, 보유 0 -> 실제로 미접수. 연결 자체가 안 된 경우는 요청이 전송되지
     않은 것이므로 이후 `ConnectTimeout`만 확실한 미접수(`REJECTED_BY_BROKER`)로
     분류하도록 변경 (연결 후 응답 유실은 계속 UNKNOWN).
  2. 11:11:29 BUY 요청이 `ReadTimeout` -> UNKNOWN. 5초 뒤 대사 결과 NOT_FOUND
     였지만 **보유 1주** -- KIS가 31초 뒤(11:12:00)에야 주문 `0000021315`를 접수.
     -> NOT_FOUND는 미접수의 증거가 아님이 실제로 확인됨 (비상 정지 자동 해제를
     안 넣은 이유). 대사 시간창을 ±60초에서 "요청 60초 전 이후 전부"로 넓힘
     (UNKNOWN 뒤엔 비상 정지로 이 프로그램이 추가 주문을 못 내므로 혼동 없음).
     재대사 결과 PLACED `0000021315` 정상 식별.
  3. **버그 수정:** 조회(GET)의 네트워크 예외가 `KisApiError`로 감싸지지 않아
     주문 후 체결 확인 중 타임아웃 나면 알림 없이 죽을 수 있었음 -> 수정.
- 남은 1주는 SELL `0000021381` -> `confirm_fill` 결과 FILLED 1/1 @ 274,500,
  보유 0 확인. 모의계좌 총평가 9,998,763원.
- 테스트 25개 추가, 전체 96/96 통과. **Phase 8 종료.** 다음은 PHASE 9
  (Trade Memory -- SQLite에 판단/주문/체결 기록, `day_start_equity`·실행된
  `decision_id` 영속화).

## 2026-09-28 -- Phase 7 완료: 실제 모의 BUY / SELL 검증

- **블로커 해결 (아래 항목):** 사용자가 KIS에서 모의투자를 재신청하고 새
  앱키를 `.env`에 저장 (처음엔 에디터에서 저장을 안 해서 옛 키로 실행됨 ->
  파일 수정 시각으로 확인). 새 모의계좌 평가금액 10,000,000원.
- **버그 수정:** 토큰 캐시가 `base_url`만 비교해서, 앱키를 바꿔도 옛 앱키의
  토큰을 재사용 -> "기간이 만료된 token" 에러. 캐시에 앱키 SHA-256 지문(원본
  키는 저장 안 함)을 함께 저장해 앱키가 바뀌면 재발급하도록 수정 + 테스트 추가.
- `OrderExecutor` 경유 실검증 (삼성전자 1주 시장가):
  BUY `SUBMITTED` 주문번호 0000020558 (11:02:38) -> 보유 1주 확인 ->
  SELL `SUBMITTED` 주문번호 0000020599 (11:03:04) -> 보유 0주 확인.
  평가금액 10,000,000 -> 9,999,142원 (수수료/스프레드).
- **발견:** 모의투자 서버의 초당 호출 한도가 매우 낮아, 연속 조회 시
  `초당 거래건수를 초과` 에러가 남 (조회라 재시도로 해결). Phase 11 연속 루프
  전에 호출 간 간격(throttle)이 필요. 주문 API는 이 경우 `rt_cd != 0`이므로
  `REJECTED_BY_BROKER`로 분류됨(확실히 미접수) -- 동작상 문제 없음.
- 전체 테스트 71/71 통과. **Phase 7 종료.** 다음은 PHASE 8 (주문/체결 확인).

## 2026-09-28 -- Phase 7: 모의 BUY / SELL (코드 완료, 실 주문 검증 대기)

- `broker/order_executor.py`: `OrderExecutor.submit(decision, decision_id,
  account)`가 주문의 유일한 경로. 내부에서 `RiskManager.evaluate()`를 먼저
  호출하므로 Risk Manager를 건너뛰는 공개 경로가 없음. 결과는
  `models/order.py`의 `OrderResult` (`SUBMITTED` / `REJECTED_BY_RISK` /
  `REJECTED_BY_BROKER` / `UNKNOWN`). `SUBMITTED`는 접수일 뿐 체결 아님(Phase 8).
- **안전 결정:** 타임아웃/응답 유실/해석 불가 응답은 `UNKNOWN` -> 해당
  `decision_id` 잠금 + **비상 정지 자동 발동** (주문이 들어갔을 수도 있으므로
  사람이 확인하기 전까지 추가 주문 금지). 자동 재시도 없음. KIS가 명시적으로
  거부한 경우만 재시도 허용.
- **안전 결정:** `OrderExecutor`는 실전 `KisClient`로 생성 시
  `RealTradingDisabledError` -- `.env`에 `KIS_REAL_*`를 채워도 주문은 안 나감.
  Phase 11 검증 후 해제 예정.
- `broker/kis_client.py`: `place_cash_order()` (`order-cash`, 모의
  `VTTC0012U`/`VTTC0011U`, 실전 `TTTC0012U`/`TTTC0011U`, 기본 시장가). 공식
  `open-trading-api`의 `order_cash` 예제 기준. `KisOrderUncertainError` 추가.
- `risk/risk_manager.py`: `AccountState`에 `symbol` 추가, AI 결정의 종목과
  다르면 거부 (AI가 요청과 다른 종목을 답할 경우 대비).
- `market/data_collector.py`: `build_account_state(symbol)` -- 실제
  잔고(`tot_evlu_amt`, 보유수량)/현재가로 `AccountState` 생성. 값이 없으면
  0으로 채우지 않고 에러.
- `main.py --once SYMBOL`: 스냅샷 -> AI 판단 -> OrderExecutor -> Telegram
  한 사이클. 라이브 실행 결과: Claude가 HOLD 판단 -> Risk Manager 거부 ->
  알림 발송까지 정상 동작.
- 테스트 17개 추가(타임아웃/응답 유실/중복 decision_id/브로커 거부/실전 차단/
  종목 불일치 등), 전체 70/70 통과.
- **블로커:** 실제 모의 주문(삼성전자 1주 시장가 BUY)이 KIS에서
  `40910000 모의투자 주문이 불가한 계좌입니다`로 거부됨. 장중(10:45, 거래량
  정상)이고 구/신 tr_id(`VTTC0802U`/`VTTC0012U`) 모두 같은 응답 -> 코드가 아닌
  계좌 쪽 문제로 판단. 같은 계좌로 잔고 조회는 정상. 사용자가 KIS 홈페이지에서
  모의투자 신청 상태(기간 만료/재신청)와 앱키에 연결된 모의 계좌번호를 확인해야 함.

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

- PHASE 11 장기 검증: 사용자 PC에서 `main.py --loop` + `telegram.bot`을 며칠간 실행하며
  관찰. 매일 일일 요약 확인.
- 결정 필요: SK하이닉스처럼 1주 가격이 포지션 한도(평가금액 10%)를 넘는 종목은 매수
  불가 -- 한도 조정 또는 감시 종목 교체 여부.
- 주문 취소 기능 없음 -- 지정가 주문을 쓰게 되면 필요. 현재는 시장가만 사용.
- HOLD 판단도 `orders`에 REJECTED_BY_RISK로 기록됨 -- 분석(Phase 12) 때 구분 필요.
- PHASE 12: 백테스트 및 거래 결과 분석.
