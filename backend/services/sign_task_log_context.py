from __future__ import annotations

from contextvars import ContextVar, Token


_current_sign_task_run_id: ContextVar[str | None] = ContextVar(
    "current_sign_task_run_id",
    default=None,
)


def get_sign_task_run_id() -> str | None:
    return _current_sign_task_run_id.get()


def set_sign_task_run_id(run_id: str) -> Token[str | None]:
    return _current_sign_task_run_id.set(run_id)


def reset_sign_task_run_id(token: Token[str | None]) -> None:
    _current_sign_task_run_id.reset(token)
