from __future__ import annotations

import asyncio
import logging
import os
import time
import traceback
from typing import Any, Dict, List

from backend.core.config import get_settings
from backend.services.sign_task_runtime import (
    BackendUserSigner,
    TaskLogHandler,
)
from backend.services.task_flow_logger import TaskFlowLogger
from backend.utils.account_locks import get_account_lock
from backend.utils.proxy import build_proxy_dict
from backend.utils.tg_session import (
    get_account_proxy,
    get_account_session_string,
    get_global_semaphore,
)
from tg_signer.client_manager import close_client_by_name
from tg_signer.wait_dispatcher import BusinessRetryableError

settings = get_settings()


class SignTaskExecutor:
    def __init__(
        self,
        *,
        workdir,
        active_logs: Dict[tuple[str, str], List[str]],
        active_log_offsets: Dict[tuple[str, str], int],
        active_tasks: Dict[tuple[str, str], bool],
        cleanup_tasks: Dict[tuple[str, str], asyncio.Task],
        account_locks: Dict[str, asyncio.Lock],
        account_last_run_end: Dict[str, float],
        account_cooldown_seconds: int,
        get_task,
        save_run_info,
    ):
        self.workdir = workdir
        self._active_logs = active_logs
        self._active_log_offsets = active_log_offsets
        self._active_tasks = active_tasks
        self._cleanup_tasks = cleanup_tasks
        self._account_locks = account_locks
        self._account_last_run_end = account_last_run_end
        self._account_cooldown_seconds = account_cooldown_seconds
        self._get_task = get_task
        self._save_run_info = save_run_info

    @staticmethod
    def task_requires_updates(task_config: Dict[str, Any] | None) -> bool:
        if not isinstance(task_config, dict):
            return True
        if task_config.get("engine", "event") == "event":
            return True
        chats = task_config.get("chats")
        if not isinstance(chats, list):
            return True
        response_actions = {3, 4, 5, 6, 7, 8}
        for chat in chats:
            if not isinstance(chat, dict):
                continue
            actions = chat.get("actions")
            if not isinstance(actions, list):
                continue
            for action in actions:
                if not isinstance(action, dict):
                    continue
                try:
                    action_id = int(action.get("action"))
                except (TypeError, ValueError):
                    continue
                if action_id in response_actions:
                    return True
        return False

    @staticmethod
    def _read_int_env(name: str, default: int, minimum: int = 0) -> int:
        try:
            return max(int(os.getenv(name, default) or default), minimum)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _read_float_env(name: str, default: float, minimum: float = 0.0) -> float:
        try:
            return max(float(os.getenv(name, default) or default), minimum)
        except (TypeError, ValueError):
            return default

    @classmethod
    def task_timeout_seconds(cls, task_config: Dict[str, Any] | None) -> int:
        configured_timeout = cls._read_int_env("SIGN_TASK_RUN_TIMEOUT", 180, minimum=30)
        if not isinstance(task_config, dict) or task_config.get("engine", "event") != "event":
            return configured_timeout

        chats = task_config.get("chats")
        if not isinstance(chats, list) or not chats:
            return configured_timeout

        default_event_timeout = cls._read_float_env("TG_EVENT_ENGINE_TIMEOUT", 120.0, minimum=1.0)
        event_budget = 0.0
        for chat in chats:
            if not isinstance(chat, dict):
                event_budget += default_event_timeout
                continue
            try:
                event_budget += max(float(chat.get("event_timeout") or default_event_timeout), 1.0)
            except (TypeError, ValueError):
                event_budget += default_event_timeout

        try:
            sign_interval = max(float(task_config.get("sign_interval", 0) or 0), 0.0)
        except (TypeError, ValueError):
            sign_interval = 0.0
        if len(chats) > 1:
            event_budget += sign_interval * (len(chats) - 1)

        overhead = cls._read_int_env("SIGN_TASK_RUN_TIMEOUT_OVERHEAD", 90, minimum=30)
        return max(configured_timeout, int(event_budget + overhead))

    @classmethod
    def task_runtime_config_meta(cls, task_config: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(task_config, dict):
            return {"engine": "event", "chat_count": 0}
        chats = task_config.get("chats") if isinstance(task_config.get("chats"), list) else []
        engine = str(task_config.get("engine") or "event")
        meta: Dict[str, Any] = {
            "engine": engine,
            "chat_count": len(chats),
        }
        if engine != "event":
            return meta

        event_timeouts: list[float] = []
        event_retries: list[int] = []
        event_history_limits: list[int] = []
        event_action_timeouts: list[float] = []
        known_targets: list[str] = []
        for chat in chats:
            if not isinstance(chat, dict):
                continue
            identity = " ".join(
                str(chat.get(key) or "").lower()
                for key in ("chat_id", "name", "username")
            )
            if "8060839337" in identity or "peach_emby_bot" in identity or "peach" in identity:
                known_targets.append("peach")
            elif "7516512581" in identity or "gymeowfly_bot" in identity or "喵了个咪" in identity or "飞了个喵" in identity:
                known_targets.append("meow")
            elif "1429576125" in identity or "embypublicbot" in identity or "厂妹" in identity:
                known_targets.append("emby_public")

            for source, target, caster in (
                ("event_timeout", event_timeouts, float),
                ("event_retries", event_retries, int),
                ("event_history_limit", event_history_limits, int),
                ("event_action_timeout", event_action_timeouts, float),
            ):
                value = chat.get(source)
                if value is None:
                    continue
                try:
                    target.append(caster(value))
                except (TypeError, ValueError):
                    continue

        meta.update(
            {
                "event_chat_count": sum(1 for chat in chats if isinstance(chat, dict)),
                "known_targets": ",".join(sorted(set(known_targets))),
            }
        )
        if event_timeouts:
            meta["max_event_timeout"] = max(event_timeouts)
        if event_retries:
            meta["max_event_retries"] = max(event_retries)
        if event_history_limits:
            meta["max_event_history_limit"] = max(event_history_limits)
        if event_action_timeouts:
            meta["max_event_action_timeout"] = max(event_action_timeouts)
        return meta

    @staticmethod
    def _extract_last_reply(final_logs: List[str]) -> str:
        last_reply = ""
        for line in reversed(final_logs):
            if "收到来自「" not in line or (
                "」的消息:" not in line and "」对消息的更新，消息:" not in line
            ):
                continue
            try:
                splitter = "」的消息:" if "」的消息:" in line else "」对消息的更新，消息:"
                reply_part = line.split(splitter, 1)[-1].strip()
                if reply_part.startswith("Message:"):
                    reply_part = reply_part[len("Message:") :].strip()

                if "text: " in reply_part:
                    text_content = reply_part.split("text: ", 1)[-1].split("\n")[0].strip()
                    if text_content:
                        last_reply = text_content
                    elif "图片: " in reply_part:
                        last_reply = "[图片] " + reply_part.split("图片: ", 1)[-1].split("\n")[0].strip()
                    else:
                        last_reply = reply_part.replace("\n", " ").strip()
                else:
                    last_reply = reply_part.replace("\n", " ").strip()

                if len(last_reply) > 200:
                    last_reply = last_reply[:197] + "..."
            except Exception:
                pass
            if last_reply:
                break
        return last_reply

    @staticmethod
    def _failure_meta(
        exc: Exception,
        *,
        task_attempt: int,
        total_attempts: int,
        retryable: bool,
        timeout_seconds: int | None,
    ) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "error_type": type(exc).__name__,
            "retryable": retryable,
            "attempt": task_attempt,
            "total_attempts": total_attempts,
        }
        if timeout_seconds is not None:
            meta["timeout_seconds"] = timeout_seconds
        return meta

    async def run_task_with_logs(self, account_name: str, task_name: str) -> Dict[str, Any]:
        task_key = (account_name, task_name)
        if self._active_tasks.get(task_key, False):
            return {"success": False, "error": "任务已经在运行中", "output": ""}

        if account_name not in self._account_locks:
            self._account_locks[account_name] = get_account_lock(account_name)
        account_lock = self._account_locks[account_name]

        self._active_tasks[task_key] = True
        self._active_logs[task_key] = []
        self._active_log_offsets[task_key] = 0
        active_log_offset_ref = {"value": 0}
        flow_items: List[Dict[str, Any]] = []
        flow_logger = TaskFlowLogger(
            self._active_logs[task_key],
            flow_items,
            active_log_offset_ref,
        )

        tg_logger = logging.getLogger("tg-signer")
        previous_tg_logger_level = tg_logger.level
        should_restore_tg_logger_level = False
        if tg_logger.getEffectiveLevel() > logging.INFO:
            tg_logger.setLevel(logging.INFO)
            should_restore_tg_logger_level = True
        log_handler = TaskLogHandler(self._active_logs[task_key], flow_items, active_log_offset_ref)
        log_handler.setLevel(logging.INFO)
        tg_logger.addHandler(log_handler)

        success = False
        error_msg = ""
        output_str = ""
        signer = None
        configured_retry_count = 0
        total_attempts = 1
        current_task_attempt = 0
        task_timeout_seconds: int | None = None

        try:
            async with account_lock:
                last_end = self._account_last_run_end.get(account_name)
                if last_end:
                    gap = time.time() - last_end
                    wait_seconds = self._account_cooldown_seconds - gap
                    if wait_seconds > 0:
                        flow_logger.append(
                            f"等待账号冷却 {int(wait_seconds)} 秒",
                            level="info",
                            stage="task",
                            event="cooldown_wait",
                            meta={"wait_seconds": int(wait_seconds)},
                        )
                        await asyncio.sleep(wait_seconds)

                flow_logger.append(
                    f"开始执行任务: {task_name} (账号: {account_name})",
                    level="info",
                    stage="task",
                    event="task_started",
                    meta={"task_name": task_name, "account_name": account_name},
                )

                from backend.services.config import get_config_service

                config_service = get_config_service()
                tg_config = config_service.get_telegram_config()
                api_id = os.getenv("TG_API_ID") or tg_config.get("api_id")
                api_hash = os.getenv("TG_API_HASH") or tg_config.get("api_hash")

                try:
                    api_id = int(api_id) if api_id is not None else None
                except (TypeError, ValueError):
                    api_id = None

                if isinstance(api_hash, str):
                    api_hash = api_hash.strip()

                if not api_id or not api_hash:
                    raise ValueError("未配置 Telegram API ID 或 API Hash")

                session_dir = settings.resolve_session_dir()
                session_string = get_account_session_string(account_name)
                if not session_string:
                    raise ValueError(f"账号 {account_name} 的 session_string 不存在")

                proxy_dict = None
                proxy_value = get_account_proxy(account_name)
                if proxy_value:
                    proxy_dict = build_proxy_dict(proxy_value)

                task_cfg = self._get_task(task_name, account_name=account_name)
                disable_sign_task_updates = (
                    (os.getenv("TG_SIGN_TASK_DISABLE_UPDATES") or "").strip().lower()
                    in {"1", "true", "yes", "on"}
                )
                requires_updates = (
                    self.task_requires_updates(task_cfg)
                    and not disable_sign_task_updates
                )
                signer_no_updates = not requires_updates
                if isinstance(task_cfg, dict):
                    try:
                        configured_retry_count = max(int(task_cfg.get("retry_count", 0) or 0), 0)
                    except (TypeError, ValueError):
                        configured_retry_count = 0
                runtime_meta = self.task_runtime_config_meta(task_cfg)
                flow_logger.append(
                    f"运行配置: engine={runtime_meta.get('engine')} chats={runtime_meta.get('chat_count')}",
                    level="info",
                    stage="task",
                    event="task_runtime_config",
                    meta=runtime_meta,
                )
                flow_logger.append(
                    f"消息更新监听: {'开启' if requires_updates else '关闭'}",
                    level="info",
                    stage="session",
                    event="updates_mode",
                    meta={"requires_updates": requires_updates},
                )
                flow_logger.append(
                    f"失败重试次数: {configured_retry_count}",
                    level="info",
                    stage="task",
                    event="task_retry_config",
                    meta={"retry_count": configured_retry_count},
                )

                signer = BackendUserSigner(
                    task_name=task_name,
                    session_dir=str(session_dir),
                    account=account_name,
                    workdir=self.workdir,
                    proxy=proxy_dict,
                    session_string=session_string,
                    in_memory=True,
                    api_id=api_id,
                    api_hash=api_hash,
                    no_updates=signer_no_updates,
                )

                task_timeout_seconds = self.task_timeout_seconds(task_cfg)
                flow_logger.append(
                    f"单次执行总超时: {task_timeout_seconds} 秒",
                    level="info",
                    stage="task",
                    event="task_timeout_config",
                    meta={"timeout_seconds": task_timeout_seconds},
                )

                async with get_global_semaphore():
                    max_session_retries = 3
                    total_attempts = configured_retry_count + 1
                    last_exception = None
                    for task_attempt in range(1, total_attempts + 1):
                        current_task_attempt = task_attempt
                        if task_attempt > 1:
                            flow_logger.append(
                                f"开始第 {task_attempt}/{total_attempts} 次重试",
                                level="warning",
                                stage="task",
                                event="task_retry_started",
                                meta={"attempt": task_attempt, "total_attempts": total_attempts},
                            )
                        try:
                            for attempt in range(max_session_retries):
                                try:
                                    await asyncio.wait_for(
                                        signer.run_once(num_of_dialogs=0),
                                        timeout=task_timeout_seconds,
                                    )
                                    last_exception = None
                                    break
                                except Exception as e:
                                    if "database is locked" in str(e).lower() and attempt < max_session_retries - 1:
                                        delay = (attempt + 1) * 3
                                        flow_logger.append(
                                            f"Session 被锁定，{delay} 秒后重试...",
                                            level="warning",
                                            stage="session",
                                            event="session_retry",
                                            meta={"retry_delay_seconds": delay, "attempt": attempt + 1},
                                        )
                                        await asyncio.sleep(delay)
                                        continue
                                    raise
                            if last_exception is None:
                                break
                        except Exception as e:
                            last_exception = e
                            is_retryable_business_error = isinstance(e, BusinessRetryableError)
                            if not is_retryable_business_error or task_attempt >= total_attempts:
                                raise
                            flow_logger.append(
                                f"业务失败，准备重试: {e}",
                                level="warning",
                                stage="task",
                                event="task_retry_scheduled",
                                meta={
                                    "attempt": task_attempt,
                                    "total_attempts": total_attempts,
                                    "error": str(e),
                                    "error_type": type(e).__name__,
                                    "retryable": True,
                                },
                            )
                            await asyncio.sleep(2)
                            continue

                success = True
                flow_logger.append(
                    "任务执行完成",
                    level="success",
                    stage="result",
                    event="task_completed",
                    meta={
                        "task_name": task_name,
                        "account_name": account_name,
                        "attempt": current_task_attempt,
                        "total_attempts": total_attempts,
                    },
                )
                await asyncio.sleep(2)

        except Exception as e:
            error_detail = str(e) or type(e).__name__
            error_msg = f"任务执行出错: {error_detail}"
            is_retryable_business_error = isinstance(e, BusinessRetryableError)
            flow_logger.append(
                error_msg,
                level="error",
                stage="result",
                event="task_failed",
                meta={
                    "task_name": task_name,
                    "account_name": account_name,
                    **self._failure_meta(
                        e,
                        task_attempt=current_task_attempt,
                        total_attempts=total_attempts,
                        retryable=is_retryable_business_error,
                        timeout_seconds=task_timeout_seconds,
                    ),
                },
            )
            traceback.print_exc()
            logging.getLogger("backend").error(error_msg)
        finally:
            self._active_log_offsets[task_key] = active_log_offset_ref["value"]
            self._account_last_run_end[account_name] = time.time()
            self._active_tasks[task_key] = False
            if signer is not None:
                try:
                    await close_client_by_name(account_name, workdir=signer._session_dir)
                except Exception as cleanup_exc:
                    logging.getLogger("backend").warning(
                        "清理 Telegram client 失败: %s", cleanup_exc
                    )
            tg_logger.removeHandler(log_handler)
            if should_restore_tg_logger_level:
                tg_logger.setLevel(previous_tg_logger_level)

            final_logs = list(self._active_logs.get(task_key, []))
            output_str = "\n".join(final_logs)
            msg = error_msg if not success else self._extract_last_reply(final_logs)
            self._save_run_info(
                task_name,
                success,
                msg,
                account_name,
                flow_logs=final_logs,
                flow_items=flow_items,
            )

            self._active_logs.pop(task_key, None)
            self._active_log_offsets.pop(task_key, None)

            old_cleanup_task = self._cleanup_tasks.get(task_key)
            if old_cleanup_task and not old_cleanup_task.done():
                old_cleanup_task.cancel()
            self._cleanup_tasks.pop(task_key, None)

        return {
            "success": success,
            "output": output_str,
            "error": error_msg,
        }
