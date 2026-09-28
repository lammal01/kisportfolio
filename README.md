# KIS AI Trading Agent

AI가 한국투자증권(KIS) 시장 데이터를 관찰·분석해 BUY/SELL/HOLD를 판단하고,
**독립적인 Risk Manager 검증을 통과한 주문만** 실제로 실행하는 자동매매 시스템.

> ⚠️ `.env`는 항상 `KIS_PAPER_*` (모의투자)만 채운 상태로 시작해야 합니다.
> `KIS_REAL_*` (실전투자)는 아래 Phase 로드맵의 각 단계가 충분히 검증된 뒤에만 채웁니다.
> 자세한 전환 방법은 "모의투자 ↔ 실전투자 전환" 절 참고.

작업 진행 기록은 [`PROGRESS.md`](./PROGRESS.md)에서 확인할 수 있습니다.

## 아키텍처

```
Market Data
  -> Data Collector
  -> Signal / Event Filter          (Python으로 먼저 계산: MA/RSI/MACD/거래량 등)
  -> AI Trading Agent                (LLM 호출은 여기서만, 필요할 때만)
  -> BUY / SELL / HOLD (structured)
  -> Risk Manager                    (AI와 독립적인 deterministic code)
  -> Order Executor
  -> KIS Open API
  -> 체결 확인 (UNKNOWN이면 대사 + 비상 정지 유지)
  -> Trade Memory / Database
  -> Telegram Notification
```

AI Agent와 실제 주문 실행 모듈은 항상 분리되어 있고, AI가 Risk Manager를 우회하거나
수정할 수 있는 경로는 없습니다. Risk Manager가 거부한 주문은 Broker로 전달되지 않습니다.

## 프로젝트 구조

```
agents/     trading_agent.py            AI 판단 (structured decision)
broker/     kis_client.py                KIS API 인증/조회
            order_executor.py            주문 실행 (Risk Manager 승인 후에만 호출)
market/     data_collector.py            시세/잔고/보유종목 등 수집
            indicators.py                MA/RSI/MACD 등 결정론적 지표
            signal_filter.py             LLM 호출 여부 필터링 (비용 절감)
risk/       risk_manager.py              AI와 독립적인 주문 검증/승인/거부
telegram/   bot.py                       /status /pause /resume 등 제어 명령
            notifier.py                  체결/판단/거부 알림 발송
memory/     database.py                  SQLite 연결/스키마
            trade_memory.py              AI 판단과 결과 저장/조회
models/     decision.py                  AI structured output 모델
            order.py                     주문/체결 모델
config/     settings.py                  .env 기반 설정 (fail-closed)
tests/      test_risk_manager.py, test_decision_parser.py, test_order_executor.py
main.py     엔트리 포인트 (`--once SYMBOL`로 한 사이클 실행)
```

**설계 노트:** `telegram/` 패키지가 `python-telegram-bot` 라이브러리의 import 이름과
겹치므로, 해당 라이브러리 대신 `requests`로 Telegram Bot API를 직접 호출합니다.

## 데스크탑 + 노트북 동기화

GitHub(`lammal01/lammal0921`, private)를 Single Source of Truth로 사용합니다.

```
Desktop  <->  GitHub  <->  Laptop
```

새 컴퓨터에서 환경을 복구하는 절차:

1. **Clone**
   ```bash
   git clone https://github.com/lammal01/lammal0921.git
   cd lammal0921
   ```
2. **Python 가상환경 생성**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate        # Windows(PowerShell): .venv\Scripts\Activate.ps1
   ```
   - Ubuntu/Debian 계열에서 `python3 -m pip`가 없다면(`No module named pip`),
     `sudo apt-get install python3-pip python3-venv`로 설치하거나,
     sudo를 쓸 수 없는 환경이면 `python3 get-pip.py`로 venv 안에 직접 부트스트랩합니다
     ([get-pip.py](https://bootstrap.pypa.io/get-pip.py)).
3. **의존성 설치**
   ```bash
   pip install -e ".[dev]"
   ```
4. **환경변수 설정** (PC마다 개별적으로, 절대 git에 올리지 않음)
   ```bash
   cp .env.example .env
   # .env를 열어 이 PC에서 쓸 API Key/Secret/계좌번호/Telegram Token을 채운다
   ```
5. **GitHub 인증** (push용, 최초 1회)
   ```bash
   gh auth login
   ```
6. **(선택) MCP 설정** — KIS Trading MCP / KIS Code Assistant MCP / GitHub MCP가
   사용 가능해지면 필요에 따라 연동. 없어도 REST/WebSocket 기반으로 동작하도록
   핵심 로직은 특정 MCP에 결합시키지 않습니다.
7. **테스트 실행**
   ```bash
   pytest
   ```
8. **Paper Trading 실행** (한 사이클: 판단 → Risk Manager → 모의 주문)
   ```bash
   python main.py --once 005930
   ```

## KIS 공식 Toolkit

한국투자증권 공식 `open-trading-api` 저장소(https://github.com/koreainvestment/open-trading-api)의
`examples_user/kis_auth.py`, `examples_user/domestic_stock/domestic_stock_functions.py`를
참고해 엔드포인트/`tr_id`/응답 스키마(`rt_cd`, `output` 등)를 그대로 따르되,
`broker/kis_client.py`는 pandas/pycryptodome 등 무거운 의존성 없이 `requests`만으로
최소 구현했습니다. 참고: 이 공식 저장소에는 `KIS Trading MCP` / `KIS Code Assistant MCP`도
포함되어 있어 (섹션 17), 추후 MCP 연동 시 별도 설치 없이 이 저장소를 그대로 쓸 수 있습니다.

**Phase 2 구현 범위:** OAuth 토큰 발급/캐싱(`/oauth2/tokenP`), 현재가 조회
(`/uapi/domestic-stock/v1/quotations/inquire-price`, `tr_id=FHKST01010100`).
토큰은 재발급 시마다 카카오톡 알림이 발송되므로 `.kis_token_cache.json`
(gitignored)에 캐시해 만료 직전까지 재사용합니다.

**Phase 3 구현 범위:** 주식잔고조회(`/uapi/domestic-stock/v1/trading/inquire-balance`,
`tr_id=VTTC8434R`/`TTTC8434R`)로 보유종목(수량 0 제외)과 계좌 요약(예수금/평가금액)을,
매수가능조회(`/uapi/domestic-stock/v1/trading/inquire-psbl-order`,
`tr_id=VTTC8908R`/`TTTC8908R`)로 매수가능금액/수량을 조회합니다. 계좌번호는
8자리(CANO)만 입력해도 상품코드 `01`(표준 주식계좌)이 자동으로 붙습니다.

## Telegram

`telegram/notifier.TelegramNotifier.send_message()`가 Telegram Bot HTTP API
(`sendMessage`)를 직접 호출합니다. Bot 생성/토큰 발급:

1. Telegram에서 **@BotFather** → `/newbot` → 토큰 발급 (`TELEGRAM_BOT_TOKEN`)
2. 만든 봇에게 아무 메시지나 먼저 보내기 (봇이 나에게 먼저 말 걸 수 없음)
3. `https://api.telegram.org/bot<TOKEN>/getUpdates`에서 `chat.id` 확인 (`TELEGRAM_CHAT_ID`)

결정/체결/거부 알림용 메시지 템플릿(섹션 10)은 `models.decision`/`models.order`가
실제로 생기는 Phase 5 이후에 추가합니다 -- 아직 없는 데이터 모양을 미리 짐작해서
만들지 않습니다.

## AI Trading Agent

**참고: 원래 스펙(섹션 2)은 OpenAI API를 지정했지만, 사용자 요청으로 Claude API
(Anthropic)로 변경했습니다.** `agents/trading_agent.TradingAgent`가
`anthropic` SDK의 `client.messages.create(..., output_config={"format":
{"type": "json_schema", ...}})`로 구조화된 JSON만 반환하도록 강제하고,
`models/decision.py`의 `Decision.from_dict()`가 그 값을 다시 한번 의미
검증합니다 (스키마를 통과해도 값 자체가 이상할 수 있으므로 그대로 신뢰하지
않음). 모델은 기본적으로 `claude-opus-5`, `output_config.effort="low"`로
호출해 비용을 억제합니다 (섹션 6 비용 최적화 원칙).

`ANTHROPIC_API_KEY`는 console.anthropic.com에서 발급받은 별도의 Console API
키가 필요합니다 (Claude Code 로그인과는 별개, 자체 결제/크레딧 필요).

AI Agent는 `broker.order_executor`를 직접 호출하지 않습니다 -- 출력은 항상
`risk.risk_manager`를 거쳐야 합니다.

## Risk Manager

`risk.risk_manager.RiskManager`가 AI의 `Decision`을 실행 가능 여부로 바꾸는
유일한 경로입니다. AI 출력(`confidence`/`risk_score`)은 신뢰하지 않고, 오직
호출자가 넘기는 `AccountState`(계좌 총평가금액/당일 시작 평가금액/현재가/
보유수량)와 아래 결정론적 한도만으로 승인·거부합니다.

- **포지션 크기 한도:** 1회 BUY 주문금액이 계좌 총평가금액의
  `RISK_MAX_POSITION_PCT`(기본 10%)를 넘으면 거부.
- **일일 손실 한도:** 당일 평가금액이 장 시작 대비 `RISK_DAILY_LOSS_LIMIT_PCT`
  (기본 3%) 이상 하락하면 그날 신규 BUY를 전면 차단 (기존 보유분 정리를 위한
  SELL은 계속 허용).
- **중복 주문 방지:** `decision_id` 기준 1회성 실행만 허용 -- 같은
  `decision_id`로 두 번째 `evaluate()`를 호출하면 거부. 승인만 되고 실제
  실행은 안 된 경우까지 영구 차단하지 않도록, 기록은 평가 시점이 아니라
  `order_executor`가 주문을 실제로 제출한 뒤 `mark_executed()`를 호출한
  시점에 남습니다.
- **비상 정지:** `engage_emergency_stop(reason)` 호출 시 그 뒤의 모든 주문을
  즉시 차단. 코드가 스스로 풀지 않고 `disengage_emergency_stop()`을 명시적으로
  호출해야만 해제됩니다.

한도값은 코드를 건드리지 않고 `.env`의 `RISK_MAX_POSITION_PCT` /
`RISK_DAILY_LOSS_LIMIT_PCT`로 조정합니다.

`RiskManager`는 KIS API를 직접 호출하지 않습니다 -- `AccountState`를 채우는
것은 호출자(Phase 7 주문 흐름)의 몫이며, `day_start_equity`의 영속 저장은
아직 없어 프로세스가 재시작되면 초기화됩니다 (Phase 9 Trade Memory에서
영속화할 예정). 이 모듈을 우회해 `broker.order_executor`를 직접 호출하는
경로는 존재해서는 안 됩니다.

## Order Executor (Phase 7)

`broker.order_executor.OrderExecutor.submit(decision, decision_id, account)`가
주문을 내는 유일한 경로입니다. 내부에서 먼저 `RiskManager.evaluate()`를 호출하고,
승인된 경우에만 KIS 주식주문(현금) API(`/uapi/domestic-stock/v1/trading/order-cash`,
모의 `VTTC0012U` 매수 / `VTTC0011U` 매도, 시장가)를 호출합니다. "이미 승인된 주문"을
받는 공개 메서드는 없어서 Risk Manager를 건너뛸 수 없습니다.

결과(`models.order.OrderResult.status`):

- `SUBMITTED` -- KIS가 주문번호(ODNO)를 돌려줌. **접수이지 체결이 아님** (체결 확인은 Phase 8).
- `REJECTED_BY_RISK` -- Risk Manager 거부, KIS로 전달 안 됨.
- `REJECTED_BY_BROKER` -- KIS가 명시적으로 거부(`rt_cd != '0'`). 확실히 미체결이라
  같은 `decision_id` 재시도 허용.
- `UNKNOWN` -- 타임아웃/응답 유실/해석 불가. 주문이 들어갔을 수도 있으므로 해당
  `decision_id`를 실행된 것으로 잠그고 **비상 정지를 건다** (사람이 계좌를 확인한 뒤
  해제). 자동 재시도는 절대 하지 않음. HTTP 200만으로는 성공으로 보지 않음.

`AccountState`는 `market.data_collector.build_account_state(symbol)`가 실제 잔고/현재가로
채웁니다. `AccountState.symbol`과 AI 결정의 `symbol`이 다르면 Risk Manager가 거부합니다.

한 사이클 실행 (스냅샷 → AI 판단 → Risk Manager → 주문 → Telegram):

```bash
python main.py --once 005930
```

`day_start_equity`는 아직 영속 저장이 없어 실행 시점의 총평가금액을 씁니다 (Phase 9).

## 주문/체결 확인 (Phase 8)

- **체결 확인:** `OrderExecutor.confirm_fill(result)`가 KIS 주식일별주문체결조회
  (`inquire-daily-ccld`, 모의 `VTTC0081R`)를 폴링해 `models.order.OrderFill`
  (`FILLED` / `PARTIALLY_FILLED` / `PENDING` / `CANCELLED` / `REJECTED`, 체결수량,
  평균체결가)을 돌려줍니다. 목록은 끝까지 페이지를 따라가고, 페이지 한도를 넘으면
  잘린 목록 대신 에러를 냅니다.
- **UNKNOWN 대사:** `OrderExecutor.reconcile_unknown(result)`가 당일 주문 중
  같은 종목/방향/수량이고 요청 시각(-60초) 이후에 접수된, 아직 모르는 주문번호를
  찾아 `PLACED` / `NOT_FOUND` / `AMBIGUOUS`로 알려줍니다. **비상 정지는 절대
  자동으로 풀지 않습니다** -- 실제로 KIS가 타임아웃된 주문을 31초 뒤에 접수한
  사례가 있어, `NOT_FOUND`는 "안 들어갔다"는 증거가 아닙니다.
- **재시작에도 안전한 중복 방지:** `AccountState.open_order_count`(해당 종목 KIS
  미체결 주문 수)가 0보다 크면 Risk Manager가 새 주문을 거부합니다. 확인하지
  못했으면(`None`) 역시 거부. `decision_id` 기록은 메모리에만 있지만 브로커의
  미체결 목록은 재시작 후에도 남습니다.
- **네트워크 오류 분류:** 주문 요청에서 `ConnectTimeout`(연결 자체가 안 됨 = 요청이
  전송되지 않음)은 확실한 미접수(`REJECTED_BY_BROKER`), 연결 후 응답 유실
  (`ReadTimeout` 등)은 `UNKNOWN`. 조회(GET)는 멱등이라 네트워크 오류/초당 한도
  초과 시 재시도하고, 주문은 절대 재시도하지 않습니다.
- **호출 간격 제한:** 모의투자 서버는 초당 허용 건수가 낮아 모든 KIS 호출 사이에
  최소 간격(모의 0.5초 / 실전 0.05초)을 둡니다.

### 모의투자 ↔ 실전투자 전환

`.env`에 `KIS_PAPER_*`와 `KIS_REAL_*` 두 칸이 모두 있습니다. **정확히 한쪽만
채워야** 하며, 어느 쪽이 채워져 있는지에 따라 `config/settings.py`가 자동으로
서버(모의/실전)를 결정합니다 (`Settings.resolve_kis_credentials`).

- 처음엔 `KIS_PAPER_APP_KEY/APP_SECRET/ACCOUNT_NO`만 채워서 모의투자로 검증합니다.
- 이후 실전 전환 시엔 **코드를 건드릴 필요 없이** `KIS_PAPER_*`를 비우고
  `KIS_REAL_*`를 채우기만 하면 됩니다.
- 두 세트를 동시에 채우거나 둘 다 비워두면 fail-closed로 에러를 던집니다
  (어느 쪽인지 추측하지 않음).

⚠️ Phase 7부터 주문 경로(`broker/order_executor.py`)가 존재하지만, **실전 주문은
코드에서 막혀 있습니다**: `OrderExecutor`는 실전 환경의 `KisClient`로 생성하려고 하면
`RealTradingDisabledError`를 던집니다. `KIS_REAL_*`를 채워도 조회만 실전 서버로 가고
주문은 나가지 않습니다. 이 차단은 Phase 11(장시간 Paper Trading 검증) 이후 별도
안전장치와 함께 해제합니다.

## 보안

- `.env`는 `.gitignore`에 포함되어 있으며 **절대 커밋하지 않습니다**.
- `.env.example`에는 변수명만 존재하고 실제 값은 없습니다.
- 저장소는 Private으로 유지합니다.
- 로그에는 API Secret/Token을 기록하지 않습니다.
- Telegram 제어 명령은 chat ID whitelist로만 허용합니다.
- 실전 거래는 fail-closed로 설계합니다: API Key 누락, 설정 오류, Risk Manager
  오류, AI 응답 파싱 실패, 네트워크 상태 불확실, 중복 주문 가능성 등 어떤
  경우에도 기본값은 "주문 금지"입니다.

## Phase 로드맵

| Phase | 내용 |
|---|---|
| 1 | GitHub Repository 및 프로젝트 기본 구조 (완료) |
| 2 | KIS 모의투자 인증 및 현재가 조회 (완료, 실 서버 검증 완료) |
| 3 | 잔고 / 보유종목 / 주문가능금액 조회 (완료, 실 서버 검증 완료) |
| 4 | Telegram 알림 (완료, 실제 메시지 발송 검증 완료) |
| 5 | AI structured decision (완료, Claude API 실제 호출 검증 완료) |
| 6 | Risk Manager (완료, 단위 테스트로 검증 -- 실제 주문 경로 연결은 Phase 7) |
| 7 | 모의 BUY / SELL (완료, 실제 모의 매수/매도 검증 완료) |
| 8 | 주문/체결 확인 및 중복주문 방지 (완료, 실제 모의 주문/타임아웃으로 검증) |
| 9 | Trade Memory |
| 10 | Telegram Control |
| 11 | 장시간 Paper Trading |
| 12 | 백테스트 및 거래 결과 분석 |

실전 거래 기능은 위 단계가 안정적으로 검증되기 전까지 활성화하지 않습니다.
