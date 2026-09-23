# KIS AI Trading Agent

AI가 한국투자증권(KIS) 시장 데이터를 관찰·분석해 BUY/SELL/HOLD를 판단하고,
**독립적인 Risk Manager 검증을 통과한 주문만** 실제로 실행하는 자동매매 시스템.

> ⚠️ 이 저장소는 기본적으로 `PAPER_TRADING=true` (모의투자) 상태로만 동작해야 합니다.
> 실전 거래는 아래 Phase 로드맵의 각 단계가 충분히 검증된 뒤에만 활성화합니다.

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
  -> 체결 확인
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
main.py     엔트리 포인트 (Phase 7부터 실질적인 동작 시작)
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
8. **Paper Trading 실행** (Phase 7부터 의미 있는 동작을 시작)
   ```bash
   python main.py
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
| 2 | KIS 모의투자 인증 및 현재가 조회 (구현/테스트 완료, 실제 자격증명으로 검증 대기) |
| 3 | 잔고 / 보유종목 / 주문가능금액 조회 |
| 4 | Telegram 알림 |
| 5 | AI structured decision |
| 6 | Risk Manager |
| 7 | 모의 BUY / SELL |
| 8 | 주문/체결 확인 및 중복주문 방지 |
| 9 | Trade Memory |
| 10 | Telegram Control |
| 11 | 장시간 Paper Trading |
| 12 | 백테스트 및 거래 결과 분석 |

실전 거래 기능은 위 단계가 안정적으로 검증되기 전까지 활성화하지 않습니다.
