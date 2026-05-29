from __future__ import annotations

from typing import Any, Dict


def task_already_running_result() -> Dict[str, Any]:
    error = "任务已经在运行中"
    return {
        "success": False,
        "error": error,
        "output": "",
        "started": False,
        "code": "TASK_ALREADY_RUNNING",
        "run_summary": {
            "success": False,
            "status": "failed",
            "error": error,
            "error_type": "TaskAlreadyRunning",
            "error_timeout_scope": "none",
        },
    }
