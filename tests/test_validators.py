from __future__ import annotations

import pytest

from backend.core.validators import (
    ValidationError,
    validate_account_name,
    validate_username,
)


@pytest.mark.parametrize(
    "name",
    ["木木44", "账号一", "mumu44", "木木_mumu-44"],
)
def test_account_name_accepts_chinese_and_ascii(name: str) -> None:
    assert validate_account_name(name) == name


@pytest.mark.parametrize("name", ["木 木", "木木/44", "木木@44", ""])
def test_account_name_rejects_unsafe_characters(name: str) -> None:
    with pytest.raises(ValidationError):
        validate_account_name(name)


def test_admin_username_remains_ascii_only() -> None:
    with pytest.raises(ValidationError):
        validate_username("管理员")


def test_saved_account_is_visible_while_login_cleanup_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services import telegram

    monkeypatch.setattr(telegram, "list_account_names", lambda: ["木木44"])
    monkeypatch.setattr(
        telegram,
        "get_account_profile",
        lambda _name: {"chat_cache_ttl_minutes": 1440},
    )
    telegram._login_sessions["木木44_+440000000"] = {
        "account_name": "木木44",
    }
    try:
        accounts = telegram.TelegramService().list_accounts(force_refresh=True)
        assert [account["name"] for account in accounts] == ["木木44"]
    finally:
        telegram._login_sessions.pop("木木44_+440000000", None)
