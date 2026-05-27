from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import random
from collections import defaultdict
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


async def _force_cleanup_client(client) -> None:
    for obj, methods in (
        (client, ("stop", "disconnect")),
        (getattr(client, "session", None), ("stop", "disconnect")),
        (getattr(getattr(client, "session", None), "connection", None), ("close", "disconnect")),
    ):
        if obj is None:
            continue
        for method in methods:
            try:
                await asyncio.wait_for(_call_if_exists(obj, method), timeout=3)
            except Exception:
                pass

_CLIENT_INSTANCES: dict[str, "Client"] = {}
_CLIENT_REFS: defaultdict[str, int] = defaultdict(int)
_CLIENT_ASYNC_LOCKS: dict[str, asyncio.Lock] = {}


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
        async with lock:
            _CLIENT_REFS[self.key] += 1
            if _CLIENT_REFS[self.key] == 1:
                max_retries = _TG_CONNECT_RETRIES
                for attempt in range(max_retries):
                    try:
                        if not self.is_connected:
                            await asyncio.wait_for(
                                self.connect(), timeout=_TG_CONNECT_TIMEOUT
                            )

                        try:
                            await asyncio.wait_for(
                                self.get_me(), timeout=_TG_CONNECT_TIMEOUT
                            )
                        except Exception as e:
                            raise ConnectionError(f"Session invalid: {e}")

                        try:
                            await asyncio.wait_for(
                                self.start(), timeout=_TG_CONNECT_TIMEOUT
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
                                message
                            )
                            await asyncio.sleep(wait_time)
                            continue

                        _CLIENT_REFS[self.key] -= 1
                        if _CLIENT_REFS[self.key] <= 0:
                            _CLIENT_REFS.pop(self.key, None)
                            _CLIENT_INSTANCES.pop(self.key, None)
                            await _force_cleanup_client(self)
                        raise e
            return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        lock = _CLIENT_ASYNC_LOCKS.get(self.key)
        if lock is None:
            return
        async with lock:
            _CLIENT_REFS[self.key] -= 1
            if _CLIENT_REFS[self.key] == 0:
                try:
                    await _force_cleanup_client(self)
                finally:
                    if _CLIENT_INSTANCES.get(self.key) is self:
                        _CLIENT_INSTANCES.pop(self.key, None)
                    _CLIENT_REFS.pop(self.key, None)
                    _CLIENT_ASYNC_LOCKS.pop(self.key, None)

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
    if key in _CLIENT_INSTANCES:
        return _CLIENT_INSTANCES[key]
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


async def close_client_by_name(name: str, workdir: Union[str, pathlib.Path] = "."):
    key = str(pathlib.Path(workdir).joinpath(name).resolve())

    lock = _CLIENT_ASYNC_LOCKS.get(key)
    if lock:
        try:
            await asyncio.wait_for(lock.acquire(), timeout=5.0)
            try:
                _CLIENT_REFS[key] = 0
            finally:
                lock.release()
        except asyncio.TimeoutError:
            logger.warning(
                f"Timeout waiting for lock on client {name}, proceeding with forceful cleanup"
            )
            _CLIENT_REFS[key] = 0

    client = _CLIENT_INSTANCES.get(key)
    if client:
        try:
            await _force_cleanup_client(client)
        except Exception as e:
            logger.warning(f"Error stopping client {name}: {e}")
        finally:
            _CLIENT_INSTANCES.pop(key, None)

    if key in _CLIENT_ASYNC_LOCKS:
        _CLIENT_ASYNC_LOCKS.pop(key, None)
    if key in _CLIENT_REFS:
        _CLIENT_REFS.pop(key, None)
