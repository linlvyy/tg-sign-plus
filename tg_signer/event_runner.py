from __future__ import annotations

import asyncio
import os
import random
import re
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable, Iterable

from pyrogram import errors
from pyrogram.types import InlineKeyboardMarkup, Message, ReplyKeyboardMarkup

from tg_signer.config import (
    AssertSuccessByTextAction,
    ChooseOptionByImageAction,
    ClickButtonByCalculationProblemAction,
    ClickButtonByPoetryFillAction,
    ClickKeyboardByTextAction,
    ReplyByCalculationProblemAction,
    ReplyByImageRecognitionAction,
    SendDiceAction,
    SendTextAction,
    SignChatV3,
)

from .message_helpers import extract_keyboard_options, get_message_text_content, message_version, readable_message
from .text_cleaners import clean_text_for_match, clean_text_for_send
from .wait_dispatcher import BusinessRetryableError


class EventRunStatus(str, Enum):
    SUCCESS = "success"
    CHECKED = "checked"
    FAILED = "failed"


@dataclass
class EventRunResult:
    status: EventRunStatus
    message: str = ""


@dataclass
class EventRunSpec:
    send_actions: list[SendTextAction | SendDiceAction] = field(default_factory=list)
    response_actions: list[
        SendTextAction
        | SendDiceAction
        | ClickKeyboardByTextAction
        | ChooseOptionByImageAction
        | ReplyByCalculationProblemAction
        | ReplyByImageRecognitionAction
        | ClickButtonByCalculationProblemAction
        | ClickButtonByPoetryFillAction
    ] = field(default_factory=list)
    click_texts: list[str] = field(default_factory=list)
    choose_option_by_image: bool = False
    reply_by_calculation: bool = False
    reply_by_image: bool = False
    image_caption_patterns: list[str] = field(default_factory=list)
    captcha_lengths: list[int] = field(default_factory=list)
    captcha_charsets: list[str] = field(default_factory=list)
    captcha_case: str = "preserve"
    reply_captcha_to_message: bool = False
    click_by_calculation: bool = False
    click_by_poetry: bool = False
    success_keywords: list[str] = field(default_factory=list)
    checked_keywords: list[str] = field(default_factory=list)
    retry_keywords: list[str] = field(default_factory=list)
    fail_keywords: list[str] = field(default_factory=list)
    account_fail_keywords: list[str] = field(default_factory=list)
    ignore_keywords: list[str] = field(default_factory=list)
    requires_result: bool = False


DEFAULT_SUCCESS_KEYWORDS = ("签到成功", "成功", "通过", "完成", "获得")
DEFAULT_CHECKED_KEYWORDS = ("签到过了", "已经签到", "已签到", "今天已经签到", "签过", "重复签到", "明日再来")
DEFAULT_NEGATED_SUCCESS_KEYWORDS = (
    "未通过",
    "不通过",
    "无法通过",
    "未成功",
    "不成功",
    "未完成",
    "没有完成",
    "無法通過",
    "未通過",
    "不通過",
)
DEFAULT_FAIL_KEYWORDS = (
    "失败",
    "错误",
    "验证码错误",
    "网络错误",
    "超时",
    "未通过",
    "不通过",
    "无法通过",
    "未成功",
    "不成功",
    "未完成",
)
DEFAULT_RETRY_HINTS = (
    "验证码错误",
    "网络错误",
    "签到失败",
    "验证失败",
    "识别失败",
    "校验失败",
    "未通过",
    "无法通过",
    "未成功",
    "未完成",
)
DEFAULT_ACCOUNT_FAIL_KEYWORDS = (
    "拉黑",
    "黑名单",
    "冻结",
    "未找到用户",
    "无资格",
    "退出群",
    "退群",
    "加群",
    "加入群聊",
    "请先关注",
    "请先加入",
    "請先加入",
    "未注册",
    "先注册",
    "不存在",
    "不在群组中",
    "你有号吗",
    "次数过多",
    "尝试次数过多",
    "嘗試次數過多",
    "已尝试",
    "已嘗試",
    "过多",
    "過多",
    "操作过于频繁",
    "操作過於頻繁",
)


def _read_float_env(name: str, default: float, minimum: float = 1.0) -> float:
    try:
        return max(float(os.environ.get(name, default)), minimum)
    except (TypeError, ValueError):
        return default


def _optional_float(value, *, minimum: float = 0.0) -> float | None:
    try:
        if value is None:
            return None
        return max(float(value), minimum)
    except (TypeError, ValueError):
        return None


def _optional_int(value, *, minimum: int = 0) -> int | None:
    try:
        if value is None:
            return None
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return None


def _dedupe(items: Iterable[str]) -> list[str]:
    seen = set()
    values = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _extract_button_texts(message: Message) -> list[str]:
    options = extract_keyboard_options(message)
    if options:
        return options
    reply_markup = getattr(message, "reply_markup", None)
    rows = getattr(reply_markup, "inline_keyboard", None) or getattr(reply_markup, "keyboard", None) or []
    texts = []
    for row in rows:
        for button in row:
            text = getattr(button, "text", "") or ""
            if text:
                texts.append(text)
    return texts


def build_event_spec(chat: SignChatV3) -> EventRunSpec:
    spec = EventRunSpec()
    collecting_initial_sends = True
    for action in chat.actions:
        if isinstance(action, (SendTextAction, SendDiceAction)):
            if collecting_initial_sends:
                spec.send_actions.append(action)
            else:
                spec.response_actions.append(action)
            continue
        collecting_initial_sends = False
        if isinstance(action, ClickKeyboardByTextAction):
            spec.response_actions.append(action)
            spec.click_texts.append(action.text)
        elif isinstance(action, ChooseOptionByImageAction):
            spec.response_actions.append(action)
            spec.choose_option_by_image = True
        elif isinstance(action, ReplyByCalculationProblemAction):
            spec.response_actions.append(action)
            spec.reply_by_calculation = True
        elif isinstance(action, ReplyByImageRecognitionAction):
            spec.response_actions.append(action)
            spec.reply_by_image = True
            if action.caption_pattern:
                spec.image_caption_patterns.append(action.caption_pattern)
            spec.captcha_lengths.extend(action.captcha_lengths or [])
            if action.captcha_charset:
                spec.captcha_charsets.append(action.captcha_charset)
            if action.captcha_case != "preserve":
                spec.captcha_case = action.captcha_case
            if action.reply_to_message:
                spec.reply_captcha_to_message = True
        elif isinstance(action, ClickButtonByCalculationProblemAction):
            spec.response_actions.append(action)
            spec.click_by_calculation = True
        elif isinstance(action, ClickButtonByPoetryFillAction):
            spec.response_actions.append(action)
            spec.click_by_poetry = True
        elif isinstance(action, AssertSuccessByTextAction):
            spec.requires_result = True
            spec.success_keywords.extend(action.keywords)
            spec.checked_keywords.extend(action.checked_keywords)
            spec.retry_keywords.extend(action.retry_keywords)
            spec.fail_keywords.extend(action.fail_keywords)
            spec.account_fail_keywords.extend(action.account_fail_keywords)
            spec.ignore_keywords.extend(action.ignore_keywords)
    spec.click_texts = _dedupe(spec.click_texts)
    spec.success_keywords = _dedupe(spec.success_keywords)
    spec.checked_keywords = _dedupe(spec.checked_keywords)
    spec.retry_keywords = _dedupe(spec.retry_keywords)
    spec.fail_keywords = _dedupe(spec.fail_keywords)
    spec.account_fail_keywords = _dedupe(spec.account_fail_keywords)
    spec.ignore_keywords = _dedupe(spec.ignore_keywords)
    spec.image_caption_patterns = _dedupe(spec.image_caption_patterns)
    spec.captcha_lengths = sorted(set(spec.captcha_lengths))
    spec.captcha_charsets = _dedupe(spec.captcha_charsets)
    return spec


class SignEventRunner:
    def __init__(
        self,
        *,
        chat: SignChatV3,
        app,
        log: Callable[..., None],
        send_message: Callable[..., Awaitable],
        send_dice: Callable[..., Awaitable],
        request_callback_answer: Callable[..., Awaitable[bool]],
        get_ai_tools: Callable,
        timeout: float | None = None,
    ):
        self.chat = chat
        self.app = app
        self.log = log
        self.send_message = send_message
        self.send_dice = send_dice
        self.request_callback_answer = request_callback_answer
        self.get_ai_tools = get_ai_tools
        chat_timeout = _optional_float(getattr(chat, "event_timeout", None), minimum=1.0)
        self.timeout = timeout or chat_timeout or _read_float_env("TG_EVENT_ENGINE_TIMEOUT", 120.0)
        self.spec = build_event_spec(chat)
        self.finished = asyncio.Event()
        self.result: EventRunResult | None = None
        self.processed_versions = set()
        self.processing_versions = set()
        self.sent_captcha_versions = set()
        self.clicked_versions = set()
        self.message_lock = asyncio.Lock()
        self.current_response_index = 0
        self.retry_count = 0
        chat_retries = _optional_int(getattr(chat, "event_retries", None), minimum=0)
        chat_history_limit = _optional_int(getattr(chat, "event_history_limit", None), minimum=0)
        chat_retry_wait = _optional_float(getattr(chat, "event_retry_wait", None), minimum=0.0)
        chat_action_timeout = _optional_float(getattr(chat, "event_action_timeout", None), minimum=1.0)
        self.max_inline_retries = (
            chat_retries
            if chat_retries is not None
            else int(_read_float_env("TG_EVENT_ENGINE_INLINE_RETRIES", 3, minimum=0))
        )
        self.history_limit = (
            chat_history_limit
            if chat_history_limit is not None
            else int(_read_float_env("TG_EVENT_ENGINE_HISTORY_LIMIT", 3, minimum=0))
        )
        self.retry_wait = (
            chat_retry_wait
            if chat_retry_wait is not None
            else _read_float_env("TG_EVENT_ENGINE_RETRY_WAIT", 2.0, minimum=0.0)
        )
        self.action_timeout = (
            chat_action_timeout
            if chat_action_timeout is not None
            else _read_float_env("TG_EVENT_ENGINE_ACTION_TIMEOUT", 45.0)
        )
        self.history_rescue_interval = _read_float_env(
            "TG_EVENT_ENGINE_HISTORY_RESCUE_INTERVAL",
            5.0,
            minimum=1.0,
        )
        self.history_result_max_age = _read_float_env(
            "TG_EVENT_ENGINE_HISTORY_RESULT_MAX_AGE",
            600.0,
            minimum=0.0,
        )
        chat_ai_fallback = getattr(chat, "event_ai_fallback", None)
        if chat_ai_fallback is None:
            self.ai_fallback_enabled = os.environ.get("TG_EVENT_ENGINE_AI_FALLBACK", "0").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        else:
            self.ai_fallback_enabled = bool(chat_ai_fallback)
        self._last_history_rescue_at = 0.0
        self.history_rescue_min_message_id: int | None = None
        self.history_rescue_tracked_message_ids: set[int] = set()
        self._retry_task: asyncio.Task | None = None
        self._handling_startup_history = False

    def _finish(self, status: EventRunStatus, message: str = "") -> None:
        if self.finished.is_set():
            return
        self.result = EventRunResult(status=status, message=message)
        self.finished.set()

    def _text_matches(self, text: str, keywords: Iterable[str]) -> str | None:
        normalized_text = clean_text_for_match(text)
        if not normalized_text:
            return None
        for keyword in keywords:
            normalized_keyword = clean_text_for_match(keyword)
            if normalized_keyword and normalized_keyword in normalized_text:
                return keyword
        return None

    def _ignore_text(self, text: str) -> bool:
        if keyword := self._text_matches(text, self.spec.ignore_keywords):
            self.log(
                f"事件引擎忽略消息关键字: {keyword}",
                stage="message",
                event="event_engine_ignored_keyword_matched",
                meta={"chat_id": self.chat.chat_id, "keyword": keyword},
            )
            return True
        return False

    def _has_negated_success_text(self, text: str) -> bool:
        return bool(self._text_matches(text, DEFAULT_NEGATED_SUCCESS_KEYWORDS))

    def _log_failure_preempted_response_action(self, message: Message, action) -> None:
        self.log(
            "事件引擎在响应动作前命中失败/重试文本，跳过当前响应动作",
            level="WARNING",
            stage="result",
            event="event_engine_failure_preempted_response_action",
            meta={
                "chat_id": self.chat.chat_id,
                "message_id": getattr(message, "id", None),
                "action": str(action),
            },
        )

    def _classify_hard_failure_text(self, text: str) -> bool:
        if not text or not self.spec.requires_result:
            return False
        if keyword := self._text_matches(text, self.spec.account_fail_keywords or DEFAULT_ACCOUNT_FAIL_KEYWORDS):
            self.log(
                f"事件引擎命中账户失败关键字: {keyword}",
                level="WARNING",
                stage="result",
                event="event_engine_account_failed",
                meta={"chat_id": self.chat.chat_id, "keyword": keyword},
            )
            self._finish(EventRunStatus.FAILED, f"matched account failure keyword: {keyword}")
            return True
        default_account_failure = self._text_matches(text, DEFAULT_ACCOUNT_FAIL_KEYWORDS)
        if default_account_failure:
            self.log(
                f"事件引擎命中默认硬失败关键字: {default_account_failure}",
                level="WARNING",
                stage="result",
                event="event_engine_account_failed",
                meta={"chat_id": self.chat.chat_id, "keyword": default_account_failure},
            )
            self._finish(EventRunStatus.FAILED, f"matched account failure keyword: {default_account_failure}")
            return True
        return False

    def _has_hard_failure_text(self, text: str) -> bool:
        if not text or not self.spec.requires_result:
            return False
        return bool(
            self._text_matches(text, self.spec.account_fail_keywords or DEFAULT_ACCOUNT_FAIL_KEYWORDS)
            or self._text_matches(text, DEFAULT_ACCOUNT_FAIL_KEYWORDS)
        )

    def _has_retry_or_failure_text(self, text: str) -> bool:
        if not text or not self.spec.requires_result:
            return False
        return bool(
            self._has_hard_failure_text(text)
            or self._text_matches(text, self.spec.retry_keywords or DEFAULT_RETRY_HINTS)
            or self._text_matches(text, self.spec.fail_keywords or DEFAULT_FAIL_KEYWORDS)
            or self._has_negated_success_text(text)
        )

    def _classify_failure_text(self, text: str) -> bool:
        if not text or not self.spec.requires_result:
            return False
        if keyword := self._text_matches(text, self.spec.retry_keywords or DEFAULT_RETRY_HINTS):
            self._schedule_retry(f"matched retry keyword: {keyword}")
            return True
        if keyword := self._text_matches(text, self.spec.fail_keywords or DEFAULT_FAIL_KEYWORDS):
            self.log(
                f"事件引擎命中失败关键字: {keyword}",
                level="WARNING",
                stage="result",
                event="event_engine_failed_matched",
                meta={"chat_id": self.chat.chat_id, "keyword": keyword},
            )
            self._finish(EventRunStatus.FAILED, f"matched failure keyword: {keyword}")
            return True
        return False

    def _classify_text(
        self,
        text: str,
        *,
        include_failures: bool = True,
        skip_hard_failures: bool = False,
        allow_default_success: bool = True,
    ) -> bool:
        if not text or not self.spec.requires_result:
            return False
        if skip_hard_failures and self._text_matches(text, self.spec.account_fail_keywords or DEFAULT_ACCOUNT_FAIL_KEYWORDS):
            return False
        if skip_hard_failures and self._text_matches(text, DEFAULT_ACCOUNT_FAIL_KEYWORDS):
            return False
        if include_failures and self._ignore_text(text):
            return True
        if include_failures and self._classify_hard_failure_text(text):
            return True
        if keyword := self._text_matches(text, self.spec.checked_keywords or DEFAULT_CHECKED_KEYWORDS):
            self.log(
                f"事件引擎命中已签到关键字: {keyword}",
                level="success",
                stage="result",
                event="event_engine_checked_matched",
                meta={"chat_id": self.chat.chat_id, "keyword": keyword},
            )
            self._finish(EventRunStatus.CHECKED, f"matched checked keyword: {keyword}")
            return True
        if include_failures:
            if self._classify_failure_text(text):
                return True
        if self._has_negated_success_text(text):
            return False
        success_keywords = self.spec.success_keywords
        if not success_keywords and allow_default_success:
            success_keywords = list(DEFAULT_SUCCESS_KEYWORDS)
        if keyword := self._text_matches(text, success_keywords):
            self.log(
                f"事件引擎命中成功关键字: {keyword}",
                level="success",
                stage="result",
                event="event_engine_success_matched",
                meta={"chat_id": self.chat.chat_id, "keyword": keyword},
            )
            self._finish(EventRunStatus.SUCCESS, f"matched success keyword: {keyword}")
            return True
        return False

    async def _handle_callback_text(self, text: str, *, chat_id=None, message_id=None) -> None:
        if not text:
            return
        self.log(
            f"事件引擎处理按钮弹窗: {text}",
            stage="result",
            event="event_engine_callback_text_received",
            meta={"chat_id": chat_id or self.chat.chat_id, "message_id": message_id},
        )
        self._classify_text(text)

    def _schedule_retry(self, reason: str) -> None:
        if self._retry_task and not self._retry_task.done():
            return
        self.retry_count += 1
        if self.retry_count > self.max_inline_retries:
            self.log(
                f"事件引擎内部重试次数耗尽: {reason}",
                level="ERROR",
                stage="result",
                event="event_engine_retry_limit_exceeded",
                meta={
                    "chat_id": self.chat.chat_id,
                    "retry_count": self.retry_count,
                    "max_inline_retries": self.max_inline_retries,
                    "reason": reason,
                },
            )
            self._finish(EventRunStatus.FAILED, f"retry limit exceeded: {reason}")
            return
        self.current_response_index = 0
        self._reset_attempt_state()
        self.log(
            f"事件引擎准备重试入口动作: {reason}",
            level="WARNING",
            stage="action",
            event="event_engine_retry_scheduled",
            meta={"chat_id": self.chat.chat_id, "retry_count": self.retry_count},
        )
        self._retry_task = asyncio.create_task(self._retry_initial_actions(reason))

    def _reset_attempt_state(self) -> None:
        self.processed_versions.clear()
        self.sent_captcha_versions.clear()
        self.clicked_versions.clear()
        self.history_rescue_tracked_message_ids.clear()

    def _current_response_action(self):
        if self.current_response_index >= len(self.spec.response_actions):
            return None
        return self.spec.response_actions[self.current_response_index]

    def _can_handle_action_from_startup_history(self, action) -> bool:
        return isinstance(action, ReplyByImageRecognitionAction)

    def _startup_history_skip_relevant(self, action, message: Message) -> bool:
        if isinstance(action, ClickKeyboardByTextAction):
            target = clean_text_for_match(action.text)
            return bool(target and any(target in clean_text_for_match(button) for button in _extract_button_texts(message)))
        if isinstance(action, ChooseOptionByImageAction):
            return bool(message.photo and _extract_button_texts(message))
        if isinstance(action, ReplyByCalculationProblemAction):
            return bool(get_message_text_content(message))
        if isinstance(action, (ClickButtonByCalculationProblemAction, ClickButtonByPoetryFillAction)):
            return bool(get_message_text_content(message) and _extract_button_texts(message))
        return True

    def _advance_response_action(self) -> None:
        if self.current_response_index < len(self.spec.response_actions):
            self.current_response_index += 1

    def _finish_if_no_result_required(self) -> None:
        if self.spec.requires_result or self.finished.is_set():
            return
        if self.current_response_index < len(self.spec.response_actions):
            return
        self.log(
            "事件引擎动作已完成，无需等待结果断言",
            level="success",
            stage="result",
            event="event_engine_completed_without_result_assertion",
            meta={"chat_id": self.chat.chat_id},
        )
        self._finish(EventRunStatus.SUCCESS, "completed without result assertion")

    async def _click_button(self, message: Message, target_text: str, *, trusted_timeout: bool = True) -> bool:
        reply_markup = message.reply_markup
        target = clean_text_for_match(target_text)
        if not target:
            return False
        if isinstance(reply_markup, InlineKeyboardMarkup):
            for row in reply_markup.inline_keyboard:
                for button in row:
                    button_text = getattr(button, "text", "") or ""
                    if target not in clean_text_for_match(button_text):
                        continue
                    callback_data = getattr(button, "callback_data", None)
                    if callback_data is None:
                        self.log(
                            f"事件引擎跳过无回调数据按钮: [{button_text}]",
                            level="WARNING",
                            stage="action",
                            event="event_engine_button_without_callback_data",
                            meta={
                                "chat_id": message.chat.id,
                                "message_id": message.id,
                                "button_text": button_text,
                            },
                        )
                        continue
                    version = (
                        message_version(message),
                        target,
                        button_text,
                        callback_data,
                    )
                    if version in self.clicked_versions:
                        return False
                    self.clicked_versions.add(version)
                    self.log(
                        f"事件引擎点击按钮: [{button_text}]",
                        stage="action",
                        event="event_engine_button_clicked",
                        meta={"chat_id": message.chat.id, "message_id": message.id, "button_text": button_text},
                    )
                    confirmed = await self.request_callback_answer(
                        self.app,
                        message.chat.id,
                        message.id,
                        callback_data,
                        trust_consumed_after_timeout=trusted_timeout,
                        callback_text_handler=self._handle_callback_text,
                    )
                    if not confirmed:
                        self.log(
                            f"事件引擎按钮回调未确认，允许后续重试: [{button_text}]",
                            level="WARNING",
                            stage="action",
                            event="event_engine_button_callback_unconfirmed",
                            meta={
                                "chat_id": message.chat.id,
                                "message_id": message.id,
                                "button_text": button_text,
                                "source": "startup_history" if self._handling_startup_history else "realtime",
                            },
                        )
                        self.clicked_versions.discard(version)
                    return confirmed
        elif isinstance(reply_markup, ReplyKeyboardMarkup):
            for row in reply_markup.keyboard:
                for button in row:
                    button_text = getattr(button, "text", "") or ""
                    if target not in clean_text_for_match(button_text):
                        continue
                    version = (message_version(message), target, button_text)
                    if version in self.clicked_versions:
                        return False
                    self.clicked_versions.add(version)
                    self.log(
                        f"事件引擎发送回复键盘文本: [{button_text}]",
                        stage="action",
                        event="event_engine_reply_keyboard_sent",
                        meta={"chat_id": message.chat.id, "message_id": message.id, "button_text": button_text},
                    )
                    await self.send_message(message.chat.id, button_text)
                    return True
        return False

    async def _choose_option_by_image(self, message: Message) -> bool:
        reply_markup = message.reply_markup
        if not (message.photo and isinstance(reply_markup, InlineKeyboardMarkup)):
            return False
        buttons = []
        for row in reply_markup.inline_keyboard:
            for button in row:
                button_text = getattr(button, "text", "") or ""
                if not button_text:
                    continue
                if getattr(button, "callback_data", None) is None:
                    self.log(
                        f"事件引擎跳过无回调数据图片选项按钮: [{button_text}]",
                        level="WARNING",
                        stage="action",
                        event="event_engine_button_without_callback_data",
                        meta={
                            "chat_id": message.chat.id,
                            "message_id": message.id,
                            "button_text": button_text,
                            "source": "image_option",
                        },
                    )
                    continue
                buttons.append(button)
        if not buttons:
            return False
        image_buffer = await self.app.download_media(message.photo.file_id, in_memory=True)
        image_buffer.seek(0)
        image_bytes = image_buffer.read()
        options = [button.text for button in buttons]
        result_index = await self.get_ai_tools().choose_option_by_image(
            image_bytes,
            "选择正确的选项",
            list(enumerate(options, start=1)),
        )
        if not 1 <= result_index <= len(buttons):
            self.log(
                f"事件引擎 AI 返回非法选项序号: {result_index}",
                level="WARNING",
                stage="action",
                event="event_engine_invalid_option_index",
                meta={"chat_id": message.chat.id, "message_id": message.id},
            )
            return False
        button = buttons[result_index - 1]
        version = (
            message_version(message),
            "image_option",
            button.text,
            button.callback_data,
        )
        if version in self.clicked_versions:
            return False
        self.clicked_versions.add(version)
        self.log(
            f"事件引擎选择图片选项: {button.text}",
            stage="action",
            event="event_engine_image_option_selected",
            meta={"chat_id": message.chat.id, "message_id": message.id, "result": button.text},
        )
        confirmed = await self.request_callback_answer(
            self.app,
            message.chat.id,
            message.id,
            button.callback_data,
            trust_consumed_after_timeout=False,
            callback_text_handler=self._handle_callback_text,
        )
        if not confirmed:
            self.log(
                f"事件引擎图片选项回调未确认，允许后续重试: {button.text}",
                level="WARNING",
                stage="action",
                event="event_engine_button_callback_unconfirmed",
                meta={
                    "chat_id": message.chat.id,
                    "message_id": message.id,
                    "button_text": button.text,
                    "source": "image_option",
                },
            )
            self.clicked_versions.discard(version)
        return confirmed

    async def _reply_image_captcha(self, message: Message) -> bool:
        if not message.photo:
            return False
        if self.spec.image_caption_patterns:
            caption = message.caption or ""
            if not any(re.search(pattern, caption) for pattern in self.spec.image_caption_patterns):
                return False
        version = message_version(message)
        if version in self.sent_captcha_versions:
            return False
        self.sent_captcha_versions.add(version)
        image_buffer = await self.app.download_media(message.photo.file_id, in_memory=True)
        image_buffer.seek(0)
        image_bytes = image_buffer.read()
        text = await self.get_ai_tools().extract_text_by_image(image_bytes)
        text = clean_text_for_send(text)
        text = text.translate(str.maketrans("", "", string.punctuation)).replace(" ", "")
        if self.spec.captcha_case == "upper":
            text = text.upper()
        elif self.spec.captcha_case == "lower":
            text = text.lower()
        if self.spec.captcha_charsets:
            allowed = set("".join(self.spec.captcha_charsets))
            text = "".join(char for char in text if char in allowed)
        if not text:
            self.log(
                "事件引擎 OCR 返回空验证码，准备重试入口动作",
                level="WARNING",
                stage="action",
                event="event_engine_empty_captcha_retry",
                meta={"chat_id": message.chat.id, "message_id": message.id},
            )
            self._schedule_retry("empty captcha")
            return True
        if self.spec.captcha_lengths and len(text) not in self.spec.captcha_lengths:
            self.log(
                f"事件引擎 OCR 验证码长度不匹配: {text}",
                level="WARNING",
                stage="action",
                event="event_engine_captcha_length_mismatch",
                meta={
                    "chat_id": message.chat.id,
                    "message_id": message.id,
                    "length": len(text),
                    "expected_lengths": self.spec.captcha_lengths,
                },
            )
            self._schedule_retry("captcha length mismatch")
            return True
        self.log(
            f"事件引擎识别验证码: {text}",
            stage="action",
            event="event_engine_captcha_recognized",
            meta={"chat_id": message.chat.id, "message_id": message.id},
        )
        await asyncio.sleep(random.uniform(1.5, 3.5))
        if self.spec.reply_captcha_to_message:
            sent = await self.send_message(message.chat.id, text, reply_to_message_id=message.id)
            self._mark_entry_message_sent(sent)
            reply_to_message = True
        else:
            sent = await self.send_message(message.chat.id, text)
            self._mark_entry_message_sent(sent)
            reply_to_message = False
        self.log(
            "事件引擎已发送验证码",
            stage="action",
            event="event_engine_captcha_sent",
            meta={
                "chat_id": message.chat.id,
                "message_id": getattr(sent, "id", None),
                "source_message_id": message.id,
                "reply_to_message": reply_to_message,
            },
        )
        return True

    async def _reply_calculation(self, message: Message) -> bool:
        text = get_message_text_content(message)
        if not text:
            return False
        answer = (await self.get_ai_tools().calculate_problem(text) or "").strip()
        if not answer:
            return False
        self.log(
            f"事件引擎计算回答: {answer}",
            stage="action",
            event="event_engine_calculation_answered",
            meta={"chat_id": message.chat.id, "message_id": message.id},
        )
        await self.send_message(message.chat.id, answer)
        return True

    async def _click_calculation_answer(self, message: Message) -> bool:
        text = get_message_text_content(message)
        if not text or not isinstance(message.reply_markup, InlineKeyboardMarkup):
            return False
        answer = (await self.get_ai_tools().calculate_problem(text) or "").strip()
        if not answer:
            return False
        self.log(
            f"事件引擎计算并尝试点击答案: {answer}",
            stage="action",
            event="event_engine_calculation_click_answered",
            meta={"chat_id": message.chat.id, "message_id": message.id},
        )
        return await self._click_button(message, answer, trusted_timeout=False)

    async def _click_poetry_answer(self, message: Message) -> bool:
        text = get_message_text_content(message)
        options = extract_keyboard_options(message)
        if not text or not options:
            return False
        answer = clean_text_for_send(await self.get_ai_tools().solve_poetry_fill(text, options) or "")
        if not answer:
            return False
        candidates = [answer]
        if len(answer) > 1:
            candidates.extend([char for char in answer if char.strip()])
        self.log(
            f"事件引擎填诗并尝试点击答案: {answer}",
            stage="action",
            event="event_engine_poetry_click_answered",
            meta={"chat_id": message.chat.id, "message_id": message.id},
        )
        for candidate in _dedupe(candidates):
            if await self._click_button(message, candidate, trusted_timeout=False):
                return True
        return False

    async def _send_initial_actions(self, *, retry: bool = False) -> None:
        for index, action in enumerate(self.spec.send_actions, start=1):
            if retry and index == 1:
                await asyncio.sleep(self.retry_wait)
            if index > 1:
                await asyncio.sleep(max(float(getattr(self.chat, "action_interval", 1) or 0), 0))
            try:
                if isinstance(action, SendTextAction):
                    self.log(
                        f"事件引擎发送入口文本: {action.text}",
                        stage="action",
                        event="event_engine_send_text",
                        meta={"chat_id": self.chat.chat_id, "text": action.text, "retry": retry},
                    )
                    message = await self.send_message(self.chat.chat_id, action.text, self.chat.delete_after)
                    self._mark_entry_message_sent(message)
                elif isinstance(action, SendDiceAction):
                    self.log(
                        f"事件引擎发送入口骰子: {action.dice}",
                        stage="action",
                        event="event_engine_send_dice",
                        meta={"chat_id": self.chat.chat_id, "emoji": action.dice, "retry": retry},
                    )
                    message = await self.send_dice(self.chat.chat_id, action.dice, self.chat.delete_after)
                    self._mark_entry_message_sent(message)
            except (TimeoutError, asyncio.TimeoutError, errors.RPCError) as e:
                self.log(
                    f"事件引擎入口动作发送失败: {e}",
                    level="WARNING",
                    stage="action",
                    event="event_engine_initial_send_retryable_error",
                    meta={"chat_id": self.chat.chat_id, "action": str(action), "retry": retry, "error_type": type(e).__name__},
                )
                raise BusinessRetryableError(
                    f"Event engine initial action failed. chat_id={self.chat.chat_id}, action={action}"
                ) from e

    def _mark_entry_message_sent(self, message) -> None:
        message_id = getattr(message, "id", None)
        if isinstance(message_id, int):
            if self.history_rescue_min_message_id is None:
                self.history_rescue_min_message_id = message_id
            else:
                self.history_rescue_min_message_id = max(self.history_rescue_min_message_id, message_id)

    def _track_history_rescue_message(self, message: Message) -> None:
        message_id = getattr(message, "id", None)
        if isinstance(message_id, int):
            self.history_rescue_tracked_message_ids.add(message_id)

    async def _retry_initial_actions(self, reason: str) -> None:
        try:
            await self._send_initial_actions(retry=True)
            await self._drain_immediate_response_actions(ignore_retry_pending=True)
            self._finish_if_no_result_required()
        except BusinessRetryableError as e:
            self.log(
                f"事件引擎重试入口动作失败: {e}",
                level="ERROR",
                stage="action",
                event="event_engine_retry_initial_send_failed",
                meta={"chat_id": self.chat.chat_id, "reason": reason, "retry_count": self.retry_count},
            )
            self._finish(EventRunStatus.FAILED, str(e))
        except Exception as e:
            self.log(
                f"事件引擎重试入口动作异常: {e}",
                level="ERROR",
                stage="action",
                event="event_engine_retry_initial_send_error",
                meta={
                    "chat_id": self.chat.chat_id,
                    "reason": reason,
                    "retry_count": self.retry_count,
                    "error_type": type(e).__name__,
                },
            )
            self._finish(EventRunStatus.FAILED, str(e))

    async def _drain_immediate_response_actions(self, *, ignore_retry_pending: bool = False) -> None:
        while not self.finished.is_set():
            if not ignore_retry_pending and self._retry_task and not self._retry_task.done():
                return
            action = self._current_response_action()
            if not isinstance(action, (SendTextAction, SendDiceAction)):
                return
            await asyncio.sleep(max(float(getattr(self.chat, "action_interval", 1) or 0), 0))
            try:
                if isinstance(action, SendTextAction):
                    self.log(
                        f"事件引擎发送后续文本: {action.text}",
                        stage="action",
                        event="event_engine_send_followup_text",
                        meta={"chat_id": self.chat.chat_id, "text": action.text},
                    )
                    message = await self.send_message(self.chat.chat_id, action.text, self.chat.delete_after)
                    self._mark_entry_message_sent(message)
                elif isinstance(action, SendDiceAction):
                    self.log(
                        f"事件引擎发送后续骰子: {action.dice}",
                        stage="action",
                        event="event_engine_send_followup_dice",
                        meta={"chat_id": self.chat.chat_id, "emoji": action.dice},
                    )
                    message = await self.send_dice(self.chat.chat_id, action.dice, self.chat.delete_after)
                    self._mark_entry_message_sent(message)
            except (TimeoutError, asyncio.TimeoutError, errors.RPCError) as e:
                self.log(
                    f"事件引擎后续发送动作失败: {e}",
                    level="WARNING",
                    stage="action",
                    event="event_engine_followup_send_retryable_error",
                    meta={"chat_id": self.chat.chat_id, "action": str(action), "error_type": type(e).__name__},
                )
                self._schedule_retry(f"followup send failed: {e}")
                return
            self._advance_response_action()
            self._finish_if_no_result_required()

    async def handle_message(self, message: Message) -> None:
        if self.finished.is_set():
            return
        if not self._is_inbound_chat_message(message):
            return
        async with self.message_lock:
            await self._handle_message_locked(message)

    async def _handle_message_locked(self, message: Message) -> None:
        if self.finished.is_set():
            return
        version = message_version(message)
        if version in self.processed_versions:
            return
        if version in self.processing_versions:
            return
        self.processing_versions.add(version)
        try:
            try:
                message_readable = readable_message(message)
            except Exception:
                message_readable = f"Message(id={getattr(message, 'id', None)})"
            self.log(
                f"事件引擎收到消息: {message_readable}",
                stage="message",
                event="event_engine_message_received",
                meta={"chat_id": message.chat.id, "message_id": message.id},
            )
            text = get_message_text_content(message)
            ignored = self._ignore_text(text)
            current_action = self._current_response_action()
            if not ignored and self._classify_hard_failure_text(text):
                self.processed_versions.add(version)
                return
            if not ignored and self._classify_text(
                text,
                include_failures=False,
                allow_default_success=current_action is None,
            ):
                self.processed_versions.add(version)
                return
            if (
                isinstance(current_action, ReplyByImageRecognitionAction)
                and not ignored
            ):
                if self._classify_hard_failure_text(text) or self._classify_failure_text(text):
                    self._log_failure_preempted_response_action(message, current_action)
                    self.processed_versions.add(version)
                    return
            retry_pending = self._retry_task and not self._retry_task.done()
            if not retry_pending and await self._handle_current_response_action(message):
                self._track_history_rescue_message(message)
                self.processed_versions.add(version)
                return
            if ignored:
                self.processed_versions.add(version)
                return
            if self._classify_text(text):
                self.processed_versions.add(version)
                return
            if await self._handle_unexpected_interaction(message):
                self.processed_versions.add(version)
                return
        except (TimeoutError, asyncio.TimeoutError, errors.RPCError) as e:
            self.log(
                f"事件引擎处理消息超时/Telegram 错误: {e}",
                level="WARNING",
                stage="action",
                event="event_engine_message_retryable_error",
                meta={"chat_id": self.chat.chat_id, "message_id": getattr(message, "id", None), "error_type": type(e).__name__},
            )
            self._schedule_retry(f"retryable error: {e}")
        except Exception as e:
            self.log(
                f"事件引擎处理消息失败: {e}",
                level="ERROR",
                stage="action",
                event="event_engine_message_error",
                meta={"chat_id": self.chat.chat_id, "message_id": getattr(message, "id", None), "error_type": type(e).__name__},
            )
            self._finish(EventRunStatus.FAILED, str(e))
        finally:
            self.processing_versions.discard(version)

    async def run(self) -> EventRunResult:
        self.log(
            "事件引擎开始执行",
            stage="action",
            event="event_engine_started",
            meta={"chat_id": self.chat.chat_id, "timeout": self.timeout},
        )
        history_handled = await self._walk_history()
        if self.finished.is_set():
            return self.result or EventRunResult(EventRunStatus.FAILED, "missing result")
        if not history_handled:
            await self._send_initial_actions()
            await self._drain_immediate_response_actions()
            self._finish_if_no_result_required()
            if self.finished.is_set():
                return self.result or EventRunResult(EventRunStatus.FAILED, "missing result")
        try:
            return await asyncio.wait_for(self._wait_finished(), timeout=self.timeout)
        except asyncio.TimeoutError:
            self._log_timeout_state()
            raise BusinessRetryableError(
                f"Event engine timed out after {self.timeout}s. chat_id={self.chat.chat_id}"
            )
        finally:
            if self._retry_task and not self._retry_task.done():
                self._retry_task.cancel()

    async def _wait_finished(self) -> EventRunResult:
        while not self.finished.is_set():
            if self.history_limit > 0:
                now = asyncio.get_running_loop().time()
                if now - self._last_history_rescue_at >= self.history_rescue_interval:
                    self._last_history_rescue_at = now
                    await self._walk_history(rescue=True)
            await asyncio.sleep(0.2)
        return self.result or EventRunResult(EventRunStatus.FAILED, "missing result")

    def _log_timeout_state(self) -> None:
        action = self._current_response_action()
        self.log(
            "事件引擎等待超时状态快照",
            level="WARNING",
            stage="result",
            event="event_engine_timeout_state",
            meta={
                "chat_id": self.chat.chat_id,
                "timeout": self.timeout,
                "current_response_index": self.current_response_index,
                "response_action_count": len(self.spec.response_actions),
                "current_action": str(action) if action is not None else None,
                "retry_count": self.retry_count,
                "max_inline_retries": self.max_inline_retries,
                "retry_pending": bool(self._retry_task and not self._retry_task.done()),
                "history_limit": self.history_limit,
                "history_rescue_min_message_id": self.history_rescue_min_message_id,
                "history_rescue_tracked_message_ids": len(self.history_rescue_tracked_message_ids),
                "processed_versions": len(self.processed_versions),
                "processing_versions": len(self.processing_versions),
                "sent_captcha_versions": len(self.sent_captcha_versions),
                "clicked_versions": len(self.clicked_versions),
            },
        )

    async def _handle_current_response_action(self, message: Message) -> bool:
        action = self._current_response_action()
        if action is None:
            return False
        if self._handling_startup_history and not self._can_handle_action_from_startup_history(action):
            if self._startup_history_skip_relevant(action, message):
                self.log(
                    "事件引擎跳过启动历史中的旧交互动作",
                    stage="action",
                    event="event_engine_startup_history_action_skipped",
                    meta={
                        "chat_id": self.chat.chat_id,
                        "message_id": getattr(message, "id", None),
                        "action": str(action),
                        "source": "startup_history",
                    },
                )
            return False
        try:
            handled = await asyncio.wait_for(
                self._execute_response_action(action, message),
                timeout=self.action_timeout,
            )
        except asyncio.TimeoutError as e:
            self.log(
                f"事件引擎响应动作超时: {action}",
                level="WARNING",
                stage="action",
                event="event_engine_response_action_timeout",
                meta={
                    "chat_id": self.chat.chat_id,
                    "message_id": getattr(message, "id", None),
                    "action": str(action),
                    "timeout": self.action_timeout,
                },
            )
            raise e
        if handled:
            if not self.finished.is_set() and not (self._retry_task and not self._retry_task.done()):
                before_index = self.current_response_index
                self._advance_response_action()
                self.log(
                    "事件引擎响应动作已推进",
                    stage="action",
                    event="event_engine_response_action_advanced",
                    meta={
                        "chat_id": self.chat.chat_id,
                        "message_id": getattr(message, "id", None),
                        "action": str(action),
                        "from_index": before_index,
                        "to_index": self.current_response_index,
                        "source": "startup_history" if self._handling_startup_history else "realtime",
                    },
                )
                await self._drain_immediate_response_actions()
                self._finish_if_no_result_required()
        return handled

    async def _execute_response_action(self, action, message: Message) -> bool:
        if isinstance(action, ClickKeyboardByTextAction):
            return await self._click_button(message, action.text, trusted_timeout=not self._handling_startup_history)
        if isinstance(action, ChooseOptionByImageAction):
            return await self._choose_option_by_image(message)
        if isinstance(action, ReplyByCalculationProblemAction):
            return await self._reply_calculation(message)
        if isinstance(action, ReplyByImageRecognitionAction):
            return await self._reply_image_captcha(message)
        if isinstance(action, ClickButtonByCalculationProblemAction):
            return await self._click_calculation_answer(message)
        if isinstance(action, ClickButtonByPoetryFillAction):
            return await self._click_poetry_answer(message)
        return False

    async def _handle_unexpected_interaction(self, message: Message) -> bool:
        if not self.ai_fallback_enabled or not self.spec.requires_result:
            return False
        if self._current_response_action() is not None:
            return False
        if self._retry_task and not self._retry_task.done():
            return False
        text = get_message_text_content(message)
        buttons = _extract_button_texts(message)
        if not text and not buttons:
            return False
        tools = self.get_ai_tools()
        infer = getattr(tools, "infer_sign_interaction", None)
        if infer is None:
            return False
        self.log(
            "事件引擎尝试 AI 处理未配置的后续交互",
            stage="action",
            event="event_engine_ai_fallback_started",
            meta={"chat_id": message.chat.id, "message_id": message.id, "buttons": buttons},
        )
        decision = await infer(text, buttons)
        action = str((decision or {}).get("action") or "noop").lower()
        value = str((decision or {}).get("value") or "").strip()
        if action == "click" and value:
            self.log(
                f"事件引擎 AI 决定点击按钮: {value}",
                stage="action",
                event="event_engine_ai_fallback_click",
                meta={"chat_id": message.chat.id, "message_id": message.id, "button_text": value},
            )
            return await self._click_button(message, value, trusted_timeout=True)
        if action == "send" and value:
            self.log(
                f"事件引擎 AI 决定发送文本: {value}",
                stage="action",
                event="event_engine_ai_fallback_send",
                meta={"chat_id": message.chat.id, "message_id": message.id},
            )
            sent = await self.send_message(message.chat.id, value)
            self._mark_entry_message_sent(sent)
            return True
        if action == "status":
            self.log(
                "事件引擎 AI 判断该消息为状态消息，继续等待明确结果",
                stage="result",
                event="event_engine_ai_fallback_status",
                meta={"chat_id": message.chat.id, "message_id": message.id},
            )
            return True
        self.log(
            "事件引擎 AI 判断无需处理该消息",
            stage="action",
            event="event_engine_ai_fallback_noop",
            meta={"chat_id": message.chat.id, "message_id": message.id},
        )
        return False

    async def _walk_history(self, *, rescue: bool = False) -> bool:
        if self.history_limit <= 0:
            return False
        if rescue:
            self.log(
                "事件引擎扫描最近历史消息进行补漏",
                stage="message",
                event="event_engine_history_rescue_started",
                meta={"chat_id": self.chat.chat_id, "limit": self.history_limit},
            )
        try:
            messages = []
            async for message in self.app.get_chat_history(self.chat.chat_id, limit=self.history_limit):
                messages.append(message)
        except (TimeoutError, asyncio.TimeoutError, errors.RPCError) as e:
            self.log(
                f"事件引擎读取历史消息失败，跳过历史救援: {e}",
                level="WARNING",
                stage="message",
                event="event_engine_history_failed",
                meta={"chat_id": self.chat.chat_id, "error_type": type(e).__name__},
            )
            return False
        ordered_messages = list(reversed(messages))
        if not rescue:
            ids = [
                int(message.id)
                for message in messages
                if isinstance(getattr(message, "id", None), int)
            ]
            if ids:
                self.history_rescue_min_message_id = max(ids)
        startup_failure_skipped_ids: set[int] = set()
        result_scan_messages = messages if not rescue else ordered_messages
        for message in result_scan_messages:
            if not self._history_message_allowed(message, rescue=rescue):
                continue
            if not self._is_inbound_chat_message(message):
                continue
            if rescue and self._is_tracked_history_rescue_message(message):
                self._log_tracked_history_recheck(message)
            if self._ignore_text(get_message_text_content(message)):
                continue
            if not rescue and self._has_retry_or_failure_text(get_message_text_content(message)):
                self._log_startup_history_failure_skipped(message)
                message_id = getattr(message, "id", None)
                if isinstance(message_id, int):
                    startup_failure_skipped_ids.add(message_id)
                return False
            if self._classify_text(
                get_message_text_content(message),
                include_failures=rescue,
                skip_hard_failures=not rescue,
                allow_default_success=not self.spec.response_actions,
            ):
                return True
        handled = False
        for message in ordered_messages:
            if not self._history_message_allowed(message, rescue=rescue):
                continue
            message_id = getattr(message, "id", None)
            if not rescue and isinstance(message_id, int) and message_id in startup_failure_skipped_ids:
                continue
            if not rescue and self._has_retry_or_failure_text(get_message_text_content(message)):
                self._log_startup_history_failure_skipped(message)
                continue
            if rescue and self._is_tracked_history_rescue_message(message):
                self._log_tracked_history_recheck(message)
            if self.finished.is_set():
                return True
            before_index = self.current_response_index
            before_finished = self.finished.is_set()
            previous_startup_history = self._handling_startup_history
            self._handling_startup_history = not rescue
            try:
                await self.handle_message(message)
            finally:
                self._handling_startup_history = previous_startup_history
            if self.finished.is_set() or self.current_response_index != before_index or before_finished:
                handled = True
                if not rescue:
                    self._track_history_rescue_message(message)
        return handled

    def _log_startup_history_failure_skipped(self, message: Message) -> None:
        self.log(
            "事件引擎跳过启动历史中的旧失败/重试消息",
            stage="message",
            event="event_engine_history_hard_failure_skipped",
            meta={
                "chat_id": self.chat.chat_id,
                "message_id": getattr(message, "id", None),
            },
        )

    def _log_tracked_history_recheck(self, message: Message) -> None:
        self.log(
            "事件引擎复查已处理消息的编辑版本",
            stage="message",
            event="event_engine_history_tracked_message_rechecked",
            meta={
                "chat_id": self.chat.chat_id,
                "message_id": getattr(message, "id", None),
            },
        )

    def _is_tracked_history_rescue_message(self, message: Message) -> bool:
        message_id = getattr(message, "id", None)
        return (
            isinstance(message_id, int)
            and self.history_rescue_min_message_id is not None
            and message_id <= self.history_rescue_min_message_id
            and message_id in self.history_rescue_tracked_message_ids
        )

    def _history_message_allowed(self, message: Message, *, rescue: bool) -> bool:
        if rescue and self.history_rescue_min_message_id is not None:
            message_id = getattr(message, "id", None)
            if (
                isinstance(message_id, int)
                and message_id <= self.history_rescue_min_message_id
                and message_id not in self.history_rescue_tracked_message_ids
            ):
                return False
        if not rescue and self.history_result_max_age > 0:
            timestamp = getattr(message, "edit_date", None) or getattr(message, "date", None)
            if isinstance(timestamp, datetime):
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
                if age > self.history_result_max_age:
                    self.log(
                        "事件引擎跳过过期历史消息",
                        stage="message",
                        event="event_engine_history_message_expired",
                        meta={
                            "chat_id": self.chat.chat_id,
                            "message_id": getattr(message, "id", None),
                            "age_seconds": int(age),
                        },
                    )
                    return False
        return True

    def _is_inbound_chat_message(self, message: Message) -> bool:
        if getattr(message, "outgoing", False):
            return False
        from_user = getattr(message, "from_user", None)
        if getattr(from_user, "is_self", False):
            return False
        chat = getattr(message, "chat", None)
        return bool(chat is not None and getattr(chat, "id", None) == self.chat.chat_id)
