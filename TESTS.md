# 테스트 점검 문서

`tests/` 폴더의 테스트 **206개**가 각각 무엇을 확인하는지 정리한 문서입니다. 점검하면서 오른쪽 `확인` 칸에 체크하세요.

## 먼저 알아둘 것

- 테스트는 **가짜 데이터(mock)** 로 코드가 규칙대로 동작하는지만 확인합니다. 실제 증권사에 주문을 내거나 AI를 호출하지 않습니다.
- 테스트가 통과한다는 것은 "코드가 의도한 대로 동작한다"는 뜻이지, "수익이 난다"는 뜻이 아닙니다.
- 실행: `cd lammal0921 && .venv/bin/python -m pytest -q` (약 1초)
- 한 줄 요약표의 `#`은 이 문서 안의 번호이고, 같은 이름으로 표시된 항목은 입력만 다른 반복 테스트입니다.

## 묶음별 요약

| 묶음 | 개수 | 지키는 것 |
|---|---|---|
| 리스크 매니저 (한도 검사) | 30 | 한도를 넘는 주문은 절대 통과하지 못함 |
| 주문 실행기·증권사 주문 경로 | 40 | 승인된 주문만 전송, 응답 불명이면 정지, 실전 주문 차단 |
| 지표 계산·AI 호출 신호·매매 루프 | 27 | 필요할 때만 AI 호출, 루프의 시간·휴장·정지 처리 |
| 분할매수·손절·익절 규칙 | 23 | 분할매수·손절·익절 규칙 (AI 호출 없음) |
| 매매 기록 DB와 재시작 후 유지 | 18 | 재시작해도 비상 정지·중복 방지가 유지됨 |
| AI 응답 검증 | 15 | AI의 이상한 응답을 그대로 믿지 않음 |
| 텔레그램 명령 봇 | 19 | 지정 채팅만, 안전한 정지·재개 |
| 증권사(KIS) API 클라이언트 | 14 | 모의/실전 구분, 인증, 조회 형식 |
| 매매일지 엑셀 | 6 | 매매일지 엑셀 |
| AI 호출 | 6 | AI 호출과 응답 해석 |
| 공격 모드 프롬프트 | 2 | 공격 모드 날짜 경계 |
| 텔레그램 알림 전송 | 6 | 알림 전송 (토큰 노출 방지) |

## 리스크 매니저 (한도 검사) — `tests/test_risk_manager.py` (30개)

AI·규칙이 낸 주문을 증권사로 보내도 되는지 결정하는 마지막 관문. 이 프로젝트에서 가장 중요한 테스트 묶음입니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 1 | `test_buy_within_limits_is_approved` | 한도 이내의 정상 매수 | 승인되고 사유가 '한도 이내' | ☐ |
| 2 | `test_hold_is_never_executable` | 관망(HOLD)은 주문 대상이 아님 | 거절 (사유에 HOLD) | ☐ |
| 3 | `test_buy_exceeding_position_limit_is_rejected` | 1회 매수금액이 종목당 한도(총평가의 N%)를 넘음 | 거절 (사유에 '포지션 한도') | ☐ |
| 4 | `test_sell_exceeding_held_quantity_is_rejected` | 보유 2주인데 5주 매도 | 거절 (보유 수량 초과) | ☐ |
| 5 | `test_sell_within_held_quantity_is_approved` | 보유 5주 중 2주 매도 | 승인 | ☐ |
| 6 | `test_duplicate_decision_id_is_rejected` | 이미 실행한 판단 ID로 같은 주문을 다시 냄 | 거절 (중복 주문 방지) | ☐ |
| 7 | `test_unmarked_decision_id_can_be_reevaluated` | 주문 전송 실패 등으로 '실행됨' 표시가 안 된 판단 ID | 다시 평가해 승인 (영구 차단되지 않음) | ☐ |
| 8 | `test_daily_loss_limit_blocks_new_buy` | 당일 평가금액이 -4%로 일일 손실 한도(3%) 초과 | 신규 매수 거절 | ☐ |
| 9 | `test_daily_loss_limit_still_allows_sell` | 일일 손실 한도를 넘은 상태에서도 매도 | 승인 (손실 중에도 팔 수 있어야 함) | ☐ |
| 10 | `test_daily_loss_exactly_at_limit_blocks_buy` | 손실이 정확히 -3%인 경계값 | 매수 거절 (경계는 포함, 안전 쪽으로) | ☐ |
| 11 | `test_emergency_stop_blocks_everything` | 비상 정지 상태 | 모든 주문 거절 | ☐ |
| 12 | `test_emergency_stop_can_be_disengaged` | 비상 정지 해제 후 | 다시 승인 | ☐ |
| 13 | `test_invalid_current_price_is_rejected` | 현재가가 0 | 거절 (잘못된 값으로 계산하지 않음) | ☐ |
| 14 | `test_invalid_total_equity_is_rejected` | 총평가금액이 0 | 거절 | ☐ |
| 15 | `test_limits_default_from_settings` | 한도가 .env 설정(RISK_*)에서 읽힘 | 환경변수 값이 그대로 반영 | ☐ |
| 16 | `test_symbol_mismatch_is_rejected` | AI 판단 종목과 계좌 스냅샷 종목이 다름 | 거절 (다른 종목 가격으로 한도 계산 방지) | ☐ |
| 17 | `test_open_order_for_symbol_blocks_new_order` | 그 종목에 미체결 주문이 있음 | 추가 주문 거절 | ☐ |
| 18 | `test_unknown_open_order_state_is_rejected` | 미체결 여부를 확인 못 함 | 거절 (모르면 막는다) | ☐ |
| 19 | `test_buy_exceeding_total_exposure_is_rejected` | 매수 후 총 투자금이 총평가의 90% 초과 | 거절 (사유에 '총 투자 한도') | ☐ |
| 20 | `test_buy_exactly_at_total_exposure_limit_is_approved` | 매수 후 총 투자금이 정확히 한도와 같음 | 승인 (한도 '이하'는 허용) | ☐ |
| 21 | `test_over_invested_account_blocks_buy_but_allows_sell` | 보유 주식이 총평가보다 큰 과투자 계좌 (10/06 실제 상태) | 매수 거절, 매도는 승인 | ☐ |
| 22 | `test_buy_exceeding_buying_power_is_rejected` | 주문가능금액(현금)보다 큰 매수 | 거절 | ☐ |
| 23 | `test_buy_with_zero_buying_power_is_rejected` | 주문가능금액 0 | 매수 거절 | ☐ |
| 24 | `test_buy_is_rejected_when_exposure_or_cash_is_unknown` | 보유 평가금액 또는 주문가능금액을 조회하지 못함 | 매수 거절 (각각 따로 확인) | ☐ |
| 25 | `test_sell_does_not_need_exposure_or_cash_figures` | 위 두 값이 없어도 매도 | 승인 (매도는 막지 않음) | ☐ |
| 26 | `test_nan_or_negative_figures_reject_buy` | NaN, 음수 같은 비정상 숫자 | 매수 4가지 모두 거절 | ☐ |
| 27 | `test_max_buy_value_is_the_smallest_cap` | AI에게 알려주는 최대 매수금액 계산 | 종목 한도·총 투자 여유·현금 중 가장 작은 값 | ☐ |
| 28 | `test_max_buy_value_is_zero_when_blocked_or_unknown` | 과투자·음수 현금·값 불명 상태 | 최대 매수금액 0 | ☐ |
| 29 | `test_max_buy_value_never_exceeds_what_evaluate_accepts` | 최대 매수금액으로 산 수량 vs 1주 더 | 그 수량은 승인, 1주 더는 거절 (계산과 실제 검사가 일치) | ☐ |
| 30 | `test_total_exposure_limit_comes_from_settings` | 총 투자 한도 설정 | 환경변수 0.5 반영, 없으면 기본 0.90 | ☐ |

## 주문 실행기·증권사 주문 경로 — `tests/test_order_executor.py` (40개)

승인된 주문만 증권사로 보내고, 응답이 불명확하면 멈추고, 체결을 확인하는 부분입니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 31 | `test_approved_order_is_submitted` | 리스크 승인된 주문 | 증권사 주문 API가 정확히 1번, 올바른 인자로 호출됨 | ☐ |
| 32 | `test_risk_rejection_never_reaches_kis` | 리스크가 거절한 주문 | 증권사 API 호출 0회 | ☐ |
| 33 | `test_hold_never_reaches_kis` | HOLD 판단 | 증권사 API 호출 0회 | ☐ |
| 34 | `test_duplicate_decision_id_is_submitted_once` | 같은 판단 ID를 두 번 제출 | 첫 번째만 전송, 두 번째 거절 | ☐ |
| 35 | `test_uncertain_outcome_locks_decision_and_engages_emergency_stop` | 주문 후 응답 타임아웃 (접수 여부 불명) | UNKNOWN 상태 + 자동 비상 정지, 이후 모든 주문 차단 | ☐ |
| 36 | `test_broker_rejection_does_not_lock_decision_id` | 증권사가 명확히 거절 (예: 주문가능금액 초과) | 비상 정지 없음, 같은 판단 ID 재시도 가능 | ☐ |
| 37 | `test_real_trading_client_is_refused` | 실전투자 클라이언트로 주문 실행기를 만들려 함 | RealTradingDisabledError (실전 주문 원천 차단) | ☐ |
| 38 | `test_place_order_success_uses_paper_buy_tr_id` | 모의투자 매수 주문 요청 형식 | 모의 매수 TR ID, 수량·시장가 구분·계좌번호가 맞음 | ☐ |
| 39 | `test_place_order_sell_uses_paper_sell_tr_id` | 모의투자 매도 주문 요청 형식 | 모의 매도 TR ID 사용 | ☐ |
| 40 | `test_place_order_timeout_is_uncertain` | 주문 중 읽기 타임아웃 | '불확실'로 분류, 재전송 없음 | ☐ |
| 41 | `test_place_order_connect_timeout_is_definitely_not_placed` | 주문 중 연결 타임아웃 (서버에 닿지 못함) | '불확실' 아님 (확실히 미접수) | ☐ |
| 42 | `test_place_order_connection_reset_is_uncertain` | 주문 중 연결 끊김 | 불확실 | ☐ |
| 43 | `test_place_order_unparseable_response_is_uncertain` | 해석할 수 없는 응답 (502) | 불확실 | ☐ |
| 44 | `test_place_order_http_200_without_order_no_is_uncertain` | 성공 응답인데 주문번호가 없음 | 불확실 | ☐ |
| 45 | `test_place_order_explicit_rejection_is_definite` | 증권사가 오류코드로 명확히 거절 | 확실한 거절로 분류, 오류코드 보존 | ☐ |
| 46 | `test_place_order_rejects_invalid_side_and_quantity` | 매매구분 HOLD, 수량 0 | ValueError (주문 요청 자체를 막음) | ☐ |
| 47 | `test_build_account_state_from_kis_responses` | 증권사 응답으로 계좌 스냅샷 생성 | 총평가·보유수량·평단·보유평가·주문가능금액·미체결 수가 정확 (다른 종목·취소 행 제외) | ☐ |
| 48 | `test_build_account_state_missing_field_raises` | 증권사 응답에 필수 값이 없음 | 0으로 채우지 않고 오류 | ☐ |
| 49 | `test_order_fill_status` (전량 체결) | 체결 행 → 상태 분류 | FILLED, 열려 있지 않음 | ☐ |
| 50 | `test_order_fill_status` (일부 체결) | 체결 행 → 상태 분류 | PARTIALLY_FILLED, 열려 있음 | ☐ |
| 51 | `test_order_fill_status` (미체결) | 체결 행 → 상태 분류 | PENDING, 열려 있음 | ☐ |
| 52 | `test_order_fill_status` (취소) | 체결 행 → 상태 분류 | CANCELLED, 열려 있지 않음 | ☐ |
| 53 | `test_order_fill_status` (거부) | 체결 행 → 상태 분류 | REJECTED, 열려 있지 않음 | ☐ |
| 54 | `test_order_fill_parses_side_and_price` | 체결 행 해석 | 매도 구분·평균가가 정확 | ☐ |
| 55 | `test_order_fill_malformed_row_raises` | 숫자가 아닌 수량, 모르는 매매구분 | OrderFillParseError | ☐ |
| 56 | `test_confirm_fill_polls_until_filled` | 주문 후 체결 확인 반복 조회 | 목록에 없음 → 미체결 → 체결 순서로 보고 최종 FILLED | ☐ |
| 57 | `test_confirm_fill_returns_last_open_state_when_attempts_run_out` | 조회 횟수 소진까지 미체결 | 마지막 상태(PENDING) 반환 | ☐ |
| 58 | `test_confirm_fill_rejects_non_submitted_result` | 접수되지 않은 주문에 체결 확인 | ValueError | ☐ |
| 59 | `test_reconcile_unknown_finds_placed_order` | UNKNOWN 주문을 증권사 주문목록과 대조 | 일치하는 1건을 찾으면 PLACED, 비상 정지는 자동 해제 안 됨 | ☐ |
| 60 | `test_reconcile_unknown_ignores_known_and_out_of_window_orders` | 이미 아는 주문, 시간대가 다른 주문, 수량·매매구분이 다른 주문 | 모두 제외하고 NOT_FOUND | ☐ |
| 61 | `test_reconcile_unknown_finds_order_registered_late` | 증권사가 3분 늦게 접수한 주문 (실제 31초 지연 사례 반영) | PLACED로 찾음 | ☐ |
| 62 | `test_reconcile_unknown_ambiguous_with_several_candidates` | 후보가 2건 이상 | AMBIGUOUS (사람이 확인) | ☐ |
| 63 | `test_reconcile_rejects_non_unknown_result` | UNKNOWN이 아닌 주문에 대사 | ValueError | ☐ |
| 64 | `test_get_daily_orders_follows_pagination` | 주문 내역이 여러 페이지 | 모든 페이지를 이어서 가져옴 | ☐ |
| 65 | `test_get_daily_orders_refuses_partial_list` | 페이지 한도까지 가도 끝나지 않음 | 일부만 쓰지 않고 오류 | ☐ |
| 66 | `test_read_request_retries_on_rate_limit` | 조회 중 호출 횟수 초과 오류 | 재시도해서 성공 | ☐ |
| 67 | `test_order_is_never_retried_on_rate_limit` | 주문 중 호출 횟수 초과 오류 | 재시도하지 않음 (중복 주문 방지) | ☐ |
| 68 | `test_read_request_retries_network_error_then_raises_kis_error` | 조회 중 네트워크 오류 반복 | 총 3번 시도 후 오류 | ☐ |
| 69 | `test_read_request_recovers_after_network_error` | 조회 중 일시 네트워크 오류 | 재시도로 성공 | ☐ |
| 70 | `test_build_account_state_without_buying_power_still_builds_for_sells` | 보유평가·주문가능금액 값이 없음 | 스냅샷은 만들어지고 해당 값은 None (매도는 가능, 매수는 거절) | ☐ |

## 지표 계산·AI 호출 신호·매매 루프 — `tests/test_signal_filter.py` (27개)

10분 사이클 루프가 언제 AI를 부르고, 언제 쉬고, 어떻게 멈추는지 확인합니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 71 | `test_sma_and_short_history` | 단순이동평균 계산 | 정확한 평균, 데이터가 부족하면 None | ☐ |
| 72 | `test_rsi_extremes_and_known_value` | RSI 계산 | 계속 상승이면 100, 계속 하락이면 0, 데이터 부족이면 None, 오르내림이 반복되면 40~60 | ☐ |
| 73 | `test_compute_trading_day_ma_and_volume_ratio` | 오늘 시세가 있는 날의 MA5·거래량 비율 | MA5, 어제 MA5, 시간대 보정 거래량 비율이 정확 | ☐ |
| 74 | `test_compute_golden_cross` | MA5가 MA20을 위로 뚫은 경우 | 골든크로스 감지 | ☐ |
| 75 | `test_compute_detects_non_trading_day` | 오늘 시세 행이 없음 (휴장) | 거래일 아님, 거래량 비율 None | ☐ |
| 76 | `test_compute_rejects_malformed_rows` | 빈 시세, 가격 0인 시세 | IndicatorError | ☐ |
| 77 | `test_first_check_of_day_triggers` | 오늘 처음 보는 종목 | '오늘 첫 점검'으로 AI 호출 | ☐ |
| 78 | `test_quiet_symbol_does_not_trigger` | 변화가 없는 종목 | AI 호출 안 함 (비용 절감) | ☐ |
| 79 | `test_price_move_since_last_call_triggers` | 마지막 AI 판단 이후 -2.5% 변동 | '변동' 신호로 AI 호출 | ☐ |
| 80 | `test_indicator_triggers` (거래량 2.5배) | AI 호출 신호 종류별 확인 | '거래량 급증' 신호로 AI 호출 | ☐ |
| 81 | `test_indicator_triggers` (골든크로스) | AI 호출 신호 종류별 확인 | '골든크로스' 신호로 AI 호출 | ☐ |
| 82 | `test_indicator_triggers` (데드크로스) | AI 호출 신호 종류별 확인 | '데드크로스' 신호로 AI 호출 | ☐ |
| 83 | `test_indicator_triggers` (RSI 25) | AI 호출 신호 종류별 확인 | 'RSI 과매도' 신호로 AI 호출 | ☐ |
| 84 | `test_indicator_triggers` (RSI 75) | AI 호출 신호 종류별 확인 | 'RSI 과매수' 신호로 AI 호출 | ☐ |
| 85 | `test_volume_surge_ignored_right_after_open` | 개장 10분 후 거래량 급증 | 신호 무시 (개장 직후 거래량은 부정확) | ☐ |
| 86 | `test_held_position_reviewed_hourly` | 보유 종목, 마지막 판단 61분 경과 | '보유 종목 정기 점검'으로 AI 호출 | ☐ |
| 87 | `test_cooldown_blocks_repeat_calls` | 10분 전에 판단한 종목에 -10% 변동 | 쿨다운으로 AI 호출 차단 | ☐ |
| 88 | `test_daily_limit_counts_failed_calls_too` | 하루 호출 상한 3회, 실패한 호출 3건 | 실패 호출도 세어 상한 도달 → 차단 | ☐ |
| 89 | `test_loop_runs_triggered_symbols_with_context` | 루프 한 틱 | 신호가 있는 종목마다 사이클 실행, 지표·신호 이유를 함께 전달, 평소엔 알림 없음 | ☐ |
| 90 | `test_loop_skips_outside_hours_and_weekends` | 08:50, 15:25(동시호가), 토요일 | 사이클 실행 없음 | ☐ |
| 91 | `test_loop_detects_holiday_once` | 당일 시세가 없는 휴장일 | 휴장 알림 1번만, 매매 안 함 | ☐ |
| 92 | `test_loop_does_not_call_holiday_right_after_open` | 09:00 직후 시세가 아직 없는 경우 | 휴장으로 단정하지 않음 | ☐ |
| 93 | `test_loop_honors_stop_and_pause_and_notifies_transitions` | 비상 정지 → 해제 → 일시정지 → 재개 | 정지·일시정지 중 사이클 없음, 상태가 바뀔 때만 알림, 재개되면 다시 실행 | ☐ |
| 94 | `test_loop_symbol_error_is_isolated_and_throttled` | 한 종목 조회가 오류 | 다른 종목은 계속 처리, 오류 알림은 30분에 1번 | ☐ |
| 95 | `test_loop_daily_summary_once_after_close` | 15:35 이후 틱 두 번 | 일일 요약 알림 1번만 (시작 대비 손익 포함) | ☐ |
| 96 | `test_seconds_until_next_tick_aligns_to_cycle` | 10:33:30에 다음 틱까지 대기 시간 | 10:40 정각까지 390초 | ☐ |
| 97 | `test_second_loop_lock_is_refused` | 루프를 두 번 실행 | 두 번째는 거부 (주문 중복 방지), 첫 번째가 끝나면 다시 가능 | ☐ |

## 분할매수·손절·익절 규칙 — `tests/test_ladder.py` (23개)

AI 호출 없이 동작하는 규칙(market/ladder.py)과 루프 연결을 확인합니다. 10/13에 새로 추가됐습니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 98 | `test_atr_pct_uses_completed_sessions` | 평균 변동폭(ATR) 계산 | 하루 2% 범위가 일정하면 ATR 2%, 데이터가 부족하면 None | ☐ |
| 99 | `test_estimate_uses_closer_support_and_clamps` | 예상 하락폭 계산 | ATR과 지지선 중 작은 값 사용, 2%~8%로 보정 | ☐ |
| 100 | `test_estimate_none_without_history` | 시세 기록이 5일뿐 | 계산 불가(None) | ☐ |
| 101 | `test_build_plan_matches_the_worked_example` | 기준가 100,000원, 예상 하락 5% | 매수 97,000/96,000/95,000원, 비중 20/30/50%, 손절 93,000원 | ☐ |
| 102 | `test_plan_json_roundtrip` | 계획의 DB 저장 형식 | 저장 후 읽어도 동일 | ☐ |
| 103 | `test_should_plan` (전일 대비 -1.5%, 10:00) | 분할매수 계획 대상 판정 | 대상 (오늘 하락 중) | ☐ |
| 104 | `test_should_plan` (하락 추세(현재가<MA5<MA20), 10:00) | 분할매수 계획 대상 판정 | 대상 | ☐ |
| 105 | `test_should_plan` (전일 대비 -0.5%, 10:00) | 분할매수 계획 대상 판정 | 대상 아님 (하락폭이 작음) | ☐ |
| 106 | `test_should_plan` (전일 대비 -3%, 09:10) | 분할매수 계획 대상 판정 | 대상 아님 (09:30 이전) | ☐ |
| 107 | `test_should_plan` (전일 대비 -3%, 14:50) | 분할매수 계획 대상 판정 | 대상 아님 (14:30 이후 새 계획 금지) | ☐ |
| 108 | `test_due_levels_and_gap_down_buys_several_at_once` | 현재가가 매수 지점에 닿았는지 판정 | 닿은 단계만 선택, 갭하락이면 여러 단계 동시, 손절가 이하·중단된 계획은 매수 안 함 | ☐ |
| 109 | `test_ladder_decision_quantity_from_weights` | 단계별 매수 수량 | 예산×비중÷현재가로 계산, 1주도 못 사면 주문 없음 | ☐ |
| 110 | `test_no_exit_without_position_or_in_range` | 보유 없음, 평단 모름, 손익 -2% | 청산 주문 없음 | ☐ |
| 111 | `test_plan_stop_only_after_a_level_filled` | 분할매수 손절가 이탈 | 1단계도 체결 안 됐으면 손절 안 함, 체결 후엔 전량 매도 | ☐ |
| 112 | `test_hard_stop_sells_everything` | 평단 대비 -10% | 전량 손절 | ☐ |
| 113 | `test_take_profit_sells_half_once_per_step_per_day` | +4%, +8%, +12% 단계별 익절 | 단계마다 하루 1번 남은 물량의 절반, +7%는 아직 1단계 | ☐ |
| 114 | `test_memory_plan_persistence_and_count` | 분할매수 계획 저장 | 저장·갱신·날짜별 구분·개수 집계 | ☐ |
| 115 | `test_count_decided_today_ignores_rejected_orders` | 오늘 익절 횟수 집계 | 거절된 주문은 세지 않음 (거절되면 다시 시도) | ☐ |
| 116 | `test_loop_draws_up_plan_then_buys_levels` | 루프에서 하락 종목 처리 | 계획 수립+알림, 매수 지점 도달 시 규칙 매수 주문, 체결로 표시 | ☐ |
| 117 | `test_loop_does_not_mark_level_filled_when_order_rejected` | 규칙 주문이 거절됨 | 그 단계를 체결로 표시하지 않음 | ☐ |
| 118 | `test_loop_stop_sells_and_stops_plan` | 손절가 이탈 | 전량 매도 주문, 계획 중단 (이후 매수 없음) | ☐ |
| 119 | `test_loop_rules_respect_settings_and_plan_cap` | 설정으로 끔, 하루 계획 수 상한 0 | 계획이 만들어지지 않음 | ☐ |
| 120 | `test_loop_rule_failure_falls_back_to_ai` | 시세 데이터가 깨져 규칙 계산 실패 | 오류를 알리고 그 종목은 기존 AI 판단으로 계속 | ☐ |

## 매매 기록 DB와 재시작 후 유지 — `tests/test_trade_memory.py` (18개)

판단·주문·체결 기록과, 프로그램을 껐다 켜도 안전장치가 유지되는지 확인합니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 121 | `test_recent_trades_joins_decision_order_and_fill` | 최근 거래 조회 | 판단·주문·체결이 합쳐져 나오고, 원본 스냅샷은 AI에게 다시 주지 않음 | ☐ |
| 122 | `test_recent_trades_shows_risk_rejection_reason` | 리스크가 거절한 판단 | 거절 사유가 표시되고 체결 정보 없음 | ☐ |
| 123 | `test_recent_trades_newest_first_capped_and_per_symbol` | 최근 거래 목록 | 최신순, 개수 상한, 종목별 분리 | ☐ |
| 124 | `test_sell_fill_records_realized_pnl` | 매도 체결 | (체결가−평단)×수량으로 실현손익 기록 | ☐ |
| 125 | `test_buy_fill_has_no_realized_pnl` | 매수 체결 | 실현손익 없음 | ☐ |
| 126 | `test_fill_is_upserted_by_order_no` | 같은 주문의 체결 상태 갱신 | 주문번호 기준으로 덮어씀 (중복 행 없음) | ☐ |
| 127 | `test_order_requires_recorded_decision` | 판단 기록 없이 주문만 저장 | DB 무결성 오류 (판단 → 주문 순서 강제) | ☐ |
| 128 | `test_day_start_equity_first_value_of_day_wins` | 당일 시작 평가금액 | 그날 처음 기록한 값 유지 (재시작해도 손실 기준선이 리셋되지 않음) | ☐ |
| 129 | `test_day_start_equity_survives_reopen` | DB 파일을 닫았다 다시 열기 | 시작 평가금액 유지 | ☐ |
| 130 | `test_executed_decision_id_survives_restart` | 재시작 후 같은 판단 ID | 계속 '이미 실행됨'으로 거절 | ☐ |
| 131 | `test_emergency_stop_survives_restart_until_released` | 비상 정지 후 재시작 | 정지 유지, 해제는 사람이 할 때만 | ☐ |
| 132 | `test_failed_persist_engages_emergency_stop_without_raising` | 주문은 나갔는데 실행 기록 저장 실패 (디스크 가득) | 예외 없이 비상 정지로 전환 | ☐ |
| 133 | `test_failed_release_keeps_emergency_stop` | 해제 기록 저장 실패 | 비상 정지 유지 | ☐ |
| 134 | `test_store_load_failure_prevents_startup` | 저장된 안전 상태를 읽지 못함 | 시작 자체를 거부 | ☐ |
| 135 | `test_run_once_skips_ai_while_emergency_stop_engaged` | 비상 정지 중 사이클 | AI를 호출하지 않고 알림만 | ☐ |
| 136 | `test_release_emergency_stop_clears_persisted_state` | 비상 정지 해제 명령 | 저장된 정지 상태가 지워짐 | ☐ |
| 137 | `test_run_once_records_decision_order_and_fill` | 사이클 전체 흐름 (가짜 증권사) | 판단·주문·체결·실현손익이 기록되고 실행 ID가 저장됨 | ☐ |
| 138 | `test_run_once_skips_while_paused` | 일시정지 중 사이클 | AI를 호출하지 않음 | ☐ |

## AI 응답 검증 — `tests/test_decision_parser.py` (15개)

AI가 준 결과가 이상하면 그대로 믿지 않고 거부합니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 139 | `test_valid_buy_decision_parses` | 정상 매수 판단 | 행동·종목·수량·확신도·근거가 정확히 읽힘 | ☐ |
| 140 | `test_valid_hold_decision_allows_zero_quantity` | 관망 판단 | 수량 0 허용 | ☐ |
| 141 | `test_optional_fields_default_to_none` | 선택 항목 없음 | 손절가·익절가 등이 None | ☐ |
| 142 | `test_optional_fields_parsed_when_present` | 선택 항목 있음 | 위험점수·손절가·익절가·무효화 조건이 읽힘 | ☐ |
| 143 | `test_non_dict_input_raises` | JSON 객체가 아닌 응답 | DecisionParseError | ☐ |
| 144 | `test_missing_required_field_raises` | 필수 항목(확신도) 누락 | 오류 | ☐ |
| 145 | `test_invalid_action_raises` | BUY/SELL/HOLD 외의 행동 | 오류 | ☐ |
| 146 | `test_negative_quantity_raises` | 음수 수량 | 오류 | ☐ |
| 147 | `test_zero_quantity_for_buy_raises` | 매수인데 수량 0 | 오류 | ☐ |
| 148 | `test_non_integer_quantity_raises` | 수량 2.5 | 오류 | ☐ |
| 149 | `test_confidence_out_of_range_raises` | 확신도 1.5 | 오류 | ☐ |
| 150 | `test_negative_confidence_raises` | 확신도 -0.1 | 오류 | ☐ |
| 151 | `test_empty_reason_list_raises` | 근거 목록이 비어 있음 | 오류 | ☐ |
| 152 | `test_non_string_reason_items_raise` | 근거에 숫자가 섞임 | 오류 | ☐ |
| 153 | `test_empty_symbol_raises` | 종목코드가 빈 문자열 | 오류 | ☐ |

## 텔레그램 명령 봇 — `tests/test_telegram_bot.py` (19개)

텔레그램으로 상태를 보고 멈추거나 재개하는 명령이 안전하게 동작하는지 확인합니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 154 | `test_other_chats_are_ignored` | 지정하지 않은 채팅에서 /stop | 무시 (아무 일도 일어나지 않음) | ☐ |
| 155 | `test_messages_from_before_startup_are_dropped` | 봇 시작 전에 보낸 오래된 명령 | 버림 | ☐ |
| 156 | `test_missing_chat_id_refuses_to_start` | 허용 채팅 ID 미설정 | 시작 거부 | ☐ |
| 157 | `test_unknown_command_and_plain_text` | 알 수 없는 명령 / 일반 문장 | '알 수 없는 명령' 답변 / 무응답 | ☐ |
| 158 | `test_bot_suffix_is_accepted` | /help@봇이름 형식 | 정상 처리 | ☐ |
| 159 | `test_balance_and_positions` | /balance, /positions | 평가금액·보유종목·수익률 표시 | ☐ |
| 160 | `test_price_validates_symbol` | /price | 종목코드 형식 검사, 정상이면 현재가 조회 | ☐ |
| 161 | `test_kis_error_is_reported_not_raised` | 증권사 오류 | 봇이 죽지 않고 실패 메시지 답장 | ☐ |
| 162 | `test_status_shows_stop_and_pause` | /status | 비상 정지·일시정지 상태와 사유 표시 | ☐ |
| 163 | `test_trades_and_decision` | /trades, /decision | 기록이 없으면 안내, 있으면 판단과 근거 표시 | ☐ |
| 164 | `test_pause_then_resume_after_checks` | /pause 후 /resume | 재개 전에 증권사 연결을 확인하고 재개 | ☐ |
| 165 | `test_resume_refused_while_emergency_stop_engaged` | 비상 정지 중 /resume | 재개 거부 | ☐ |
| 166 | `test_resume_refused_when_kis_unreachable` | 증권사 연결 불가 시 /resume | 재개 거부 | ☐ |
| 167 | `test_stop_is_persisted_and_seen_by_running_risk_manager` | /stop | DB에 저장되고, 이미 돌고 있는 리스크 매니저도 곧바로 주문 거절 | ☐ |
| 168 | `test_release_requires_matching_code` | /release_stop | 일회용 코드를 입력해야 해제 | ☐ |
| 169 | `test_release_wrong_code_invalidates_pending` | 틀린 코드 입력 | 불일치 안내, 코드는 1회 시도 후 만료 | ☐ |
| 170 | `test_release_code_expires` | 코드 유효시간 경과 | 만료, 해제 안 됨 | ☐ |
| 171 | `test_release_when_not_engaged` | 정지가 없는데 해제 요청 | '걸려 있지 않음' 안내 | ☐ |
| 172 | `test_poll_once_replies_and_acknowledges` | 텔레그램 메시지 수신 처리 | 허용 채팅은 처리·답장, 다른 채팅은 무시하되 확인 처리 | ☐ |

## 증권사(KIS) API 클라이언트 — `tests/test_kis_client.py` (14개)

인증·모의/실전 구분·조회 형식이 맞는지 확인합니다. 실제 서버 호출은 없습니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 173 | `test_no_credentials_fails_closed` | 키가 하나도 없음 | 오류 (실행 거부) | ☐ |
| 174 | `test_both_paper_and_real_credentials_fails_closed` | 모의·실전 키가 둘 다 있음 | 오류 (어느 쪽인지 추측하지 않음) | ☐ |
| 175 | `test_paper_credentials_select_paper_base_url` | 모의 키만 있음 | 모의투자 서버 주소, 실전 아님 | ☐ |
| 176 | `test_real_credentials_select_real_base_url` | 실전 키만 있음 | 실전 서버 주소, 실전 표시 | ☐ |
| 177 | `test_get_access_token_caches_across_calls` | 접근 토큰 발급 | 두 번째 호출은 저장된 토큰 재사용 | ☐ |
| 178 | `test_get_access_token_refetches_once_expired` | 토큰 만료 | 새로 발급 | ☐ |
| 179 | `test_get_access_token_refetches_when_app_key_changes` | 앱 키가 바뀜 (계좌 교체 시) | 이전 키의 토큰을 재사용하지 않고 새로 발급 | ☐ |
| 180 | `test_get_access_token_raises_on_http_error` | 토큰 발급 401 | KisApiError | ☐ |
| 181 | `test_get_current_price_returns_output_dict` | 현재가 조회 | 올바른 TR ID와 인증 헤더, 결과 반환 | ☐ |
| 182 | `test_get_current_price_raises_on_api_error` | 증권사 오류코드 응답 | KisApiError | ☐ |
| 183 | `test_invalid_account_number_format_fails_closed` | 형식이 잘못된 계좌번호 | 오류 | ☐ |
| 184 | `test_get_balance_splits_holdings_and_summary` | 잔고 조회 | 보유종목과 요약 분리, 보유 0주 종목 제외, 모의 TR ID | ☐ |
| 185 | `test_get_balance_uses_real_tr_id_for_real_environment` | 실전 환경 잔고 조회 | 실전 TR ID 사용 | ☐ |
| 186 | `test_get_buying_power_returns_output_dict` | 매수가능금액 조회 | 시장가 기준으로 조회 (증거금률 반영) | ☐ |

## 매매일지 엑셀 — `tests/test_daily_report.py` (6개)

그날의 매매·보유·AI 판단을 엑셀로 내보내는 조회 전용 기능입니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 187 | `test_day_journal_joins_order_fill_and_snapshot` | 하루치 일지 조립 | 판단·주문·체결·지표·호출 신호가 합쳐지고 기록 없는 날은 빈 목록 | ☐ |
| 188 | `test_start_equity_on_is_read_only` | 시작 평가금액 조회 | 기록을 읽기만 하고 새로 만들지 않음 | ☐ |
| 189 | `test_export_writes_all_sheets` | 엑셀 생성 | 요약·주문체결·보유현황·AI판단·증권사대조 5개 시트와 수치가 정확 | ☐ |
| 190 | `test_export_without_kis_still_writes_local_data` | 증권사 조회 생략 | 로컬 기록만으로 엑셀 생성 | ☐ |
| 191 | `test_past_date_skips_today_only_figures` | 과거 날짜 일지 | 오늘 전용 수치(전일 대비 손익)는 제외 | ☐ |
| 192 | `test_fetch_kis_records_failures_instead_of_raising` | 증권사 조회 오류 | 예외 대신 오류 내용을 기록 | ☐ |

## AI 호출 — `tests/test_trading_agent.py` (6개)

Claude API를 부르고 응답을 해석하는 부분입니다. 실제 API 호출은 없습니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 193 | `test_missing_api_key_raises` | API 키 없음 | TradingAgentError (호출 거부) | ☐ |
| 194 | `test_valid_response_parses_into_decision` | 정상 응답 | 판단으로 변환, JSON 스키마 출력·effort low·종목 정보 전달 확인 | ☐ |
| 195 | `test_malformed_json_raises_decision_parse_error` | JSON이 아닌 응답 | DecisionParseError | ☐ |
| 196 | `test_invalid_decision_shape_raises_decision_parse_error` | 형식은 JSON이지만 내용이 잘못됨 | DecisionParseError | ☐ |
| 197 | `test_no_text_block_raises_decision_parse_error` | 텍스트 없는 응답 (토큰 초과 등) | DecisionParseError | ☐ |
| 198 | `test_anthropic_call_failure_raises_trading_agent_error` | API 호출 자체가 실패 | TradingAgentError | ☐ |

## 공격 모드 프롬프트 — `tests/test_trading_agent_prompt.py` (2개)

공격 모드가 지정한 날짜까지만 적용되는지 확인합니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 199 | `test_aggressive_prompt_ends_after_until_date` | 종료일 당일과 다음 날 | 당일(10/16)은 공격 지침 포함, 다음 날(10/17)은 기본 프롬프트 | ☐ |
| 200 | `test_aggressive_prompt_off_by_default_and_on_bad_date` | 설정이 없거나 날짜 형식이 틀림 | 기본 프롬프트 (공격 모드 꺼짐) | ☐ |

## 텔레그램 알림 전송 — `tests/test_telegram_notifier.py` (6개)

알림 전송이 실패해도 안전한지 확인합니다.

| # | 테스트 | 점검 내용 | 통과 기준 | 확인 |
|---|---|---|---|---|
| 201 | `test_missing_credentials_raises` | 봇 토큰 없음 | TelegramError | ☐ |
| 202 | `test_send_message_success` | 정상 전송 | 올바른 주소·채팅 ID·본문 | ☐ |
| 203 | `test_send_message_raises_on_api_error` | 텔레그램이 ok=false 응답 | TelegramError | ☐ |
| 204 | `test_send_message_raises_on_http_error` | HTTP 401 | TelegramError | ☐ |
| 205 | `test_network_error_becomes_telegram_error_without_token` | 네트워크 오류 | TelegramError로 바꾸되 오류 메시지에 봇 토큰이 노출되지 않음 | ☐ |
| 206 | `test_send_message_without_parse_mode_omits_field` | 서식 지정 없이 전송 | parse_mode 항목을 보내지 않음 | ☐ |

## 이 테스트들이 확인하지 못하는 것

- **실제 증권사 동작:** 모의투자 서버가 주문을 늦게 접수하거나 타임아웃이 나는 상황은 사례를 반영해 시뮬레이션했을 뿐, 실제 서버에서 매번 확인한 것은 아닙니다.
- **AI 판단의 질:** AI가 좋은 종목을 고르는지는 테스트할 수 없습니다. 형식과 한도만 검사합니다.
- **전략의 수익성:** 분할매수 지점, 손절·익절 비율이 시장에서 유리한지는 운영 결과로만 확인됩니다.
- **한도에 걸린 주문이 반복되는 문제:** 총 투자 한도에 막힌 규칙 매수가 사이클마다 다시 시도되는 동작은 의도된 동작으로 테스트되어 있지만, 기록이 쌓이는 문제는 개선 대상입니다.
- **AI 응답의 `stop_loss`/`take_profit`:** 기록만 되고 실행되지 않습니다. 손절·익절은 `test_ladder.py`의 규칙으로만 동작합니다.
