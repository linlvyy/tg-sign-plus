import unittest

from tg_signer.challenge_parsers import (
    clean_captcha_ocr_text,
    parse_ordered_button_challenge,
)
from tg_signer.event_runner import (
    _callback_blocks_challenge_progress,
    _interaction_content_signature,
)


BUTTONS = ["🖥️", "🐇", "👑", "1", "👘", "✈️", "🙂", "🔮", "🥕"]


class ChallengeParserTests(unittest.TestCase):
    def test_parse_left_to_right_ordered_button_challenge(self):
        challenge = parse_ordered_button_challenge(
            "🤖 人机验证\n请按照下面目标序列从左往右依次点击：\n👑 ✈ 👘 1 🙂",
            BUTTONS,
        )

        self.assertIsNotNone(challenge)
        self.assertEqual(challenge.direction, "left_to_right")
        self.assertEqual(challenge.targets, ("👑", "✈️", "👘", "1", "🙂"))

    def test_parse_right_to_left_reverses_visual_order(self):
        challenge = parse_ordered_button_challenge(
            "请按照下面目标序列从右往左依次点击：\n👑 ✈️ 👘 1 🙂",
            BUTTONS,
        )

        self.assertIsNotNone(challenge)
        self.assertEqual(challenge.direction, "right_to_left")
        self.assertEqual(challenge.targets, ("🙂", "1", "👘", "✈️", "👑"))

    def test_parse_requires_explicit_direction(self):
        self.assertIsNone(
            parse_ordered_button_challenge("请依次点击：👑 ✈️", BUTTONS)
        )

    def test_clean_captcha_ocr_text_removes_spaces_and_explanation(self):
        self.assertEqual(clean_captcha_ocr_text("Gk GX"), "GkGX")
        self.assertEqual(clean_captcha_ocr_text("验证码是： Gk GX"), "GkGX")
        self.assertEqual(clean_captcha_ocr_text("```text\npf b A\n```"), "pfbA")

    def test_callback_blocking_text_is_not_success(self):
        self.assertTrue(
            _callback_blocks_challenge_progress(
                "已有未完成的人机验证，请先完成当前题目。"
            )
        )
        self.assertTrue(_callback_blocks_challenge_progress("验证码错误"))
        self.assertFalse(_callback_blocks_challenge_progress("签到成功"))

    def test_interaction_signature_ignores_volatile_photo_file_id(self):
        from types import SimpleNamespace

        def message(file_id):
            return SimpleNamespace(
                id=1965,
                text=None,
                caption="请选择功能",
                photo=SimpleNamespace(
                    file_unique_id="stable-photo",
                    file_id=file_id,
                ),
                reply_markup=SimpleNamespace(
                    inline_keyboard=[
                        [
                            SimpleNamespace(
                                text="签到",
                                callback_data=b"sign",
                                url=None,
                            )
                        ]
                    ]
                ),
            )

        self.assertEqual(
            _interaction_content_signature(message("first-file-id")),
            _interaction_content_signature(message("refreshed-file-id")),
        )


if __name__ == "__main__":
    unittest.main()
