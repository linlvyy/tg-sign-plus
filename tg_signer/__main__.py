from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Iterable, Optional

import click

from .ai_tools import OpenAIConfigManager
from .core import UserMonitor, UserSigner
from .logger import configure_logger
from backend.core.database import get_engine, init_engine
from backend.core.schema_migrator import upgrade_schema
from backend.repositories.sign_task_config_repo import get_sign_task_config_repo
from backend.repositories.sign_task_history_repo import get_sign_task_history_repo
from backend.services.sign_task_canary import generate_canary_report
from backend.services.sign_task_diagnostics import analyze_sign_task_run


def _run(coro):
    return asyncio.run(coro)


def _init_backend_schema() -> None:
    import backend.models  # noqa: F401

    init_engine()
    upgrade_schema(get_engine())


def _reset_backend_runtime_config() -> None:
    import backend.core.config as config_module
    import backend.core.database as database_module
    import backend.utils.storage as storage_module

    config_module.get_settings.cache_clear()
    database_module._engine = None
    database_module._SessionLocal = None
    storage_module._BASE_DIR = None


def _load_env_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key.startswith("export "):
            key = key.removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)
    return True


def _load_cli_env_files(paths: Iterable[Path]) -> bool:
    loaded = False
    seen: set[Path] = set()
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        loaded = _load_env_file(resolved) or loaded
    return loaded


def _resolve_account(global_account: Optional[str], args: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    if global_account:
        return global_account, args
    if not args:
        raise click.UsageError("缺少账号名；可以使用 --account 或把账号名作为第一个参数")
    return args[0], args[1:]


def _resolve_account_and_task(
    global_account: Optional[str],
    args: tuple[str, ...],
    *,
    task_label: str = "任务名",
) -> tuple[str, str]:
    account, rest = _resolve_account(global_account, args)
    if len(rest) != 1:
        raise click.UsageError(f"需要提供{task_label}")
    return account, rest[0]


def _signer(ctx, account: str, task_name: str = "my_task", *, no_updates: bool | None = None) -> UserSigner:
    return UserSigner(
        task_name=task_name,
        account=account,
        workdir=ctx.obj["workdir"],
        session_dir=ctx.obj["session_dir"],
        proxy=ctx.obj.get("proxy"),
        no_updates=no_updates,
    )


def _monitor(ctx, account: str, task_name: str = "my_monitor") -> UserMonitor:
    return UserMonitor(
        task_name=task_name,
        account=account,
        workdir=ctx.obj["workdir"],
        session_dir=ctx.obj["session_dir"],
        proxy=ctx.obj.get("proxy"),
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--workdir", type=click.Path(path_type=Path), default=".", show_default=True)
@click.option("--session-dir", "--session_dir", type=click.Path(path_type=Path), default=".", show_default=True)
@click.option("--data-dir", type=click.Path(path_type=Path), default=None, help="后端数据目录；用于 diagnose/canary 读取默认 sqlite")
@click.option("--database-url", default=None, help="后端数据库 URL；优先级高于 --data-dir")
@click.option("--account", default=None, help="默认账号名；多数子命令也兼容把账号名作为第一个参数")
@click.option("--proxy", default=None, help="Telegram 代理 URL")
@click.option("--log-level", default="INFO", show_default=True)
@click.pass_context
def signer(
    ctx,
    workdir: Path,
    session_dir: Path,
    data_dir: Optional[Path],
    database_url: Optional[str],
    account: Optional[str],
    proxy: Optional[str],
    log_level: str,
):
    """TG Sign Plus command line interface."""
    workdir = workdir.expanduser().resolve()
    session_dir = session_dir.expanduser().resolve()
    env_loaded = _load_cli_env_files((workdir / ".env",))
    backend_source_changed = env_loaded
    if data_dir is not None:
        data_dir = data_dir.expanduser().resolve()
        os.environ["APP_DATA_DIR"] = str(data_dir)
        backend_source_changed = True
    if database_url:
        os.environ["APP_DATABASE_URL"] = database_url
        backend_source_changed = True
    if backend_source_changed:
        _reset_backend_runtime_config()
    ctx.obj = {
        "workdir": workdir,
        "session_dir": session_dir,
        "data_dir": data_dir,
        "database_url": database_url,
        "account": account,
        "proxy": proxy,
    }
    configure_logger(log_level=log_level, log_dir=workdir / "logs")


@signer.command()
@click.argument("args", nargs=-1)
@click.option("--num-of-dialogs", default=20, show_default=True, type=int)
@click.pass_context
def login(ctx, args: tuple[str, ...], num_of_dialogs: int):
    """登录 Telegram 账号。用法：login ACCOUNT。"""
    account, rest = _resolve_account(ctx.obj.get("account"), args)
    if rest:
        raise click.UsageError("login 只接受一个账号名")
    _run(_signer(ctx, account).login(num_of_dialogs=num_of_dialogs))


@signer.command("config")
@click.argument("args", nargs=-1)
@click.pass_context
def config_task(ctx, args: tuple[str, ...]):
    """交互式配置签到任务。用法：config ACCOUNT TASK。"""
    account, task_name = _resolve_account_and_task(ctx.obj.get("account"), args)
    _signer(ctx, account, task_name).reconfig()


@signer.command("run")
@click.argument("args", nargs=-1)
@click.option("--num-of-dialogs", default=20, show_default=True, type=int)
@click.option("--no-updates/--updates", default=None, help="覆盖 Telegram updates 监听模式")
@click.pass_context
def run_task(ctx, args: tuple[str, ...], num_of_dialogs: int, no_updates: Optional[bool]):
    """执行一次签到任务。用法：run ACCOUNT TASK 或 --account ACCOUNT run TASK。"""
    account, task_name = _resolve_account_and_task(ctx.obj.get("account"), args)
    _run(
        _signer(ctx, account, task_name, no_updates=no_updates).run_once(
            num_of_dialogs=num_of_dialogs
        )
    )


@signer.command("run-once")
@click.argument("args", nargs=-1)
@click.option("--num-of-dialogs", default=20, show_default=True, type=int)
@click.option("--no-updates/--updates", default=None, help="覆盖 Telegram updates 监听模式")
@click.pass_context
def run_once(ctx, args: tuple[str, ...], num_of_dialogs: int, no_updates: Optional[bool]):
    """执行一次签到任务；等同于 run，保留用于兼容旧文档。"""
    account, task_name = _resolve_account_and_task(ctx.obj.get("account"), args)
    _run(
        _signer(ctx, account, task_name, no_updates=no_updates).run_once(
            num_of_dialogs=num_of_dialogs
        )
    )


@signer.command("diagnose-run")
@click.argument("args", nargs=-1)
@click.option("--index", default=0, show_default=True, type=int, help="诊断第几条历史记录，0 为最新")
@click.option("--json-output", is_flag=True, help="输出完整 JSON")
@click.pass_context
def diagnose_run(ctx, args: tuple[str, ...], index: int, json_output: bool):
    """诊断一次签到历史。用法：diagnose-run ACCOUNT TASK。"""
    account, task_name = _resolve_account_and_task(ctx.obj.get("account"), args)
    if index < 0:
        raise click.UsageError("--index 不能小于 0")
    _init_backend_schema()
    task = get_sign_task_config_repo().get_config(task_name, account)
    if not task:
        raise click.ClickException(f"任务不存在: {account}/{task_name}")
    history = get_sign_task_history_repo().load_entries(task_name, account)
    if index >= len(history):
        raise click.ClickException(f"没有第 {index} 条历史记录，当前只有 {len(history)} 条")
    entry = history[index]
    task_config = {
        "engine": task.get("engine", "event"),
        "chats": task.get("chats") or [],
    }
    diagnostics = analyze_sign_task_run(
        flow_items=entry.get("flow_items") or [],
        task_config=task_config,
        success=bool(entry.get("success", False)),
    )
    if json_output:
        click.echo(json.dumps(diagnostics, ensure_ascii=False, indent=2))
        return
    click.echo(f"{account}/{task_name} #{index}: {diagnostics['status']} - {diagnostics['summary']}")
    for check in diagnostics.get("checks") or []:
        detail = f" — {check.get('detail')}" if check.get("detail") else ""
        click.echo(f"- [{check.get('status')}] {check.get('label')}{detail}")


@signer.command("canary-report")
@click.argument("args", nargs=-1)
@click.option("--history-limit", default=1, show_default=True, type=int, help="每个任务纳入最近几条历史")
@click.option("--max-age-hours", default=36.0, show_default=True, type=float, help="最新证据允许的最大年龄；小于 0 表示不检查")
@click.option("--json-output", is_flag=True, help="输出完整 JSON")
@click.option("--strict", is_flag=True, help="报告状态不是 pass 时以非零状态码退出，用于 CI/部署验收")
@click.pass_context
def canary_report(
    ctx,
    args: tuple[str, ...],
    history_limit: int,
    max_age_hours: float,
    json_output: bool,
    strict: bool,
):
    """汇总 peach、喵了个咪、厂妹的事件引擎 canary 诊断。"""
    account = ctx.obj.get("account")
    if args:
        if len(args) != 1:
            raise click.UsageError("canary-report 最多接受一个账号名")
        account = args[0]
    _init_backend_schema()
    from backend.core.config import get_settings

    settings = get_settings()
    report = generate_canary_report(
        config_repo=get_sign_task_config_repo(),
        history_repo=get_sign_task_history_repo(),
        account_name=account,
        history_limit=history_limit,
        max_age_hours=None if max_age_hours < 0 else max_age_hours,
        source={
            "database_url": settings.database_url,
            "data_dir": str(settings.data_dir) if settings.data_dir else "",
            "resolved_base_dir": str(settings.resolve_base_dir()),
        },
    )
    if json_output:
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
        if strict and report.get("status") != "pass":
            ctx.exit(1)
        return
    scope = account or "all accounts"
    click.echo(f"canary {scope}: {report['status']}")
    source = report.get("source") or {}
    if source:
        click.echo(
            "source: "
            f"database_url={source.get('database_url', '-')} "
            f"data_dir={source.get('data_dir', '-')} "
            f"base_dir={source.get('resolved_base_dir', '-')}"
        )
    if report.get("hint"):
        click.echo(f"hint: {report['hint']}")
    for target in report.get("targets") or []:
        click.echo(f"- [{target.get('status')}] {target.get('label')}: {target.get('summary')}")
        for task in target.get("tasks") or []:
            runs = task.get("runs") or []
            latest = runs[0] if runs else {}
            diagnostics = latest.get("diagnostics") or {}
            click.echo(
                f"  {task.get('account_name')}/{task.get('task_name')} "
                f"engine={task.get('engine')} status={task.get('status')} "
                f"config={task.get('config_status', '-')} "
                f"run={task.get('run_status', '-')} "
                f"diag={diagnostics.get('status', '-')} "
                f"latest={task.get('latest_time') or '-'}"
            )
            failed_config_checks = [
                check
                for check in task.get("config_checks") or []
                if isinstance(check, dict) and check.get("status") in {"fail", "warn"}
            ]
            if failed_config_checks:
                labels = [
                    str(check.get("label") or check.get("id") or "-")
                    for check in failed_config_checks[:3]
                ]
                click.echo(f"    config checks: {', '.join(labels)}")
            latest_summary = task.get("latest_summary") or diagnostics.get("summary")
            if latest_summary:
                click.echo(f"    {latest_summary}")
    if strict and report.get("status") != "pass":
        ctx.exit(1)


@signer.command("list")
@click.argument("args", nargs=-1)
@click.pass_context
def list_tasks(ctx, args: tuple[str, ...]):
    """列出账号下的签到任务。用法：list ACCOUNT。"""
    account, rest = _resolve_account(ctx.obj.get("account"), args)
    if rest:
        raise click.UsageError("list 只接受一个账号名")
    _signer(ctx, account).list_()


@signer.command("send")
@click.argument("args", nargs=-1)
@click.option("--chat-id", required=True)
@click.option("--text", required=True)
@click.option("--delete-after", default=None, type=int)
@click.pass_context
def send(ctx, args: tuple[str, ...], chat_id: str, text: str, delete_after: Optional[int]):
    """发送一条 Telegram 文本消息。用法：send ACCOUNT --chat-id ID --text TEXT。"""
    account, rest = _resolve_account(ctx.obj.get("account"), args)
    if rest:
        raise click.UsageError("send 只接受一个账号名")
    target = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
    _run(_signer(ctx, account).send_text(target, text, delete_after=delete_after))


@signer.command("send-dice")
@click.argument("args", nargs=-1)
@click.option("--chat-id", required=True)
@click.option("--emoji", default="🎲", show_default=True)
@click.option("--delete-after", default=None, type=int)
@click.pass_context
def send_dice(ctx, args: tuple[str, ...], chat_id: str, emoji: str, delete_after: Optional[int]):
    """发送一条 Telegram 骰子消息。"""
    account, rest = _resolve_account(ctx.obj.get("account"), args)
    if rest:
        raise click.UsageError("send-dice 只接受一个账号名")
    target = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
    _run(_signer(ctx, account).send_dice_cli(target, emoji=emoji, delete_after=delete_after))


@signer.command("monitor")
@click.argument("args", nargs=-1)
@click.option("--num-of-dialogs", default=20, show_default=True, type=int)
@click.pass_context
def monitor(ctx, args: tuple[str, ...], num_of_dialogs: int):
    """运行消息监控任务。用法：monitor ACCOUNT MONITOR。"""
    account, task_name = _resolve_account_and_task(ctx.obj.get("account"), args, task_label="监控任务名")
    _run(_monitor(ctx, account, task_name).run(num_of_dialogs=num_of_dialogs))


@signer.command("monitor-config")
@click.argument("args", nargs=-1)
@click.pass_context
def monitor_config(ctx, args: tuple[str, ...]):
    """交互式配置消息监控任务。用法：monitor-config ACCOUNT MONITOR。"""
    account, task_name = _resolve_account_and_task(ctx.obj.get("account"), args, task_label="监控任务名")
    _monitor(ctx, account, task_name).reconfig()


@signer.command("llm-config")
@click.pass_context
def llm_config(ctx):
    """交互式保存 OpenAI/兼容接口配置。"""
    OpenAIConfigManager(ctx.obj["workdir"]).ask_for_config()


if __name__ == "__main__":
    signer()
