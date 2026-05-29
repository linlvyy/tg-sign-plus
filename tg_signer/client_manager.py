from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import random
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Union
from urllib import parse

from pyrogram import Client as BaseClient
from pyrogram.methods.utilities.idle import idle
from pyrogram.session import Session

try:
    from pyrogram.storage import MemoryStorage
except ImportError:
    MemoryStorage = None

logger = logging.getLogger("tg-signer")

def _read_float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _read_int_env(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(int(os.environ.get(name, default)), minimum)
    except (TypeError, ValueError):
        return default


Session.START_TIMEOUT = _read_float_env("TG_CONNECT_TIMEOUT", 15)
_TG_CONNECT_TIMEOUT = _read_float_env("TG_CONNECT_TIMEOUT", 20)
_TG_CONNECT_RETRIES = _read_int_env("TG_CONNECT_RETRIES", 3, minimum=1)
_TG_CONNECT_RETRY_WAIT = _read_float_env("TG_CONNECT_RETRY_WAIT", 3)
_TG_CLEANUP_STEP_TIMEOUT = _read_float_env("TG_CLEANUP_STEP_TIMEOUT", 3)
_TG_CLIENT_LOCK_TIMEOUT = _read_float_env("TG_CLIENT_LOCK_TIMEOUT", 5)

try:
    from pyrogram.connection.transport.tcp.tcp import TCP

    TCP.TIMEOUT = _read_float_env("TG_TCP_TIMEOUT", 8)
except Exception:
    pass


async def _call_if_exists(obj, name: str):
    method = getattr(obj, name, None)
    if not method:
        return
    result = method()
    if asyncio.iscoroutine(result):
        await result


async def _await_with_hard_timeout(awaitable, *, timeout: float, late_label: str):
    task = asyncio.create_task(awaitable)
    done, _ = await asyncio.wait({task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        await task
        return task.result()

    operation = late_label.replace(" ", "_")
    timeout_scope = "client_cleanup" if operation == "cleanup_step" else "client_rpc"
    late_meta = {
        "source": "client_manager",
        "operation": operation,
        "timeout_scope": timeout_scope,
        "timeout": timeout,
    }
    timeout_meta = late_meta | {"error_type": "TimeoutError"}
    logger.warning(
        "Telegram %s timed out after %.2fs; cancelling background task",
        late_label,
        timeout,
        extra={
            "flow_stage": "session",
            "flow_event": "client_rpc_hard_timeout",
            "flow_meta": timeout_meta,
        },
    )
    task.cancel()

    def consume_late_result(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except asyncio.CancelledError:
            logger.info(
                "Late Telegram %s cancelled after hard timeout",
                late_label,
                extra={
                    "flow_stage": "session",
                    "flow_event": "client_rpc_late_cancelled",
                    "flow_meta": late_meta,
                },
            )
        except Exception as exc:
            logger.warning(
                "Late Telegram %s failed after hard timeout: %s",
                late_label,
                exc,
                extra={
                    "flow_stage": "session",
                    "flow_event": "client_rpc_late_exception",
                    "flow_meta": late_meta | {"error_type": type(exc).__name__},
                },
            )
        else:
            logger.info(
                "Late Telegram %s completed after hard timeout",
                late_label,
                extra={
                    "flow_stage": "session",
                    "flow_event": "client_rpc_late_completed",
                    "flow_meta": late_meta,
                },
            )

    task.add_done_callback(consume_late_result)
    raise asyncio.TimeoutError()


async def _await_cleanup_step(awaitable, *, timeout: float) -> None:
    await _await_with_hard_timeout(
        awaitable,
        timeout=timeout,
        late_label="cleanup step",
    )


async def _force_cleanup_client(client):
    report = ClientCleanupReport()
    for obj, methods in (
        (client, ("stop", "disconnect")),
        (getattr(client, "session", None), ("stop", "disconnect")),
        (getattr(getattr(client, "session", None), "connection", None), ("close", "disconnect")),
    ):
        if obj is None:
            continue
        for method in methods:
            if not getattr(obj, method, None):
                continue
            report.cleanup_step_attempts += 1
            try:
                await _await_cleanup_step(
                    _call_if_exists(obj, method),
                    timeout=_TG_CLEANUP_STEP_TIMEOUT,
                )
            except (TimeoutError, asyncio.TimeoutError) as exc:
                report.cleanup_step_timeouts += 1
                report.cleanup_step_errors += 1
                report.cleanup_step_last_error_type = type(exc).__name__
                report.cleanup_step_last_timeout = _TG_CLEANUP_STEP_TIMEOUT
                await asyncio.sleep(0)
            except Exception as exc:
                report.cleanup_step_errors += 1
                report.cleanup_step_last_error_type = type(exc).__name__
                pass
    return report

_CLIENT_INSTANCES: dict[str, "Client"] = {}
_CLIENT_REFS: defaultdict[str, int] = defaultdict(int)
_CLIENT_ASYNC_LOCKS: dict[str, asyncio.Lock] = {}


def _mark_client_retired(client, *, reason: str) -> None:
    setattr(client, "_tg_signer_retired", True)
    setattr(client, "_tg_signer_retired_reason", reason)


def _client_retired_reason(client) -> str:
    if not getattr(client, "_tg_signer_retired", False):
        return ""
    reason = getattr(client, "_tg_signer_retired_reason", "")
    return reason if isinstance(reason, str) and reason else "unknown"


@dataclass
class ClientCleanupReport:
    lock_present: bool = False
    lock_acquired: bool = False
    lock_wait_timeout: bool = False
    lock_timeout_seconds: float = 0.0
    force_cleanup: bool = False
    client_found: bool = False
    cleanup_attempted: bool = False
    cleanup_error_type: str = ""
    cleanup_step_attempts: int = 0
    cleanup_step_timeouts: int = 0
    cleanup_step_errors: int = 0
    cleanup_step_last_error_type: str = ""
    cleanup_step_last_timeout: float = 0.0

    def as_meta(self) -> dict:
        return asdict(self)


def _merge_cleanup_report(target: ClientCleanupReport, source: ClientCleanupReport) -> None:
    target.cleanup_step_attempts += source.cleanup_step_attempts
    target.cleanup_step_timeouts += source.cleanup_step_timeouts
    target.cleanup_step_errors += source.cleanup_step_errors
    if source.cleanup_step_last_error_type:
        target.cleanup_step_last_error_type = source.cleanup_step_last_error_type
    if source.cleanup_step_last_timeout:
        target.cleanup_step_last_timeout = source.cleanup_step_last_timeout


def _lock_has_waiters(lock: asyncio.Lock) -> bool:
    waiters = getattr(lock, "_waiters", None)
    return bool(waiters)


def _drop_client_lock_if_idle(key: str, lock: asyncio.Lock) -> None:
    if _CLIENT_ASYNC_LOCKS.get(key) is not lock:
        return
    if key in _CLIENT_INSTANCES or _CLIENT_REFS.get(key, 0) > 0:
        return
    if lock.locked() or _lock_has_waiters(lock):
        return
    _CLIENT_ASYNC_LOCKS.pop(key, None)


def _client_lock_timeout_meta(operation: str) -> dict:
    return {
        "source": "client_manager",
        "operation": operation,
        "timeout_seconds": _TG_CLIENT_LOCK_TIMEOUT,
        "locked": True,
        "error_type": "TimeoutError",
    }


def _client_startup_retry_meta(
    *,
    attempt: int,
    total_attempts: int,
    wait_seconds: float,
    error: Exception,
    reason: str,
) -> dict:
    return {
        "source": "client_manager",
        "operation": "startup_retry",
        "attempt": attempt,
        "total_attempts": total_attempts,
        "retry_budget_remaining": max(total_attempts - attempt, 0),
        "wait_seconds": wait_seconds,
        "cleanup_attempted": True,
        "error_type": type(error).__name__,
        "reason": reason,
    }


class Client(BaseClient):
    def __init__(self, name: str, *args, **kwargs):
        key = kwargs.pop("key", None)
        super().__init__(name, *args, **kwargs)
        self.key = key or str(pathlib.Path(self.workdir).joinpath(self.name).resolve())
        if MemoryStorage is not None and self.in_memory and self.session_string:
            self.storage = MemoryStorage(self.name, self.session_string)

    async def __aenter__(self):
        lock = _CLIENT_ASYNC_LOCKS.get(self.key)
        if lock is None:
            lock = asyncio.Lock()
            _CLIENT_ASYNC_LOCKS[self.key] = lock
        drop_lock_on_exit = False
        lock_acquired = False
        try:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=_TG_CLIENT_LOCK_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(
                    "Timeout waiting for lock on client %s during startup after %.2fs; "
                    "startup will be retried or cleaned up by caller",
                    self.name,
                    _TG_CLIENT_LOCK_TIMEOUT,
                    extra={
                        "flow_stage": "session",
                        "flow_event": "client_startup_lock_timeout",
                        "flow_meta": _client_lock_timeout_meta("startup_lock"),
                    },
                )
                raise

            lock_acquired = True
            _CLIENT_REFS[self.key] += 1
            if _CLIENT_REFS[self.key] == 1:
                max_retries = _TG_CONNECT_RETRIES
                for attempt in range(max_retries):
                    try:
                        if not self.is_connected:
                            await _await_with_hard_timeout(
                                self.connect(),
                                timeout=_TG_CONNECT_TIMEOUT,
                                late_label="connect",
                            )

                        try:
                            await _await_with_hard_timeout(
                                self.get_me(),
                                timeout=_TG_CONNECT_TIMEOUT,
                                late_label="get_me",
                            )
                        except Exception as e:
                            raise ConnectionError(f"Session invalid: {e}")

                        try:
                            await _await_with_hard_timeout(
                                self.start(),
                                timeout=_TG_CONNECT_TIMEOUT,
                                late_label="start",
                            )
                        except ConnectionError as e:
                            if "already connected" not in str(e).lower():
                                raise e

                        if hasattr(self, "storage") and hasattr(self.storage, "conn"):
                            try:
                                self.storage.conn.execute("PRAGMA journal_mode=WAL")
                                self.storage.conn.execute("PRAGMA busy_timeout=30000")
                            except Exception as e:
                                logger.error(f"Failed to enable WAL mode: {e}")
                        break
                    except Exception as e:
                        is_locked = "database is locked" in str(e)
                        is_connect_error = isinstance(e, (TimeoutError, asyncio.TimeoutError, ConnectionError))
                        if (is_locked or is_connect_error) and attempt < max_retries - 1:
                            await _force_cleanup_client(self)
                            reason = "database_locked" if is_locked else "connect_error"
                            wait_time = (
                                (attempt + 1) * 2
                                if is_locked
                                else max(_TG_CONNECT_RETRY_WAIT, 0)
                            )
                            if is_locked:
                                message = (
                                    f"Database locked when starting client {self.name}, "
                                    f"retrying in {wait_time}s... ({attempt + 1}/{max_retries})"
                                )
                            else:
                                message = (
                                    f"Telegram connection failed when starting client {self.name}: "
                                    f"{type(e).__name__}: {e}. Retrying in {wait_time}s... "
                                    f"({attempt + 1}/{max_retries})"
                                )
                            logger.warning(
                                message,
                                extra={
                                    "flow_stage": "session",
                                    "flow_event": "client_startup_retry_scheduled",
                                    "flow_meta": _client_startup_retry_meta(
                                        attempt=attempt + 1,
                                        total_attempts=max_retries,
                                        wait_seconds=wait_time,
                                        error=e,
                                        reason=reason,
                                    ),
                                },
                            )
                            await asyncio.sleep(wait_time)
                            continue

                        _CLIENT_REFS[self.key] -= 1
                        if _CLIENT_REFS[self.key] <= 0:
                            _CLIENT_REFS.pop(self.key, None)
                            _CLIENT_INSTANCES.pop(self.key, None)
                            await _force_cleanup_client(self)
                            drop_lock_on_exit = True
                        raise e
                retired_reason = _client_retired_reason(self)
                if retired_reason:
                    logger.warning(
                        "Client %s startup completed after it was retired by %s; "
                        "discarding stale client",
                        self.name,
                        retired_reason,
                        extra={
                            "flow_stage": "session",
                            "flow_event": "client_startup_discarded_after_retired",
                            "flow_meta": {
                                "source": "client_manager",
                                "operation": "startup",
                                "reason": retired_reason,
                                "error_type": "ConnectionError",
                            },
                        },
                    )
                    if _CLIENT_INSTANCES.get(self.key) is self:
                        _CLIENT_INSTANCES.pop(self.key, None)
                    _CLIENT_REFS.pop(self.key, None)
                    await _force_cleanup_client(self)
                    drop_lock_on_exit = True
                    raise ConnectionError(
                        f"Client {self.name} was retired during startup"
                    )
            return self
        finally:
            if lock_acquired and lock.locked():
                lock.release()
            if drop_lock_on_exit:
                _drop_client_lock_if_idle(self.key, lock)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        lock = _CLIENT_ASYNC_LOCKS.get(self.key)
        if lock is None:
            return
        lock_acquired = False
        try:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=_TG_CLIENT_LOCK_TIMEOUT)
                lock_acquired = True
            except asyncio.TimeoutError:
                logger.warning(
                    "Timeout waiting for lock on client %s during exit after %.2fs; "
                    "proceeding with forceful cleanup",
                    self.name,
                    _TG_CLIENT_LOCK_TIMEOUT,
                    extra={
                        "flow_stage": "session",
                        "flow_event": "client_exit_lock_timeout",
                        "flow_meta": _client_lock_timeout_meta("exit_lock"),
                    },
                )
                _CLIENT_REFS[self.key] = 0

            if lock_acquired:
                _CLIENT_REFS[self.key] -= 1

            if _CLIENT_REFS.get(self.key, 0) <= 0:
                try:
                    await _force_cleanup_client(self)
                finally:
                    if _CLIENT_INSTANCES.get(self.key) is self:
                        _CLIENT_INSTANCES.pop(self.key, None)
                    _CLIENT_REFS.pop(self.key, None)
                    _CLIENT_ASYNC_LOCKS.pop(self.key, None)
        finally:
            if lock_acquired and lock.locked():
                lock.release()

    async def log_out(self):
        await super().log_out()


def get_api_config():
    api_id_env = os.environ.get("TG_API_ID")
    api_hash_env = os.environ.get("TG_API_HASH")

    api_id = 611335
    if api_id_env:
        try:
            api_id = int(api_id_env)
        except (TypeError, ValueError):
            pass

    if isinstance(api_hash_env, str) and api_hash_env.strip():
        api_hash = api_hash_env.strip()
    else:
        api_hash = "d524b414d21f4d37f08684c1df41ac9c"

    return api_id, api_hash


def get_proxy(proxy: str = None):
    proxy = proxy or os.environ.get("TG_PROXY")
    if proxy:
        r = parse.urlparse(proxy)
        return {
            "scheme": r.scheme,
            "hostname": r.hostname,
            "port": r.port,
            "username": r.username,
            "password": r.password,
        }
    return None


def get_client(
    name: str = "my_account",
    proxy: dict = None,
    workdir: Union[str, pathlib.Path] = ".",
    session_string: str = None,
    in_memory: bool = False,
    api_id: int = None,
    api_hash: str = None,
    **kwargs,
) -> Client:
    proxy = proxy or get_proxy()
    if not api_id or not api_hash:
        _api_id, _api_hash = get_api_config()
        api_id = api_id or _api_id
        api_hash = api_hash or _api_hash

    key = str(pathlib.Path(workdir).joinpath(name).resolve())
    existing_client = _CLIENT_INSTANCES.get(key)
    if existing_client is not None:
        if not _client_retired_reason(existing_client):
            return existing_client
        _CLIENT_INSTANCES.pop(key, None)
    client = Client(
        name,
        api_id=api_id,
        api_hash=api_hash,
        proxy=proxy,
        workdir=workdir,
        session_string=session_string,
        in_memory=in_memory,
        key=key,
        **kwargs,
    )
    _CLIENT_INSTANCES[key] = client
    return client


async def close_client_by_name(name: str, workdir: Union[str, pathlib.Path] = ".") -> dict:
    key = str(pathlib.Path(workdir).joinpath(name).resolve())
    report = ClientCleanupReport(lock_timeout_seconds=_TG_CLIENT_LOCK_TIMEOUT)

    lock = _CLIENT_ASYNC_LOCKS.get(key)
    if not lock:
        client = _CLIENT_INSTANCES.pop(key, None)
        _CLIENT_REFS.pop(key, None)
        report.client_found = client is not None
        if client:
            _mark_client_retired(client, reason="close_without_lock")
            report.cleanup_attempted = True
            try:
                _merge_cleanup_report(report, await _force_cleanup_client(client))
            except Exception as e:
                report.cleanup_error_type = type(e).__name__
                logger.warning(f"Error stopping client {name}: {e}")
        return report.as_meta()

    report.lock_present = True
    lock_acquired = False
    try:
        try:
            await asyncio.wait_for(lock.acquire(), timeout=_TG_CLIENT_LOCK_TIMEOUT)
            report.lock_acquired = True
            lock_acquired = True
        except asyncio.TimeoutError:
            report.lock_wait_timeout = True
            report.force_cleanup = True
            logger.warning(
                f"Timeout waiting for lock on client {name} after "
                f"{_TG_CLIENT_LOCK_TIMEOUT:.2f}s, proceeding with forceful cleanup",
                extra={
                    "flow_stage": "session",
                    "flow_event": "client_close_lock_timeout",
                    "flow_meta": _client_lock_timeout_meta("close_lock"),
                },
            )
        _CLIENT_REFS[key] = 0
        client = _CLIENT_INSTANCES.get(key)
        report.client_found = client is not None
        if client:
            _mark_client_retired(
                client,
                reason="close_lock_timeout" if not lock_acquired else "close",
            )
            report.cleanup_attempted = True
            try:
                _merge_cleanup_report(report, await _force_cleanup_client(client))
            except Exception as e:
                report.cleanup_error_type = type(e).__name__
                logger.warning(f"Error stopping client {name}: {e}")
            finally:
                if _CLIENT_INSTANCES.get(key) is client:
                    _CLIENT_INSTANCES.pop(key, None)
        _CLIENT_REFS.pop(key, None)
        if not lock_acquired:
            _CLIENT_ASYNC_LOCKS.pop(key, None)
    finally:
        if lock_acquired and lock.locked():
            lock.release()
        if lock_acquired:
            _drop_client_lock_if_idle(key, lock)
    return report.as_meta()
