from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


def apply_event_chat_presets(chats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a detached chat config copy without bot-specific mutations."""

    return deepcopy(chats)


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

    chats = normalized.get("chats")
    if isinstance(chats, list):
        normalized["chats"] = apply_event_chat_presets(chats)

    return normalized
