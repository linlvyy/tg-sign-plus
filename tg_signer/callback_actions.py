from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass, replace
from typing import Union

from pyrogram import errors


@dataclass(frozen=True)
class CallbackAnswerResult:
    confirmed: bool
    status: str
    reason: str = ""
    attempt: int = 0
    max_retries: int = 0
    timeout: float = 0.0
    error_type: str = ""
    had_timeout: bool = False
    callback_text: str = ""
    trusted_consumed: bool = False

    def __bool__(self) -> bool:
        return self.confirmed


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(int(os.environ.get(name, default)), minimum)
    except (TypeError, ValueError):
        return default


def _read_float_env(name: str, default: float, minimum: float = 1.0) -> float:
    try:
        return max(float(os.environ.get(name, default)), minimum)
    except (TypeError, ValueError):
        return default


def _optional_int(value, *, minimum: int = 1) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return None


def _optional_float(value, *, minimum: float = 0.1) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return max(float(value), minimum)
    except (TypeError, ValueError):
        return None


def _is_callback_timeout_error(exc: BaseException) -> bool:
    pyrogram_timeout = getattr(errors, "Timeout", None)
    return (
        isinstance(exc, (TimeoutError, asyncio.TimeoutError))
        or (pyrogram_timeout is not None and isinstance(exc, pyrogram_timeout))
        or "timed out" in str(exc).lower()
        or "timeout" in str(exc).lower()
    )


async def request_callback_answer(
    *,
    client,
    chat_id: Union[int, str],
    message_id: int,
    callback_data: Union[str, bytes],
    log,
    callback_text_store=None,
    callback_text_handler=None,
    trust_consumed_after_timeout: bool = False,
    return_result: bool = False,
    callback_retries: int | None = None,
    callback_timeout: float | None = None,
    **kwargs,
) -> bool | CallbackAnswerResult:
    def done(result: CallbackAnswerResult) -> bool | CallbackAnswerResult:
        result = replace(
            result,
            max_retries=result.max_retries or max_retries,
            timeout=result.timeout or callback_timeout,
            had_timeout=result.had_timeout or had_timeout,
        )
        return result if return_result else result.confirmed

    max_retries = _optional_int(callback_retries, minimum=1)
    if max_retries is None:
        max_retries = _read_int_env("TG_CALLBACK_RETRIES", 3)
    callback_timeout = _optional_float(callback_timeout, minimum=0.1)
    if callback_timeout is None:
        callback_timeout = _read_float_env("TG_CALLBACK_TIMEOUT", 10.0, minimum=0.1)
    had_timeout = False
    for attempt in range(1, max_retries + 1):
        try:
            result = await asyncio.wait_for(
                client.request_callback_answer(
                    chat_id, message_id, callback_data=callback_data, **kwargs
                ),
                timeout=callback_timeout,
            )
            callback_text = getattr(result, "message", None) or ""
            if isinstance(callback_text_store, dict):
                callback_text_store[chat_id] = str(callback_text or "")
            if callback_text and callable(callback_text_handler):
                result = callback_text_handler(str(callback_text), chat_id=chat_id, message_id=message_id)
                if inspect.isawaitable(result):
                    await result
            if callback_text:
                log(
                    f"点击完成，弹窗提示: {callback_text}",
                    stage="result",
                    event="callback_answer_received",
                    meta={"chat_id": chat_id, "message_id": message_id},
                )
            else:
                log(
                    "点击完成",
                    stage="action",
                    event="callback_answer_completed",
                    meta={"chat_id": chat_id, "message_id": message_id},
                )
            return done(
                CallbackAnswerResult(
                    confirmed=True,
                    status="confirmed",
                    attempt=attempt,
                    callback_text=str(callback_text or ""),
                )
            )
        except errors.FloodWait as e:
            wait_seconds = max(int(getattr(e, "value", 1) or 1), 1)
            log(
                f"触发 FloodWait，{wait_seconds}s 后重试 ({attempt}/{max_retries})",
                level="WARNING",
                stage="action",
                event="callback_flood_wait",
                meta={"chat_id": chat_id, "message_id": message_id, "attempt": attempt},
            )
            if attempt >= max_retries:
                log(e, level="ERROR")
                return done(
                    CallbackAnswerResult(
                        confirmed=False,
                    status="flood_wait_exceeded",
                    reason=str(e),
                    attempt=attempt,
                    error_type=type(e).__name__,
                )
            )
            await asyncio.sleep(wait_seconds)
        except (TimeoutError, asyncio.TimeoutError) as e:
            had_timeout = True
            if trust_consumed_after_timeout:
                log(
                    "回调请求超时，按已触发点击处理，后续依赖消息更新继续推进",
                    level="WARNING",
                    stage="action",
                    event="callback_timeout_trusted",
                    meta={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "attempt": attempt,
                        "timeout": callback_timeout,
                        "max_retries": max_retries,
                    },
                )
                return done(
                    CallbackAnswerResult(
                        confirmed=True,
                        status="trusted_timeout",
                        reason=str(e),
                        attempt=attempt,
                        error_type=type(e).__name__,
                        trusted_consumed=True,
                    )
                )
            if attempt < max_retries:
                log(
                    f"回调请求超时，准备重试 ({attempt}/{max_retries})",
                    level="WARNING",
                    stage="action",
                    event="callback_timeout_retry",
                    meta={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "attempt": attempt,
                        "timeout": callback_timeout,
                        "max_retries": max_retries,
                    },
                )
                await asyncio.sleep(1)
                continue
            log(
                f"回调请求超时，点击未确认 ({attempt}/{max_retries})",
                level="WARNING",
                stage="action",
                event="callback_timeout_failed",
                meta={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "attempt": attempt,
                    "error_type": type(e).__name__,
                    "timeout": callback_timeout,
                    "max_retries": max_retries,
                },
            )
            return done(
                CallbackAnswerResult(
                    confirmed=False,
                    status="timeout_failed",
                    reason=str(e),
                    attempt=attempt,
                    error_type=type(e).__name__,
                )
            )
        except errors.BadRequest as e:
            err_text = str(e).upper()
            if "DATA_INVALID" in err_text:
                if had_timeout and trust_consumed_after_timeout:
                    log(
                        "按钮回调数据已失效，但前一次点击请求已超时送出，按已点击继续等待后续消息",
                        level="WARNING",
                        stage="action",
                        event="callback_data_invalid_after_timeout",
                        meta={"chat_id": chat_id, "message_id": message_id},
                    )
                    return done(
                        CallbackAnswerResult(
                            confirmed=True,
                            status="data_invalid_after_timeout",
                            reason=str(e),
                            attempt=attempt,
                            error_type=type(e).__name__,
                            trusted_consumed=True,
                        )
                    )
                log(
                    "按钮回调数据已失效，改为等待消息更新或历史消息继续执行",
                    level="WARNING",
                    stage="action",
                    event="callback_data_invalid",
                    meta={"chat_id": chat_id, "message_id": message_id},
                )
                return done(
                    CallbackAnswerResult(
                        confirmed=False,
                        status="data_invalid",
                        reason=str(e),
                        attempt=attempt,
                        error_type=type(e).__name__,
                    )
                )
            log(e, level="ERROR")
            return done(
                CallbackAnswerResult(
                    confirmed=False,
                    status="bad_request",
                    reason=str(e),
                    attempt=attempt,
                    error_type=type(e).__name__,
                )
            )
        except errors.RPCError as e:
            if not _is_callback_timeout_error(e):
                log(e, level="ERROR")
                return done(
                    CallbackAnswerResult(
                        confirmed=False,
                        status="rpc_error",
                        reason=str(e),
                        attempt=attempt,
                        error_type=type(e).__name__,
                    )
                )
            had_timeout = True
            if trust_consumed_after_timeout:
                log(
                    "回调请求超时，按已触发点击处理，后续依赖消息更新继续推进",
                    level="WARNING",
                    stage="action",
                    event="callback_timeout_trusted",
                    meta={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "attempt": attempt,
                        "error_type": type(e).__name__,
                        "timeout": callback_timeout,
                        "max_retries": max_retries,
                    },
                )
                return done(
                    CallbackAnswerResult(
                        confirmed=True,
                        status="trusted_timeout",
                        reason=str(e),
                        attempt=attempt,
                        error_type=type(e).__name__,
                        trusted_consumed=True,
                    )
                )
            if attempt < max_retries:
                log(
                    f"回调请求超时，准备重试 ({attempt}/{max_retries})",
                    level="WARNING",
                    stage="action",
                    event="callback_timeout_retry",
                    meta={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "attempt": attempt,
                        "error_type": type(e).__name__,
                        "timeout": callback_timeout,
                        "max_retries": max_retries,
                    },
                )
                await asyncio.sleep(1)
                continue
            log(
                f"回调请求超时，点击未确认 ({attempt}/{max_retries})",
                level="WARNING",
                stage="action",
                event="callback_timeout_failed",
                meta={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "attempt": attempt,
                    "error_type": type(e).__name__,
                    "timeout": callback_timeout,
                    "max_retries": max_retries,
                },
            )
            return done(
                CallbackAnswerResult(
                    confirmed=False,
                    status="timeout_failed",
                    reason=str(e),
                    attempt=attempt,
                    error_type=type(e).__name__,
                )
            )
    return done(CallbackAnswerResult(confirmed=False, status="unknown"))
