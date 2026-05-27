from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from backend.services.sign_task_diagnostics import analyze_sign_task_run
from backend.services.sign_task_event_presets import normalize_event_task_config


def _text(value: Any) -> str:
    return str(value or "").strip()


def _redact_source_value(key: str, value: Any) -> Any:
    if key != "database_url" or not isinstance(value, str):
        return value
    if not value or value.startswith("sqlite:"):
        return value

    try:
        parts = urlsplit(value)
    except ValueError:
        return "<redacted>"

    if not parts.scheme or not parts.netloc:
        return "<redacted>"

    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    credentials = f"{parts.username}:***@" if parts.username else ""
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    safe_query = urlencode(
        [
            (item_key, item_value if item_key.lower() in {"sslmode", "channel_binding"} else "***")
            for item_key, item_value in query_items
        ]
    )
    return urlunsplit((parts.scheme, f"{credentials}{host}{port}", parts.path, safe_query, parts.fragment))


def _sanitize_source(source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: _redact_source_value(key, value)
        for key, value in source.items()
        if value not in (None, "")
    }


def _identity(chat: Dict[str, Any]) -> str:
    parts = [_text(chat.get("chat_id")), _text(chat.get("name")), _text(chat.get("username"))]
    return " ".join(part.lower() for part in parts if part)


def _matches(chat: Dict[str, Any], *, ids: Iterable[int], names: Iterable[str]) -> bool:
    try:
        if int(chat.get("chat_id")) in set(ids):
            return True
    except (TypeError, ValueError):
        pass
    identity = _identity(chat)
    return any(name.lower() in identity for name in names)


@dataclass(frozen=True)
class CanaryTarget:
    id: str
    label: str
    chat_ids: tuple[int, ...]
    names: tuple[str, ...]

    def matches(self, chat: Dict[str, Any]) -> bool:
        return _matches(chat, ids=self.chat_ids, names=self.names)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chat_ids": list(self.chat_ids),
            "names": list(self.names),
        }


CANARY_TARGETS: tuple[CanaryTarget, ...] = (
    CanaryTarget(
        id="peach",
        label="peach_emby_bot",
        chat_ids=(8060839337,),
        names=("peach_emby_bot", "peach"),
    ),
    CanaryTarget(
        id="meow",
        label="喵了个咪",
        chat_ids=(7516512581,),
        names=("gymeowfly_bot", "喵了个咪", "飞了个喵"),
    ),
    CanaryTarget(
        id="emby_public",
        label="厂妹",
        chat_ids=(1429576125,),
        names=("embypublicbot", "厂妹"),
    ),
)


def _task_matches_target(task: Dict[str, Any], target: CanaryTarget) -> bool:
    for chat in task.get("chats") or []:
        if isinstance(chat, dict) and target.matches(chat):
            return True
    return False


def _status_rank(status: str) -> int:
    return {
        "pass": 0,
        "warn": 1,
        "missing": 2,
        "unconfigured": 2,
        "stale": 3,
        "fail": 4,
    }.get(status, 2)


def _target_status(*statuses: str) -> str:
    values = [status for status in statuses if status]
    if any(status == "fail" for status in values):
        return "fail"
    if any(status == "stale" for status in values):
        return "stale"
    if any(status in {"missing", "unconfigured"} for status in values):
        return "missing"
    if any(status == "warn" for status in values):
        return "warn"
    if values:
        return "pass"
    return "missing"


def _check_status(checks: Sequence[Dict[str, Any]]) -> str:
    return _target_status(*(str(check.get("status") or "") for check in checks))


def _actions(chat: Dict[str, Any]) -> list[Dict[str, Any]]:
    return [action for action in chat.get("actions") or [] if isinstance(action, dict)]


def _action_id(action: Dict[str, Any]) -> int | None:
    try:
        return int(action.get("action"))
    except (TypeError, ValueError):
        return None


def _action_text(action: Dict[str, Any]) -> str:
    return _text(action.get("text"))


def _action_keywords(action: Dict[str, Any], key: str) -> list[str]:
    value = action.get(key)
    if isinstance(value, str):
        return [item.strip() for item in value.split("#") if item.strip()]
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    return []


def _has_action(actions: Sequence[Dict[str, Any]], action_id: int, text: str | None = None) -> bool:
    target = _text(text)
    for action in actions:
        if _action_id(action) != action_id:
            continue
        if not target or target in _action_text(action):
            return True
    return False


def _config_check(check_id: str, label: str, ok: bool, detail: str = "") -> Dict[str, str]:
    return {
        "id": check_id,
        "label": label,
        "status": "pass" if ok else "fail",
        "detail": "" if ok else detail,
    }


def _target_config_checks(task: Dict[str, Any], target: CanaryTarget) -> list[Dict[str, str]]:
    checks: list[Dict[str, str]] = [
        _config_check(
            "engine_event",
            "事件引擎配置",
            str(task.get("engine") or "event") == "event",
            "当前任务不是 event 引擎。",
        )
    ]
    matched_chats = [
        chat
        for chat in task.get("chats") or []
        if isinstance(chat, dict) and target.matches(chat)
    ]
    if not matched_chats:
        checks.append(_config_check("target_chat", "目标会话配置", False, "任务中没有匹配目标 bot 的 chat。"))
        return checks
    chat = matched_chats[0]
    actions = _actions(chat)
    result_actions = [action for action in actions if _action_id(action) == 9]
    checks.append(_config_check("target_chat", "目标会话配置", True))
    checks.append(
        _config_check(
            "result_assertion",
            "结果断言动作",
            bool(result_actions),
            "缺少 action=9 结果断言。",
        )
    )
    if target.id == "peach":
        captcha_actions = [action for action in actions if _action_id(action) == 6]
        captcha = captcha_actions[0] if captcha_actions else {}
        checks.extend(
            [
                _config_check("peach_sign_button", "peach 签到按钮", _has_action(actions, 3, "签到"), "缺少点击「签到」按钮动作。"),
                _config_check("peach_captcha_action", "peach 验证码动作", bool(captcha_actions), "缺少 action=6 验证码回复动作。"),
                _config_check(
                    "peach_captcha_pattern",
                    "peach 验证码图文约束",
                    _text(captcha.get("caption_pattern")) == "请输入验证码",
                    "action=6 未限制 caption_pattern=请输入验证码。",
                ),
                _config_check(
                    "peach_captcha_length",
                    "peach 验证码长度",
                    4 in (captcha.get("captcha_lengths") or []),
                    "action=6 未限制 4 位验证码。",
                ),
                _config_check(
                    "peach_reply_to_message",
                    "peach 回复原验证码图",
                    bool(captcha.get("reply_to_message")),
                    "action=6 未开启 reply_to_message。",
                ),
            ]
        )
    elif target.id == "meow":
        checks.extend(
            [
                _config_check("meow_sign_button", "喵了个咪签到按钮", _has_action(actions, 3, "签到"), "缺少点击「签到」按钮动作。"),
                _config_check(
                    "meow_human_button",
                    "喵了个咪人机验证按钮",
                    _has_action(actions, 3, "我不是机器人"),
                    "缺少点击「我不是机器人」按钮动作。",
                ),
            ]
        )
        if result_actions:
            retry_keywords = set().union(*[set(_action_keywords(action, "retry_keywords")) for action in result_actions])
            checks.append(
                _config_check(
                    "meow_retry_keywords",
                    "喵了个咪验证失败重试词",
                    bool({"验证失败", "验证码错误"} & retry_keywords),
                    "结果断言缺少验证失败/验证码错误重试词。",
                )
            )
    elif target.id == "emby_public":
        checks.append(
            _config_check(
                "emby_public_image_choice",
                "厂妹图片选项动作",
                _has_action(actions, 4),
                "缺少严格图片选项 action=4。",
            )
        )
    return checks


def _parse_time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _overall_status(targets: Sequence[Dict[str, Any]]) -> str:
    statuses = [str(target.get("status") or "missing") for target in targets]
    if not statuses:
        return "missing"
    if any(status in {"fail", "missing", "unconfigured", "stale"} for status in statuses):
        return "fail"
    if any(status == "warn" for status in statuses):
        return "warn"
    return "pass"


def _entry_summary(
    *,
    entry: Dict[str, Any],
    task: Dict[str, Any],
    now: datetime,
    max_age_hours: float | None,
) -> Dict[str, Any]:
    diagnostics = analyze_sign_task_run(
        flow_items=entry.get("flow_items") or [],
        task_config={"engine": task.get("engine", "event"), "chats": task.get("chats") or []},
        success=bool(entry.get("success", False)),
    )
    diagnostic_status = str(diagnostics.get("status") or "unknown")
    success = bool(entry.get("success", False))
    status = diagnostic_status if success else "fail"
    if success and diagnostic_status == "unknown":
        status = "warn"
    time_value = entry.get("time", "")
    parsed_time = _parse_time(time_value)
    age_hours: float | None = None
    fresh = True
    if max_age_hours is not None:
        fresh = False
        if parsed_time is not None:
            age_hours = max((now - parsed_time).total_seconds() / 3600, 0)
            fresh = age_hours <= max_age_hours
        if not fresh and success:
            status = "stale"
    return {
        "status": status,
        "success": success,
        "time": time_value,
        "age_hours": age_hours,
        "fresh": fresh,
        "message": entry.get("message", ""),
        "diagnostics": diagnostics,
    }


def generate_canary_report(
    *,
    config_repo,
    history_repo,
    account_name: str | None = None,
    history_limit: int = 1,
    max_age_hours: float | None = 36,
    now: datetime | None = None,
    source: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a canary report for the known emby-keeper-aligned bot flows."""

    history_limit = max(int(history_limit or 1), 1)
    if max_age_hours is not None:
        max_age_hours = max(float(max_age_hours), 0)
    now = (now or datetime.now(UTC)).astimezone(UTC)
    tasks = [
        normalize_event_task_config(task)
        for task in config_repo.list_configs(account_name=account_name)
        if isinstance(task, dict)
    ]
    target_reports: list[Dict[str, Any]] = []

    for target in CANARY_TARGETS:
        matched_tasks = [
            task
            for task in tasks
            if _task_matches_target(task, target)
        ]
        if not matched_tasks:
            target_reports.append(
                {
                    "id": target.id,
                    "label": target.label,
                    "status": "unconfigured",
                    "summary": f"未找到匹配的任务配置（已扫描 {len(tasks)} 个任务）",
                    "expected": target.to_dict(),
                    "tasks": [],
                }
            )
            continue

        task_reports = []
        for task in matched_tasks:
            task_name = task.get("name") or ""
            task_account = task.get("account_name") or account_name or ""
            entries = history_repo.load_entries(task_name, task_account)
            run_reports = [
                _entry_summary(
                    entry=entry,
                    task=task,
                    now=now,
                    max_age_hours=max_age_hours,
                )
                for entry in entries[:history_limit]
                if isinstance(entry, dict)
            ]
            latest_status = run_reports[0]["status"] if run_reports else "missing"
            latest_time = run_reports[0].get("time") if run_reports else ""
            config_checks = _target_config_checks(task, target)
            config_status = _check_status(config_checks)
            task_status = _target_status(config_status, latest_status)
            task_reports.append(
                {
                    "task_name": task_name,
                    "account_name": task_account,
                    "engine": task.get("engine", "event"),
                    "status": task_status,
                    "config_status": config_status,
                    "config_checks": config_checks,
                    "run_status": latest_status,
                    "latest_time": latest_time,
                    "latest_summary": _latest_run_summary(run_reports),
                    "runs": run_reports,
                }
            )

        representative_task = _representative_task(task_reports)
        target_reports.append(
            {
                "id": target.id,
                "label": target.label,
                "status": representative_task.get("status", "missing"),
                "summary": _target_summary(representative_task),
                "expected": target.to_dict(),
                "tasks": task_reports,
            }
        )

    report = {
        "status": _overall_status(target_reports),
        "generated_at": now.isoformat(),
        "max_age_hours": max_age_hours,
        "task_count": len(tasks),
        "targets": target_reports,
    }
    if source:
        report["source"] = _sanitize_source(source)
    if not tasks:
        report["hint"] = (
            "没有扫描到任务配置。请确认 canary-report 连接的是后台正在使用的数据库；"
            "本地/容器环境通常需要显式传入 --data-dir 或 --database-url。"
        )
    return report


def _representative_task(task_reports: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    with_history = [
        task
        for task in task_reports
        if task.get("runs") and _parse_time(task.get("latest_time")) is not None
    ]
    if with_history:
        return max(with_history, key=lambda task: _parse_time(task.get("latest_time")) or datetime.min.replace(tzinfo=UTC))
    return min(task_reports, key=lambda item: _status_rank(str(item.get("status") or "missing")))


def _target_summary(task_report: Dict[str, Any]) -> str:
    status = str(task_report.get("status") or "missing")
    task_name = task_report.get("task_name") or "-"
    account_name = task_report.get("account_name") or "-"
    config_status = str(task_report.get("config_status") or "")
    run_status = str(task_report.get("run_status") or "")
    if config_status == "fail":
        failed_checks = [
            check.get("label") or check.get("id")
            for check in task_report.get("config_checks") or []
            if isinstance(check, dict) and check.get("status") == "fail"
        ]
        suffix = f"：{', '.join(str(item) for item in failed_checks[:3])}" if failed_checks else ""
        return f"{account_name}/{task_name} 当前配置未通过 canary{suffix}"
    if status == "pass":
        return f"{account_name}/{task_name} 最新记录通过"
    if status == "warn":
        return f"{account_name}/{task_name} 最新记录需观察"
    if status == "fail":
        if run_status == "pass":
            return f"{account_name}/{task_name} 当前配置未通过 canary"
        return f"{account_name}/{task_name} 最新记录失败"
    if status == "stale":
        latest_time = task_report.get("latest_time") or "-"
        return f"{account_name}/{task_name} 最新记录已过期（{latest_time}）"
    return f"{account_name}/{task_name} 没有可诊断历史"


def _latest_run_summary(run_reports: Sequence[Dict[str, Any]]) -> str:
    if not run_reports:
        return "没有历史记录"
    latest = run_reports[0]
    diagnostics = latest.get("diagnostics") or {}
    summary = _text(diagnostics.get("summary"))
    if summary:
        return summary
    message = _text(latest.get("message"))
    if message:
        return message
    return str(latest.get("status") or "unknown")
