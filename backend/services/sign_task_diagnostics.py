from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence


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


def _completion_result_status(items: Sequence[Dict[str, Any]]) -> str:
    for item in reversed(items):
        if _event(item) != "event_engine_completed":
            continue
        meta = _meta(item)
        status = _clean(meta.get("status"))
        message = _clean(meta.get("message"))
        if status == "checked" or "matched checked keyword" in message:
            return "checked"
        if status == "success" and "matched success keyword" in message:
            return "success"
    return ""


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
    """Analyze structured sign-task flow logs for event-engine canary checks."""

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
        completion_result_status = _completion_result_status(items)
        checks.extend(cls._check_expected_buttons(items, actions, bool(success), completion_result_status))
        checks.extend(cls._check_captcha_path(items, events, actions, bool(success)))
        checks.extend(cls._check_result(events, items, bool(success)))
        checks.extend(cls._check_timeouts(events, bool(success)))
        checks.extend(cls._check_callback_text_progress(items, bool(success)))
        checks.extend(cls._check_callback_recovery(items, events, bool(success)))
        checks.extend(cls._check_strict_image_choice(items))
        checks.extend(cls._check_button_without_callback_data(events))
        checks.extend(cls._check_button_callback_unconfirmed(items, events, bool(success)))
        checks.extend(cls._check_startup_history_action_skipped(events))
        checks.extend(cls._check_response_action_advanced(events))
        checks.extend(cls._check_no_duplicate_ocr_after_result(events))
        checks.extend(cls._check_failure_preempted_response_action(events))
        checks.extend(cls._check_history_hard_failure_skipped(events))
        checks.extend(cls._check_history_rescue(events, bool(success)))
        checks.extend(cls._check_retry_limit(events, bool(success)))
        checks.extend(cls._check_runtime_config(items))
        checks.extend(cls._check_task_failure_context(items, bool(success)))

        status = cls._overall_status(checks, success)
        return {
            "status": status,
            "summary": cls._summary(status, checks),
            "checks": [check.to_dict() for check in checks],
            "milestones": {
                "event_engine_started": "event_engine_started" in events,
                "button_clicks": events.count("event_engine_button_clicked"),
                "image_options": events.count("event_engine_image_option_selected"),
                "captchas": events.count("event_engine_captcha_recognized"),
                "captcha_replies": sum(
                    1
                    for item in items
                    if _event(item) == "event_engine_captcha_sent"
                    and bool(_meta(item).get("reply_to_message"))
                ),
                "captcha_sends": events.count("event_engine_captcha_sent"),
                "success_matched": "event_engine_success_matched" in events or completion_result_status == "success",
                "checked_matched": "event_engine_checked_matched" in events or completion_result_status == "checked",
                "callback_texts": events.count("event_engine_callback_text_received"),
                "history_rescues": events.count("event_engine_history_rescue_started"),
                "tracked_history_rechecks": events.count("event_engine_history_tracked_message_rechecked"),
                "history_hard_failures_skipped": events.count("event_engine_history_hard_failure_skipped"),
                "timeouts": events.count("event_engine_timeout_state"),
                "response_action_timeouts": events.count("event_engine_response_action_timeout"),
                "failure_preemptions": events.count("event_engine_failure_preempted_response_action"),
                "buttons_without_callback_data": events.count("event_engine_button_without_callback_data"),
                "button_callback_unconfirmed": events.count("event_engine_button_callback_unconfirmed"),
                "startup_history_actions_skipped": events.count("event_engine_startup_history_action_skipped"),
                "response_action_advances": events.count("event_engine_response_action_advanced"),
                "retry_schedules": events.count("event_engine_retry_scheduled"),
                "retry_limit_exceeded": events.count("event_engine_retry_limit_exceeded"),
                "task_failures": events.count("task_failed"),
                "runtime_config_logged": "task_runtime_config" in events,
            },
        }

    @staticmethod
    def _check_event_engine_started(events: list[str]) -> list[DiagnosticCheck]:
        if "event_engine_started" in events:
            return [DiagnosticCheck("event_engine_started", "事件引擎启动", "pass")]
        return [DiagnosticCheck("event_engine_started", "事件引擎启动", "fail", "日志中没有 event_engine_started。")]

    @staticmethod
    def _check_expected_buttons(
        items: Sequence[Dict[str, Any]],
        actions: Sequence[Dict[str, Any]],
        success: bool,
        completion_result_status: str = "",
    ) -> list[DiagnosticCheck]:
        checks: list[DiagnosticCheck] = []
        expected = [str(action.get("text") or "").strip() for action in actions if int(action.get("action", 0) or 0) == 3]
        clicked = [
            _meta(item).get("button_text") or _text(item)
            for item in items
            if _event(item) == "event_engine_button_clicked"
        ]
        for index, target in enumerate(expected, start=1):
            if not target:
                continue
            if any(_contains(button, target) for button in clicked):
                checks.append(DiagnosticCheck(f"button_{index}", f"点击按钮「{target}」", "pass"))
            else:
                if success and completion_result_status:
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
        if "event_engine_captcha_recognized" in events:
            checks.append(DiagnosticCheck("captcha_recognized", "验证码识别", "pass"))
        elif "event_engine_checked_matched" in events or "event_engine_success_matched" in events:
            checks.append(
                DiagnosticCheck(
                    "captcha_recognized",
                    "验证码识别",
                    "skip",
                    "结果已在验证码前命中，未继续 OCR。",
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
        else:
            checks.append(
                DiagnosticCheck(
                    "captcha_sent",
                    "验证码发送",
                    "warn" if success else "fail",
                    "日志中没有看到 event_engine_captcha_sent。",
                )
            )
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
        items: Sequence[Dict[str, Any]],
        success: bool,
    ) -> list[DiagnosticCheck]:
        if "event_engine_success_matched" in events:
            return [DiagnosticCheck("result_matched", "成功结果命中", "pass")]
        if "event_engine_checked_matched" in events:
            return [DiagnosticCheck("result_matched", "已签到结果命中", "pass")]
        completion_status = _completion_result_status(items)
        if completion_status == "success":
            return [
                DiagnosticCheck(
                    "result_matched",
                    "成功结果命中",
                    "pass",
                    "event_engine_completed 记录了成功关键字命中。",
                )
            ]
        if completion_status == "checked":
            return [
                DiagnosticCheck(
                    "result_matched",
                    "已签到结果命中",
                    "pass",
                    "event_engine_completed 记录了已签到关键字命中。",
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
    def _check_timeouts(events: list[str], success: bool) -> list[DiagnosticCheck]:
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
        return checks

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
            return [
                DiagnosticCheck(
                    "button_callback_unconfirmed",
                    "旧历史按钮回调未确认已隔离",
                    "pass",
                    "启动历史旧按钮回调未确认，但事件引擎未推进该步骤，并由后续新消息完成任务。",
                )
            ]
        return [
            DiagnosticCheck(
                "button_callback_unconfirmed",
                "按钮回调未确认",
                "warn" if success else "fail",
                "按钮回调未确认；事件引擎会允许后续消息或历史补漏重试。",
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
    def _check_no_duplicate_ocr_after_result(events: list[str]) -> list[DiagnosticCheck]:
        result_indexes = [
            index
            for index, event in enumerate(events)
            if event in {"event_engine_success_matched", "event_engine_checked_matched"}
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
    def _check_history_rescue(events: list[str], success: bool) -> list[DiagnosticCheck]:
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
    def _check_retry_limit(events: list[str], success: bool) -> list[DiagnosticCheck]:
        if "event_engine_retry_limit_exceeded" not in events:
            return []
        return [
            DiagnosticCheck(
                "retry_limit_exceeded",
                "事件内部重试耗尽",
                "warn" if success else "fail",
                "事件引擎内部重试预算已耗尽，需要看前序 retry 原因和机器人返回。",
            )
        ]

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
    def _check_runtime_config(
        items: Sequence[Dict[str, Any]],
    ) -> list[DiagnosticCheck]:
        runtime_items = [item for item in items if _event(item) == "task_runtime_config"]
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
        engine = str(meta.get("engine") or "")
        if engine != "event":
            return [
                DiagnosticCheck(
                    "runtime_config",
                    "运行配置快照",
                    "fail",
                    f"运行快照 engine={engine or '-'}，预期为 event。",
                )
            ]
        detail_parts = [f"engine={engine or '-'}", f"chats={meta.get('chat_count', '-')}"]
        if meta.get("max_event_timeout") is not None:
            detail_parts.append(f"event_timeout<={meta.get('max_event_timeout')}")
        if meta.get("max_event_retries") is not None:
            detail_parts.append(f"event_retries<={meta.get('max_event_retries')}")
        return [
            DiagnosticCheck(
                "runtime_config",
                "运行配置快照",
                "pass",
                ", ".join(detail_parts),
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
