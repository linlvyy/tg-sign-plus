from __future__ import annotations

import asyncio
import time
from typing import Callable

from pyrogram.types import Message

from tg_signer.config import AssertSuccessByTextAction
from tg_signer.message_helpers import get_message_text_content, readable_message


async def assert_success_by_text(
    *,
    action: AssertSuccessByTextAction,
    chat,
    app,
    context,
    log: Callable[..., None],
    clean_text_for_match: Callable[[str], str],
    timeout: float = 15.0,
) -> bool:
    keywords = [item.strip() for item in action.keywords if item and item.strip()]
    if not keywords:
        log(
            "成功判定失败：未配置有效关键字",
            level="WARNING",
            stage="action",
            event="success_assert_empty_keywords",
            meta={"chat_id": chat.chat_id},
        )
        return False

    log(
        f"开始等待签到结果（超时 {timeout}s）",
        stage="action",
        event="success_assert_wait_started",
        meta={"chat_id": chat.chat_id, "keywords": ", ".join(keywords), "timeout": timeout},
    )

    start = time.perf_counter()
    last_checked_message_id = None
    last_checked_callback_text = None

    while True:
        # 检查弹窗（callback）
        last_callback_text = str((context.last_callback_texts or {}).get(chat.chat_id, "") or "")
        if last_callback_text and last_callback_text != last_checked_callback_text:
            last_checked_callback_text = last_callback_text
            normalized_callback_text = clean_text_for_match(last_callback_text)
            log(
                f"开始根据最近一次弹窗判断签到结果: {last_callback_text}",
                stage="action",
                event="success_assert_started",
                meta={"chat_id": chat.chat_id, "source": "callback", "keywords": ", ".join(keywords)},
            )
            for keyword in keywords:
                normalized_keyword = clean_text_for_match(keyword)
                if normalized_keyword and normalized_keyword in normalized_callback_text:
                    log(
                        f"成功命中关键字: {keyword}",
                        level="success",
                        stage="result",
                        event="success_assert_matched",
                        meta={"chat_id": chat.chat_id, "source": "callback", "keyword": keyword},
                    )
                    return True

        # 检查实时消息
        messages_dict = context.chat_messages.get(chat.chat_id) or {}
        messages = [message for message in messages_dict.values() if isinstance(message, Message)]

        latest_message = None
        if messages:
            latest_message = max(
                messages,
                key=lambda message: (
                    getattr(message, "edit_date", None) or getattr(message, "date", None),
                    message.id,
                ),
            )

        if latest_message is not None:
            current_id = (latest_message.id, getattr(latest_message, "edit_date", None))
            if current_id != last_checked_message_id:
                last_checked_message_id = current_id
                message_text = get_message_text_content(latest_message)
                normalized_text = clean_text_for_match(message_text)
                log(
                    f"开始根据最后一条消息判断签到结果: {readable_message(latest_message)}",
                    stage="action",
                    event="success_assert_started",
                    meta={"chat_id": chat.chat_id, "source": "message", "message_id": latest_message.id, "keywords": ", ".join(keywords)},
                )
                for keyword in keywords:
                    normalized_keyword = clean_text_for_match(keyword)
                    if normalized_keyword and normalized_keyword in normalized_text:
                        log(
                            f"成功命中关键字: {keyword}",
                            level="success",
                            stage="result",
                            event="success_assert_matched",
                            meta={"chat_id": chat.chat_id, "source": "message", "message_id": latest_message.id, "keyword": keyword},
                        )
                        return True

        # 超时检查
        if time.perf_counter() - start >= timeout:
            break

        await asyncio.sleep(0.5)

    # 超时后最后尝试从历史消息获取
    latest_message = None
    try:
        async for message in app.get_chat_history(chat.chat_id, limit=1):
            latest_message = message
            break
    except Exception as e:
        log(
            f"查询最后一条消息失败: {e}",
            level="WARNING",
            stage="message",
            event="success_assert_history_fetch_failed",
            meta={"chat_id": chat.chat_id, "error_type": type(e).__name__},
        )

    if latest_message is not None:
        message_text = get_message_text_content(latest_message)
        normalized_text = clean_text_for_match(message_text)
        for keyword in keywords:
            normalized_keyword = clean_text_for_match(keyword)
            if normalized_keyword and normalized_keyword in normalized_text:
                log(
                    f"成功命中关键字（历史消息兜底）: {keyword}",
                    level="success",
                    stage="result",
                    event="success_assert_matched",
                    meta={"chat_id": chat.chat_id, "source": "history", "message_id": latest_message.id, "keyword": keyword},
                )
                return True

    # 所有检查均未命中
    last_callback_text = str((context.last_callback_texts or {}).get(chat.chat_id, "") or "")
    log(
        f"等待 {timeout}s 后仍未命中任何成功关键字",
        level="WARNING",
        stage="result",
        event="success_assert_failed",
        meta={
            "chat_id": chat.chat_id,
            "keywords": ", ".join(keywords),
            "callback_text": last_callback_text,
            "message_id": latest_message.id if latest_message else None,
            "message_text": get_message_text_content(latest_message) if latest_message else None,
        },
    )
    return False
