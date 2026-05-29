from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Sequence

from backend.services.sign_task_runtime_contract import (
    EVENT_ENGINE_BUTTON_CALLBACK_RESULT,
    EVENT_ENGINE_BUTTON_CALLBACK_RELEASED_FOR_RETRY,
    EVENT_ENGINE_BUTTON_CLICKED,
    EVENT_ENGINE_CAPTCHA_RESULT_TEXT_PREEMPTED,
    EVENT_ENGINE_FINAL_STATE,
    EVENT_ENGINE_HARD_TIMEOUT_LATE_CANCELLED,
    EVENT_ENGINE_HARD_TIMEOUT_LATE_COMPLETED,
    EVENT_ENGINE_HARD_TIMEOUT_LATE_EVENTS,
    EVENT_ENGINE_HARD_TIMEOUT_LATE_EXCEPTION,
    EVENT_ENGINE_HISTORY_FAILED,
    EVENT_ENGINE_HISTORY_SCAN_CANCELLED,
    EVENT_ENGINE_HISTORY_SCAN_COMPLETED,
    EVENT_ENGINE_MESSAGE_PROCESSING_CANCELLED,
    EVENT_ENGINE_NAME,
    EVENT_ENGINE_STARTED,
    EVENT_ENGINE_TIMEOUT_STATE,
    EVENT_TASK_RUNTIME_CONFIG,
    EVENT_WORKER_EXECUTION_CONTRACT,
    WORKER_RUNTIME_CONSISTENCY_FIELDS,
    validate_button_callback_result_snapshot,
    validate_button_callback_released_snapshot,
    validate_button_callback_unconfirmed_snapshot,
    validate_event_final_state_snapshot,
    validate_history_failed_snapshot,
    validate_history_scan_cancelled_snapshot,
    validate_history_scan_completed_snapshot,
    validate_hard_timeout_late_snapshot,
    validate_message_skip_snapshot,
    validate_message_processing_cancelled_snapshot,
    validate_message_unhandled_snapshot,
    validate_runtime_config_snapshot,
    validate_retry_event_snapshot,
    validate_worker_execution_snapshot,
)

def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def _event(item: Dict[str, Any]) -> str:
    return str(item.get("event") or "")


def _text(item: Dict[str, Any]) -> str:
    return str(item.get("text") or "")


def _meta(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("meta")
    return meta if isinstance(meta, dict) else {}


def _events(flow_items: Sequence[Dict[str, Any]]) -> list[str]:
    return [_event(item) for item in flow_items if isinstance(item, dict)]


def _normalize_result_status(value: Any) -> str:
    status = _clean(value)
    if status.endswith(".success"):
        return "success"
    if status.endswith(".checked"):
        return "checked"
    if status.endswith(".failed"):
        return "failed"
    if status in {"success", "checked", "failed"}:
        return status
    return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _final_state_status(items: Sequence[Dict[str, Any]]) -> str:
    for item in reversed(items):
        if _event(item) != EVENT_ENGINE_FINAL_STATE:
            continue
        status = _normalize_result_status(_meta(item).get("status"))
        if status:
            return status
    return ""


def _completion_result_status(items: Sequence[Dict[str, Any]]) -> str:
    for item in reversed(items):
        if _event(item) != "event_engine_completed":
            continue
        meta = _meta(item)
        status = _normalize_result_status(meta.get("status"))
        message = _clean(meta.get("message"))
        if status == "checked" or "matched checked keyword" in message:
            return "checked"
        if status == "success" and "matched success keyword" in message:
            return "success"
    return ""


def _result_status(items: Sequence[Dict[str, Any]]) -> str:
    return _final_state_status(items) or _completion_result_status(items)


def _contains(haystack: Any, needle: Any) -> bool:
    target = _clean(needle)
    return bool(target and target in _clean(haystack))


def _configured_actions(task_config: Dict[str, Any] | None) -> list[Dict[str, Any]]:
    if not isinstance(task_config, dict):
        return []
    actions: list[Dict[str, Any]] = []
    for chat in task_config.get("chats") or []:
        if not isinstance(chat, dict):
            continue
        for action in chat.get("actions") or []:
            if isinstance(action, dict):
                actions.append(action)
    return actions


def _snapshot_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        try:
            return float(left) == float(right)
        except (TypeError, ValueError):
            return False
    return left == right


@dataclass
class DiagnosticCheck:
    id: str
    label: str
    status: str
    detail: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
        }


class SignTaskDiagnostics:
    """Analyze structured sign-task flow logs for event-engine diagnostics."""

    @classmethod
    def analyze_run(
        cls,
        *,
        flow_items: Sequence[Dict[str, Any]] | None,
        task_config: Dict[str, Any] | None = None,
        success: bool | None = None,
    ) -> Dict[str, Any]:
        items = [item for item in flow_items or [] if isinstance(item, dict)]
        events = _events(items)
        actions = _configured_actions(task_config)
        checks: list[DiagnosticCheck] = []
        checks.extend(cls._check_event_engine_started(events))
        result_status = _result_status(items)
        checks.extend(cls._check_expected_buttons(items, actions, bool(success), result_status))
        checks.extend(cls._check_captcha_path(items, events, actions, bool(success), result_status))
        checks.extend(cls._check_result(events, bool(success), result_status))
        checks.extend(cls._check_timeouts(items, events, bool(success)))
        checks.extend(cls._check_task_run_late_results(items, events))
        checks.extend(cls._check_client_rpc_late_results(items, events))
        checks.extend(cls._check_hard_timeout_late_results(items, events))
        checks.extend(cls._check_callback_text_progress(items, bool(success)))
        checks.extend(cls._check_callback_result_observability(items, events, bool(success)))
        checks.extend(cls._check_callback_outer_timeout_observability(items, bool(success)))
        checks.extend(cls._check_callback_recovery(items, events, bool(success)))
        checks.extend(cls._check_strict_image_choice(items))
        checks.extend(cls._check_button_without_callback_data(events))
        checks.extend(cls._check_button_callback_unconfirmed(items, events, bool(success)))
        checks.extend(cls._check_button_callback_released_for_retry(items, bool(success)))
        checks.extend(cls._check_startup_history_action_skipped(events))
        checks.extend(cls._check_response_action_advanced(events))
        checks.extend(cls._check_response_action_not_advanced(items, events, bool(success)))
        checks.extend(cls._check_no_duplicate_ocr_after_result(items, events))
        checks.extend(cls._check_failure_preempted_response_action(events))
        checks.extend(cls._check_message_skip_observability(items, bool(success)))
        checks.extend(cls._check_message_processing_cancelled(items, bool(success)))
        checks.extend(cls._check_message_unhandled_observability(items, bool(success)))
        checks.extend(cls._check_stale_attempt_marks(items, bool(success)))
        checks.extend(cls._check_stale_callback_texts(items, bool(success)))
        checks.extend(cls._check_history_hard_failure_skipped(events))
        checks.extend(cls._check_history_rescue(items, events, bool(success)))
        checks.extend(cls._check_history_duplicate_skip_observability(items))
        checks.extend(cls._check_history_unhandled_duplicate_observability(items))
        checks.extend(cls._check_history_scan_observability(items, bool(success)))
        checks.extend(cls._check_final_state_snapshot(items, bool(success)))
        checks.extend(cls._check_final_state_observability(items, bool(success)))
        checks.extend(cls._check_retry_budget_observability(items, events, bool(success)))
        checks.extend(cls._check_runtime_config(items))
        checks.extend(cls._check_worker_execution_contract(items))
        checks.extend(cls._check_worker_runtime_consistency(items))
        checks.extend(cls._check_account_lock_observability(items, events, bool(success)))
        checks.extend(cls._check_global_concurrency_observability(items, events, bool(success)))
        checks.extend(cls._check_client_cleanup_observability(items, events, bool(success)))
        checks.extend(cls._check_client_cleanup_manager_report(items))
        checks.extend(cls._check_cleanup_deferred_cancellation(items, events))
        checks.extend(cls._check_client_cleanup_late_results(items, events))
        checks.extend(cls._check_client_cleanup_lock_coverage(items, events, bool(success)))
        checks.extend(cls._check_task_failure_context(items, bool(success)))

        status = cls._overall_status(checks, success)
        return {
            "status": status,
            "summary": cls._summary(status, checks),
            "checks": [check.to_dict() for check in checks],
            "milestones": {
                "event_engine_started": EVENT_ENGINE_STARTED in events,
                "button_clicks": events.count(EVENT_ENGINE_BUTTON_CLICKED),
                "image_options": events.count("event_engine_image_option_selected"),
                "captchas": events.count("event_engine_captcha_recognized"),
                "captcha_result_text_preemptions": events.count(
                    EVENT_ENGINE_CAPTCHA_RESULT_TEXT_PREEMPTED
                ),
                "captcha_replies": sum(
                    1
                    for item in items
                    if _event(item) == "event_engine_captcha_sent"
                    and bool(_meta(item).get("reply_to_message"))
                ),
                "captcha_sends": events.count("event_engine_captcha_sent"),
                "response_messages_sent": events.count("event_engine_response_message_sent"),
                "success_matched": "event_engine_success_matched" in events or result_status == "success",
                "checked_matched": "event_engine_checked_matched" in events or result_status == "checked",
                "callback_texts": events.count("event_engine_callback_text_received"),
                "history_rescues": events.count("event_engine_history_rescue_started"),
                "tracked_history_rechecks": events.count("event_engine_history_tracked_message_rechecked"),
                "history_hard_failures_skipped": events.count("event_engine_history_hard_failure_skipped"),
                "timeouts": events.count(EVENT_ENGINE_TIMEOUT_STATE),
                "task_run_timeouts": events.count("task_run_timeout"),
                "task_run_late_cancelled": events.count("task_run_late_cancelled"),
                "task_run_late_completed": events.count("task_run_late_completed"),
                "task_run_late_exception": events.count("task_run_late_exception"),
                "client_startup_lock_timeouts": events.count("client_startup_lock_timeout"),
                "client_startup_retries": events.count("client_startup_retry_scheduled"),
                "client_exit_lock_timeouts": events.count("client_exit_lock_timeout"),
                "client_close_lock_timeouts": events.count("client_close_lock_timeout"),
                "client_rpc_timeouts": events.count("client_rpc_hard_timeout"),
                "client_rpc_late_cancelled": events.count("client_rpc_late_cancelled"),
                "client_rpc_late_completed": events.count("client_rpc_late_completed"),
                "client_rpc_late_exception": events.count("client_rpc_late_exception"),
                "final_state_snapshots": events.count(EVENT_ENGINE_FINAL_STATE),
                "final_state_status": _final_state_status(items),
                "response_action_timeouts": events.count("event_engine_response_action_timeout"),
                "send_rpc_timeouts": events.count("event_engine_initial_send_retryable_error")
                + events.count("event_engine_followup_send_retryable_error")
                + events.count("event_engine_response_send_retryable_error"),
                "media_rpc_timeouts": events.count("event_engine_media_download_retryable_error"),
                "ai_rpc_timeouts": events.count("event_engine_ai_retryable_error"),
                "message_retryable_errors": events.count("event_engine_message_retryable_error"),
                "hard_timeout_late_cancelled": events.count(EVENT_ENGINE_HARD_TIMEOUT_LATE_CANCELLED),
                "hard_timeout_late_completed": events.count(EVENT_ENGINE_HARD_TIMEOUT_LATE_COMPLETED),
                "hard_timeout_late_exception": events.count(EVENT_ENGINE_HARD_TIMEOUT_LATE_EXCEPTION),
                "failure_preemptions": events.count("event_engine_failure_preempted_response_action"),
                "unhandled_messages": events.count("event_engine_message_unhandled"),
                "message_skips": events.count("event_engine_message_skip_recorded"),
                "message_skip_duplicates": sum(
                    1
                    for item in items
                    if _event(item) == "event_engine_message_skip_recorded"
                    and _meta(item).get("reason") == "duplicate"
                ),
                "message_skip_finished": sum(
                    1
                    for item in items
                    if _event(item) == "event_engine_message_skip_recorded"
                    and _meta(item).get("reason") == "finished"
                ),
                "message_skip_non_inbound": sum(
                    1
                    for item in items
                    if _event(item) == "event_engine_message_skip_recorded"
                    and _meta(item).get("reason") == "non_inbound"
                ),
                "message_processing_cancelled": events.count(
                    EVENT_ENGINE_MESSAGE_PROCESSING_CANCELLED
                ),
                "stale_attempt_processed_marks": events.count(
                    "event_engine_stale_attempt_processed_mark_skipped"
                ),
                "stale_callback_texts": events.count("event_engine_stale_callback_text_skipped"),
                "buttons_without_callback_data": events.count("event_engine_button_without_callback_data"),
                "button_callback_unconfirmed": events.count("event_engine_button_callback_unconfirmed"),
                "button_callback_results": events.count(EVENT_ENGINE_BUTTON_CALLBACK_RESULT),
                "button_callback_released_for_retry": events.count(
                    EVENT_ENGINE_BUTTON_CALLBACK_RELEASED_FOR_RETRY
                ),
                "button_callback_outer_timeouts": events.count("event_engine_button_callback_outer_timeout"),
                "button_callback_exceptions": events.count("event_engine_button_callback_exception"),
                "button_callback_trusted_timeouts": sum(
                    1
                    for item in items
                    if _event(item) == EVENT_ENGINE_BUTTON_CALLBACK_RESULT
                    and _meta(item).get("callback_status") == "trusted_timeout"
                ),
                "startup_history_actions_skipped": events.count("event_engine_startup_history_action_skipped"),
                "history_scan_completions": events.count(EVENT_ENGINE_HISTORY_SCAN_COMPLETED),
                "history_scan_failures": events.count(EVENT_ENGINE_HISTORY_FAILED),
                "history_scan_cancelled": events.count(EVENT_ENGINE_HISTORY_SCAN_CANCELLED),
                "history_scan_concurrent_skipped": events.count("event_engine_history_scan_concurrent_skipped"),
                "history_duplicate_skipped": events.count("event_engine_history_duplicate_skipped"),
                "history_unhandled_duplicate_skipped": events.count(
                    "event_engine_history_unhandled_duplicate_skipped"
                ),
                "history_messages_filtered": events.count("event_engine_history_message_filtered"),
                "history_filtered_before_entry": sum(
                    1
                    for item in items
                    if _event(item) == "event_engine_history_message_filtered"
                    and _meta(item).get("reason") == "before_entry_untracked"
                ),
                "history_filtered_expired": sum(
                    1
                    for item in items
                    if _event(item) == "event_engine_history_message_filtered"
                    and _meta(item).get("reason") == "expired"
                ),
                "history_rescue_suspended": events.count("event_engine_history_rescue_suspended"),
                "response_action_advances": events.count("event_engine_response_action_advanced"),
                "response_action_not_advanced": events.count(
                    "event_engine_response_action_not_advanced"
                ),
                "retry_schedules": events.count("event_engine_retry_scheduled"),
                "retry_started": events.count("event_engine_retry_started"),
                "retry_completed": events.count("event_engine_retry_completed"),
                "retry_cancelled": events.count("event_engine_retry_cancelled"),
                "retry_suppressed": events.count("event_engine_retry_suppressed"),
                "retry_initial_send_failed": events.count(
                    "event_engine_retry_initial_send_failed"
                ),
                "retry_initial_send_error": events.count(
                    "event_engine_retry_initial_send_error"
                ),
                "retry_limit_exceeded": events.count("event_engine_retry_limit_exceeded"),
                "task_retry_config": events.count("task_retry_config"),
                "task_retry_scheduled": events.count("task_retry_scheduled"),
                "task_retry_started": events.count("task_retry_started"),
                "task_failures": events.count("task_failed"),
                "client_cleanup_started": events.count("client_cleanup_started"),
                "client_cleanup_completed": events.count("client_cleanup_completed"),
                "client_cleanup_failed": events.count("client_cleanup_failed"),
                "cleanup_deferred_cancellations": events.count(
                    "task_cancellation_deferred_for_cleanup"
                ),
                "client_cleanup_late_cancelled": events.count("client_cleanup_late_cancelled"),
                "client_cleanup_late_completed": events.count("client_cleanup_late_completed"),
                "client_cleanup_late_exception": events.count("client_cleanup_late_exception"),
                "account_lock_waits": events.count("account_lock_wait_started"),
                "account_lock_wait_timeouts": events.count("account_lock_wait_timeout"),
                "account_lock_acquired": events.count("account_lock_acquired"),
                "account_lock_released": events.count("account_lock_released"),
                "global_concurrency_waits": events.count("global_concurrency_wait_started"),
                "global_concurrency_wait_timeouts": events.count("global_concurrency_wait_timeout"),
                "global_concurrency_acquired": events.count("global_concurrency_acquired"),
                "global_concurrency_released": events.count("global_concurrency_released"),
                "runtime_config_logged": EVENT_TASK_RUNTIME_CONFIG in events,
                "worker_execution_contract_logged": EVENT_WORKER_EXECUTION_CONTRACT in events,
            },
        }

    @staticmethod
    def _check_event_engine_started(events: list[str]) -> list[DiagnosticCheck]:
        if EVENT_ENGINE_STARTED in events:
            return [DiagnosticCheck("event_engine_started", "事件引擎启动", "pass")]
        return [DiagnosticCheck("event_engine_started", "事件引擎启动", "fail", "日志中没有 event_engine_started。")]

    @staticmethod
    def _check_expected_buttons(
        items: Sequence[Dict[str, Any]],
        actions: Sequence[Dict[str, Any]],
        success: bool,
        result_status: str = "",
    ) -> list[DiagnosticCheck]:
        checks: list[DiagnosticCheck] = []
        expected = [str(action.get("text") or "").strip() for action in actions if int(action.get("action", 0) or 0) == 3]
        clicked = [
            _meta(item).get("button_text") or _text(item)
            for item in items
            if _event(item) == EVENT_ENGINE_BUTTON_CLICKED
        ]
        for index, target in enumerate(expected, start=1):
            if not target:
                continue
            if any(_contains(button, target) for button in clicked):
                checks.append(DiagnosticCheck(f"button_{index}", f"点击按钮「{target}」", "pass"))
            else:
                if success and result_status in {"success", "checked"}:
                    checks.append(
                        DiagnosticCheck(
                            f"button_{index}",
                            f"点击按钮「{target}」",
                            "skip",
                            "结果已由启动历史或补漏历史命中，未重复点击该按钮。",
                        )
                    )
                    continue
                checks.append(
                    DiagnosticCheck(
                        f"button_{index}",
                        f"点击按钮「{target}」",
                        "warn" if success else "fail",
                        "日志中没有看到对应 event_engine_button_clicked。",
                    )
                )
        return checks

    @staticmethod
    def _check_captcha_path(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        actions: Sequence[Dict[str, Any]],
        success: bool,
        result_status: str = "",
    ) -> list[DiagnosticCheck]:
        captcha_actions = [
            action
            for action in actions
            if int(action.get("action", 0) or 0) == 6
        ]
        expects_captcha = bool(captcha_actions)
        if not expects_captcha:
            return []
        checks: list[DiagnosticCheck] = []
        preempted_by_result = EVENT_ENGINE_CAPTCHA_RESULT_TEXT_PREEMPTED in events
        if preempted_by_result:
            checks.append(
                DiagnosticCheck(
                    "captcha_recognized",
                    "验证码识别",
                    "pass",
                    "OCR 结果已直接命中签到状态。",
                )
            )
        elif "event_engine_captcha_recognized" in events:
            checks.append(DiagnosticCheck("captcha_recognized", "验证码识别", "pass"))
        elif (
            "event_engine_checked_matched" in events
            or "event_engine_success_matched" in events
            or result_status in {"checked", "success"}
        ):
            checks.append(
                DiagnosticCheck(
                    "captcha_recognized",
                    "验证码识别",
                    "skip",
                    "结果已在验证码前或历史补漏中命中，未继续 OCR。",
                )
            )
            return checks
        else:
            checks.append(
                DiagnosticCheck(
                    "captcha_recognized",
                    "验证码识别",
                    "warn" if success else "fail",
                    "配置包含 action=6，但日志中没有识别验证码。",
                )
            )
            return checks

        sent_items = [
            item for item in items if _event(item) == "event_engine_captcha_sent"
        ]
        if sent_items:
            checks.append(DiagnosticCheck("captcha_sent", "验证码发送", "pass"))
        elif preempted_by_result and (
            "event_engine_checked_matched" in events
            or "event_engine_success_matched" in events
            or result_status in {"checked", "success"}
        ):
            checks.append(
                DiagnosticCheck(
                    "captcha_sent",
                    "验证码发送",
                    "skip",
                    "OCR 结果已命中签到状态，跳过验证码发送。",
                )
            )
        else:
            checks.append(
                DiagnosticCheck(
                    "captcha_sent",
                    "验证码发送",
                    "warn" if success else "fail",
                    "日志中没有看到 event_engine_captcha_sent。",
                )
            )
        if preempted_by_result and not sent_items:
            return checks
        if any(bool(action.get("reply_to_message")) for action in captcha_actions):
            if any(bool(_meta(item).get("reply_to_message")) for item in sent_items):
                checks.append(DiagnosticCheck("captcha_reply_to_message", "验证码回复原图", "pass"))
            else:
                checks.append(
                    DiagnosticCheck(
                        "captcha_reply_to_message",
                        "验证码回复原图",
                        "fail",
                        "配置要求 reply_to_message，但日志中没有看到验证码回复到原图。",
                    )
                )
        return checks

    @staticmethod
    def _check_result(
        events: list[str],
        success: bool,
        result_status: str = "",
    ) -> list[DiagnosticCheck]:
        if "event_engine_success_matched" in events:
            return [DiagnosticCheck("result_matched", "成功结果命中", "pass")]
        if "event_engine_checked_matched" in events:
            return [DiagnosticCheck("result_matched", "已签到结果命中", "pass")]
        if result_status == "success":
            return [
                DiagnosticCheck(
                    "result_matched",
                    "成功结果命中",
                    "pass",
                    "最终状态记录了成功结果。",
                )
            ]
        if result_status == "checked":
            return [
                DiagnosticCheck(
                    "result_matched",
                    "已签到结果命中",
                    "pass",
                    "最终状态记录了已签到结果。",
                )
            ]
        return [
            DiagnosticCheck(
                "result_matched",
                "结果关键字命中",
                "warn" if success else "fail",
                "没有看到 event_engine_success_matched 或 event_engine_checked_matched。",
            )
        ]

    @staticmethod
    def _check_timeouts(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        checks = []
        if "event_engine_timeout_state" not in events:
            checks.append(DiagnosticCheck("event_timeout", "事件引擎总超时", "pass"))
        else:
            checks.append(
                DiagnosticCheck(
                    "event_timeout",
                    "事件引擎总超时",
                    "warn" if success else "fail",
                    "日志出现 event_engine_timeout_state。",
                )
            )
        if "event_engine_response_action_timeout" in events:
            checks.append(
                DiagnosticCheck(
                    "event_response_action_timeout",
                    "事件响应动作超时",
                    "warn" if success else "fail",
                    "日志出现 event_engine_response_action_timeout。",
                )
            )
        if "event_engine_button_callback_outer_timeout" in events:
            timeout_items = [
                item for item in items if _event(item) == "event_engine_button_callback_outer_timeout"
            ]
            latest = _meta(timeout_items[-1])
            checks.append(
                DiagnosticCheck(
                    "event_callback_outer_timeout",
                    "按钮回调 RPC 外层超时",
                    "pass" if latest.get("trusted_timeout") else ("warn" if success else "fail"),
                    "count="
                    f"{len(timeout_items)}, source={latest.get('source') or '-'}, "
                    f"trusted={str(bool(latest.get('trusted_timeout'))).lower()}",
                )
            )
        send_timeout_events = [
            "event_engine_initial_send_retryable_error",
            "event_engine_followup_send_retryable_error",
            "event_engine_response_send_retryable_error",
        ]
        if any(event in events for event in send_timeout_events):
            send_items = [
                item for item in items if _event(item) in send_timeout_events
            ]
            latest = _meta(send_items[-1]) if send_items else {}
            checks.append(
                DiagnosticCheck(
                    "event_send_rpc_timeout",
                    "事件发送 RPC 超时",
                    "warn" if success else "fail",
                    "count="
                    f"{len(send_items)}, operation={latest.get('operation') or '-'}, "
                    f"timeout_scope={latest.get('timeout_scope') or '-'}, "
                    f"source={latest.get('source') or '-'}, timeout={latest.get('timeout', '-')}s",
                )
            )
        if "event_engine_media_download_retryable_error" in events:
            media_items = [
                item for item in items if _event(item) == "event_engine_media_download_retryable_error"
            ]
            latest = _meta(media_items[-1])
            checks.append(
                DiagnosticCheck(
                    "event_media_rpc_timeout",
                    "事件媒体下载 RPC 超时",
                    "warn" if success else "fail",
                    "count="
                    f"{len(media_items)}, timeout_scope={latest.get('timeout_scope') or '-'}, "
                    f"source={latest.get('source') or '-'}, timeout={latest.get('timeout', '-')}s",
                )
            )
        if "event_engine_ai_retryable_error" in events:
            ai_items = [
                item for item in items if _event(item) == "event_engine_ai_retryable_error"
            ]
            latest = _meta(ai_items[-1])
            checks.append(
                DiagnosticCheck(
                    "event_ai_rpc_timeout",
                    "事件 AI/OCR 调用超时",
                    "warn" if success else "fail",
                    "count="
                    f"{len(ai_items)}, operation={latest.get('operation') or '-'}, "
                    f"timeout_scope={latest.get('timeout_scope') or '-'}, "
                    f"source={latest.get('source') or '-'}, timeout={latest.get('timeout', '-')}s",
                )
            )
        if "event_engine_message_retryable_error" in events:
            message_items = [
                item for item in items if _event(item) == "event_engine_message_retryable_error"
            ]
            latest = _meta(message_items[-1])
            checks.append(
                DiagnosticCheck(
                    "event_message_retryable_error",
                    "消息处理可重试错误",
                    "warn" if success else "fail",
                    "count="
                    f"{len(message_items)}, message_id={latest.get('message_id') or '-'}, "
                    f"error={latest.get('error_type') or '-'}, "
                    f"operation={latest.get('operation') or '-'}, "
                    f"current_action={latest.get('current_action') or '-'}, "
                    f"retry_budget_remaining={latest.get('retry_budget_remaining', '-')}",
                )
            )
        if "client_rpc_hard_timeout" in events:
            timeout_items = [
                item for item in items if _event(item) == "client_rpc_hard_timeout"
            ]
            latest = _meta(timeout_items[-1])
            cleanup_step_count = sum(
                1
                for item in timeout_items
                if str(_meta(item).get("operation") or "") == "cleanup_step"
            )
            checks.append(
                DiagnosticCheck(
                    "client_rpc_hard_timeout",
                    "Telegram client RPC 硬超时",
                    "warn" if success else "fail",
                    f"count={len(timeout_items)}, "
                    f"cleanup_steps={cleanup_step_count}, "
                    f"operation={latest.get('operation') or '-'}, "
                    f"timeout={latest.get('timeout', '-')}s, "
                    f"source={latest.get('source') or '-'}, "
                    f"timeout_scope={latest.get('timeout_scope') or '-'}",
                )
            )
        if "client_startup_retry_scheduled" in events:
            retry_items = [
                item for item in items if _event(item) == "client_startup_retry_scheduled"
            ]
            latest = _meta(retry_items[-1])
            checks.append(
                DiagnosticCheck(
                    "client_startup_retry_scheduled",
                    "Telegram client 启动失败后重试",
                    "warn",
                    f"count={len(retry_items)}, "
                    f"attempt={latest.get('attempt') or '-'}/{latest.get('total_attempts') or '-'}, "
                    f"remaining={latest.get('retry_budget_remaining') if latest.get('retry_budget_remaining') is not None else '-'}, "
                    f"wait={latest.get('wait_seconds', '-')}s, "
                    f"cleanup_attempted={str(bool(latest.get('cleanup_attempted'))).lower()}, "
                    f"reason={latest.get('reason') or '-'}, "
                    f"error={latest.get('error_type') or '-'}",
                )
            )
        if "task_run_timeout" in events:
            latest = _meta([item for item in items if _event(item) == "task_run_timeout"][-1])
            detail = f"timeout={latest.get('timeout_seconds', '-')}s"
            if latest.get("operation"):
                detail += f", operation={latest.get('operation')}"
            if latest.get("timeout_scope"):
                detail += f", timeout_scope={latest.get('timeout_scope')}"
            if latest.get("run_task_cancelled") is not None:
                detail += f", run_task_cancelled={str(bool(latest.get('run_task_cancelled'))).lower()}"
            if latest.get("cleanup_expected") is not None:
                detail += f", cleanup_expected={str(bool(latest.get('cleanup_expected'))).lower()}"
            checks.append(
                DiagnosticCheck(
                    "task_run_timeout",
                    "Worker 单次执行总超时",
                    "warn" if success else "fail",
                    detail,
                )
            )
        if "client_startup_lock_timeout" in events:
            latest = _meta(
                [item for item in items if _event(item) == "client_startup_lock_timeout"][-1]
            )
            checks.append(
                DiagnosticCheck(
                    "client_startup_lock_timeout",
                    "Telegram client 启动锁超时",
                    "warn" if success else "fail",
                    f"count={events.count('client_startup_lock_timeout')}, "
                    f"timeout={latest.get('timeout_seconds', '-')}s",
                )
            )
        if "client_exit_lock_timeout" in events:
            latest = _meta(
                [item for item in items if _event(item) == "client_exit_lock_timeout"][-1]
            )
            checks.append(
                DiagnosticCheck(
                    "client_exit_lock_timeout",
                    "Telegram client 退出锁超时",
                    "warn" if success else "fail",
                    f"count={events.count('client_exit_lock_timeout')}, "
                    f"timeout={latest.get('timeout_seconds', '-')}s",
                )
            )
        if "client_close_lock_timeout" in events:
            latest = _meta(
                [item for item in items if _event(item) == "client_close_lock_timeout"][-1]
            )
            checks.append(
                DiagnosticCheck(
                    "client_close_lock_timeout",
                    "Telegram client 清理锁超时",
                    "warn" if success else "fail",
                    f"count={events.count('client_close_lock_timeout')}, "
                    f"timeout={latest.get('timeout_seconds', '-')}s",
                )
            )
        return checks

    @staticmethod
    def _check_task_run_late_results(
        items: Sequence[Dict[str, Any]],
        events: list[str],
    ) -> list[DiagnosticCheck]:
        late_events = {
            "task_run_late_cancelled",
            "task_run_late_completed",
            "task_run_late_exception",
        }
        if not any(event in late_events for event in events):
            return []
        latest = next(item for item in reversed(items) if _event(item) in late_events)
        latest_event = _event(latest)
        latest_meta = _meta(latest)
        detail = (
            f"cancelled={events.count('task_run_late_cancelled')}, "
            f"completed={events.count('task_run_late_completed')}, "
            f"exception={events.count('task_run_late_exception')}, "
            f"latest={latest_event}, "
            f"timeout={latest_meta.get('timeout_seconds', '-')}s, "
            f"attempt={latest_meta.get('attempt', '-')}/"
            f"{latest_meta.get('total_attempts', '-')}"
        )
        if latest_meta.get("operation"):
            detail += f", operation={latest_meta.get('operation')}"
        if latest_meta.get("timeout_scope"):
            detail += f", timeout_scope={latest_meta.get('timeout_scope')}"
        if latest_meta.get("error_type"):
            detail += f", error={latest_meta.get('error_type')}"
        status = "pass" if latest_event == "task_run_late_cancelled" else "warn"
        return [
            DiagnosticCheck(
                "task_run_late_result",
                "Worker 总超时后台任务晚到结果",
                status,
                detail,
            )
        ]

    @staticmethod
    def _check_client_rpc_late_results(
        items: Sequence[Dict[str, Any]],
        events: list[str],
    ) -> list[DiagnosticCheck]:
        late_events = {
            "client_rpc_late_cancelled",
            "client_rpc_late_completed",
            "client_rpc_late_exception",
        }
        if not any(event in late_events for event in events):
            return []
        latest = next(item for item in reversed(items) if _event(item) in late_events)
        latest_event = _event(latest)
        latest_meta = _meta(latest)
        detail = (
            f"cancelled={events.count('client_rpc_late_cancelled')}, "
            f"completed={events.count('client_rpc_late_completed')}, "
            f"exception={events.count('client_rpc_late_exception')}, "
            f"latest={latest_event}, "
            f"operation={latest_meta.get('operation') or '-'}, "
            f"timeout={latest_meta.get('timeout', '-')}s"
        )
        if latest_meta.get("timeout_scope"):
            detail += f", timeout_scope={latest_meta.get('timeout_scope')}"
        if latest_meta.get("error_type"):
            detail += f", error={latest_meta.get('error_type')}"
        status = "pass" if latest_event == "client_rpc_late_cancelled" else "warn"
        return [
            DiagnosticCheck(
                "client_rpc_late_result",
                "Telegram client RPC 超时后台结果",
                status,
                detail,
            )
        ]

    @staticmethod
    def _check_hard_timeout_late_results(
        items: Sequence[Dict[str, Any]],
        events: list[str],
    ) -> list[DiagnosticCheck]:
        if not any(event in events for event in EVENT_ENGINE_HARD_TIMEOUT_LATE_EVENTS):
            return []
        latest = next(item for item in reversed(items) if _event(item) in EVENT_ENGINE_HARD_TIMEOUT_LATE_EVENTS)
        latest_event = _event(latest)
        latest_meta = _meta(latest)
        validation = validate_hard_timeout_late_snapshot(
            latest_meta,
            event_name=latest_event,
        )
        parent_cancelled = sum(
            1
            for item in items
            if _event(item) in EVENT_ENGINE_HARD_TIMEOUT_LATE_EVENTS
            and bool(_meta(item).get("cancelled_by_parent"))
        )
        detail = (
            f"cancelled={events.count(EVENT_ENGINE_HARD_TIMEOUT_LATE_CANCELLED)}, "
            f"completed={events.count(EVENT_ENGINE_HARD_TIMEOUT_LATE_COMPLETED)}, "
            f"exception={events.count(EVENT_ENGINE_HARD_TIMEOUT_LATE_EXCEPTION)}, "
            f"parent_cancelled={parent_cancelled}, "
            f"latest={latest_event}, "
            + ", ".join(validation.detail_parts)
        )
        status = (
            "pass"
            if validation.valid and latest_event == EVENT_ENGINE_HARD_TIMEOUT_LATE_CANCELLED
            else "warn"
        )
        return [
            DiagnosticCheck(
                "event_hard_timeout_late_result",
                "硬超时后台任务晚到结果",
                status,
                detail,
            )
        ]

    @staticmethod
    def _check_callback_recovery(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        trusted_indexes = [
            index for index, item in enumerate(items) if _event(item) == "callback_timeout_trusted"
        ]
        if not trusted_indexes:
            return []
        recovered = False
        recovery_events = {
            "event_engine_button_clicked",
            "event_engine_captcha_recognized",
            "event_engine_success_matched",
            "event_engine_checked_matched",
            "event_engine_completed",
            EVENT_ENGINE_FINAL_STATE,
        }
        for index in trusted_indexes:
            if any(_event(item) in recovery_events for item in items[index + 1 :]):
                recovered = True
                break
        return [
            DiagnosticCheck(
                "trusted_callback_timeout_recovery",
                "可信按钮超时后继续推进",
                "pass" if recovered else ("warn" if success else "fail"),
                "" if recovered else "callback timeout 已按点击处理，但之后没有看到推进事件。",
            )
        ]

    @staticmethod
    def _check_callback_result_observability(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        result_items = [
            item for item in items if _event(item) == EVENT_ENGINE_BUTTON_CALLBACK_RESULT
        ]
        if not result_items:
            return []
        statuses = [_clean(_meta(item).get("callback_status")) or "unknown" for item in result_items]
        validations = [
            validate_button_callback_result_snapshot(_meta(item))
            for item in result_items
        ]
        missing_budget = [
            validation.engine or "unknown"
            for validation in validations
            if not validation.valid
        ]
        unconfirmed = [
            status for status, item in zip(statuses, result_items)
            if not bool(_meta(item).get("confirmed"))
        ]
        trusted = [status for status in statuses if status in {"trusted_timeout", "data_invalid_after_timeout"}]
        if missing_budget:
            return [
                DiagnosticCheck(
                    "button_callback_result_observable",
                    "按钮回调结果可观测",
                    "warn" if success else "fail",
                    "回调结果缺少预算上下文: "
                    + ", ".join(sorted(set(str(item) for item in missing_budget)))
                    + "; missing="
                    + ",".join(
                        sorted(
                            {
                                key
                                for validation in validations
                                for key in validation.missing_required
                            }
                        )
                    ),
                )
            ]
        if unconfirmed:
            latest = _meta(result_items[-1])
            latest_validation = validate_button_callback_result_snapshot(latest)
            return [
                DiagnosticCheck(
                    "button_callback_result_observable",
                    "按钮回调结果可观测",
                    "warn" if success else "fail",
                    "存在未确认回调: "
                    + ", ".join(sorted(set(unconfirmed)))
                    + "; "
                    + ", ".join(latest_validation.detail_parts),
                )
            ]
        if trusted:
            latest = _meta(result_items[-1])
            latest_validation = validate_button_callback_result_snapshot(latest)
            return [
                DiagnosticCheck(
                    "button_callback_result_observable",
                    "按钮回调结果可观测",
                    "pass",
                    "可信消费回调: "
                    + ", ".join(sorted(set(trusted)))
                    + "; "
                    + ", ".join(latest_validation.detail_parts),
                )
            ]
        return [
            DiagnosticCheck(
                "button_callback_result_observable",
                "按钮回调结果可观测",
                "pass",
                "回调状态: " + ", ".join(sorted(set(statuses))),
            )
        ]

    @staticmethod
    def _check_callback_outer_timeout_observability(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        timeout_items = [
            item for item in items if _event(item) == "event_engine_button_callback_outer_timeout"
        ]
        exception_items = [
            item for item in items if _event(item) == "event_engine_button_callback_exception"
        ]
        if not timeout_items and not exception_items:
            return []
        if exception_items:
            latest = _meta(exception_items[-1])
            return [
                DiagnosticCheck(
                    "button_callback_outer_timeout_observable",
                    "按钮回调外层超时兜底",
                    "warn" if success else "fail",
                    "callback_exceptions="
                    f"{len(exception_items)}, source={latest.get('source') or '-'}, "
                    f"error={latest.get('error_type') or '-'}, "
                    f"operation_timeout={latest.get('operation_timeout') or '-'}, "
                    f"callback_timeout={latest.get('callback_timeout') or '-'}, "
                    f"attempt_epoch={latest.get('attempt_epoch', '-')}, "
                    f"current_action={latest.get('current_action') or '-'}, "
                    f"retry_budget_remaining={latest.get('retry_budget_remaining', '-')}, "
                    f"retry_pending={str(bool(latest.get('retry_pending'))).lower()}",
                )
            ]
        latest = _meta(timeout_items[-1])
        trusted = bool(latest.get("trusted_timeout"))
        return [
            DiagnosticCheck(
                "button_callback_outer_timeout_observable",
                "按钮回调外层超时兜底",
                "pass" if trusted else ("warn" if success else "fail"),
                "count="
                f"{len(timeout_items)}, source={latest.get('source') or '-'}, "
                f"trusted={str(trusted).lower()}, "
                f"operation_timeout={latest.get('operation_timeout') or '-'}, "
                f"callback_timeout={latest.get('callback_timeout') or '-'}, "
                f"attempt_epoch={latest.get('attempt_epoch', '-')}, "
                f"current_action={latest.get('current_action') or '-'}, "
                f"retry_budget_remaining={latest.get('retry_budget_remaining', '-')}, "
                f"retry_pending={str(bool(latest.get('retry_pending'))).lower()}",
            )
        ]

    @staticmethod
    def _check_callback_text_progress(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        callback_indexes = [
            index for index, item in enumerate(items) if _event(item) == "event_engine_callback_text_received"
        ]
        if not callback_indexes:
            return []
        progress_events = {
            "event_engine_success_matched",
            "event_engine_checked_matched",
            EVENT_ENGINE_FINAL_STATE,
            "event_engine_retry_scheduled",
            "event_engine_failed_matched",
            "event_engine_account_failed",
            "task_retry_scheduled",
            "task_failed",
        }
        progressed = False
        for index in callback_indexes:
            if any(_event(item) in progress_events for item in items[index + 1 :]):
                progressed = True
                break
        return [
            DiagnosticCheck(
                "callback_text_progress",
                "按钮弹窗状态推进",
                "pass" if progressed else ("warn" if success else "fail"),
                "" if progressed else "收到按钮弹窗文本，但后续没有看到结果、失败或重试事件。",
            )
        ]

    @staticmethod
    def _check_strict_image_choice(items: Sequence[Dict[str, Any]]) -> list[DiagnosticCheck]:
        image_indexes = [
            index for index, item in enumerate(items) if _event(item) == "event_engine_image_option_selected"
        ]
        if not image_indexes:
            return []
        boundary_events = {
            "event_engine_button_clicked",
            "event_engine_captcha_recognized",
            "event_engine_success_matched",
            "event_engine_checked_matched",
            "event_engine_completed",
            EVENT_ENGINE_FINAL_STATE,
            "event_engine_retry_scheduled",
        }
        for image_index in image_indexes:
            for item in items[image_index + 1 :]:
                event = _event(item)
                if event == "callback_timeout_trusted":
                    return [
                        DiagnosticCheck(
                            "strict_image_choice",
                            "图片选项题严格回调",
                            "fail",
                            "action=4 出现 callback_timeout_trusted，可能误把未确认点击当成功。",
                        )
                    ]
                if event in boundary_events:
                    break
        return [DiagnosticCheck("strict_image_choice", "图片选项题严格回调", "pass")]

    @staticmethod
    def _check_button_without_callback_data(events: list[str]) -> list[DiagnosticCheck]:
        if "event_engine_button_without_callback_data" not in events:
            return []
        return [
            DiagnosticCheck(
                "button_without_callback_data",
                "无回调按钮跳过",
                "pass",
                "事件引擎跳过了匹配文本但无法回调的按钮。",
            )
        ]

    @staticmethod
    def _check_button_callback_unconfirmed(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        if "event_engine_button_callback_unconfirmed" not in events:
            return []
        unconfirmed_items = [
            item for item in items if _event(item) == "event_engine_button_callback_unconfirmed"
        ]
        if success and unconfirmed_items and all(_meta(item).get("source") == "startup_history" for item in unconfirmed_items):
            latest = _meta(unconfirmed_items[-1])
            validation = validate_button_callback_unconfirmed_snapshot(latest)
            return [
                DiagnosticCheck(
                    "button_callback_unconfirmed",
                    "旧历史按钮回调未确认已隔离",
                    "pass" if validation.valid else "warn",
                    "启动历史旧按钮回调未确认，但事件引擎未推进该步骤，并由后续新消息完成任务。"
                    + " "
                    + ", ".join(validation.detail_parts),
                )
            ]
        latest = _meta(unconfirmed_items[-1])
        validation = validate_button_callback_unconfirmed_snapshot(latest)
        return [
            DiagnosticCheck(
                "button_callback_unconfirmed",
                "按钮回调未确认",
                "warn" if success else "fail",
                "按钮回调未确认；事件引擎会允许后续消息或历史补漏重试。"
                + " "
                + ", ".join(validation.detail_parts),
            )
        ]

    @staticmethod
    def _check_button_callback_released_for_retry(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        released_items = [
            item
            for item in items
            if _event(item) == EVENT_ENGINE_BUTTON_CALLBACK_RELEASED_FOR_RETRY
        ]
        if not released_items:
            return []
        latest = _meta(released_items[-1])
        validation = validate_button_callback_released_snapshot(latest)
        detail = ", ".join(validation.detail_parts)
        return [
            DiagnosticCheck(
                "button_callback_released_for_retry",
                "未确认回调释放点击版本",
                "pass" if validation.valid else ("warn" if success else "fail"),
                detail,
            )
        ]

    @staticmethod
    def _check_startup_history_action_skipped(events: list[str]) -> list[DiagnosticCheck]:
        if "event_engine_startup_history_action_skipped" not in events:
            return []
        return [
            DiagnosticCheck(
                "startup_history_action_skipped",
                "启动历史旧交互跳过",
                "pass",
                "启动历史中的旧按钮或旧挑战未推进当前流程，会等待 fresh 入口后的新消息。",
            )
        ]

    @staticmethod
    def _check_response_action_advanced(events: list[str]) -> list[DiagnosticCheck]:
        if "event_engine_response_action_advanced" not in events:
            return []
        return [
            DiagnosticCheck(
                "response_action_advanced",
                "消息驱动动作推进",
                "pass",
                "事件引擎记录了消息触发的响应动作推进，便于确认流程不是按脚本盲目前进。",
            )
        ]

    @staticmethod
    def _check_response_action_not_advanced(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        if "event_engine_response_action_not_advanced" not in events:
            return []
        latest = next(
            item
            for item in reversed(items)
            if _event(item) == "event_engine_response_action_not_advanced"
        )
        meta = _meta(latest)
        reason = str(meta.get("reason") or "")
        expected = reason in {"finished", "retry_pending"}
        detail = (
            f"reason={reason or '-'}, "
            f"index={meta.get('current_response_index', '-')}/"
            f"{meta.get('response_action_count', '-')}, "
            f"source={meta.get('source') or '-'}, "
            f"finished={str(bool(meta.get('finished'))).lower()}, "
            f"retry_pending={str(bool(meta.get('retry_pending'))).lower()}, "
            f"attempt_epoch={meta.get('attempt_epoch', '-')}, "
            f"message_id={meta.get('message_id', '-')}"
        )
        return [
            DiagnosticCheck(
                "response_action_not_advanced",
                "消息已处理但未推进动作",
                "pass" if expected else ("warn" if success else "fail"),
                detail,
            )
        ]

    @staticmethod
    def _check_no_duplicate_ocr_after_result(
        items: Sequence[Dict[str, Any]],
        events: list[str],
    ) -> list[DiagnosticCheck]:
        result_indexes = [
            index
            for index, item in enumerate(items)
            if _event(item) in {"event_engine_success_matched", "event_engine_checked_matched"}
            or (
                _event(item) == EVENT_ENGINE_FINAL_STATE
                and _normalize_result_status(_meta(item).get("status")) in {"success", "checked"}
            )
        ]
        if not result_indexes:
            return []
        first_result = min(result_indexes)
        if "event_engine_captcha_recognized" in events[first_result + 1 :]:
            return [
                DiagnosticCheck(
                    "no_ocr_after_result",
                    "结果命中后不再 OCR",
                    "fail",
                    "成功/已签到后仍出现 event_engine_captcha_recognized。",
                )
            ]
        return [DiagnosticCheck("no_ocr_after_result", "结果命中后不再 OCR", "pass")]

    @staticmethod
    def _check_failure_preempted_response_action(events: list[str]) -> list[DiagnosticCheck]:
        if "event_engine_failure_preempted_response_action" not in events:
            return []
        return [
            DiagnosticCheck(
                "failure_preempted_response_action",
                "失败提示阻止继续 OCR",
                "pass",
                "验证码/响应动作前已识别失败或重试提示，避免继续执行当前响应动作。",
            )
        ]

    @staticmethod
    def _check_history_hard_failure_skipped(events: list[str]) -> list[DiagnosticCheck]:
        if "event_engine_history_hard_failure_skipped" not in events:
            return []
        return [
            DiagnosticCheck(
                "history_hard_failure_skipped",
                "启动历史旧失败/重试跳过",
                "pass",
                "启动历史中的旧失败、重试或否定成功消息未作为本次任务结果使用。",
            )
        ]

    @staticmethod
    def _check_message_unhandled_observability(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        unhandled = [
            item for item in items if _event(item) == "event_engine_message_unhandled"
        ]
        if not unhandled:
            return []
        latest = _meta(unhandled[-1])
        validation = validate_message_unhandled_snapshot(latest)
        return [
            DiagnosticCheck(
                "message_unhandled_observable",
                "未消费消息可观测",
                "pass"
                if success and validation.valid
                else ("warn" if success or validation.valid else "fail"),
                ", ".join(validation.detail_parts),
            )
        ]

    @staticmethod
    def _check_message_skip_observability(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        skip_items = [
            item for item in items if _event(item) == "event_engine_message_skip_recorded"
        ]
        if not skip_items:
            return []
        reason_counts: dict[str, int] = {}
        for item in skip_items:
            reason = str(_meta(item).get("reason") or "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        latest = _meta(skip_items[-1])
        validation = validate_message_skip_snapshot(latest)
        details = ", ".join(
            f"{reason}={count}" for reason, count in sorted(reason_counts.items())
        )
        details += "; " + ", ".join(validation.detail_parts)
        if latest.get("outgoing"):
            details += ", latest_outgoing=true"
        if latest.get("from_self"):
            details += ", latest_from_self=true"
        return [
            DiagnosticCheck(
                "message_skip_observable",
                "跳过消息可观测",
                "pass"
                if success and validation.valid
                else ("warn" if success or validation.valid else "fail"),
                details,
            )
        ]

    @staticmethod
    def _check_message_processing_cancelled(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        cancelled_items = [
            item
            for item in items
            if _event(item) == EVENT_ENGINE_MESSAGE_PROCESSING_CANCELLED
        ]
        if not cancelled_items:
            return []
        latest = _meta(cancelled_items[-1])
        validation = validate_message_processing_cancelled_snapshot(latest)
        details = ", ".join(validation.detail_parts)
        return [
            DiagnosticCheck(
                "message_processing_cancelled",
                "消息处理取消可观测",
                "pass"
                if success and validation.valid
                else ("warn" if validation.valid else "fail"),
                details,
            )
        ]

    @staticmethod
    def _check_stale_attempt_marks(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        stale_items = [
            item for item in items if _event(item) == "event_engine_stale_attempt_processed_mark_skipped"
        ]
        if not stale_items:
            return []
        latest = _meta(stale_items[-1])
        return [
            DiagnosticCheck(
                "stale_attempt_processed_mark",
                "旧尝试消息标记已隔离",
                "pass" if success else "warn",
                f"count={len(stale_items)}, "
                f"message_epoch={latest.get('message_attempt_epoch', '-')}, "
                f"current_epoch={latest.get('current_attempt_epoch', '-')}",
            )
        ]

    @staticmethod
    def _check_stale_callback_texts(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        stale_items = [
            item for item in items if _event(item) == "event_engine_stale_callback_text_skipped"
        ]
        if not stale_items:
            return []
        latest = _meta(stale_items[-1])
        return [
            DiagnosticCheck(
                "stale_callback_text",
                "旧尝试按钮弹窗文本已隔离",
                "pass" if success else "warn",
                f"count={len(stale_items)}, "
                f"callback_epoch={latest.get('callback_attempt_epoch', '-')}, "
                f"current_epoch={latest.get('current_attempt_epoch', '-')}",
            )
        ]

    @staticmethod
    def _check_history_rescue(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        if "event_engine_history_tracked_message_rechecked" in events:
            return [
                DiagnosticCheck(
                    "tracked_history_recheck",
                    "历史已处理消息编辑复查",
                    "pass",
                    "运行期间复查了启动历史中已处理消息的编辑版本。",
                )
            ]
        if "event_engine_history_failed" not in events:
            return []
        return [
            DiagnosticCheck(
                "history_rescue_failure",
                "历史补漏失败隔离",
                "warn" if success else "fail",
                "读取历史消息失败；成功任务可接受，失败任务需要继续观察网络/RPC。",
            )
        ]

    @staticmethod
    def _check_history_duplicate_skip_observability(
        items: Sequence[Dict[str, Any]],
    ) -> list[DiagnosticCheck]:
        duplicate_items = [
            item for item in items if _event(item) == "event_engine_history_duplicate_skipped"
        ]
        if not duplicate_items:
            return []
        latest = _meta(duplicate_items[-1])
        return [
            DiagnosticCheck(
                "history_duplicate_skip_observable",
                "历史重复消息跳过可观测",
                "pass",
                "count="
                f"{latest.get('duplicate_count') or len(duplicate_items)}, "
                f"source={latest.get('source') or '-'}, "
                f"message_id={latest.get('message_id') or '-'}",
            )
        ]

    @staticmethod
    def _check_history_unhandled_duplicate_observability(
        items: Sequence[Dict[str, Any]],
    ) -> list[DiagnosticCheck]:
        skipped = [
            item
            for item in items
            if _event(item) == "event_engine_history_unhandled_duplicate_skipped"
        ]
        if not skipped:
            return []
        latest = _meta(skipped[-1])
        return [
            DiagnosticCheck(
                "history_unhandled_duplicate_skip_observable",
                "历史未处理重复跳过",
                "pass",
                "count="
                f"{latest.get('unhandled_duplicate_count', len(skipped))}, "
                f"source={latest.get('source') or '-'}",
            )
        ]

    @staticmethod
    def _check_history_scan_observability(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        scan_items = [
            item for item in items if _event(item) == EVENT_ENGINE_HISTORY_SCAN_COMPLETED
        ]
        timeout_items = [
            item for item in items if _event(item) == EVENT_ENGINE_TIMEOUT_STATE
        ]
        failed_items = [
            item for item in items if _event(item) == EVENT_ENGINE_HISTORY_FAILED
        ]
        suspended_items = [
            item for item in items if _event(item) == "event_engine_history_rescue_suspended"
        ]
        cancelled_items = [
            item for item in items if _event(item) == EVENT_ENGINE_HISTORY_SCAN_CANCELLED
        ]
        concurrent_skipped_items = [
            item for item in items if _event(item) == "event_engine_history_scan_concurrent_skipped"
        ]
        filtered_items = [
            item for item in items if _event(item) == "event_engine_history_message_filtered"
        ]
        if (
            not scan_items
            and not timeout_items
            and not failed_items
            and not suspended_items
            and not cancelled_items
            and not concurrent_skipped_items
            and not filtered_items
        ):
            return []
        latest_timeout_meta = _meta(timeout_items[-1]) if timeout_items else {}
        failed_scans = int(latest_timeout_meta.get("history_failed_scans") or 0)
        cancelled_scans = int(latest_timeout_meta.get("history_cancelled_scans") or 0) or len(
            cancelled_items
        )
        suspended = bool(latest_timeout_meta.get("history_rescue_suspended")) or bool(suspended_items)
        concurrent_skipped = int(latest_timeout_meta.get("history_concurrent_skipped") or 0) or len(
            concurrent_skipped_items
        )
        handled_scans = [
            item
            for item in scan_items
            if int(_meta(item).get("handled_count") or 0) > 0
        ]
        if concurrent_skipped:
            latest_skipped_meta = _meta(concurrent_skipped_items[-1]) if concurrent_skipped_items else {}
            source = latest_skipped_meta.get("source") or latest_timeout_meta.get("last_history_scan_source") or "-"
            return [
                DiagnosticCheck(
                    "history_scan_observable",
                    "历史补漏扫描状态",
                    "pass",
                    f"concurrent_skipped={concurrent_skipped}, source={source}, "
                    f"attempt_epoch={latest_skipped_meta.get('attempt_epoch', '-')}, "
                    f"current_response_index={latest_skipped_meta.get('current_response_index', '-')}, "
                    f"current_action={latest_skipped_meta.get('current_action') or '-'}, "
                    f"retry_budget_remaining={latest_skipped_meta.get('retry_budget_remaining', '-')}, "
                    f"retry_pending={str(bool(latest_skipped_meta.get('retry_pending'))).lower()}",
                )
            ]
        if cancelled_items or cancelled_scans > 0:
            latest_cancelled_meta = _meta(cancelled_items[-1]) if cancelled_items else {}
            validation = validate_history_scan_cancelled_snapshot(latest_cancelled_meta)
            detail = ", ".join(validation.detail_parts)
            detail += f", total_cancelled={cancelled_scans}"
            return [
                DiagnosticCheck(
                    "history_scan_cancelled",
                    "历史扫描取消隔离",
                    "pass" if validation.valid else "fail",
                    detail,
                )
            ]
        if failed_items or failed_scans > 0 or suspended:
            detail = f"history_failed_scans={failed_scans or len(failed_items)}"
            latest_failed_meta = _meta(failed_items[-1]) if failed_items else {}
            failed_validation = validate_history_failed_snapshot(latest_failed_meta)
            error_type = (
                latest_timeout_meta.get("last_history_scan_error_type")
                or latest_failed_meta.get("error_type")
            )
            if error_type:
                detail += f", error={error_type}"
            source = (
                latest_timeout_meta.get("last_history_scan_source")
                or latest_failed_meta.get("source")
            )
            if source:
                detail += f", source={source}"
            timeout_scope = (
                latest_timeout_meta.get("last_history_scan_timeout_scope")
                or latest_failed_meta.get("timeout_scope")
            )
            if timeout_scope:
                detail += f", timeout_scope={timeout_scope}"
            if latest_failed_meta.get("will_open_circuit") is not None:
                detail += f", will_open_circuit={str(bool(latest_failed_meta.get('will_open_circuit'))).lower()}"
            if latest_failed_meta.get("rescue_will_continue") is not None:
                detail += (
                    ", rescue_will_continue="
                    f"{str(bool(latest_failed_meta.get('rescue_will_continue'))).lower()}"
                )
            if latest_failed_meta.get("failed_scans") is not None:
                detail += f", failed_scans={latest_failed_meta.get('failed_scans')}"
            if latest_failed_meta.get("scan_in_progress") is not None:
                detail += (
                    ", scan_in_progress="
                    f"{str(bool(latest_failed_meta.get('scan_in_progress'))).lower()}"
                )
            if latest_failed_meta.get("blocks_main_flow") is not None:
                detail += (
                    ", blocks_main_flow="
                    f"{str(bool(latest_failed_meta.get('blocks_main_flow'))).lower()}"
                )
            if latest_failed_meta.get("retry_pending") is not None:
                detail += (
                    ", retry_pending="
                    f"{str(bool(latest_failed_meta.get('retry_pending'))).lower()}"
                )
            if latest_failed_meta.get("attempt_epoch") is not None:
                detail += f", attempt_epoch={latest_failed_meta.get('attempt_epoch')}"
            if latest_failed_meta.get("current_response_index") is not None:
                detail += (
                    ", current_response_index="
                    f"{latest_failed_meta.get('current_response_index')}"
                )
            if latest_failed_meta.get("current_action"):
                detail += f", current_action={latest_failed_meta.get('current_action')}"
            if latest_failed_meta.get("retry_budget_remaining") is not None:
                detail += (
                    ", retry_budget_remaining="
                    f"{latest_failed_meta.get('retry_budget_remaining')}"
                )
            if latest_failed_meta and not failed_validation.valid:
                detail += "; missing=" + ",".join(failed_validation.missing_required)
            if suspended:
                latest_suspended_meta = _meta(suspended_items[-1]) if suspended_items else {}
                threshold = (
                    latest_timeout_meta.get("history_failure_threshold")
                    or latest_suspended_meta.get("failure_threshold")
                    or "-"
                )
                detail += f", rescue_suspended=true, threshold={threshold}"
                if latest_suspended_meta.get("source"):
                    detail += f", suspended_source={latest_suspended_meta.get('source')}"
                if latest_suspended_meta.get("attempt_epoch") is not None:
                    detail += (
                        ", suspended_attempt_epoch="
                        f"{latest_suspended_meta.get('attempt_epoch')}"
                    )
                if latest_suspended_meta.get("current_response_index") is not None:
                    detail += (
                        ", suspended_current_response_index="
                        f"{latest_suspended_meta.get('current_response_index')}"
                    )
                if latest_suspended_meta.get("retry_budget_remaining") is not None:
                    detail += (
                        ", suspended_retry_budget_remaining="
                        f"{latest_suspended_meta.get('retry_budget_remaining')}"
                    )
            return [
                DiagnosticCheck(
                    "history_scan_observable",
                    "历史补漏扫描状态",
                    "warn" if success else "fail",
                    detail,
                )
            ]
        if handled_scans:
            latest = _meta(handled_scans[-1])
            validation = validate_history_scan_completed_snapshot(latest)
            detail = ", ".join(validation.detail_parts)
            if filtered_items:
                latest_filtered = _meta(filtered_items[-1])
                detail += (
                    f", filtered_before_entry={latest_filtered.get('filtered_before_entry') or 0}"
                    f", filtered_expired={latest_filtered.get('filtered_expired') or 0}"
                )
            return [
                DiagnosticCheck(
                    "history_scan_observable",
                    "历史补漏扫描状态",
                    "pass" if validation.valid else ("warn" if success else "fail"),
                    detail,
                )
            ]
        if scan_items:
            latest = _meta(scan_items[-1])
            validation = validate_history_scan_completed_snapshot(latest)
            detail = ", ".join(validation.detail_parts)
            if filtered_items:
                latest_filtered = _meta(filtered_items[-1])
                detail += (
                    f", filtered_before_entry={latest_filtered.get('filtered_before_entry') or 0}"
                    f", filtered_expired={latest_filtered.get('filtered_expired') or 0}"
                )
            return [
                DiagnosticCheck(
                    "history_scan_observable",
                    "历史补漏扫描状态",
                    "pass" if validation.valid else ("warn" if success else "fail"),
                    detail,
                )
            ]
        if filtered_items:
            latest_filtered = _meta(filtered_items[-1])
            return [
                DiagnosticCheck(
                    "history_scan_observable",
                    "历史补漏扫描状态",
                    "pass",
                    "filtered_before_entry="
                    f"{latest_filtered.get('filtered_before_entry') or 0}, "
                    f"filtered_expired={latest_filtered.get('filtered_expired') or 0}",
                )
            ]
        return []

    @staticmethod
    def _check_final_state_snapshot(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        final_items = [item for item in items if _event(item) == EVENT_ENGINE_FINAL_STATE]
        if not final_items:
            if not any(_event(item) == "event_engine_completed" for item in items):
                return []
            return [
                DiagnosticCheck(
                    "final_state_snapshot",
                    "事件最终状态快照",
                    "skip",
                    "历史日志中没有 event_engine_final_state，无法直接查看最终计数。",
                )
            ]
        meta = _meta(final_items[-1])
        required = {"status", "current_response_index", "retry_count", "callback_confirmed"}
        missing = sorted(key for key in required if key not in meta)
        if missing:
            return [
                DiagnosticCheck(
                    "final_state_snapshot",
                    "事件最终状态快照",
                    "warn" if success else "fail",
                    "event_engine_final_state 缺少字段: " + ", ".join(missing),
                )
            ]
        return [
            DiagnosticCheck(
                "final_state_snapshot",
                "事件最终状态快照",
                "pass",
                f"status={meta.get('status')}, actions={meta.get('current_response_index')}/{meta.get('response_action_count')}, retries={meta.get('retry_count')}",
            )
        ]

    @staticmethod
    def _check_final_state_observability(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        final_items = [item for item in items if _event(item) == EVENT_ENGINE_FINAL_STATE]
        if not final_items:
            return []
        meta = _meta(final_items[-1])
        validation = validate_event_final_state_snapshot(meta)
        if not validation.current:
            return [
                DiagnosticCheck(
                    "final_state_observability",
                    "最终状态观测字段",
                    "skip",
                    "历史或精简日志未包含当前快照标记，跳过核心观测字段校验。",
                )
            ]

        if not validation.valid:
            return [
                DiagnosticCheck(
                    "final_state_observability",
                    "最终状态观测字段",
                    "warn" if success else "fail",
                    "event_engine_final_state 缺少核心观测字段: "
                    + ", ".join(validation.missing_required),
                )
            ]

        return [
            DiagnosticCheck(
                "final_state_observability",
                "最终状态观测字段",
                "pass",
                ", ".join(validation.detail_parts),
            )
        ]

    @staticmethod
    def _check_retry_budget_observability(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        checks: list[DiagnosticCheck] = []
        task_retry_config = [item for item in items if _event(item) == "task_retry_config"]
        if task_retry_config:
            latest = _meta(task_retry_config[-1])
            missing = [
                key
                for key in ("retry_count", "total_attempts", "retry_budget_remaining")
                if key not in latest
            ]
            detail = (
                f"retry_count={latest.get('retry_count', '-')}, "
                f"attempts={latest.get('total_attempts', '-')}, "
                f"remaining={latest.get('retry_budget_remaining', '-')}"
            )
            if missing:
                detail += ", missing=" + ",".join(missing)
            checks.append(
                DiagnosticCheck(
                    "task_retry_config",
                    "Worker 任务级重试配置",
                    "pass" if not missing else ("warn" if success else "fail"),
                    detail,
                )
            )
        task_retry_scheduled = [
            item for item in items if _event(item) == "task_retry_scheduled"
        ]
        if task_retry_scheduled:
            latest = _meta(task_retry_scheduled[-1])
            missing = [
                key
                for key in (
                    "attempt",
                    "total_attempts",
                    "retry_count",
                    "retry_budget_remaining",
                    "error_type",
                    "retryable",
                )
                if key not in latest
            ]
            detail = (
                f"attempt={latest.get('attempt', '-')}/{latest.get('total_attempts', '-')}, "
                f"retry_count={latest.get('retry_count', '-')}, "
                f"remaining={latest.get('retry_budget_remaining', '-')}, "
                f"error={latest.get('error_type') or '-'}, "
                f"retryable={str(bool(latest.get('retryable'))).lower()}"
            )
            if missing:
                detail += ", missing=" + ",".join(missing)
            checks.append(
                DiagnosticCheck(
                    "task_retry_scheduled",
                    "Worker 任务级重试调度",
                    "pass" if not missing else ("warn" if success else "fail"),
                    detail,
                )
            )
        task_retry_started = [item for item in items if _event(item) == "task_retry_started"]
        if task_retry_started:
            latest = _meta(task_retry_started[-1])
            missing = [
                key
                for key in (
                    "attempt",
                    "total_attempts",
                    "retry_count",
                    "retry_budget_remaining",
                )
                if key not in latest
            ]
            detail = (
                f"attempt={latest.get('attempt', '-')}/{latest.get('total_attempts', '-')}, "
                f"retry_count={latest.get('retry_count', '-')}, "
                f"remaining={latest.get('retry_budget_remaining', '-')}"
            )
            if missing:
                detail += ", missing=" + ",".join(missing)
            checks.append(
                DiagnosticCheck(
                    "task_retry_started",
                    "Worker 任务级重试开始",
                    "pass" if not missing else ("warn" if success else "fail"),
                    detail,
                )
            )
        scheduled = [item for item in items if _event(item) == "event_engine_retry_scheduled"]
        if scheduled:
            latest = _meta(scheduled[-1])
            validation = validate_retry_event_snapshot(
                latest,
                event_name="event_engine_retry_scheduled",
            )
            checks.append(
                DiagnosticCheck(
                    "retry_scheduled",
                    "事件内部重试调度",
                    "pass" if validation.valid else ("warn" if success else "fail"),
                    ", ".join(validation.detail_parts),
                )
            )
        started = [item for item in items if _event(item) == "event_engine_retry_started"]
        if started:
            latest = _meta(started[-1])
            validation = validate_retry_event_snapshot(
                latest,
                event_name="event_engine_retry_started",
            )
            checks.append(
                DiagnosticCheck(
                    "retry_started",
                    "事件内部重试开始",
                    "pass" if validation.valid else ("warn" if success else "fail"),
                    ", ".join(validation.detail_parts),
                )
            )
        completed = [item for item in items if _event(item) == "event_engine_retry_completed"]
        if completed:
            latest = _meta(completed[-1])
            validation = validate_retry_event_snapshot(
                latest,
                event_name="event_engine_retry_completed",
            )
            checks.append(
                DiagnosticCheck(
                    "retry_completed",
                    "事件内部重试完成",
                    "pass" if validation.valid else ("warn" if success else "fail"),
                    ", ".join(validation.detail_parts),
                )
            )
        cancelled = [item for item in items if _event(item) == "event_engine_retry_cancelled"]
        if cancelled:
            latest = _meta(cancelled[-1])
            validation = validate_retry_event_snapshot(
                latest,
                event_name="event_engine_retry_cancelled",
            )
            checks.append(
                DiagnosticCheck(
                    "retry_cancelled",
                    "事件内部重试取消",
                    "warn" if success else "fail",
                    ", ".join(validation.detail_parts),
                )
            )
        suppressed = [item for item in items if _event(item) == "event_engine_retry_suppressed"]
        if suppressed:
            latest = _meta(suppressed[-1])
            validation = validate_retry_event_snapshot(
                latest,
                event_name="event_engine_retry_suppressed",
            )
            checks.append(
                DiagnosticCheck(
                    "retry_suppressed",
                    "重复重试信号抑制",
                    "pass" if validation.valid else ("warn" if success else "fail"),
                    ", ".join(validation.detail_parts),
                )
            )
        initial_send_failed = [
            item for item in items if _event(item) == "event_engine_retry_initial_send_failed"
        ]
        if initial_send_failed:
            latest = _meta(initial_send_failed[-1])
            validation = validate_retry_event_snapshot(
                latest,
                event_name="event_engine_retry_initial_send_failed",
            )
            checks.append(
                DiagnosticCheck(
                    "retry_initial_send_failed",
                    "事件内部重试入口发送失败",
                    "warn" if success else "fail",
                    ", ".join(validation.detail_parts),
                )
            )
        initial_send_error = [
            item for item in items if _event(item) == "event_engine_retry_initial_send_error"
        ]
        if initial_send_error:
            latest = _meta(initial_send_error[-1])
            validation = validate_retry_event_snapshot(
                latest,
                event_name="event_engine_retry_initial_send_error",
            )
            checks.append(
                DiagnosticCheck(
                    "retry_initial_send_error",
                    "事件内部重试入口异常",
                    "warn" if success else "fail",
                    ", ".join(validation.detail_parts),
                )
            )
        limit_exceeded = [
            item for item in items if _event(item) == "event_engine_retry_limit_exceeded"
        ]
        if limit_exceeded:
            latest = _meta(limit_exceeded[-1])
            validation = validate_retry_event_snapshot(
                latest,
                event_name="event_engine_retry_limit_exceeded",
            )
            checks.append(
                DiagnosticCheck(
                    "retry_limit_exceeded",
                    "事件内部重试耗尽",
                    "warn" if success else "fail",
                    "事件引擎内部重试预算已耗尽: "
                    + ", ".join(validation.detail_parts),
                )
            )
        return checks

    @staticmethod
    def _check_task_failure_context(
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        failures = [item for item in items if _event(item) == "task_failed"]
        if not failures:
            return []
        latest = failures[-1]
        meta = _meta(latest)
        required = {"error_type", "attempt", "total_attempts", "retryable"}
        missing = sorted(key for key in required if key not in meta)
        if not missing:
            return [
                DiagnosticCheck(
                    "task_failure_context",
                    "任务失败上下文",
                    "pass",
                    f"{meta.get('error_type')} attempt={meta.get('attempt')}/{meta.get('total_attempts')}",
                )
            ]
        return [
            DiagnosticCheck(
                "task_failure_context",
                "任务失败上下文",
                "warn" if success else "fail",
                f"task_failed 缺少结构化字段: {', '.join(missing)}。",
            )
        ]

    @staticmethod
    def _check_client_cleanup_observability(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        cleanup_events = {
            "client_cleanup_started",
            "client_cleanup_completed",
            "client_cleanup_failed",
        }
        if not any(event in cleanup_events for event in events):
            return []
        if "client_cleanup_failed" in events:
            failed_items = [
                item for item in items if _event(item) == "client_cleanup_failed"
            ]
            meta = _meta(failed_items[-1]) if failed_items else {}
            detail = f"error={meta.get('error_type') or '-'}"
            if meta.get("operation"):
                detail += f", operation={meta.get('operation')}"
            if meta.get("timeout_scope"):
                detail += f", timeout_scope={meta.get('timeout_scope')}"
            return [
                DiagnosticCheck(
                    "client_cleanup",
                    "Telegram client 清理",
                    "warn" if success else "fail",
                    detail,
                )
            ]
        if "client_cleanup_completed" in events:
            completed_items = [
                item for item in items if _event(item) == "client_cleanup_completed"
            ]
            meta = _meta(completed_items[-1]) if completed_items else {}
            return [
                DiagnosticCheck(
                    "client_cleanup",
                    "Telegram client 清理",
                    "pass",
                    (
                        f"attempt={meta.get('attempt', '-')}/{meta.get('total_attempts', '-')}, "
                        f"operation={meta.get('operation') or '-'}, "
                        f"timeout_scope={meta.get('timeout_scope') or '-'}"
                    ),
                )
            ]
        return [
            DiagnosticCheck(
                "client_cleanup",
                "Telegram client 清理",
                "warn" if success else "fail",
                "已开始清理但没有看到 client_cleanup_completed 或 client_cleanup_failed。",
            )
        ]

    @staticmethod
    def _check_client_cleanup_late_results(
        items: Sequence[Dict[str, Any]],
        events: list[str],
    ) -> list[DiagnosticCheck]:
        late_events = {
            "client_cleanup_late_cancelled",
            "client_cleanup_late_completed",
            "client_cleanup_late_exception",
        }
        if not any(event in late_events for event in events):
            return []
        latest = next(item for item in reversed(items) if _event(item) in late_events)
        latest_event = _event(latest)
        latest_meta = _meta(latest)
        detail = (
            f"cancelled={events.count('client_cleanup_late_cancelled')}, "
            f"completed={events.count('client_cleanup_late_completed')}, "
            f"exception={events.count('client_cleanup_late_exception')}, "
            f"latest={latest_event}, timeout={latest_meta.get('timeout_seconds') or '-'}s"
        )
        if latest_meta.get("operation"):
            detail += f", operation={latest_meta.get('operation')}"
        if latest_meta.get("timeout_scope"):
            detail += f", timeout_scope={latest_meta.get('timeout_scope')}"
        if latest_meta.get("error_type"):
            detail += f", error={latest_meta.get('error_type')}"
        status = "pass" if latest_event == "client_cleanup_late_cancelled" else "warn"
        return [
            DiagnosticCheck(
                "client_cleanup_late_result",
                "client 清理超时后台任务晚到结果",
                status,
                detail,
            )
        ]

    @staticmethod
    def _check_cleanup_deferred_cancellation(
        items: Sequence[Dict[str, Any]],
        events: list[str],
    ) -> list[DiagnosticCheck]:
        event_name = "task_cancellation_deferred_for_cleanup"
        if event_name not in events:
            return []
        latest_index, latest = next(
            (index, item)
            for index, item in reversed(list(enumerate(items)))
            if _event(item) == event_name
        )
        meta = _meta(latest)
        cleanup_after = any(
            _event(item) in {"client_cleanup_completed", "client_cleanup_failed"}
            for item in items[latest_index + 1 :]
        )
        detail = (
            f"count={events.count(event_name)}, "
            f"attempt={meta.get('attempt', '-')}/{meta.get('total_attempts', '-')}, "
            f"success={str(bool(meta.get('success'))).lower()}, "
            f"timeout={meta.get('timeout_seconds', '-')}s, "
            f"cleanup_after={str(cleanup_after).lower()}"
        )
        return [
            DiagnosticCheck(
                "cleanup_deferred_cancellation",
                "取消请求延后到 client 清理后处理",
                "pass" if cleanup_after else "warn",
                detail,
            )
        ]

    @staticmethod
    def _check_client_cleanup_manager_report(
        items: Sequence[Dict[str, Any]],
    ) -> list[DiagnosticCheck]:
        completed_items = [
            item for item in items if _event(item) == "client_cleanup_completed"
        ]
        if not completed_items:
            return []
        meta = _meta(completed_items[-1])
        has_report = any(
            key in meta
            for key in (
                "lock_wait_timeout",
                "force_cleanup",
                "cleanup_error_type",
                "lock_present",
                "client_found",
                "cleanup_attempted",
                "cleanup_step_attempts",
                "cleanup_step_timeouts",
                "cleanup_step_errors",
            )
        )
        if not has_report:
            return []
        lock_wait_timeout = bool(meta.get("lock_wait_timeout"))
        force_cleanup = bool(meta.get("force_cleanup"))
        cleanup_error_type = str(meta.get("cleanup_error_type") or "")
        cleanup_step_timeouts = _safe_int(meta.get("cleanup_step_timeouts"), 0)
        cleanup_step_errors = _safe_int(meta.get("cleanup_step_errors"), 0)
        if not (
            lock_wait_timeout
            or force_cleanup
            or cleanup_error_type
            or cleanup_step_timeouts
            or cleanup_step_errors
        ):
            return []
        detail = (
            f"lock_present={str(bool(meta.get('lock_present'))).lower()}, "
            f"lock_acquired={str(bool(meta.get('lock_acquired'))).lower()}, "
            f"lock_wait_timeout={str(lock_wait_timeout).lower()}, "
            f"force_cleanup={str(force_cleanup).lower()}, "
            f"client_found={str(bool(meta.get('client_found'))).lower()}, "
            f"cleanup_attempted={str(bool(meta.get('cleanup_attempted'))).lower()}, "
            f"cleanup_step_timeouts={cleanup_step_timeouts}, "
            f"lock_timeout={meta.get('lock_timeout_seconds') or '-'}s"
        )
        if meta.get("cleanup_step_last_error_type"):
            detail += f", cleanup_step_error={meta.get('cleanup_step_last_error_type')}"
        if cleanup_error_type:
            detail += f", cleanup_error={cleanup_error_type}"
        return [
            DiagnosticCheck(
                "client_cleanup_manager_report",
                "底层 client 清理报告",
                "warn",
                detail,
            )
        ]

    @staticmethod
    def _check_account_lock_observability(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        lock_events = {
            "account_lock_wait_started",
            "account_lock_wait_timeout",
            "account_lock_acquired",
            "account_lock_released",
        }
        if not any(event in lock_events for event in events):
            return []

        acquired_items = [
            item for item in items if _event(item) == "account_lock_acquired"
        ]
        released_items = [
            item for item in items if _event(item) == "account_lock_released"
        ]
        waited = "account_lock_wait_started" in events
        wait_timeout_items = [
            item for item in items if _event(item) == "account_lock_wait_timeout"
        ]

        if wait_timeout_items and not acquired_items:
            timeout_meta = _meta(wait_timeout_items[-1])
            return [
                DiagnosticCheck(
                    "account_lock",
                    "账号执行锁",
                    "warn" if success else "fail",
                    "等待账号执行锁超时。"
                    f" wait={timeout_meta.get('wait_seconds', '-')}s, "
                    f"timeout={timeout_meta.get('timeout_seconds', '-')}s, "
                    f"operation={timeout_meta.get('operation') or '-'}, "
                    f"timeout_scope={timeout_meta.get('timeout_scope') or '-'}。",
                )
            ]

        if acquired_items and released_items:
            acquired_meta = _meta(acquired_items[-1])
            released_meta = _meta(released_items[-1])
            return [
                DiagnosticCheck(
                    "account_lock",
                    "账号执行锁",
                    "pass",
                    "wait="
                    f"{acquired_meta.get('wait_seconds', '-')}s, "
                    f"attempt={released_meta.get('attempt', '-')}/{released_meta.get('total_attempts', '-')}, "
                    f"operation={released_meta.get('operation') or '-'}, "
                    f"timeout_scope={released_meta.get('timeout_scope') or '-'}",
                )
            ]

        if acquired_items:
            acquired_meta = _meta(acquired_items[-1])
            return [
                DiagnosticCheck(
                    "account_lock",
                    "账号执行锁",
                    "warn" if success else "fail",
                    "已获取账号执行锁但没有看到 account_lock_released。"
                    f" wait={acquired_meta.get('wait_seconds', '-')}s。",
                )
            ]

        if waited:
            return [
                DiagnosticCheck(
                    "account_lock",
                    "账号执行锁",
                    "warn" if success else "fail",
                    "已开始等待账号执行锁但没有看到 account_lock_acquired。",
                )
            ]

        return [
            DiagnosticCheck(
                "account_lock",
                "账号执行锁",
                "warn" if success else "fail",
                "看到了 account_lock_released，但没有对应的 account_lock_acquired。",
            )
        ]

    @staticmethod
    def _check_global_concurrency_observability(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        concurrency_events = {
            "global_concurrency_wait_started",
            "global_concurrency_wait_timeout",
            "global_concurrency_acquired",
            "global_concurrency_released",
        }
        if not any(event in concurrency_events for event in events):
            return []

        acquired_items = [
            item for item in items if _event(item) == "global_concurrency_acquired"
        ]
        released_items = [
            item for item in items if _event(item) == "global_concurrency_released"
        ]
        waited = "global_concurrency_wait_started" in events
        wait_timeout_items = [
            item for item in items if _event(item) == "global_concurrency_wait_timeout"
        ]

        if wait_timeout_items and not acquired_items:
            timeout_meta = _meta(wait_timeout_items[-1])
            return [
                DiagnosticCheck(
                    "global_concurrency",
                    "全局执行并发槽",
                    "warn" if success else "fail",
                    "等待全局执行并发槽超时。"
                    f" wait={timeout_meta.get('wait_seconds', '-')}s, "
                    f"timeout={timeout_meta.get('timeout_seconds', '-')}s, "
                    f"operation={timeout_meta.get('operation') or '-'}, "
                    f"timeout_scope={timeout_meta.get('timeout_scope') or '-'}。",
                )
            ]

        if acquired_items and released_items:
            acquired_meta = _meta(acquired_items[-1])
            released_meta = _meta(released_items[-1])
            return [
                DiagnosticCheck(
                    "global_concurrency",
                    "全局执行并发槽",
                    "pass",
                    "wait="
                    f"{acquired_meta.get('wait_seconds', '-')}s, "
                    f"attempt={released_meta.get('attempt', '-')}/{released_meta.get('total_attempts', '-')}, "
                    f"operation={released_meta.get('operation') or '-'}, "
                    f"timeout_scope={released_meta.get('timeout_scope') or '-'}",
                )
            ]

        if acquired_items:
            acquired_meta = _meta(acquired_items[-1])
            return [
                DiagnosticCheck(
                    "global_concurrency",
                    "全局执行并发槽",
                    "warn" if success else "fail",
                    "已获取全局执行并发槽但没有看到 global_concurrency_released。"
                    f" wait={acquired_meta.get('wait_seconds', '-')}s。",
                )
            ]

        if waited:
            return [
                DiagnosticCheck(
                    "global_concurrency",
                    "全局执行并发槽",
                    "warn" if success else "fail",
                    "已开始等待全局执行并发槽但没有看到 global_concurrency_acquired。",
                )
            ]

        return [
            DiagnosticCheck(
                "global_concurrency",
                "全局执行并发槽",
                "warn" if success else "fail",
                "看到了 global_concurrency_released，但没有对应的 global_concurrency_acquired。",
            )
        ]

    @staticmethod
    def _check_client_cleanup_lock_coverage(
        items: Sequence[Dict[str, Any]],
        events: list[str],
        success: bool,
    ) -> list[DiagnosticCheck]:
        cleanup_terminal_events = {
            "client_cleanup_completed",
            "client_cleanup_failed",
        }
        if "account_lock_released" not in events or not any(
            event in cleanup_terminal_events for event in events
        ):
            return []

        release_indexes = [
            index
            for index, item in enumerate(items)
            if _event(item) == "account_lock_released"
        ]
        cleanup_indexes = [
            index
            for index, item in enumerate(items)
            if _event(item) in cleanup_terminal_events
        ]
        if not release_indexes or not cleanup_indexes:
            return []

        latest_release_index = release_indexes[-1]
        latest_cleanup_index = cleanup_indexes[-1]
        if latest_cleanup_index < latest_release_index:
            return [
                DiagnosticCheck(
                    "client_cleanup_lock_coverage",
                    "client 清理锁覆盖",
                    "pass",
                    "账号锁在 Telegram client 清理结束后释放。",
                )
            ]
        return [
            DiagnosticCheck(
                "client_cleanup_lock_coverage",
                "client 清理锁覆盖",
                "warn" if success else "fail",
                "account_lock_released 早于 client_cleanup_completed/client_cleanup_failed，"
                "同账号下一次任务可能与旧 client 清理并发。",
            )
        ]

    @staticmethod
    def _check_runtime_config(
        items: Sequence[Dict[str, Any]],
    ) -> list[DiagnosticCheck]:
        runtime_items = [item for item in items if _event(item) == EVENT_TASK_RUNTIME_CONFIG]
        if not runtime_items:
            if not any(_event(item) == "task_started" for item in items):
                return []
            return [
                DiagnosticCheck(
                    "runtime_config",
                    "运行配置快照",
                    "warn",
                    "历史中没有 task_runtime_config，无法直接证明本次实际运行参数。",
                )
            ]
        meta = _meta(runtime_items[-1])
        validation = validate_runtime_config_snapshot(meta)
        if validation.engine != EVENT_ENGINE_NAME:
            return [
                DiagnosticCheck(
                    "runtime_config",
                    "运行配置快照",
                    "fail",
                    f"运行快照 engine={validation.engine or '-'}，预期为 event。",
                )
            ]
        if validation.missing_required:
            return [
                DiagnosticCheck(
                    "runtime_config",
                    "运行配置快照",
                    "fail",
                    "task_runtime_config 缺少字段: "
                    + ", ".join(validation.missing_required)
                    + "。",
                )
            ]
        return [
            DiagnosticCheck(
                "runtime_config",
                "运行配置快照",
                "pass",
                ", ".join(validation.detail_parts),
            )
        ]

    @staticmethod
    def _check_worker_execution_contract(
        items: Sequence[Dict[str, Any]],
    ) -> list[DiagnosticCheck]:
        worker_items = [
            item for item in items if _event(item) == EVENT_WORKER_EXECUTION_CONTRACT
        ]
        if not worker_items:
            if not any(_event(item) == "task_started" for item in items):
                return []
            return [
                DiagnosticCheck(
                    "worker_execution_contract",
                    "Worker 执行契约",
                    "warn",
                    "历史中没有 worker_execution_contract，无法直接证明 worker 实际执行预算。",
                )
            ]
        meta = _meta(worker_items[-1])
        validation = validate_worker_execution_snapshot(meta)
        if validation.engine != EVENT_ENGINE_NAME:
            return [
                DiagnosticCheck(
                    "worker_execution_contract",
                    "Worker 执行契约",
                    "fail",
                    f"Worker 快照 engine={validation.engine or '-'}，预期为 event。",
                )
            ]
        if validation.missing_required:
            return [
                DiagnosticCheck(
                    "worker_execution_contract",
                    "Worker 执行契约",
                    "fail",
                    "worker_execution_contract 缺少字段: "
                    + ", ".join(validation.missing_required)
                    + "。",
                )
            ]
        return [
            DiagnosticCheck(
                "worker_execution_contract",
                "Worker 执行契约",
                "pass",
                ", ".join(validation.detail_parts),
            )
        ]

    @staticmethod
    def _check_worker_runtime_consistency(
        items: Sequence[Dict[str, Any]],
    ) -> list[DiagnosticCheck]:
        runtime_items = [item for item in items if _event(item) == EVENT_TASK_RUNTIME_CONFIG]
        worker_items = [
            item for item in items if _event(item) == EVENT_WORKER_EXECUTION_CONTRACT
        ]
        if not runtime_items or not worker_items:
            return []

        runtime_meta = _meta(runtime_items[-1])
        worker_meta = _meta(worker_items[-1])
        compared: list[str] = []
        mismatches: list[str] = []
        for key in WORKER_RUNTIME_CONSISTENCY_FIELDS:
            if key not in runtime_meta or key not in worker_meta:
                continue
            compared.append(key)
            if not _snapshot_values_equal(runtime_meta.get(key), worker_meta.get(key)):
                mismatches.append(
                    f"{key}: runtime={runtime_meta.get(key)!r}, worker={worker_meta.get(key)!r}"
                )

        if not compared:
            return []
        if mismatches:
            return [
                DiagnosticCheck(
                    "worker_runtime_consistency",
                    "Worker/运行配置一致性",
                    "fail",
                    "worker_execution_contract 与 task_runtime_config 不一致: "
                    + "; ".join(mismatches)
                    + "。",
                )
            ]
        return [
            DiagnosticCheck(
                "worker_runtime_consistency",
                "Worker/运行配置一致性",
                "pass",
                f"matched_fields={len(compared)}",
            )
        ]

    @staticmethod
    def _overall_status(checks: Sequence[DiagnosticCheck], success: bool | None) -> str:
        statuses = {check.status for check in checks}
        if "fail" in statuses:
            return "fail"
        if "warn" in statuses:
            return "warn"
        if not checks and success is False:
            return "fail"
        if not checks:
            return "unknown"
        return "pass"

    @staticmethod
    def _summary(status: str, checks: Sequence[DiagnosticCheck]) -> str:
        failed = [check.label for check in checks if check.status == "fail"]
        warned = [check.label for check in checks if check.status == "warn"]
        if failed:
            return "失败检查: " + "、".join(failed)
        if warned:
            return "需观察: " + "、".join(warned)
        if status == "pass":
            return "事件引擎关键路径检查通过"
        return "没有足够的事件引擎日志可诊断"


def analyze_sign_task_run(
    *,
    flow_items: Sequence[Dict[str, Any]] | None,
    task_config: Dict[str, Any] | None = None,
    success: bool | None = None,
) -> Dict[str, Any]:
    return SignTaskDiagnostics.analyze_run(
        flow_items=flow_items,
        task_config=task_config,
        success=success,
    )
