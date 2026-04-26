import unittest

from core.input_sanitizer import (
    DEFAULT_USER_INPUT_LIMIT,
    build_user_input_notice,
    sanitize_user_text,
)


class InputSanitizerTests(unittest.TestCase):
    def test_sanitize_user_text_normalizes_line_breaks_and_strips_control_chars(self):
        result = sanitize_user_text("  hello\r\nworld\x00\u2028done\u200b  ")

        self.assertEqual(result.text, "hello\nworld\ndone")
        self.assertEqual(result.removed_control_chars, 2)
        self.assertFalse(result.truncated)
        self.assertTrue(result.changed)
        self.assertIn("Removed unsupported control characters", build_user_input_notice(result))

    def test_sanitize_user_text_truncates_to_runtime_limit(self):
        result = sanitize_user_text("x" * (DEFAULT_USER_INPUT_LIMIT + 25))

        self.assertEqual(len(result.text), DEFAULT_USER_INPUT_LIMIT)
        self.assertTrue(result.truncated)
        self.assertIn("truncated to 10000 characters", build_user_input_notice(result))


if __name__ == "__main__":
    unittest.main()
