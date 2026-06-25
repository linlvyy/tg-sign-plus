from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from tg_signer.ai_tools import OpenAIConfig
from tg_signer.core import UserSigner

from backend.services.sign_task_event_presets import (
    normalize_event_task_config,
    validate_writable_event_task_config,
)
from backend.services.sign_task_log_handler import TaskLogHandler


class BackendUserSigner(UserSigner):
    """后端专用 UserSigner，适配数据库配置并禁止交互输入。"""

    def __init__(self, *args, chat_cache_loader=None, log_run_id: str | None = None, **kwargs):
        self._log_run_id = log_run_id
        super().__init__(
            *args,
            chat_cache_loader=chat_cache_loader or self._load_backend_chat_cache,
            **kwargs,
        )

    async def login(self, num_of_dialogs=0, print_chat=False):
        self.log("开始登录...")
        async with self.app:
            me = await self.app.get_me()
            self.set_me(me)

    def log(
        self,
        msg,
        level: str = "INFO",
        *,
        stage: str = "message",
        event: str = "log",
        meta: Optional[Dict[str, object]] = None,
        **kwargs,
    ):
        extra = kwargs.pop("extra", {}) or {}
        extra.update({
            "flow_stage": stage,
            "flow_event": event,
            "flow_meta": meta or {},
        })
        if self._log_run_id:
            extra["flow_run_id"] = self._log_run_id
        super().log(msg, level=level, extra=extra, **kwargs)

    @staticmethod
    def _load_backend_chat_cache(account_name: str) -> List[dict]:
        from backend.core.database import get_session_local
        from backend.models.account_chat_cache import AccountChatCacheItem

        db = get_session_local()()
        try:
            rows = (
                db.query(AccountChatCacheItem)
                .filter(AccountChatCacheItem.account_name == account_name)
                .order_by(AccountChatCacheItem.title.asc(), AccountChatCacheItem.chat_id.asc())
                .all()
            )
            return [
                {
                    "id": int(row.chat_id),
                    "title": row.title,
                    "username": row.username,
                    "type": row.chat_type,
                    "first_name": row.first_name,
                }
                for row in rows
            ]
        finally:
            db.close()

    @staticmethod
    def _load_backend_ai_config() -> Optional[OpenAIConfig]:
        from backend.services.config import get_config_service

        config = get_config_service().get_ai_config()
        if not config:
            return None

        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return None

        return OpenAIConfig(
            api_key=api_key,
            base_url=config.get("base_url") or None,
            model=config.get("model") or None,
        )

    def _get_config_repo(self):
        from backend.repositories.sign_task_config_repo import get_sign_task_config_repo

        return get_sign_task_config_repo()

    @property
    def task_dir(self):
        return self.tasks_dir / self._account / self.task_name

    def write_config(self, config):
        self._get_config_repo().save_config(
            self.task_name, self._account, config.to_jsonable()
        )
        self.config = config

    def ask_for_config(self):
        raise ValueError(
            f"任务配置文件不存在: {self.config_file}，且后端模式下禁止交互式输入。"
        )

    def reconfig(self):
        raise ValueError(
            f"任务配置文件不存在: {self.config_file}，且后端模式下禁止交互式输入。"
        )

    def load_config(self, cfg_cls=None):
        cfg_cls = cfg_cls or self.cfg_cls
        payload = self._get_config_repo().get_config(self.task_name, self._account)
        if not payload:
            config = self.reconfig()
        else:
            payload = dict(payload)
            payload.pop("name", None)
            normalized_payload = normalize_event_task_config(payload)
            if normalized_payload != payload:
                self._get_config_repo().save_config(
                    self.task_name, self._account, normalized_payload
                )
                payload = normalized_payload
            config, from_old = cfg_cls.load(payload)
            if from_old:
                self.write_config(config)
        self.config = config
        return config

    def export(self):
        payload = self._get_config_repo().get_config(self.task_name, self._account)
        if not payload:
            raise FileNotFoundError(f"任务配置不存在: {self.task_name}")
        payload = dict(payload)
        payload.pop("name", None)
        return json.dumps(payload, ensure_ascii=False)

    def import_(self, config_str: str):
        payload = json.loads(config_str)
        if not isinstance(payload, dict):
            raise ValueError("任务配置必须为 JSON 对象")
        payload["account_name"] = self._account
        validate_writable_event_task_config(payload)
        payload = normalize_event_task_config(payload)
        self._get_config_repo().save_config(self.task_name, self._account, payload)
        self.config = None

    def ensure_ai_cfg(self):
        cfg = self._load_backend_ai_config()
        if cfg:
            return cfg
        raise ValueError("未配置 AI 能力，请先在系统设置中保存 AI 配置")

    def ask_one(self):
        raise ValueError("后端模式下禁止交互式输入")
