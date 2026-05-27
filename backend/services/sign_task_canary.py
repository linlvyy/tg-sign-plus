from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, Sequence
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


def _config_check(check_id: str, label: str, ok: bool, detail: str = "") -> Dict[str, str]:
    return {
        "id": check_id,
        "label": label,
        "status": "pass" if ok else "fail",
        "detail": "" if ok else detail,
    }


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
    """Compatibility wrapper for the generic event-engine health report."""

    report = generate_event_engine_health_report(
        config_repo=config_repo,
        history_repo=history_repo,
        account_name=account_name,
        history_limit=history_limit,
        max_age_hours=max_age_hours,
        now=now,
        source=source,
    )
    if not report.get("task_count"):
        report["status"] = "fail"
        report["hint"] = (
            "没有扫描到任务配置。请确认 canary-report 连接的是后台正在使用的数据库；"
            "本地/容器环境通常需要显式传入 --data-dir 或 --database-url。"
        )
    return report


def _generic_config_checks(task: Dict[str, Any]) -> list[Dict[str, str]]:
    chats = [chat for chat in task.get("chats") or [] if isinstance(chat, dict)]
    return [
        _config_check(
            "engine_event",
            "事件引擎配置",
            str(task.get("engine") or "event") == "event",
            "当前任务不是 event 引擎。",
        ),
        _config_check(
            "chat_config",
            "会话配置",
            bool(chats),
            "任务中没有可执行的 chat 配置。",
        ),
    ]


def _event_health_summary(task_report: Dict[str, Any]) -> str:
    status = str(task_report.get("status") or "missing")
    if status == "pass":
        return "最新历史诊断通过"
    if status == "warn":
        return "最新历史需要观察"
    if status == "fail":
        if str(task_report.get("config_status") or "") == "fail":
            failed_checks = [
                check.get("label") or check.get("id")
                for check in task_report.get("config_checks") or []
                if isinstance(check, dict) and check.get("status") == "fail"
            ]
            suffix = f"：{', '.join(str(item) for item in failed_checks[:3])}" if failed_checks else ""
            return f"当前配置未通过事件引擎检查{suffix}"
        return "最新历史诊断失败"
    if status == "stale":
        latest_time = task_report.get("latest_time") or "-"
        return f"最新历史已过期（{latest_time}）"
    return "没有可诊断的历史记录"


def _event_health_overall_status(targets: Sequence[Dict[str, Any]]) -> str:
    statuses = [str(target.get("status") or "missing") for target in targets]
    if not statuses:
        return "missing"
    if any(status == "fail" for status in statuses):
        return "fail"
    if any(status in {"warn", "missing", "stale", "unconfigured"} for status in statuses):
        return "warn"
    return "pass"


def generate_event_engine_health_report(
    *,
    config_repo,
    history_repo,
    account_name: str | None = None,
    history_limit: int = 1,
    max_age_hours: float | None = 36,
    now: datetime | None = None,
    source: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a generic event-engine health report from actual task configs."""

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
    for task in tasks:
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
        config_checks = _generic_config_checks(task)
        config_status = _check_status(config_checks)
        task_status = _target_status(config_status, latest_status)
        task_report = {
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
        target_reports.append(
            {
                "id": f"{task_account}/{task_name}",
                "label": task_name,
                "status": task_status,
                "summary": _event_health_summary(task_report),
                "tasks": [task_report],
            }
        )

    report = {
        "status": _event_health_overall_status(target_reports),
        "generated_at": now.isoformat(),
        "max_age_hours": max_age_hours,
        "task_count": len(tasks),
        "targets": target_reports,
    }
    if source:
        report["source"] = _sanitize_source(source)
    if not tasks:
        report["hint"] = "没有扫描到任务配置。"
    return report


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
