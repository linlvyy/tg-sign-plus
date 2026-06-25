from __future__ import annotations

import logging
import re
from typing import Dict, List

from backend.services.sign_task_log_context import get_sign_task_run_id
from backend.services.task_flow_logger import TaskFlowLogger


_CLIENT_STARTUP_LOCK_TIMEOUT_RE = re.compile(
    r"Timeout waiting for lock on client (?P<client>.+?) during startup after "
    r"(?P<timeout>[0-9.]+)s"
)
_CLIENT_EXIT_LOCK_TIMEOUT_RE = re.compile(
    r"Timeout waiting for lock on client (?P<client>.+?) during exit after "
    r"(?P<timeout>[0-9.]+)s"
)
_CLIENT_RPC_HARD_TIMEOUT_RE = re.compile(
    r"Telegram (?P<operation>connect|get_me|start|cleanup step) timed out after "
    r"(?P<timeout>[0-9.]+)s; cancelling background task"
)


class TaskLogHandler(logging.Handler):
    """将运行日志实时写入文本列表和结构化步骤流。"""

    def __init__(
        self,
        log_list: List[str],
        flow_items: List[Dict[str, object]],
        offset_ref: Dict[str, int],
        max_lines: int = 1000,
        run_id: str | None = None,
    ):
        super().__init__()
        self.run_id = run_id
        self.flow_logger = TaskFlowLogger(
            log_list,
            flow_items,
            offset_ref,
            max_lines=max_lines,
        )

    def emit(self, record):
        try:
            if self.run_id is not None:
                record_run_id = getattr(record, "flow_run_id", None)
                current_run_id = record_run_id or get_sign_task_run_id()
                if current_run_id != self.run_id:
                    return
            text = record.getMessage()
            stage = getattr(record, "flow_stage", "message")
            event = getattr(record, "flow_event", "log")
            level = record.levelname.lower()
            meta = getattr(record, "flow_meta", None)
            if event == "log":
                parsed = self._parse_generic_worker_log(text)
                if parsed is not None:
                    stage, event, meta = parsed
            self.flow_logger.append(
                text,
                level=level,
                stage=stage,
                event=event,
                meta=meta,
            )
        except Exception:
            self.handleError(record)

    @staticmethod
    def _parse_generic_worker_log(text: str) -> tuple[str, str, Dict[str, object]] | None:
        match = _CLIENT_STARTUP_LOCK_TIMEOUT_RE.search(text)
        if match:
            try:
                timeout_seconds = float(match.group("timeout"))
            except (TypeError, ValueError):
                timeout_seconds = 0.0
            return (
                "session",
                "client_startup_lock_timeout",
                {
                    "timeout_seconds": timeout_seconds,
                },
            )
        match = _CLIENT_EXIT_LOCK_TIMEOUT_RE.search(text)
        if match:
            try:
                timeout_seconds = float(match.group("timeout"))
            except (TypeError, ValueError):
                timeout_seconds = 0.0
            return (
                "session",
                "client_exit_lock_timeout",
                {
                    "timeout_seconds": timeout_seconds,
                },
            )
        match = _CLIENT_RPC_HARD_TIMEOUT_RE.search(text)
        if match:
            try:
                timeout_seconds = float(match.group("timeout"))
            except (TypeError, ValueError):
                timeout_seconds = 0.0
            return (
                "session",
                "client_rpc_hard_timeout",
                {
                    "source": "client_manager",
                    "operation": match.group("operation").replace(" ", "_"),
                    "timeout": timeout_seconds,
                    "error_type": "TimeoutError",
                },
            )
        return None
