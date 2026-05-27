from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_identity(chat: Dict[str, Any]) -> str:
    parts = [
        _text(chat.get("chat_id")),
        _text(chat.get("name")),
        _text(chat.get("username")),
    ]
    return " ".join(part.lower() for part in parts if part)


def _matches(chat: Dict[str, Any], *, ids: Iterable[int], names: Iterable[str]) -> bool:
    chat_id = chat.get("chat_id")
    try:
        if int(chat_id) in set(ids):
            return True
    except (TypeError, ValueError):
        pass
    identity = _normalized_identity(chat)
    return any(name.lower() in identity for name in names)


def _ensure_list_values(action: Dict[str, Any], field_name: str, values: Iterable[str]) -> None:
    current = action.get(field_name)
    if isinstance(current, str):
        items = [item.strip() for item in current.split("#") if item.strip()]
    elif isinstance(current, list):
        items = [_text(item) for item in current if _text(item)]
    else:
        items = []
    seen = set(items)
    for value in values:
        value = _text(value)
        if value and value not in seen:
            items.append(value)
            seen.add(value)
    if items:
        action[field_name] = items


def _set_default(chat: Dict[str, Any], key: str, value: Any) -> None:
    if chat.get(key) is None:
        chat[key] = value


def _apply_result_defaults(chat: Dict[str, Any], *, checked=(), retry=(), fail=(), account_fail=(), ignore=()) -> None:
    for action in chat.get("actions") or []:
        if not isinstance(action, dict):
            continue
        try:
            action_id = int(action.get("action"))
        except (TypeError, ValueError):
            continue
        if action_id != 9:
            continue
        if checked:
            _ensure_list_values(action, "checked_keywords", checked)
        if retry:
            _ensure_list_values(action, "retry_keywords", retry)
        if fail:
            _ensure_list_values(action, "fail_keywords", fail)
        if account_fail:
            _ensure_list_values(action, "account_fail_keywords", account_fail)
        if ignore:
            _ensure_list_values(action, "ignore_keywords", ignore)


def _apply_peach(chat: Dict[str, Any]) -> None:
    _set_default(chat, "event_timeout", 120)
    _set_default(chat, "event_retries", 3)
    _set_default(chat, "event_retry_wait", 2)
    _set_default(chat, "event_history_limit", 3)
    _set_default(chat, "event_action_timeout", 45)
    _set_default(chat, "event_ai_fallback", False)
    for action in chat.get("actions") or []:
        if not isinstance(action, dict):
            continue
        try:
            action_id = int(action.get("action"))
        except (TypeError, ValueError):
            continue
        if action_id == 6:
            action.setdefault("caption_pattern", "请输入验证码")
            action.setdefault("captcha_lengths", [4])
            action.setdefault("captcha_charset", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
            action.setdefault("captcha_case", "upper")
            action.setdefault("reply_to_message", True)
        elif action_id == 9:
            _ensure_list_values(action, "keywords", ["签到成功"])
    _apply_result_defaults(
        chat,
        checked=("签到过了", "已经签到", "已签到", "您今天已经签到过了"),
        retry=("验证码错误", "验证失败"),
        fail=("次数过多",),
        account_fail=("未绑定", "请先加入"),
        ignore=("欢迎使用", "请选择功能"),
    )


def _apply_meow(chat: Dict[str, Any]) -> None:
    _set_default(chat, "event_timeout", 120)
    _set_default(chat, "event_retries", 2)
    _set_default(chat, "event_retry_wait", 2)
    _set_default(chat, "event_history_limit", 3)
    _set_default(chat, "event_action_timeout", 45)
    _set_default(chat, "event_ai_fallback", False)
    _apply_result_defaults(
        chat,
        checked=("签到过了", "已经签到", "已签到"),
        retry=("验证失败", "验证码错误"),
        account_fail=("请先加入", "未注册"),
    )


def _apply_emby_public(chat: Dict[str, Any]) -> None:
    _set_default(chat, "event_timeout", 150)
    _set_default(chat, "event_retries", 1)
    _set_default(chat, "event_retry_wait", 2)
    _set_default(chat, "event_history_limit", 3)
    _set_default(chat, "event_action_timeout", 60)
    _set_default(chat, "event_ai_fallback", False)
    _apply_result_defaults(
        chat,
        checked=("签到过了", "已经签到", "已签到", "今日已签到"),
        retry=("验证码错误", "验证失败", "答案错误"),
        fail=("次数过多", "尝试次数过多"),
        account_fail=("请先加入", "未注册", "未绑定"),
    )


def apply_event_chat_presets(chats: List[Dict[str, Any]], *, engine: str = "event") -> List[Dict[str, Any]]:
    """Apply conservative event-engine defaults for known bot check-in flows.

    The presets mirror emby-keeper's site-specific checkiners, but only fill
    missing fields or append missing result keywords. Explicit user settings
    keep priority.
    """

    normalized = deepcopy(chats)
    if engine != "event":
        return normalized
    for chat in normalized:
        if not isinstance(chat, dict):
            continue
        if _matches(chat, ids={8060839337}, names={"peach_emby_bot", "peach"}):
            _apply_peach(chat)
        elif _matches(chat, ids={7516512581}, names={"gymeowfly_bot", "喵了个咪", "飞了个喵"}):
            _apply_meow(chat)
        elif _matches(chat, ids={1429576125}, names={"embypublicbot", "厂妹"}):
            _apply_emby_public(chat)
    return normalized


def normalize_event_task_config(
    config: Dict[str, Any],
    *,
    default_engine: str = "event",
) -> Dict[str, Any]:
    """Normalize a sign-task config before it is persisted.

    Import paths can bypass the task management service, so keep the event
    engine default and known-bot presets in one place.
    """

    normalized = deepcopy(config)
    engine = normalized.get("engine", default_engine)
    normalized_engine = engine if engine in {"legacy", "event"} else default_engine
    normalized["engine"] = normalized_engine

    chats = normalized.get("chats")
    if isinstance(chats, list):
        normalized["chats"] = apply_event_chat_presets(chats, engine=normalized_engine)

    return normalized
