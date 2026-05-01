from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text

from backend.core.database import Base


class SignTaskRun(Base):
    __tablename__ = "sign_task_runs"
    __table_args__ = (
        Index("ix_sign_task_runs_account_task_created", "account_name", "task_name", "created_at"),
        Index("ix_sign_task_runs_account_created", "account_name", "created_at"),
        Index("ix_sign_task_runs_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    account_name = Column(String(100), nullable=False, index=True)
    task_name = Column(String(100), nullable=False, index=True)
    success = Column(Boolean, nullable=False)
    message = Column(Text, nullable=True)
    flow_logs = Column(Text, nullable=True)  # 兼容旧版字符串日志 JSON
    flow_items = Column(Text, nullable=True)  # 结构化步骤日志 JSON
    flow_truncated = Column(Boolean, default=False, nullable=False)
    flow_line_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
