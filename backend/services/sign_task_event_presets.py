from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List

from backend.services.sign_task_runtime_contract import (
    EVENT_BOOLEAN_COUNT_FIELDS,
    EVENT_NUMERIC_BUDGET_FIELDS,
    normalize_sign_actions,
)


_TOP_LEVEL_INT_DEFAULTS = {
    "random_seconds": 0,
    "sign_interval": 1,
    "retry_count": 0,
}
_CHAT_INT_MINIMUMS = {"delete_after": 0}
_CHAT_NUMERIC_MINIMUMS = {
    field.source: field.minimum for field in EVENT_NUMERIC_BUDGET_FIELDS
}
_CHAT_INT_FIELDS = {
    field.source for field in EVENT_NUMERIC_BUDGET_FIELDS if field.caster is int
} | set(_CHAT_INT_MINIMUMS)
_CHAT_BOOL_FIELDS = {field.source for field in EVENT_BOOLEAN_COUNT_FIELDS}
_ASSERT_KEYWORD_FIELDS = {
    "keywords",
    "checked_keywords",
    "retry_keywords",
    "fail_keywords",
    "account_fail_keywords",
    "ignore_keywords",
}
_EXECUTION_MODES = {"fixed", "range"}


def _normalize_int(value: Any, *, default: int, minimum: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if value in ("", None):
        return default
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_optional_number(
    value: Any,
    *,
    minimum: int | float,
    as_int: bool = False,
) -> int | float | None:
    if isinstance(value, bool):
        return None
    if value in ("", None):
        return None
    try:
        number = int(value) if as_int else float(value)
    except (TypeError, ValueError):
        return None
    return max(number, minimum)


def _normalize_optional_bool(value: Any) -> bool | None:
    if value in ("", None):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _normalize_optional_config_bool(value: Any) -> bool | None:
    if value in ("", None):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _normalize_chat_id(value: Any) -> int | None:
    if isinstance(value, bool) or value in ("", None):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        chat_id = int(value)
    except (TypeError, ValueError):
        return None
    return chat_id if chat_id != 0 else None


def _normalize_string_list(value: Any) -> List[Any]:
    values = value or []
    if isinstance(values, str):
        values = [item.strip() for item in values.split("#")]
    if not isinstance(values, list):
        return []
    normalized: List[Any] = []
    seen = set()
    for item in values:
        if not isinstance(item, str):
            normalized.append(item)
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _normalize_optional_text(value: Any) -> str | None:
    if value in ("", None):
        return None
    text = str(value).strip()
    return text or None


def _normalize_execution_mode(value: Any) -> str:
    if value in ("", None):
        return "fixed"
    mode = str(value).strip().lower()
    return mode if mode in _EXECUTION_MODES else "fixed"


def _valid_range_time(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


def validate_range_window_config(config: Dict[str, Any]) -> None:
    if _normalize_execution_mode(config.get("execution_mode")) != "range":
        return
    if not _valid_range_time(config.get("range_start")):
        raise ValueError("随机范围开始时间必须为 HH:MM 或 HH:MM:SS")
    if not _valid_range_time(config.get("range_end")):
        raise ValueError("随机范围结束时间必须为 HH:MM 或 HH:MM:SS")


def _normalize_captcha_lengths(value: Any) -> List[int]:
    if value in ("", None):
        return []
    values = value if isinstance(value, list) else [value]
    lengths: List[int] = []
    seen = set()
    for item in values:
        if isinstance(item, bool):
            continue
        try:
            length = int(item)
        except (TypeError, ValueError):
            continue
        if length <= 0 or length in seen:
            continue
        seen.add(length)
        lengths.append(length)
    return sorted(lengths)


def _normalize_captcha_charset(value: Any) -> str | None:
    if value in ("", None):
        return None
    text = "".join(dict.fromkeys(str(value).strip()))
    return text or None


def _normalize_captcha_case(value: Any) -> str | None:
    if value in ("", None):
        return None
    text = str(value).strip().lower()
    if text in {"preserve", "upper", "lower"}:
        return text
    return None


def _normalize_action(action: Any) -> Any:
    if not isinstance(action, dict):
        return action
    normalized = deepcopy(action)
    for field in _ASSERT_KEYWORD_FIELDS:
        if field in normalized:
            normalized[field] = _normalize_string_list(normalized[field])
    if _safe_int(normalized.get("action")) == 6:
        if "caption_pattern" in normalized:
            caption_pattern = _normalize_optional_text(normalized["caption_pattern"])
            if caption_pattern is None:
                normalized.pop("caption_pattern", None)
            else:
                normalized["caption_pattern"] = caption_pattern
        if "captcha_lengths" in normalized:
            lengths = _normalize_captcha_lengths(normalized["captcha_lengths"])
            if lengths:
                normalized["captcha_lengths"] = lengths
            else:
                normalized.pop("captcha_lengths", None)
        if "captcha_charset" in normalized:
            charset = _normalize_captcha_charset(normalized["captcha_charset"])
            if charset is None:
                normalized.pop("captcha_charset", None)
            else:
                normalized["captcha_charset"] = charset
        if "captcha_case" in normalized:
            captcha_case = _normalize_captcha_case(normalized["captcha_case"])
            if captcha_case is None:
                normalized.pop("captcha_case", None)
            else:
                normalized["captcha_case"] = captcha_case
        if "reply_to_message" in normalized:
            reply_to_message = _normalize_optional_config_bool(normalized["reply_to_message"])
            if reply_to_message is None:
                normalized.pop("reply_to_message", None)
            else:
                normalized["reply_to_message"] = reply_to_message
    return normalized


def _normalize_chat(chat: Any) -> Any:
    if not isinstance(chat, dict):
        return chat
    normalized = deepcopy(chat)
    if "chat_id" in normalized:
        chat_id = _normalize_chat_id(normalized["chat_id"])
        if chat_id is not None:
            normalized["chat_id"] = chat_id
    for field, minimum in _CHAT_NUMERIC_MINIMUMS.items():
        if field not in normalized:
            continue
        normalized_value = _normalize_optional_number(
            normalized[field],
            minimum=minimum,
            as_int=field in _CHAT_INT_FIELDS,
        )
        if normalized_value is None:
            normalized.pop(field, None)
        else:
            normalized[field] = normalized_value
    for field, minimum in _CHAT_INT_MINIMUMS.items():
        if field not in normalized:
            continue
        normalized_value = _normalize_optional_number(
            normalized[field],
            minimum=minimum,
            as_int=True,
        )
        if normalized_value is None:
            normalized.pop(field, None)
        else:
            normalized[field] = normalized_value
    for field in _CHAT_BOOL_FIELDS:
        if field not in normalized:
            continue
        normalized_value = _normalize_optional_bool(normalized[field])
        if normalized_value is None:
            normalized.pop(field, None)
        else:
            normalized[field] = normalized_value
    actions = normalized.get("actions")
    if isinstance(actions, list):
        normalized["actions"] = [_normalize_action(action) for action in actions]
    return normalized


def apply_event_chat_presets(chats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a detached chat config copy without bot-specific mutations."""

    return [_normalize_chat(chat) for chat in chats]


def validate_writable_event_task_config(config: Dict[str, Any]) -> None:
    """Reject event task configs that cannot produce any worker session."""

    validate_range_window_config(config)
    chats = config.get("chats")
    if not isinstance(chats, list) or not chats:
        raise ValueError("任务至少需要配置一个会话")
    for chat in chats:
        if not isinstance(chat, dict):
            raise ValueError("会话配置必须为对象")
        normalized_chat = _normalize_chat(chat)
        if _normalize_chat_id(normalized_chat.get("chat_id")) is None:
            raise ValueError("会话 chat_id 必须为非零整数")
        actions = chat.get("actions")
        if not isinstance(actions, list):
            raise ValueError("动作列表不能为空")
        normalize_sign_actions(
            [_normalize_action(action) for action in normalized_chat["actions"]]
        )


def normalize_event_task_config(
    config: Dict[str, Any],
    *,
    default_engine: str = "event",
) -> Dict[str, Any]:
    """Normalize a sign-task config before it is persisted.

    Import paths can bypass the task management service, so keep the event
    engine default in one place.
    """

    normalized = deepcopy(config)
    normalized_engine = default_engine
    normalized["engine"] = normalized_engine
    for field, default in _TOP_LEVEL_INT_DEFAULTS.items():
        normalized[field] = _normalize_int(
            normalized.get(field),
            default=default,
            minimum=0,
        )
    normalized["execution_mode"] = _normalize_execution_mode(normalized.get("execution_mode"))
    for field in ("range_start", "range_end"):
        normalized[field] = str(normalized.get(field) or "").strip()

    chats = normalized.get("chats")
    if isinstance(chats, list):
        normalized["chats"] = apply_event_chat_presets(chats)

    return normalized
