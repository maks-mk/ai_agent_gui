import logging
import unittest

from core.logging_config import SensitiveDataFilter


class SensitiveDataFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.filter = SensitiveDataFilter()

    def test_filter_redacts_mapping_values_by_sensitive_key(self):
        record = logging.LogRecord(
            "agent",
            logging.INFO,
            __file__,
            10,
            "Profile payload: %s",
            ({"api_key": "sk-demo-secret", "nested": {"token": "plain-secret-token"}},),
            None,
        )

        self.assertTrue(self.filter.filter(record))
        rendered = record.getMessage()

        self.assertIn("sk-d...<redacted>", rendered)
        self.assertIn("plai...<redacted>", rendered)
        self.assertNotIn("sk-demo-secret", rendered)
        self.assertNotIn("plain-secret-token", rendered)

    def test_filter_redacts_secrets_in_plain_text_message_and_extra_fields(self):
        record = logging.LogRecord(
            "agent",
            logging.INFO,
            __file__,
            20,
            "Authorization: Bearer supersecrettoken api_key=AIzaSyDemoSecretValue",
            (),
            None,
        )
        record.api_key = "gm-demo-secret"

        self.assertTrue(self.filter.filter(record))

        rendered = record.getMessage()
        self.assertIn("Bearer supe...<redacted>", rendered)
        self.assertIn("api_key=AIza...<redacted>", rendered)
        self.assertEqual(record.api_key, "gm-d...<redacted>")
        self.assertNotIn("supersecrettoken", rendered)


if __name__ == "__main__":
    unittest.main()
